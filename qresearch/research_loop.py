"""研究闭环（计划 v2 §6 / Phase 5–8）：PLAN→审批→EXECUTE→VERIFY→ANALYZE→DECIDE 循环。

M1（计划 v3）起本模块是 `ResearchEngine` 之上的**薄驱动**（评审 A3）：只编排轮次
推进、暂停/预算闸、人工触点与轮末回调；一切科研动作（站点调用、记账、执行、验证、
决策落账、报告）都在引擎里（qresearch/engine.py）。
预算闸：rounds 上限 + 可选实验数/墙钟预算（Budget，Phase 8）——耗尽时代码强制停
（LLM 只提议，代码记账）。
暂停恢复：轮间检查暂停旗标（Phase 8）；resume_research_loop 从台账恢复续跑
（计划版本、决策链、证据累计全部衔接）。
人工触点：计划审批与终止确认——由 Decision.requires_human 承载。
全程可回放：所有对象都在 storage/events 里，报告由引擎自动生成。
站点重试耗尽（NeedsHuman）按 §7.0 转人工：落账事件、生成部分报告、返回 needs_human 状态。
"""
from __future__ import annotations

import time
from pathlib import Path

from qresearch.core.budget import Budget
from qresearch.core.events import Event, EventLog
from qresearch.core.models import Experiment, Project
from qresearch.core.status import (Actor, ApprovalChannel, PlanStatus,
                                    VerificationStatus)
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient, NeedsHuman
from qresearch.engine import ResearchEngine
from qresearch.loop import _interactive_approval, approve_plan
from qresearch.ui.plan_doc import save_plan_doc


# ================================================================ 主循环
def _rounds_loop(
    engine: ResearchEngine,
    project_id: str,
    *,
    start_round: int,
    rounds: int,
    auto_approve: bool,
    pause_flag: Path | None,
    max_parallel_experiments: int,
    t_start: float,
    round_callback=None,
) -> dict:
    """逐轮主循环（fresh/resume 共用）。返回终局片段：
    status ∈ terminated / budget_exhausted / paused；needs_human 由调用方拼装。"""
    from qresearch.core.status import DecisionRecommendation, DecisionType

    client, storage, event_log = engine.client, engine.storage, engine.event_log
    state = engine.state(project_id)

    terminated = False
    stopped_by_user = False
    paused = False
    budget_reason: str | None = None
    round_no = start_round

    try:
        while round_no <= rounds:
            # ---- 暂停检查（轮间；旗标存在 = 请求暂停）
            if pause_flag is not None and pause_flag.exists():
                paused = True
                event_log.append(Event(
                    actor=Actor.SYSTEM, action="paused", project_id=project_id,
                    object_type="Loop", object_id=project_id,
                    detail={"round": round_no,
                            "hint": f"移除 {pause_flag} 后用 resume_research_loop 续跑"}))
                break

            # ---- 预算检查（轮边界，代码层闸；resume 首轮若已耗尽也拦下）
            reason = engine.budget.exhausted(
                rounds_used=len(state.all_decisions),
                experiments_used=len(storage.list(Experiment, project_id=project_id)),
                elapsed_s=time.monotonic() - t_start,
            )
            if reason is not None and (round_no > start_round or start_round > 1):
                budget_reason = reason
                event_log.append(Event(
                    actor=Actor.SYSTEM, action="budget_exhausted", project_id=project_id,
                    object_type="Loop", object_id=project_id, detail={"reason": reason}))
                break

            # ---- PLAN（引擎站点调用 + critic 修订循环；记忆检索与用户意见在引擎内）
            plan, critique = engine.plan_create(
                project_id, user_notes=state.user_notes,
                previous_plan_id=(state.previous_plan.plan_id
                                  if state.previous_plan else None),
                decision_id=state.last_decision_id, round_no=round_no)
            state.user_notes = ""  # 意见只注入一轮
            # ---- 审批（人工触点 1）；计划文档每版都存 plans/（含 auto 存档）
            plan_doc_dir = Path(storage.path).parent
            if auto_approve:
                approve_plan(storage, event_log, plan, actor=Actor.SYSTEM,
                             note="auto-approve（演示/测试用，非人工）",
                             channel=ApprovalChannel.SYSTEM_AUTO)
                save_plan_doc(plan_doc_dir, plan, goal=state.goal,
                              hypotheses=state.hypotheses,
                              critique_verdict=critique.verdict,
                              critique_issues=[i.model_dump() for i in critique.issues])
            else:
                plan = _interactive_approval(
                    storage, event_log, plan, client=client,
                    goal=state.goal, hypotheses=state.hypotheses,
                    retries=engine.retries, plan_doc_dir=plan_doc_dir,
                    critique_verdict=critique.verdict,
                    critique_issues=[i.model_dump() for i in critique.issues])
            if plan.status != PlanStatus.APPROVED:
                return {"status": "plan_rejected", "round": round_no, "plan_id": plan.plan_id}

            # ---- EXECUTE → VERIFY（证据资格门）→ ANALYZE → DECIDE（引擎，人工触点 2）
            _rows_before = len(state.all_rows)
            _ev_before = len(state.all_evidence)
            run = engine.experiments_run(plan.plan_id,
                                         max_workers=max_parallel_experiments)
            reports = engine.verify(run["experiment_ids"])
            eligible_ids = {r.experiment_id for r in reports
                            if r.overall == VerificationStatus.PASSED}
            engine.analyze(project_id, experiment_ids=run["experiment_ids"],
                           eligible_ids=eligible_ids)
            decision = engine.decide(project_id, round_no=round_no, max_rounds=rounds)

            _terminal = decision.type is DecisionType.DECLARE_RESULT or (
                decision.type is DecisionType.ITERATE_OR_TERMINATE
                and decision.recommendation is DecisionRecommendation.TERMINATE
            )
            if _terminal:
                terminated = True
            state.previous_plan = plan
            # ---- 轮末回调（节点报告 + 中途转向）：None=继续 / "stop"=停 / str=修改意见
            # 仅在"还要继续"的轮次触发——收束轮结论走 declare_result 无条件人工确认
            if round_callback is not None and not _terminal:
                res = round_callback({
                    "round_no": round_no,
                    "decision": {"type": decision.type.value,
                                 "recommendation": decision.recommendation.value,
                                 "rationale": decision.rationale},
                    "experiments_this_round": len(state.all_rows) - _rows_before,
                    "evidence_this_round": len(state.all_evidence) - _ev_before,
                    "evidence_total": len(state.all_evidence),
                })
                if isinstance(res, str) and res.strip().lower() == "stop":
                    event_log.append(Event(
                        actor=Actor.HUMAN, action="user_stop", project_id=project_id,
                        object_type="Loop", object_id=project_id,
                        detail={"round": round_no}))
                    stopped_by_user = True
                    break
                if isinstance(res, str) and res.strip():
                    state.user_notes = res.strip()
                    event_log.append(Event(
                        actor=Actor.HUMAN, action="user_feedback", project_id=project_id,
                        object_type="Loop", object_id=project_id,
                        detail={"round": round_no, "notes": state.user_notes}))
            if _terminal:
                break
            round_no += 1
    except NeedsHuman as e:
        msg = f"第 {round_no} 轮：{e}"
        event_log.append(Event(
            actor=Actor.SYSTEM, action="needs_human", project_id=project_id,
            object_type="Loop", object_id=project_id, detail={"reason": str(e)}))
        return {"needs_human": msg}

    if terminated:
        return {"status": "terminated"}
    if stopped_by_user:
        return {"status": "stopped_by_user",
                "hint": "轮末回调请求停止；resume_research_loop 可从台账续跑"}
    if paused:
        return {"status": "paused"}
    if budget_reason is not None:
        return {"status": "budget_exhausted", "reason": budget_reason}
    return {"status": "budget_exhausted"}  # 轮数自然耗尽且未终止


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
    memory_store=None,
    budget: Budget | None = None,
    pause_flag: str | Path | None = None,
    max_parallel_experiments: int = 1,
    round_callback=None,
) -> dict:
    """完整研究闭环。返回 summary dict；所有状态可从 storage/events 回放。

    memory_store：跨项目经验库（计划 v2 §7.9），每轮检索注入、结束蒸馏入库。
    budget：实验数/墙钟预算（轮数仍由 rounds 承载，Phase 8）。
    pause_flag：暂停旗标路径——轮间存在即优雅暂停（status=paused），
    移除旗标后用 resume_research_loop 续跑。
    max_parallel_experiments：>1 时实验并行计算（Phase 8）。
    """
    engine = ResearchEngine(
        storage, event_log, client, experiments_root=experiments_root,
        memory_store=memory_store, budget=budget or Budget(max_rounds=rounds),
        retries=retries)
    pause_flag = Path(pause_flag) if pause_flag is not None else None
    needs_human: str | None = None
    outcome: dict = {}
    t_start = time.monotonic()

    # Round 0：理解 + 假设
    project = engine.open_project(project_id, question)
    state = engine.state(project_id)
    try:
        engine.understand(project_id)
        engine.hypothesize(project_id, n=n_hypotheses)
    except NeedsHuman as e:
        needs_human = f"Round 0（理解/假设）：{e}"

    if needs_human is None:
        outcome = _rounds_loop(
            engine, project_id,
            start_round=1, rounds=rounds, auto_approve=auto_approve,
            pause_flag=pause_flag,
            max_parallel_experiments=max_parallel_experiments,
            round_callback=round_callback, t_start=t_start)
        needs_human = outcome.get("needs_human")

    status, summary = _finalize(
        engine, project_id, project, state,
        needs_human=needs_human, outcome=outcome if needs_human is None else {})
    return {"status": status, **summary}


def resume_research_loop(
    client: DSHClient,
    storage: Storage,
    event_log: EventLog,
    project_id: str,
    *,
    rounds: int = 3,
    auto_approve: bool = False,
    retries: int = 1,
    experiments_root: str | Path | None = None,
    memory_store=None,
    budget: Budget | None = None,
    pause_flag: str | Path | None = None,
    max_parallel_experiments: int = 1,
    round_callback=None,
) -> dict:
    """从台账恢复暂停/中断的项目续跑（计划 v2 §7.8：真相在库里）。

    恢复内容：Goal、假设、最新计划（修订基线）、决策链与轮数进度、
    证据累计、工具映射。轮数预算按全局进度计（ decisions 数 + 本轮）。
    """
    engine = ResearchEngine(
        storage, event_log, client, experiments_root=experiments_root,
        memory_store=memory_store, budget=budget or Budget(max_rounds=rounds),
        retries=retries)
    pause_flag = Path(pause_flag) if pause_flag is not None else None
    t_start = time.monotonic()

    project = storage.get(Project, project_id)
    if project is None:
        raise KeyError(f"项目不存在，无法恢复: {project_id}")
    state = engine.resume_state(project_id)

    outcome = _rounds_loop(
        engine, project_id,
        start_round=len(state.all_decisions) + 1, rounds=rounds,
        auto_approve=auto_approve, pause_flag=pause_flag,
        max_parallel_experiments=max_parallel_experiments,
        round_callback=round_callback, t_start=t_start)
    status, summary = _finalize(
        engine, project_id, project, state,
        needs_human=outcome.get("needs_human"), outcome=outcome)
    return {"status": status, **summary}


def _finalize(
    engine: ResearchEngine,
    project_id: str,
    project: Project,
    state,
    *,
    needs_human: str | None,
    outcome: dict,
) -> tuple[str, dict]:
    """收尾：报告生成 + 记忆蒸馏 + 状态裁决（fresh/resume 共用）。"""
    report_status = needs_human if needs_human else outcome.get("status", "budget_exhausted")
    report_path = engine.report_generate(project_id, state=state, status=report_status)
    engine.distill_to_memory(project_id)

    summary = {
        "rounds_used": len(state.all_decisions),
        "decisions": [d.decision_id for d in state.all_decisions],
        "evidence": len(state.all_evidence),
        "report": str(report_path),
    }
    if needs_human is not None:
        return "needs_human", {"reason": needs_human, **summary}
    status = outcome.get("status", "budget_exhausted")
    if status in ("plan_rejected", "stopped_by_user", "paused"):
        summary.update({k: v for k, v in outcome.items()
                        if k not in ("status", "needs_human")})
    if "reason" in outcome and status == "budget_exhausted":
        summary["reason"] = outcome["reason"]
    return status, summary
