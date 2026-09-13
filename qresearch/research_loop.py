"""研究闭环（计划 v2 §6 / Phase 5）：PLAN→审批→EXECUTE→VERIFY→ANALYZE→DECIDE 循环。

确定性骨架在本模块；智能在站点执行器内（经 DSH）。
预算闸：rounds 上限，预算耗尽时代码强制 requires_human（LLM 只提议，代码记账）。
人工触点：计划审批与终止确认——由 Decision.requires_human 承载。
全程可回放：所有对象都在 storage/events 里，报告自动生成。
站点重试耗尽（NeedsHuman）按 §7.0 转人工：落账事件、生成部分报告、返回 needs_human 状态。
"""
from __future__ import annotations

from pathlib import Path

from qresearch.core.events import Event, EventLog
from qresearch.core.models import Project, ResearchPlan
from qresearch.core.status import Actor, PlanStatus, VerificationStatus
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient, NeedsHuman
from qresearch.loop import _interactive_approval, approve_plan
from .stations.executors import analyze, decide, hypothesize, plan_with_critic, understand


# ================================================================ 报告生成
def generate_report(
    project: Project, goal, hypotheses: list, plans: list,
    experiment_rows: list[dict], decisions: list, analyses: list,
) -> str:
    """生成 markdown 研究报告：结论全部可追溯到 experiment/evidence。"""
    lines = [
        f"# 研究报告：{project.title}",
        "",
        f"- 项目：`{project.project_id}`",
        f"- 研究问题：{goal.question if goal else project.question}",
        f"- 目标量：{'、'.join(goal.quantities) if goal else '—'}",
        "",
        "## 假设",
        *[
            f"- {h.statement}\n"
            f"  - 证伪：{'；'.join(h.falsification_tests)}"
            for h in hypotheses
        ],
        "",
        "## 计划",
        *[f"- 计划 v{p.version}（{p.status.value}）：{len(p.steps)} 步" for p in plans],
        "",
        "## 实验（三层验证后）",
        "| 实验 | 步骤 | 工具 | 状态 | 关键结果 |",
        "|---|---|---|---|---|",
        *[
            f"| {r['experiment_id']} | {r['step_id']} | {r['tool']} | {r['status']} | "
            f"{'; '.join(f'{k}={v}' for k, v in r['key_results'].items())} |"
            for r in experiment_rows
        ],
        "",
        "## 分析结论（每条挂实验 id）",
    ]
    for a in analyses:
        for c in a.observations:
            lines.append(f"- 观察：{c.claim}（{', '.join(c.experiment_ids)}）")
        for c in a.interpretations:
            lines.append(f"- 解读：{c.claim}（{', '.join(c.experiment_ids)}）")
        if a.uncertainties:
            lines.append(f"- 不确定：{'；'.join(a.uncertainties)}")
    lines += ["", "## 决策记录"]
    for d in decisions:
        lines.append(
            f"- [{d.type.value}] 建议 {d.recommendation.value}"
            f"（checklist {len(d.checklist)} 项，人工确认={'是' if d.requires_human else '否'}）："
            f"{d.rationale or ''}"
        )
    lines += [
        "",
        "## 局限与不确定性",
        "- 见各轮分析 uncertainties；本报告由确定性骨架自动生成，"
        "对外发布前需人工复核（declare_result 决策无条件人工确认）。",
    ]
    return "\n".join(lines)


# ================================================================ 主循环
def _experiment_rows(experiments: list, eligible_ids: set[str],
                     tool_by_step: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """实验分为"可引用（验证通过）"与"全部"（计划 v2 §7.6 证据资格门）。"""
    from qresearch.experiments.manager import _load_result

    eligible, all_rows = [], []
    for exp in experiments:
        result = _load_result(exp) or {}
        row = {
            "experiment_id": exp.experiment_id, "step_id": exp.step_id,
            "tool": tool_by_step.get(exp.step_id, "?"), "inputs": exp.parameters,
            "key_results": {
                k: result.get(k)
                for k in ("E0", "e0", "gap", "Sz2", "method") if k in result
            },
            "status": exp.status.value,
        }
        all_rows.append(row)
        if exp.experiment_id in eligible_ids and exp.status.value == "completed":
            eligible.append(row)
    return eligible, all_rows


def run_research_loop(
    client: DSHClient,
    storage: Storage,
    event_log: EventLog,
    project_id: str,
    question: str,
    *,
    rounds: int = 3,
    auto_approve: bool = False,
    n_hypotheses: int = 3,
    retries: int = 1,
    experiments_root: str | Path | None = None,
) -> dict:
    """完整研究闭环。返回 summary dict；所有状态可从 storage/events 回放。"""
    from qresearch.core.status import (
        DecisionRecommendation, DecisionType, PlanStatus, VerificationStatus,
    )
    from qresearch.experiments.manager import ExperimentManager
    from qresearch.verification.manager import VerificationManager

    needs_human: str | None = None

    # Round 0：理解 + 假设
    project = Project(project_id=project_id, title=question[:40], question=question)
    storage.save(project)
    event_log.append(Event(actor=Actor.SYSTEM, action="create_project",
                           project_id=project_id, object_type="Project",
                           object_id=project_id, detail={}))
    goal = None
    hypotheses: list = []
    try:
        goal = understand(client, project_id, question, event_log=event_log, retries=retries)
        storage.save(goal)
        event_log.append(Event(actor=Actor.SYSTEM, action="save",
                               project_id=project_id, object_type="Goal",
                               object_id=goal.goal_id, detail={}))
        hypotheses = hypothesize(client, project_id, goal, n=n_hypotheses,
                                 event_log=event_log, retries=retries)
        for h in hypotheses:
            storage.save(h)
    except NeedsHuman as e:
        needs_human = f"Round 0（理解/假设）：{e}"

    if experiments_root is None:
        experiments_root = Path(storage.path).parent / "experiments"
    manager = ExperimentManager(storage, event_log, experiments_root=experiments_root)
    verifier = VerificationManager(storage, event_log)

    previous_plan: ResearchPlan | None = None
    last_decision_id: str | None = None
    all_decisions, all_analyses, all_rows = [], [], []
    all_evidence: list = []
    tool_by_step: dict[str, str] = {}
    terminated = False

    try:
        for round_no in range(1, rounds + 1):
            # ---- PLAN（critic 修订循环）
            plan, critique = plan_with_critic(
                client, project_id, goal, hypotheses,
                previous=previous_plan, decision_id=last_decision_id,
                event_log=event_log, retries=retries,
            )
            plan.status = PlanStatus.AWAITING_APPROVAL
            storage.save(plan)
            event_log.append(Event(actor=Actor.SYSTEM, action="plan_ready",
                                   project_id=project_id, object_type="ResearchPlan",
                                   object_id=plan.plan_id,
                                   detail={"version": plan.version, "verdict": critique.verdict,
                                           "round": round_no}))
            # ---- 审批（人工触点 1）
            if auto_approve:
                approve_plan(storage, event_log, plan, actor=Actor.SYSTEM,
                             note="auto-approve（演示/测试用，非人工）")
            else:
                _interactive_approval(storage, event_log, plan)
            if plan.status != PlanStatus.APPROVED:
                return {"status": "plan_rejected", "round": round_no, "plan_id": plan.plan_id}

            for s in plan.steps:
                for t in s.tools:
                    tool_by_step.setdefault(s.step_id, t)

            # ---- EXECUTE
            experiments = manager.execute_plan(plan)

            # ---- VERIFY（证据资格门）
            eligible_ids: set[str] = set()
            for exp in experiments:
                tool = tool_by_step.get(exp.step_id)
                if tool is None:
                    continue
                report = verifier.verify_experiment(exp, tool)
                if report.overall == VerificationStatus.PASSED:
                    eligible_ids.add(exp.experiment_id)

            # ---- ANALYZE（只允许引用通过验证的实验）
            eligible, rows = _experiment_rows(experiments, eligible_ids, tool_by_step)
            all_rows.extend(rows)
            analysis, evidence_list = analyze(
                client, project_id, goal, hypotheses, eligible,
                event_log=event_log, retries=retries,
            )
            for ev in evidence_list:
                storage.save(ev)
            all_evidence.extend(evidence_list)
            all_analyses.append(analysis)

            # ---- DECIDE（人工触点 2：终止/宣布结论都 requires_human）
            decision = decide(
                client, project_id, goal, hypotheses, analysis, all_evidence,
                round_no=round_no, max_rounds=rounds,
                event_log=event_log, retries=retries,
            )
            if round_no == rounds and decision.recommendation.value == "iterate":
                decision.rationale = (
                    f"[预算闸] 已达 {rounds} 轮上限，iterate 不被自动执行，转人工。"
                    f"原 rationale：{decision.rationale}"
                )
                decision.requires_human = True
            storage.save(decision)
            all_decisions.append(decision)
            last_decision_id = decision.decision_id

            if decision.type is DecisionType.DECLARE_RESULT or (
                decision.type is DecisionType.ITERATE_OR_TERMINATE
                and decision.recommendation is DecisionRecommendation.TERMINATE
            ):
                terminated = True
                break
            previous_plan = plan
    except NeedsHuman as e:
        needs_human = f"第 {round_no} 轮：{e}"
        event_log.append(Event(actor=Actor.SYSTEM, action="needs_human",
                               project_id=project_id, object_type="Loop",
                               object_id=project_id, detail={"reason": str(e)}))

    # ---- 报告生成（全程可回放；转人工也生成部分进展报告）
    report_md = generate_report(
        project, goal, hypotheses,
        sorted(storage.list(ResearchPlan, project_id=project_id), key=lambda p: p.version),
        all_rows, all_decisions, all_analyses,
    )
    report_path = Path(storage.path).parent / "report.md"
    report_path.write_text(report_md, encoding="utf-8")
    event_log.append(Event(actor=Actor.SYSTEM, action="report_generated",
                           project_id=project_id, object_type="Report",
                           object_id=str(report_path),
                           detail={"rounds_used": len(all_decisions)}))

    summary = {
        "rounds_used": len(all_decisions),
        "decisions": [d.decision_id for d in all_decisions],
        "evidence": len(all_evidence),
        "report": str(report_path),
    }
    if needs_human is not None:
        return {"status": "needs_human", "reason": needs_human, **summary}
    return {"status": "terminated" if terminated else "budget_exhausted", **summary}
