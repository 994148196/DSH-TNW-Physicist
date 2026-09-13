"""Experiment Manager：执行计划步骤中的数值实验。

铁律（计划 v2）：实验不经 LLM。工具经 ToolRunner 执行——默认子进程隔离
（崩溃不伤主进程、可复现元数据落盘），测试可注入进程内 runner。
"""
from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qresearch import __version__
from qresearch.core.events import Event, EventLog
from qresearch.core.models import Experiment, PlanStep, ResearchPlan, ToolRecord
from qresearch.core.status import Actor, ExperimentStatus
from qresearch.core.storage import Storage
from qresearch.tools.registry import ToolSpec, get_tool, load_seed_tools

ROOT = Path(__file__).resolve().parents[2]

RUNNABLE_ACTIONS = {"run_experiment", "parameter_scan"}


class ToolRunError(RuntimeError):
    """工具执行失败（退出码非零 / 超时 / 输出不可解析）。"""


# ---------------------------------------------------------------- runners
@dataclass
class RunOutcome:
    result: dict[str, Any]
    log_text: str


class ToolRunner:
    def run(self, spec: ToolSpec, inputs: dict[str, Any], workspace: Path,
            timeout_s: float) -> RunOutcome:  # pragma: no cover - 接口
        raise NotImplementedError


class InProcessToolRunner(ToolRunner):
    """测试用：进程内直接调用。"""

    def run(self, spec, inputs, workspace, timeout_s):
        t0 = time.monotonic()
        result = spec.run(inputs)
        elapsed = time.monotonic() - t0
        log = f"in-process run {spec.name} finished in {elapsed:.2f}s"
        return RunOutcome(result=result, log_text=log)


class SubprocessToolRunner(ToolRunner):
    """默认：子进程执行 `python -m qresearch.tools.cli run`，输出 JSON 文件。"""

    def run(self, spec, inputs, workspace, timeout_s):
        workspace.mkdir(parents=True, exist_ok=True)
        in_path = workspace / "inputs.json"
        out_path = workspace / "result.json"
        in_path.write_text(json.dumps(inputs, ensure_ascii=False, indent=2), encoding="utf-8")
        cmd = [
            sys.executable, "-X", "utf8", "-m", "qresearch.tools.cli", "run",
            "--tool", spec.name, "--inputs-file", str(in_path), "--out", str(out_path),
        ]
        proc = subprocess.run(
            cmd, cwd=str(workspace), capture_output=True, text=True,
            timeout=timeout_s, encoding="utf-8",
        )
        log = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            raise ToolRunError(f"工具 {spec.name} 退出码 {proc.returncode}：{log[-2000:]}")
        if not out_path.exists():
            raise ToolRunError(f"工具 {spec.name} 未产出 result.json")
        try:
            result = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ToolRunError(f"工具 {spec.name} 输出不可解析：{e}") from e
        return RunOutcome(result=result, log_text=log)


# ---------------------------------------------------------------- manager
class ExperimentManager:
    def __init__(
        self,
        storage: Storage,
        event_log: EventLog,
        runner: ToolRunner | None = None,
        experiments_root: str | Path | None = None,
        default_timeout_s: float = 900.0,
    ):
        self.storage = storage
        self.event_log = event_log
        self.runner = runner or SubprocessToolRunner()
        self.root = Path(experiments_root or ROOT / "research_data" / "experiments")
        self.default_timeout_s = default_timeout_s
        load_seed_tools()

    # ---- 工具记录入库（跨项目共享，project_id=global）
    def _ensure_tool_record(self, spec: ToolSpec) -> ToolRecord:
        for record in self.storage.list(ToolRecord):
            if record.name == spec.name:
                return record
        record = ToolRecord(**spec.to_tool_record_kwargs())
        self.storage.save(record)
        return record

    # ---- 单步实验
    def execute_step(self, plan: ResearchPlan, step: PlanStep) -> Experiment:
        if step.action not in RUNNABLE_ACTIONS:
            raise ValueError(f"步骤 {step.step_id} 动作 {step.action} 不是可执行实验动作")
        if len(step.tools) != 1:
            raise ValueError(
                f"步骤 {step.step_id} 必须恰好引用 1 个工具（实际 {len(step.tools)}）"
            )

        experiment = Experiment(
            project_id=plan.project_id, plan_id=plan.plan_id, step_id=step.step_id,
            tool_id="", parameters=dict(step.inputs),
            status=ExperimentStatus.RUNNING, seed=0,
            code_version=__version__,
            environment=self._environment(),
        )
        # 未知工具按失败落账，不炸闭环（计划 v2 §7.0：失败也要落账）
        try:
            spec = get_tool(step.tools[0])
        except KeyError as exc:
            experiment.status = ExperimentStatus.FAILED
            experiment.tool_id = step.tools[0]
            experiment.error = f"{type(exc).__name__}: {exc}"
            experiment.finished_at = _now()
            self.storage.save(experiment)
            self._event("experiment_finished", experiment,
                        detail_extra={"status": "failed", "elapsed_s": 0.0})
            return experiment
        record = self._ensure_tool_record(spec)
        experiment.tool_id = record.tool_id

        self.storage.save(experiment)
        self._event("experiment_started", experiment)

        workspace = self.root / experiment.experiment_id
        workspace.mkdir(parents=True, exist_ok=True)
        experiment.log_path = str(workspace / "run.log")

        t0 = time.monotonic()
        try:
            outcome = self.runner.run(spec, step.inputs, workspace, self.default_timeout_s)
            elapsed = time.monotonic() - t0
            (workspace / "result.json").write_text(
                json.dumps(outcome.result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (workspace / "run.log").write_text(outcome.log_text, encoding="utf-8")
            experiment.status = ExperimentStatus.COMPLETED
            experiment.artifacts = [str(workspace / "result.json"), str(workspace / "run.log")]
            experiment.parameters = {**step.inputs, "_elapsed_s": round(elapsed, 3)}
        except Exception as exc:  # noqa: BLE001 —— 失败也要落账（计划 v2 §7.0）
            elapsed = time.monotonic() - t0
            experiment.status = ExperimentStatus.FAILED
            experiment.error = f"{type(exc).__name__}: {exc}"
            try:
                (workspace / "run.log").write_text(str(exc), encoding="utf-8")
                experiment.artifacts = [str(workspace / "run.log")]
            except OSError:
                pass
        experiment.finished_at = _now()
        self.storage.save(experiment)
        self._event(
            "experiment_finished", experiment,
            detail_extra={"status": experiment.status.value, "elapsed_s": round(elapsed, 3)},
        )
        return experiment

    # ---- 批量扫描：inputs.scan = {param: [值...]}，每个参数点一个 Experiment
    def execute_scan(self, plan: ResearchPlan, step: PlanStep) -> list[Experiment]:
        inputs = dict(step.inputs)
        scan = inputs.pop("scan", None)
        if not isinstance(scan, dict) or not scan:
            # 缺 scan 声明：按失败落账，不炸闭环（计划 v2 §7.0）
            return [self._record_invalid_step(plan, step, "声明 parameter_scan 但缺 inputs.scan")]
        keys = sorted(scan)
        combos = _product_grid([scan[k] for k in keys])
        experiments: list[Experiment] = []
        for idx, values in enumerate(combos):
            point = dict(inputs)
            for k, v in zip(keys, values):
                point[k] = v
            point["_scan"] = {k: v for k, v in zip(keys, values)}
            point["_scan_index"] = idx
            scan_step = step.model_copy(update={"inputs": point, "step_id": f"{step.step_id}_{idx}"})
            experiments.append(self.execute_step(plan, scan_step))
        return experiments

    def _record_invalid_step(self, plan: ResearchPlan, step: PlanStep, why: str) -> Experiment:
        from qresearch.core.status import ExperimentStatus

        experiment = Experiment(
            project_id=plan.project_id, plan_id=plan.plan_id, step_id=step.step_id,
            tool_id=step.tools[0] if step.tools else "", parameters=dict(step.inputs),
            status=ExperimentStatus.FAILED, seed=0, code_version=__version__,
            environment=self._environment(), error=f"ValueError: {why}",
            finished_at=_now(),
        )
        self.storage.save(experiment)
        self._event("experiment_finished", experiment,
                    detail_extra={"status": "failed", "elapsed_s": 0.0})
        return experiment

    # ---- 整计划
    def execute_plan(
        self, plan: ResearchPlan, *, include_requires_approval: bool = False
    ) -> list[Experiment]:
        experiments: list[Experiment] = []
        for step in plan.steps:
            if step.action not in RUNNABLE_ACTIONS:
                continue
            if step.requires_approval and not include_requires_approval:
                continue
            if step.action == "parameter_scan" or "scan" in step.inputs:
                experiments.extend(self.execute_scan(plan, step))
            else:
                experiments.append(self.execute_step(plan, step))
        return experiments

    # ---- 摘要
    def summarize(self, experiments: list[Experiment]) -> str:
        lines = ["| 实验 | 步骤 | 工具 | 状态 | 关键输出 |", "|---|---|---|---|---|"]
        for e in experiments:
            key = ""
            if e.status == ExperimentStatus.COMPLETED:
                r = _load_result(e)
                if r:
                    key = "; ".join(
                        f"{k}={r[k]}" for k in ("E0", "e0", "gap", "method") if k in r
                    )
            elif e.error:
                key = e.error[:60]
            lines.append(
                f"| {e.experiment_id} | {e.step_id} | {e.tool_id} | {e.status.value} | {key} |"
            )
        return "\n".join(lines)

    # ---- internals
    def _event(self, action: str, experiment: Experiment, detail_extra: dict | None = None):
        self.event_log.append(Event(
            actor=Actor.SYSTEM, action=action, project_id=experiment.project_id,
            object_type="Experiment", object_id=experiment.experiment_id,
            detail={"tool": experiment.tool_id, **(detail_extra or {})},
        ))

    @staticmethod
    def _environment() -> dict[str, Any]:
        import numpy
        import scipy

        return {
            "python": platform.python_version(),
            "numpy": numpy.__version__,
            "scipy": scipy.__version__,
            "qresearch": __version__,
            "platform": platform.platform(),
        }


def _product_grid(value_lists: list[list]) -> list[tuple]:
    import itertools

    return list(itertools.product(*value_lists))


def _load_result(e: Experiment) -> dict[str, Any] | None:
    if not e.artifacts:
        return None
    p = Path(e.artifacts[0])
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _now():
    from qresearch.core.models import utcnow

    return utcnow()
