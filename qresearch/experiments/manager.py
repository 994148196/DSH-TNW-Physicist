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
from typing import Any, Callable

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
        # resolve()：workspace 路径会传给**子进程**，而子进程 cwd 是实验目录
        # 本身——相对 experiments_root（如会话默认的 research_data/proj_x/…）在
        # 子进程里解析到错误位置，inputs.json 明明在却打不开（2026-09-15 实测：
        # 交互式会话默认 data-root 下 27 个实验全部 FileNotFoundError）。
        self.root = Path(experiments_root or ROOT / "research_data" / "experiments").resolve()
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
        experiment, spec, _s = self._prepare(plan, step)
        if spec is None:
            return experiment  # 未知工具：建账时已按失败落账
        return self._run_and_finalize(experiment, spec, _s)

    def _prepare(
        self, plan: ResearchPlan, step: PlanStep,
    ) -> tuple[Experiment, ToolSpec | None, PlanStep]:
        """建账：创建 RUNNING 实验并落 started 事件。

        未知工具当场按失败落账（计划 v2 §7.0），返回 spec=None。
        """
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
            return experiment, None, step
        record = self._ensure_tool_record(spec)
        experiment.tool_id = record.tool_id

        self.storage.save(experiment)
        self._event("experiment_started", experiment)

        workspace = self.root / experiment.experiment_id
        workspace.mkdir(parents=True, exist_ok=True)
        experiment.log_path = str(workspace / "run.log")
        return experiment, spec, step

    def _compute(self, spec: ToolSpec, inputs: dict, workspace: Path):
        """只做计算（线程池可并行段）：返回 (outcome, elapsed)，异常向上抛。"""
        t0 = time.monotonic()
        outcome = self.runner.run(spec, inputs, workspace, self.default_timeout_s)
        return outcome, time.monotonic() - t0

    def _run_and_finalize(
        self, experiment: Experiment, spec: ToolSpec, step: PlanStep,
    ) -> Experiment:
        """执行计算并落账（主线程段：storage/events 只在此触碰）。"""
        workspace = self.root / experiment.experiment_id
        t0 = time.monotonic()
        try:
            outcome, _ = self._compute(spec, step.inputs, workspace)
        except Exception as exc:  # noqa: BLE001 —— 失败也要落账（计划 v2 §7.0）
            self._finalize_failure(experiment, workspace, exc,
                                   elapsed=time.monotonic() - t0)
            return experiment
        return self._finalize_success(experiment, step, outcome,
                                      elapsed=time.monotonic() - t0)

    # ---- 批量扫描：inputs.scan = {param: [值...]}，每个参数点一个 Experiment
    def execute_scan(
        self, plan: ResearchPlan, step: PlanStep, *, max_workers: int = 1,
    ) -> list[Experiment]:
        inputs = dict(step.inputs)
        scan = inputs.pop("scan", None)
        if not isinstance(scan, dict) or not scan:
            # 缺 scan 声明：按失败落账，不炸闭环（计划 v2 §7.0）
            return [self._record_invalid_step(plan, step, "声明 parameter_scan 但缺 inputs.scan")]
        keys = sorted(scan)
        combos = _product_grid([scan[k] for k in keys])
        prepared: list[tuple[Experiment, ToolSpec, PlanStep]] = []
        for idx, values in enumerate(combos):
            point = dict(inputs)
            for k, v in zip(keys, values):
                point[k] = v
            point["_scan"] = {k: v for k, v in zip(keys, values)}
            point["_scan_index"] = idx
            scan_step = step.model_copy(update={"inputs": point, "step_id": f"{step.step_id}_{idx}"})
            prepared.append(self._prepare(plan, scan_step))
        return self._finish_prepared(prepared, max_workers=max_workers)

    def _finish_prepared(
        self,
        prepared: list[tuple[Experiment, ToolSpec | None, PlanStep]],
        *,
        max_workers: int = 1,
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[Experiment]:
        """执行已建账的实验。并行时只并行计算段，落账留在主线程按序进行
        （Storage 非线程安全；EventLog 自带锁）。

        cancel_check（M3）：每个实验开跑前检查——已建账但未执行的按取消落账
        （FAILED，事件标 cancelled）；已在跑的照常完成（协作式，不杀进程）。
        """
        ready = [(e, spec, s) for e, spec, s in prepared if spec is not None]
        done: list[Experiment] = [e for e, spec, _s in prepared if spec is None]
        if max_workers <= 1 or len(ready) <= 1:
            for e, spec, s in ready:
                if cancel_check is not None and cancel_check():
                    done.append(self._finalize_cancelled(e))
                    continue
                done.append(self._run_and_finalize(e, spec, s))
            return done
        from concurrent.futures import ThreadPoolExecutor

        done_futures = []
        cancelled: list[tuple[int, Experiment]] = []
        with ThreadPoolExecutor(max_workers=min(max_workers, len(ready))) as pool:
            for i, (e, spec, s) in enumerate(ready):
                if cancel_check is not None and cancel_check():
                    cancelled.append((i, e))
                    continue
                t0 = time.monotonic()
                done_futures.append((i, e, s, t0, pool.submit(self._compute, spec, s.inputs,
                                                              self.root / e.experiment_id)))
            # 按提交顺序落账（审计可读）；被取消的按原位次插入，保持账目次序
            results: dict[int, Experiment] = {}
            for i, e, s, t0, fut in done_futures:
                elapsed = time.monotonic() - t0
                try:
                    outcome, _ = fut.result()
                except Exception as exc:  # noqa: BLE001 —— 失败也要落账
                    self._finalize_failure(e, self.root / e.experiment_id, exc, elapsed=elapsed)
                    results[i] = e
                    continue
                results[i] = self._finalize_success(e, s, outcome, elapsed)
            for i, e in cancelled:
                results[i] = self._finalize_cancelled(e)
            done.extend(results[i] for i in sorted(results))
        return done

    def _finalize_cancelled(self, experiment: Experiment) -> Experiment:
        """协作式取消的诚实落账：账已建（started）但未执行——FAILED + 取消标注
        （状态机不引入 CANCELLED：验证层对 FAILED 的处理天然适用）。"""
        experiment.status = ExperimentStatus.FAILED
        experiment.error = "CancelledError: 人工取消（job_cancel），实验未执行"
        experiment.finished_at = _now()
        self.storage.save(experiment)
        self._event("experiment_finished", experiment,
                    detail_extra={"status": "cancelled", "elapsed_s": 0.0})
        return experiment

    def _finalize_success(
        self, experiment: Experiment, step: PlanStep, outcome, elapsed: float,
    ) -> Experiment:
        workspace = self.root / experiment.experiment_id
        (workspace / "result.json").write_text(
            json.dumps(outcome.result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (workspace / "run.log").write_text(outcome.log_text, encoding="utf-8")
        experiment.status = ExperimentStatus.COMPLETED
        experiment.artifacts = [str(workspace / "result.json"), str(workspace / "run.log")]
        experiment.parameters = {**step.inputs, "_elapsed_s": round(elapsed, 3)}
        experiment.finished_at = _now()
        self.storage.save(experiment)
        self._event(
            "experiment_finished", experiment,
            detail_extra={"status": experiment.status.value, "elapsed_s": round(elapsed, 3)},
        )
        return experiment

    def _finalize_failure(
        self, experiment: Experiment, workspace: Path, exc: Exception, *, elapsed: float,
    ) -> None:
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
            detail_extra={"status": "failed", "elapsed_s": round(elapsed, 3)},
        )

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
        self, plan: ResearchPlan, *,
        include_requires_approval: bool = False, max_workers: int = 1,
        cancel_check: Callable[[], bool] | None = None,
    ) -> list[Experiment]:
        """执行计划全部可执行步骤。max_workers>1 时实验并行计算（落账仍按序）。

        注：requires_approval 步骤默认跳过（审批后由 include_requires_approval 显式放开）。
        cancel_check（M3 协作式取消）：准备与执行两段都检查——未建账的步骤
        不再产生记录；已建账未执行的按取消落账（FAILED，事件标 cancelled）；
        已在跑的照常完成（协作式，不杀进程）。
        """
        # 先统一建账（scan 展开 + started 事件），再按需并行计算
        prepared: list[tuple[Experiment, ToolSpec | None, PlanStep]] = []
        for step in plan.steps:
            if cancel_check is not None and cancel_check():
                break
            if step.action not in RUNNABLE_ACTIONS:
                continue
            if step.requires_approval and not include_requires_approval:
                continue
            if step.action == "parameter_scan" or "scan" in step.inputs:
                inputs = dict(step.inputs)
                scan = inputs.pop("scan", None)
                if not isinstance(scan, dict) or not scan:
                    prepared.append((self._record_invalid_step(
                        plan, step, "声明 parameter_scan 但缺 inputs.scan"), None, step))
                    continue
                keys = sorted(scan)
                for idx, values in enumerate(_product_grid([scan[k] for k in keys])):
                    if cancel_check is not None and cancel_check():
                        break
                    point = dict(inputs)
                    for k, v in zip(keys, values):
                        point[k] = v
                    point["_scan"] = {k: v for k, v in zip(keys, values)}
                    point["_scan_index"] = idx
                    scan_step = step.model_copy(
                        update={"inputs": point, "step_id": f"{step.step_id}_{idx}"})
                    prepared.append(self._prepare(plan, scan_step))
            else:
                prepared.append(self._prepare(plan, step))
        return self._finish_prepared(prepared, max_workers=max_workers,
                                     cancel_check=cancel_check)

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
