"""科研主循环 Phase 2 片段：问题 → Goal → 假设 → 计划（critic）→ 审批落账。

确定性骨架由本模块定义（计划 v2 §6.1）；智能在站点执行器内（经 DSH）。
Phase 5 将把本片段扩展为含 EXECUTE/VERIFY/ANALYZE/DECIDE 的完整闭环。
"""
from __future__ import annotations

from qresearch.core.events import Event, EventLog
from qresearch.core.models import Project, ResearchPlan, utcnow
from qresearch.core.status import Actor, PlanStatus
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient
from .stations.executors import hypothesize, plan_with_critic, understand


def approve_plan(
    storage: Storage, event_log: EventLog, plan: ResearchPlan,
    *, actor: Actor, note: str = "",
) -> None:
    plan.status = PlanStatus.APPROVED
    plan.updated_at = utcnow()
    storage.save(plan)
    event_log.append(Event(
        actor=actor, action="approve", project_id=plan.project_id,
        object_type="ResearchPlan", object_id=plan.plan_id, detail={"note": note},
    ))


def reject_plan(
    storage: Storage, event_log: EventLog, plan: ResearchPlan,
    *, actor: Actor, note: str = "",
) -> None:
    plan.status = PlanStatus.REJECTED
    plan.updated_at = utcnow()
    storage.save(plan)
    event_log.append(Event(
        actor=actor, action="reject", project_id=plan.project_id,
        object_type="ResearchPlan", object_id=plan.plan_id, detail={"note": note},
    ))


def _interactive_approval(storage: Storage, event_log: EventLog, plan: ResearchPlan) -> None:
    print("\n== 计划待审批 ==")
    print(f"版本：v{plan.version}")
    for s in plan.steps:
        flag = " [需审批]" if s.requires_approval else ""
        print(f"  {s.step_id} [{s.action}] {s.purpose}{flag}")
    if plan.risks:
        print("风险：" + "；".join(plan.risks))
    if plan.diff_summary:
        print(f"与上一版差异：{plan.diff_summary}")
    answer = input("批准该计划？[y]es / [n]o：").strip().lower()
    if answer.startswith("y"):
        approve_plan(storage, event_log, plan, actor=Actor.HUMAN)
        print("已批准。")
    else:
        reject_plan(storage, event_log, plan, actor=Actor.HUMAN, note="人工拒绝")
        print("已拒绝。可修改问题或假设后重新运行。")


def run_planning_phase(
    client: DSHClient,
    storage: Storage,
    event_log: EventLog,
    project_id: str,
    question: str,
    *,
    auto_approve: bool = False,
    n_hypotheses: int = 3,
    retries: int = 1,
) -> ResearchPlan:
    """Phase 2 验收路径：科研问题 → 结构化 Goal → 候选假设 → plan（critic 通过）→ 人批。

    auto_approve 仅用于演示与测试（actor=system，事件留痕），不替代真实人工审批。
    """
    project = Project(project_id=project_id, title=question[:40], question=question)
    storage.save(project)
    event_log.append(Event(actor=Actor.SYSTEM, action="create_project",
                           project_id=project_id, object_type="Project", object_id=project_id))

    goal = understand(client, project_id, question, event_log=event_log, retries=retries)
    storage.save(goal)
    event_log.append(Event(actor=Actor.SYSTEM, action="save",
                           project_id=project_id, object_type="Goal", object_id=goal.goal_id))

    hypotheses = hypothesize(client, project_id, goal, n=n_hypotheses,
                             event_log=event_log, retries=retries)
    for h in hypotheses:
        storage.save(h)
    event_log.append(Event(actor=Actor.SYSTEM, action="save_hypotheses",
                           project_id=project_id, object_type="Hypothesis",
                           detail={"count": len(hypotheses),
                                   "ids": [h.hypothesis_id for h in hypotheses]}))

    plan, critique = plan_with_critic(client, project_id, goal, hypotheses,
                                      event_log=event_log, retries=retries)
    plan.status = PlanStatus.AWAITING_APPROVAL
    storage.save(plan)
    event_log.append(Event(actor=Actor.SYSTEM, action="plan_ready",
                           project_id=project_id, object_type="ResearchPlan",
                           object_id=plan.plan_id,
                           detail={"version": plan.version, "verdict": critique.verdict,
                                   "issues": [i.model_dump() for i in critique.issues]}))

    if auto_approve:
        approve_plan(storage, event_log, plan, actor=Actor.SYSTEM,
                     note="auto-approve（演示/测试用，非人工）")
    else:
        _interactive_approval(storage, event_log, plan)
    return storage.get(ResearchPlan, plan.plan_id)
