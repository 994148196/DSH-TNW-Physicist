"""科研主循环 Phase 2 片段：问题 → Goal → 假设 → 计划（critic）→ 审批落账。

确定性骨架由本模块定义（计划 v2 §6.1）；智能在站点执行器内（经 DSH）。
Phase 5 将本片段扩展为含 EXECUTE/VERIFY/ANALYZE/DECIDE 的完整闭环；
Phase 9.2 把审批升级为"文档 + 多通道反馈"（markdown 计划 / 多行意见 / 编辑回读）。
"""
from __future__ import annotations

from pathlib import Path

from qresearch.core.events import Event, EventLog
from qresearch.core.models import Goal, Hypothesis, Project, ResearchPlan, utcnow
from qresearch.core.status import Actor, PlanStatus
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient
from .stations.executors import (
    critique, hypothesize, plan_with_critic, transcribe_plan, understand,
)
from .ui.plan_doc import save_plan_doc, split_user_notes


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


def _read_multiline() -> str:
    """多行意见输入：空行结束（EOF 按已输入内容提交）。"""
    print("输入修改意见（可多行；空行结束）：")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _interactive_approval(
    storage: Storage, event_log: EventLog, plan: ResearchPlan, *,
    client: DSHClient, goal: Goal, hypotheses: list[Hypothesis],
    retries: int = 1, max_revisions: int = 5,
    critique_verdict: str | None = None,
    critique_issues: list[dict] | None = None,
    plan_doc_dir: str | Path | None = None,
) -> ResearchPlan:
    """对话式计划审批（类 Claude Code，Phase 9.2）：文档 + 多通道反馈。

    进入时把计划写成 <plan_doc_dir>/plans/plan_vN.md（可读、可编辑），然后循环：
    - y：以 actor=HUMAN 批准；
    - c（或直接输入一段文字）：多行修改意见 → plan_feedback（actor=HUMAN）→
      plan_with_critic（previous=当前版 + user_notes）生成新版本，重新过 critic；
    - e：在编辑器里直接修改计划文档 → 保存回终端按回车 → transcribe_plan
      站点把编辑转录回结构化计划（过语义校验）→ critic 再审 → 新版本；
    - s：在终端展开每步 inputs/expected_outputs；
    - q（EOF / Ctrl+C）：放弃——绝不默认批准。
    所有版本与反馈都落账，修订次数受 max_revisions 约束（防止无限对话）。
    返回最终计划（可能是修订后的新版本；拒绝时为当前版，状态 REJECTED）。
    """
    doc_dir = Path(plan_doc_dir) if plan_doc_dir else Path(storage.path).parent

    def _save_doc(cur: ResearchPlan, verdict, issues) -> Path:
        return save_plan_doc(doc_dir, cur, goal=goal, hypotheses=hypotheses,
                             critique_verdict=verdict, critique_issues=issues)

    revisions = 0
    doc_path = _save_doc(plan, critique_verdict, critique_issues)
    doc_text = doc_path.read_text(encoding="utf-8")
    while True:
        _show_plan_brief(plan)
        print(f"计划文档：{doc_path}（可直接编辑；e 键走编辑回读流程）")
        try:
            answer_raw = input(
                "审批：[y]批准 / [c]提修改意见 / [e]编辑文档 / [s]看步骤细节 / [q]放弃：")
            answer = answer_raw.strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "q"  # 无人在场/中断：绝不默认批准
        if answer.startswith("y"):
            approve_plan(storage, event_log, plan, actor=Actor.HUMAN)
            print("已批准。")
            return plan
        if answer.startswith("q"):
            reject_plan(storage, event_log, plan, actor=Actor.HUMAN, note="人工放弃/拒绝")
            print("已放弃。可修正问题或假设后重新运行。")
            return plan
        if answer.startswith("s"):
            for st in plan.steps:
                print(f"  {st.step_id} [{st.action}] {st.purpose}")
                print(f"    工具：{'、'.join(st.tools) or '—'}")
                print(f"    输入：{st.inputs}")
                print(f"    预期输出：{st.expected_outputs}")
            continue

        if revisions >= max_revisions:
            print(f"已达修订上限（{max_revisions} 次）：请 [y] 批准当前版，或 [q] 放弃。")
            continue

        if answer.startswith("e"):
            print(f"请在编辑器中修改 {doc_path}，保存后回到终端按回车（不改直接回车返回菜单）。")
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                print("（取消编辑回读）")
                continue
            edited = doc_path.read_text(encoding="utf-8")
            if edited == doc_text:
                print("（文档未改动）")
                continue
            doc_text = edited
            body_md, doc_notes = split_user_notes(edited)
            event_log.append(Event(
                actor=Actor.HUMAN, action="plan_feedback", project_id=plan.project_id,
                object_type="ResearchPlan", object_id=plan.plan_id,
                detail={"version": plan.version, "mode": "edit_doc",
                        "notes": doc_notes or "（直接编辑步骤）"}))
            new_plan = transcribe_plan(
                client, plan.project_id, goal, body_md, plan,
                user_notes=doc_notes, event_log=event_log, retries=retries)
            crit = critique(client, plan.project_id, goal, new_plan,
                            event_log=event_log, retries=retries)
            new_plan.status = PlanStatus.AWAITING_APPROVAL
            storage.save(new_plan)
            event_log.append(Event(
                actor=Actor.SYSTEM, action="plan_ready", project_id=plan.project_id,
                object_type="ResearchPlan", object_id=new_plan.plan_id,
                detail={"version": new_plan.version, "verdict": crit.verdict,
                        "issues": [i.model_dump() for i in crit.issues],
                        "from_user_edit": plan.plan_id}))
            source = "按文档编辑转录"
        else:
            # 意见通道：c 引导多行输入；其余任意非命令文本直接当意见（口述一长串）
            notes = _read_multiline() if answer.startswith("c") else answer_raw.strip()
            if not notes:
                print("（空意见，未修订）")
                continue
            event_log.append(Event(
                actor=Actor.HUMAN, action="plan_feedback", project_id=plan.project_id,
                object_type="ResearchPlan", object_id=plan.plan_id,
                detail={"version": plan.version, "mode": "verbal", "notes": notes}))
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
            source = "按意见修订"

        plan = new_plan
        critique_verdict = crit.verdict
        critique_issues = [i.model_dump() for i in crit.issues]
        revisions += 1
        doc_path = _save_doc(plan, critique_verdict, critique_issues)
        doc_text = doc_path.read_text(encoding="utf-8")
        print(f"{source}，已生成 v{plan.version}（critic 判定：{critique_verdict}）。"
              f"建议按 s 核对是否符合你的修改。")


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
        save_plan_doc(Path(storage.path).parent, plan, goal=goal,
                      hypotheses=hypotheses, critique_verdict=critique.verdict,
                      critique_issues=[i.model_dump() for i in critique.issues])
    else:
        plan = _interactive_approval(
            storage, event_log, plan, client=client, goal=goal,
            hypotheses=hypotheses, retries=retries,
            critique_verdict=critique.verdict,
            critique_issues=[i.model_dump() for i in critique.issues])
    return storage.get(ResearchPlan, plan.plan_id)
