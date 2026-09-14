"""对 DeepSeek Harness Python SDK 的唯一封装点（计划 v2 §2.4 / §7.0）。

qresearch 其余代码只依赖 DSHClient 接口；更换运行时只改本文件。
`runner` 注入用于测试（无需安装/启动真实 runtime）。
"""
from __future__ import annotations

import os
import re
import threading
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


# 站点轮次 wallclock 上限：runtime 子进程可能中途死亡而 SDK 等待永不返回
# （P8 live 实测：runner 断链后等待悬挂 >17 分钟才由 runtime 侧超时兜底）。
# 有界超时 + 重启 runtime 把"可能永远卡死"变成"确定性失败 + 重试"。
DEFAULT_STATION_TIMEOUT_S = 15 * 60


class StationTimeout(RuntimeError):
    """站点轮次超时：runtime 无响应，已重启 runtime 子进程。"""

    def __init__(self, session_id: str, timeout_s: float):
        self.session_id = session_id
        self.timeout_s = timeout_s
        super().__init__(
            f"runtime {timeout_s:.0f}s 无响应（session={session_id}），已重启 runtime")


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
        station_timeout_s: float | None = None,
        **harness_kwargs,
    ):
        self._runner = runner
        self._harness = None
        # 超时优先级：显式参数 > 环境变量 > 默认
        configured = (station_timeout_s if station_timeout_s is not None
                      else os.environ.get("QRESEARCH_STATION_TIMEOUT_S"))
        self._timeout_s = (float(configured) if configured is not None
                           else DEFAULT_STATION_TIMEOUT_S)
        if runner is None:
            self._harness, self._harness_args = self._create_harness(
                dsh_home or GLOBAL_DSH_HOME, cwd or ROOT, model, **harness_kwargs)

    def _create_harness(self, dsh_home, cwd, model, **harness_kwargs):
        from deepseek_harness import DeepSeekHarness  # 延迟导入：离线测试无需 SDK

        home = Path(dsh_home)
        home.mkdir(parents=True, exist_ok=True)
        kwargs: dict = {
            "dsh_home": str(home),
            "cwd": str(Path(cwd)),
            "dsh_bin": resolve_dsh_bin(),
            "initialize_timeout_seconds": 120,
            **harness_kwargs,
        }
        if model:
            kwargs["model"] = model
        return DeepSeekHarness(**kwargs), (dsh_home, cwd, model, harness_kwargs)

    def _restart_harness(self) -> None:
        """runtime 挂起/失联后重启子进程：旧 RPC 随旧进程消亡。"""
        try:
            if self._harness is not None:
                self._harness.close()
        except Exception:  # noqa: BLE001 —— 旧 runtime 关闭失败不阻断重启
            pass
        dsh_home, cwd, model, harness_kwargs = self._harness_args
        self._harness, self._harness_args = self._create_harness(
            dsh_home, cwd, model, **harness_kwargs)

    def _run(self, prompt: str, session_id: str) -> str:
        if self._runner is not None:
            return self._runner(prompt, session_id)
        harness = self._harness
        box: dict = {}

        def _work():
            try:
                box["result"] = harness.run(
                    prompt, session_id=session_id).final_response
            except BaseException as e:  # noqa: BLE001 —— 超时后由主线程路径兜底
                box["error"] = e

        worker = threading.Thread(target=_work, daemon=True,
                                  name=f"dsh-run:{session_id}")
        worker.start()
        worker.join(self._timeout_s)
        if worker.is_alive():
            # 响应线程无法终止（daemon 弃置），重启 runtime 换干净通道
            self._restart_harness()
            raise StationTimeout(session_id, self._timeout_s)
        if "error" in box:
            raise box["error"]
        return box["result"]

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
            try:
                last_text = self._run(full, session_id)
            except Exception as e:  # noqa: BLE001 —— runtime 层失败（超时重启/
                # 连接断开/协议错误）：换 session 重试，耗尽转人工
                last_err = e
                full = base
                if event_log is not None:
                    event_log.append(Event(
                        actor=Actor.SYSTEM, action="station_retry",
                        project_id=project_id, object_type="station",
                        object_id=station,
                        detail={"attempt": attempt,
                                "error": f"{type(e).__name__}: {e}"},
                    ))
                continue
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
        raise NeedsHuman(station, last_text,
                         f"{type(last_err).__name__}: {last_err}")
