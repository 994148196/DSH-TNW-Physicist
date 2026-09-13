"""对 DeepSeek Harness Python SDK 的唯一封装点（计划 v2 §2.4 / §7.0）。

qresearch 其余代码只依赖 DSHClient 接口；更换运行时只改本文件。
`runner` 注入用于测试（无需安装/启动真实 runtime）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError

from qresearch.core.events import Event, EventLog
from qresearch.core.status import Actor

ROOT = Path(__file__).resolve().parents[1]

T = TypeVar("T", bound=BaseModel)

# runner 协议：(prompt, session_id) -> 模型回复文本
Runner = Callable[[str, str], str]

GLOBAL_DSH_HOME = ROOT / "research_data" / "dsh_home"

SCHEMA_INSTRUCTION = (
    "\n\n输出要求：只输出一个 JSON 对象，不要输出任何其他文字、解释或代码围栏。"
    "JSON 必须符合以下 JSON Schema：\n{schema}"
)

_RETRY_INSTRUCTION = (
    "\n\n注意：你上一次的输出不合法（{error}）。"
    "请修正后重新输出，仍然只输出一个符合 Schema 的 JSON 对象。"
)


class NeedsHuman(RuntimeError):
    """站点输出校验重试耗尽，转人工（计划 v2 §7.0 错误处理三分类之一）。"""

    def __init__(self, station: str, last_output: str, reason: str):
        self.station = station
        self.last_output = last_output
        self.reason = reason
        super().__init__(f"站点 {station} 需要人工介入: {reason}")


def resolve_dsh_bin() -> str | None:
    """解析 dsh 可执行文件：环境变量 → 项目隔离安装的 npm dsh → 交给 SDK 内置解析。"""
    override = os.environ.get("QRESEARCH_DSH_BIN")
    if override:
        return override
    local = ROOT / ".dsh-runtime" / "node_modules" / ".bin" / "dsh.cmd"
    return str(local) if local.exists() else None


def extract_json(text: str) -> str:
    """从模型回复中提取 JSON 文本（容忍 markdown 围栏与前后散文）。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("回复中未找到 JSON 对象")
    return text[start : end + 1]


class DSHClient:
    """持有并复用一个 DSH runtime 子进程；`runner` 注入时完全离线（测试用）。"""

    def __init__(
        self,
        dsh_home: str | Path | None = None,
        cwd: str | Path | None = None,
        model: str | None = None,
        runner: Runner | None = None,
        **harness_kwargs,
    ):
        self._runner = runner
        self._harness = None
        if runner is None:
            from deepseek_harness import DeepSeekHarness  # 延迟导入：离线测试无需 SDK

            home = Path(dsh_home or GLOBAL_DSH_HOME)
            home.mkdir(parents=True, exist_ok=True)
            kwargs: dict = {
                "dsh_home": str(home),
                "cwd": str(Path(cwd or ROOT)),
                "dsh_bin": resolve_dsh_bin(),
                "initialize_timeout_seconds": 120,
                **harness_kwargs,
            }
            if model:
                kwargs["model"] = model
            self._harness = DeepSeekHarness(**kwargs)

    def _run(self, prompt: str, session_id: str) -> str:
        if self._runner is not None:
            return self._runner(prompt, session_id)
        return self._harness.run(prompt, session_id=session_id).final_response

    def close(self) -> None:
        if self._harness is not None:
            self._harness.close()

    def __enter__(self) -> "DSHClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def run_agent(self, prompt: str, session_id: str) -> str:
        """开放轮次：无 JSON Schema 约束的 agent 任务（如 Tool Builder 编码站）。

        与 call_station 的区别：不校验、不重试——交付物是文件而非结构化输出，
        由调用方在文件层面验收。
        """
        return self._run(prompt, session_id)

    def call_station(
        self,
        station: str,
        project_id: str,
        schema: type[T],
        prompt: str,
        retries: int = 1,
        event_log: EventLog | None = None,
        validator: Callable[[T], None] | None = None,
    ) -> T:
        """执行一次站点轮次：prompt + schema 约束 → Pydantic 校验 → 可选语义校验。

        失败带错误反馈重试；耗尽抛 NeedsHuman（转人工）。
        `validator`：schema 通过后的领域校验（如"引用的实验必须通过验证"），
        抛 ValueError 视同校验失败。
        """
        base = prompt + SCHEMA_INSTRUCTION.format(schema=schema.model_json_schema())
        full = base
        last_text = ""
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            session_id = f"{project_id}:{station}:a{attempt}"
            last_text = self._run(full, session_id)
            try:
                out = schema.model_validate_json(extract_json(last_text))
                if validator is not None:
                    validator(out)
            except (ValueError, ValidationError) as e:
                last_err = e
                full = base + _RETRY_INSTRUCTION.format(error=e)
                continue
            if event_log is not None:
                event_log.append(Event(
                    actor=Actor.MODEL, action="station_call",
                    project_id=project_id, object_type="station", object_id=station,
                    detail={"attempt": attempt, "session": session_id},
                ))
            return out
        raise NeedsHuman(station, last_text, str(last_err))
