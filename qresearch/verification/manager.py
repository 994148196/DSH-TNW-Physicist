"""Verification Manager：三层验证编排 + VerificationReport + 证据资格门（计划 v2 §7.6）。

层协议：
- 程序层（software）：同参数可重复（复跑比对）、结果字段完备、边界 case；
- 算法层（numerical）：收敛残差/能量方差/变分上界等数值不变量（工具输出缺失则判 uncertain）；
- 物理层（physics）：工具级 golden 基准（解析值 / 独立实现差分 / 热力学极限 / 自洽性）。

铁律：**不过关的实验取消证据资格**——evidence_gate 只对 overall=PASSED 放行；
UNCERTAIN 需人工复核，同样不放行。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qresearch.core.events import Event, EventLog
from qresearch.core.models import Experiment, VerificationReport, VerificationReportItem
from qresearch.core.status import Actor, VerificationStatus
from qresearch.core.storage import Storage
from qresearch.tools.registry import get_tool, load_seed_tools
from .golden import ROOT as _GOLDEN_ROOT, CheckResult, run_benchmark

RESIDUAL_THRESHOLD = 1e-8  # Lanczos 残差阈值（与 golden 成功标准一致）
RERUN_RTOL = 1e-10  # 复跑可重复的相对容差：物理精度内可重复，ulp 级 BLAS 抖动允许


def _results_match(a: dict[str, Any], b: dict[str, Any]) -> tuple[bool, str]:
    if set(a) != set(b):
        missing = set(a) ^ set(b)
        return False, f"字段集不一致：{sorted(missing)}"
    for k in a:
        x, y = a[k], b[k]
        if isinstance(x, (int, float)) and not isinstance(x, bool) \
                and isinstance(y, (int, float)) and not isinstance(y, bool):
            if abs(float(x) - float(y)) > RERUN_RTOL * max(1.0, abs(float(x))):
                return False, f"字段 {k} 超出相对容差 {RERUN_RTOL:.0e}：{x!r} vs {y!r}"
        elif x != y:
            return False, f"字段 {k} 不一致：{x!r} vs {y!r}"
    return True, f"逐字段一致（相对容差 {RERUN_RTOL:.0e}）"


def _worst(statuses: list[VerificationStatus]) -> VerificationStatus:
    if VerificationStatus.FAILED in statuses:
        return VerificationStatus.FAILED
    if VerificationStatus.UNCERTAIN in statuses:
        return VerificationStatus.UNCERTAIN
    return VerificationStatus.PASSED


class VerificationManager:
    def __init__(
        self,
        storage: Storage,
        event_log: EventLog,
        golden_dir: str | Path | None = None,
    ):
        self.storage = storage
        self.event_log = event_log
        self.golden_dir = Path(golden_dir or _GOLDEN_ROOT / "benchmarks" / "golden")
        load_seed_tools()

    # ---------------------------------------------------------- 工具级验证
    def tool_check_results(self, tool_name: str) -> list[CheckResult]:
        """跑该工具的全部 golden 基准，返回逐检查结果（带 layer）。"""
        results: list[CheckResult] = []
        for path in sorted(self.golden_dir.glob("*.yaml")):
            import yaml

            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if raw.get("tool") != tool_name:
                continue
            report = run_benchmark(path)
            results.extend(report.results)
        return results

    def verify_tool(self, tool_name: str) -> list[VerificationReportItem]:
        """工具三层基准 → 逐层 VerificationReportItem（验收 Phase 6 也复用）。"""
        results = self.tool_check_results(tool_name)
        if not results:
            return [VerificationReportItem(
                claim=f"工具 {tool_name} 无 golden 基准",
                layer="software", status=VerificationStatus.UNCERTAIN,
                detail="基准清单缺失：无法验证，禁止出具物理证据",
            )]
        items: list[VerificationReportItem] = []
        for layer in ("software", "numerical", "physics"):
            layer_results = [r for r in results if r.layer == layer]
            if not layer_results:
                items.append(VerificationReportItem(
                    claim=f"[{layer}] 层无基准覆盖", layer=layer,
                    status=VerificationStatus.UNCERTAIN,
                    detail="该层没有 golden case：判定为不确定",
                ))
                continue
            failed = [r for r in layer_results if not r.passed]
            status = VerificationStatus.FAILED if failed else VerificationStatus.PASSED
            detail = (
                f"{len(layer_results)} 项检查，{len(failed)} 项失败："
                + "; ".join(f"{r.case}（{r.detail}）" for r in failed[:5])
                if failed
                else f"{len(layer_results)} 项检查全部通过："
                + "; ".join(f"{r.case}" for r in layer_results[:8])
            )
            items.append(VerificationReportItem(
                claim=f"工具 {tool_name} [{layer}] 层基准", layer=layer,
                status=status, detail=detail,
            ))
        return items

    # ---------------------------------------------------------- 实验级验证
    def verify_experiment(
        self, experiment: Experiment, tool_name: str, *, check_determinism: bool = True
    ) -> VerificationReport:
        items: list[VerificationReportItem] = []
        stored = self._stored_result(experiment)

        # ---- 程序层 1：结果文件可读且字段完备
        if stored is None:
            items.append(VerificationReportItem(
                claim="实验结果可读", layer="software",
                status=VerificationStatus.FAILED,
                detail=f"无法读取或解析 {experiment.artifacts}",
            ))
        else:
            items.append(VerificationReportItem(
                claim="实验结果可读", layer="software",
                status=VerificationStatus.PASSED,
                detail=f"字段：{sorted(stored)}",
            ))

        # ---- 程序层 2：同参数复跑可重复（物理精度内）
        if check_determinism and stored is not None:
            try:
                rerun = get_tool(tool_name).run(
                    {k: v for k, v in experiment.parameters.items()
                     if not k.startswith("_")}
                )
                same, why = _results_match(rerun, stored)
                items.append(VerificationReportItem(
                    claim="同参数复跑可重复", layer="software",
                    status=VerificationStatus.PASSED if same else VerificationStatus.FAILED,
                    detail=why,
                ))
            except Exception as exc:  # noqa: BLE001
                items.append(VerificationReportItem(
                    claim="同参数复跑可重复", layer="software",
                    status=VerificationStatus.FAILED,
                    detail=f"复跑异常：{type(exc).__name__}: {exc}",
                ))

        # ---- 算法层：数值不变量（字段缺失 → uncertain，不冒充合格）
        if stored is None:
            items.append(VerificationReportItem(
                claim="数值收敛指标", layer="numerical",
                status=VerificationStatus.UNCERTAIN,
                detail="结果缺失，无法检查",
            ))
        else:
            if "residual" in stored:
                resid = float(stored["residual"])
                ok = resid <= RESIDUAL_THRESHOLD
                items.append(VerificationReportItem(
                    claim=f"Lanczos 残差 ≤ {RESIDUAL_THRESHOLD:.0e}", layer="numerical",
                    status=VerificationStatus.PASSED if ok else VerificationStatus.FAILED,
                    detail=f"residual = {resid:.3e}",
                ))
            if "E_var" in stored:
                e_var, e0 = float(stored["E_var"]), float(stored["E0"])
                ok = e_var >= e0
                items.append(VerificationReportItem(
                    claim="变分上界 E_var ≥ E0", layer="numerical",
                    status=VerificationStatus.PASSED if ok else VerificationStatus.FAILED,
                    detail=f"E_var = {e_var!r}, E0 = {e0!r}",
                ))
            if not any(i.layer == "numerical" for i in items):
                items.append(VerificationReportItem(
                    claim="数值收敛指标", layer="numerical",
                    status=VerificationStatus.UNCERTAIN,
                    detail="工具输出无 residual/E_var 字段，数值层无法自动判定",
                ))

        # ---- 物理层：继承工具级 golden 基准结论
        items.extend(self.verify_tool(tool_name))

        report = VerificationReport(
            project_id=experiment.project_id, experiment_id=experiment.experiment_id,
            items=items, overall=_worst([i.status for i in items]),
        )
        self.storage.save(report)

        experiment.verification_status = report.overall
        self.storage.save(experiment)
        self.event_log.append(Event(
            actor=Actor.SYSTEM, action="verify_experiment",
            project_id=experiment.project_id, object_type="VerificationReport",
            object_id=report.report_id,
            detail={
                "experiment": experiment.experiment_id, "overall": report.overall.value,
                "n_items": len(items),
                "failed": [i.claim for i in items if i.status == VerificationStatus.FAILED],
            },
        ))
        return report

    # ---------------------------------------------------------- 证据资格门
    def evidence_gate(self, report: VerificationReport) -> tuple[bool, str]:
        """不过关的实验取消证据资格。只有 overall=PASSED 放行。"""
        if report.overall == VerificationStatus.PASSED:
            return True, "验证通过，允许出具证据"
        if report.overall == VerificationStatus.UNCERTAIN:
            failed = [i.claim for i in report.items if i.status == VerificationStatus.UNCERTAIN]
            return False, f"存在不确定项（{'; '.join(failed)}），需人工复核后才能出具证据"
        failed = [i.claim for i in report.items if i.status == VerificationStatus.FAILED]
        return False, f"验证未通过（{'; '.join(failed)}），禁止出具证据"

    # ---------------------------------------------------------- internals
    @staticmethod
    def _stored_result(experiment: Experiment) -> dict[str, Any] | None:
        for artifact in experiment.artifacts:
            p = Path(artifact)
            if p.name == "result.json" and p.exists():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    return None
        return None
