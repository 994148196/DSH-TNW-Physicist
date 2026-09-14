"""科研主循环 Phase 2 片段：问题 → Goal → 假设 → 计划（critic）→ 审批落账。

确定性骨架由本模块定义（计划 v2 §6.1）；智能在站点执行器内（经 DSH）。
Phase 5 将把本片段扩展为含 EXECUTE/VERIFY/ANALYZE/DECIDE 的完整闭环。
"""
from __future__ import annotations

from qresearch.core.events import Event, EventLog
from qresearch.core.models import Goal, Hypothesis, Project, ResearchPlan, utcnow
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


def _show_plan_brief(plan: ResearchPlan) -> None:
    print(f"\n== 计划待审批 ==（v{plan.version}，共 {len(plan.steps)} 步）")
    for st in plan.steps:
        flag = " [需审批]" if st.requires_approval else ""
        tools = "、".join(st.tools) or "—"
        print(f"  {st.step_id} [{st.action}] {st.purpose}（工具：{tools}）{flag}")
    if plan.risks:
        print("风险：" + "；".join(plan.risks))
    if plan.diff_summary:
        print(f"与上一版差异：{plan.diff_summary}")


def _interactive_approval(
    storage: Storage, event_log: EventLog, plan: ResearchPlan, *,
    client: DSHClient, goal: Goal, hypotheses: list[Hypothesis],
    retries: int = 1, max_revisions: int = 5,
) -> ResearchPlan:
    """对话式计划审批（类 Claude Code）：看计划 → 批准 / 提意见修订 / 看细节 / 放弃。

    - y：以 actor=HUMAN 批准；
    - c：输入修改意见 → plan_feedback 事件（actor=HUMAN）→ plan_with_critic
      （previous=当前版 + user_notes）生成新版本，重新过 critic、重新等审批；
    - s：展开每步的 inputs/expected_outputs 细节；
    - q（或 EOF）：放弃——绝不默认批准。
    所有版本与反馈都落账，修订次数受 max_revisions 约束（防止无限对话）。
    返回最终计划（可能是按意见修订后的新版本；拒绝时为当前版，状态 REJECTED）。
    """
    revisions = 0
    while True:
        _show_plan_brief(plan)
        try:
            answer = input("审批：[y]批准 / [c]提修改意见 / [s]看步骤细节 / [q]放弃：").strip().lower()
        except EOFError:
            answer = "q"  # 无人在场：绝不默认批准
        if answer.startswith("y"):
            approve_plan(storage, event_log, plan, actor=Actor.HUMAN)
            print("已批准。")
            return plan
        if answer.startswith("s"):
            for st in plan.steps:
                print(f"  {st.step_id} [{st.action}] {st.purpose}")
                print(f"    输入：{st.inputs}")
                print(f"    预期输出：{st.expected_outputs}")
            continue
        if answer.startswith("c"):
            if revisions >= max_revisions:
                print(f"已达修订上限（{max_revisions} 次）：请 [y] 批准当前版，或 [q] 放弃。")
                continue
            try:
                notes = input("修改意见（一行，将作为最高优先级注入下一版计划）：").strip()
            except EOFError:
                notes = ""
            if not notes:
                print("（空意见，未修订）")
                continue
            event_log.append(Event(
                actor=Actor.HUMAN, action="plan_feedback", project_id=plan.project_id,
                object_type="ResearchPlan", object_id=plan.plan_id,
                detail={"version": plan.version, "notes": notes}))
            new_plan, crit = plan_with_critic(
                client, plan.project_id, goal, hypotheses,
                previous=plan, user_notes=notes,
                event_log=event_log, retries=retries)
            new_plan.status = PlanStatus.AWAITING_APPROVAL
            storage.save(new_plan)
            event_log.append(Event(
                actor=Actor.SYSTEM, action="plan_ready", project_id=plan.project_id,
                object_type="ResearchPlan", object_id=new_plan.plan_id,
                detail={"version": new_plan.version, "verdict": crit.verdict,
                        "issues": [i.model_dump() for i in crit.issues],
                        "from_user_feedback": plan.plan_id}))
            plan = new_plan
            revisions += 1
            print(f"已按意见生成 v{plan.version}（critic 判定：{crit.verdict}）。")
            continue
        reject_plan(storage, event_log, plan, actor=Actor.HUMAN, note="人工放弃/拒绝")
        print("已放弃。可修正问题或假设后重新运行。")
        return plan


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
        plan = _interactive_approval(storage, event_log, plan, client=client,
                                     goal=goal, hypotheses=hypotheses,
                                     retries=retries)
    return storage.get(ResearchPlan, plan.plan_id)
