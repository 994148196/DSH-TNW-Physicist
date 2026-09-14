"""研究闭环（计划 v2 §6 / Phase 5–8）：PLAN→审批→EXECUTE→VERIFY→ANALYZE→DECIDE 循环。

确定性骨架在本模块；智能在站点执行器内（经 DSH）。
预算闸：rounds 上限 + 可选实验数/墙钟预算（Budget，Phase 8）——耗尽时代码强制停
（LLM 只提议，代码记账）。
暂停恢复：轮间检查暂停旗标（Phase 8）；resume_research_loop 从台账恢复续跑
（计划版本、决策链、证据累计全部衔接）。
人工触点：计划审批与终止确认——由 Decision.requires_human 承载。
全程可回放：所有对象都在 storage/events 里，报告自动生成。
站点重试耗尽（NeedsHuman）按 §7.0 转人工：落账事件、生成部分报告、返回 needs_human 状态。
"""
from __future__ import annotations

import time
from pathlib import Path

from qresearch.core.budget import Budget
from qresearch.core.events import Event, EventLog
from qresearch.core.models import (
    Decision, Evidence, Experiment, Goal, Hypothesis, Project, ResearchPlan, ToolRecord,
)
from qresearch.core.status import Actor, PlanStatus, VerificationStatus
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient, NeedsHuman
from qresearch.loop import _interactive_approval, approve_plan
from qresearch.ui.plan_doc import save_plan_doc
from .stations.executors import analyze, decide, hypothesize, plan_with_critic, understand


# ================================================================ 报告生成
def _hypothesis_fate_line(h) -> str:
    fate = ("支持" if h.supporting_evidence
            else ("反驳" if h.contradicting_evidence else "未判定"))
    return f"- {h.statement} → **{fate}**\n  - 证伪试验：{'；'.join(h.falsification_tests)}"


def _summary_section(project: Project, status: str, hypotheses: list,
                     decisions: list, experiment_rows: list[dict],
                     all_evidence: list) -> list[str]:
    """研究总结（置顶）：最终状态 / 结论 / 关键证据 / 假设命运 / 规模统计。

    全部内容确定性来自台账（铁律：LLM 只提议，代码记账）——结论正文取自
    收束决策的 rationale 原文，证据逐条挂 id 可回放。
    """
    rec_chain = " → ".join(d.recommendation.value for d in decisions) or "（无决策）"
    lines = ["## 研究总结", "", f"- **最终状态**：`{status}`（{len(decisions)} 轮；"
             f"决策链 {rec_chain}）"]
    final = decisions[-1] if decisions else None
    concluded = final is not None and (
        final.type.value == "declare_result"
        or final.recommendation.value in ("terminate", "accept"))
    if concluded and final is not None:
        lines.append(f"- **结论**（收束决策 rationale 原文，"
                     f"{final.decision_id}）：{final.rationale or '（未填写）'}")
        passed = [c for c in final.checklist if c.status == "passed" and c.evidence]
        if passed:
            lines.append("- **支撑结论的关键证据**：")
            lines += [f"  - {c.claim}（evidence: `{c.evidence}`）" for c in passed]
        lines.append("- **提示**：收束决策 requires_human=是——结论确认权在人。")
    else:
        lines.append("- **结论**：（本轮运行未给出最终结论——"
                     "以 iterate/replan 收尾或中途转出；见决策记录与局限）")
    if hypotheses:
        lines.append("- **假设命运**：")
        lines += [f"  { _hypothesis_fate_line(h) }" for h in hypotheses]
    n_done = sum(1 for r in experiment_rows if r["status"] == "completed")
    n_fail = sum(1 for r in experiment_rows if r["status"] == "failed")
    tools = sorted({r["tool"] for r in experiment_rows}) or ["—"]
    lines.append(
        f"- **规模**：实验 {len(experiment_rows)} 个（完成 {n_done} / 失败 {n_fail}，"
        f"工具：{'、'.join(tools)}），证据 {len(all_evidence)} 条")
    lines.append("")
    return lines


def _plan_history_section(plans: list, plan_notes: dict | None) -> list[str]:
    """计划版本历史：每版的审查结论、阻塞项、步骤与修订说明（从台账/事件重建）。"""
    lines = ["## 计划版本历史"]
    for p in plans:
        notes = (plan_notes or {}).get(p.plan_id, {})
        verdict = notes.get("verdict", "—")
        issues = notes.get("issues", [])
        lines.append("")
        lines.append(f"### 计划 v{p.version}（{p.status.value}，{len(p.steps)} 步）")
        if p.based_on_decision:
            lines.append(f"- 依据决策：`{p.based_on_decision}`（replan/修订）")
        if p.diff_summary:
            lines.append(f"- 与上一版差异：{p.diff_summary}")
        blockers = [i for i in issues if isinstance(i, dict) and i.get("severity") == "blocker"]
        lines.append(f"- critic 审查：{verdict}"
                     + (f"（blocker {len(blockers)} 项）" if blockers else ""))
        for i in issues:
            if isinstance(i, dict):
                sev = i.get("severity", "?")
                mark = "⛔" if sev == "blocker" else "·"
                lines.append(f"  - {mark} [{sev}] {i.get('step_id') or '整体'}："
                             f"{i.get('description', '')[:200]}")
        for s in p.steps:
            flag = " [需审批]" if getattr(s, "requires_approval", False) else ""
            lines.append(f"- `{s.step_id}` [{s.action}]{flag} {s.purpose}"
                         f"（工具：{'、'.join(s.tools) or '—'}）")
    lines.append("")
    return lines


def generate_report(
    project: Project, goal, hypotheses: list, plans: list,
    experiment_rows: list[dict], decisions: list, analyses: list,
    *,
    status: str = "terminated",
    plan_notes: dict | None = None,
    all_evidence: list | None = None,
) -> str:
    """生成 markdown 研究报告：置顶研究总结 + 计划版本历史；结论全部可追溯。"""
    all_evidence = all_evidence or []
    lines = [
        f"# 研究报告：{project.title}",
        "",
        f"- 项目：`{project.project_id}`",
        f"- 研究问题：{goal.question if goal else project.question}",
        f"- 目标量：{'、'.join(goal.quantities) if goal else '—'}",
        "",
    ]
    lines += _summary_section(project, status, hypotheses, decisions,
                              experiment_rows, all_evidence)
    lines += [
        "## 假设",
        *[f"- {h.statement}\n"
          f"  - 证伪：{'；'.join(h.falsification_tests)}" for h in hypotheses],
        "",
    ]
    lines += _plan_history_section(plans, plan_notes)
    lines += [
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
                     tool_for) -> tuple[list[dict], list[dict]]:
    """实验分为"可引用（验证通过）"与"全部"（计划 v2 §7.6 证据资格门）。

    tool_for：step_id → 工具名（含 parameter_scan 子实验的父步骤回溯）。
    """
    from qresearch.experiments.manager import _load_result

    eligible, all_rows = [], []
    for exp in experiments:
        result = _load_result(exp) or {}
        row = {
            "experiment_id": exp.experiment_id, "step_id": exp.step_id,
            "tool": tool_for(exp.step_id) or "?", "inputs": exp.parameters,
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


class _LoopState:
    """跨轮累计状态（fresh 与 resume 共用的可变载体）。"""

    def __init__(self) -> None:
        self.goal: Goal | None = None
        self.hypotheses: list = []
        self.previous_plan: ResearchPlan | None = None
        self.last_decision_id: str | None = None
        self.all_decisions: list = []
        self.all_analyses: list = []
        self.all_rows: list[dict] = []
        self.all_evidence: list = []
        self.tool_by_step: dict[str, str] = {}
        self.user_notes: str = ""  # 轮末回调收集的用户修改意见（下一轮计划注入）


def _memory_event(event_log: EventLog, project_id: str, action: str, detail: dict) -> None:
    event_log.append(Event(actor=Actor.SYSTEM, action=action,
                           project_id=project_id, object_type="Memory",
                           object_id=project_id, detail=detail))


def _memory_digest_text(event_log: EventLog, project_id: str,
                        memory_store, state: _LoopState, question: str) -> str:
    if memory_store is None:
        return ""
    from qresearch.memory import memory_digest

    goal = state.goal
    query = f"{goal.question} {' '.join(goal.quantities)}" if goal else question
    digest, n_hits = memory_digest(memory_store, query)
    _memory_event(event_log, project_id, "memory_retrieved",
                  {"query": query[:200], "n_hits": n_hits})
    return digest


def _distill_to_memory(event_log: EventLog, project_id: str, storage: Storage,
                       memory_store) -> None:
    """项目结束蒸馏入库（转人工的部分经验同样有价值）。"""
    if memory_store is None:
        return
    from qresearch.memory import distill_project

    entries = distill_project(storage, project_id)
    for entry in entries:
        memory_store.add(entry)
    _memory_event(event_log, project_id, "memory_written", {
        "n_entries": len(entries),
        "layers": sorted({e.layer.value for e in entries}),
    })


def _rounds_loop(
    client: DSHClient,
    storage: Storage,
    event_log: EventLog,
    project_id: str,
    state: _LoopState,
    *,
    start_round: int,
    rounds: int,
    manager,
    verifier,
    retries: int,
    auto_approve: bool,
    memory_store,
    budget: Budget,
    pause_flag: Path | None,
    max_parallel_experiments: int,
    t_start: float,
    question: str,
    round_callback=None,
) -> dict:
    """逐轮主循环（fresh/resume 共用）。返回终局片段：
    status ∈ terminated / budget_exhausted / paused；needs_human 由调用方拼装。"""
    from qresearch.core.status import DecisionRecommendation, DecisionType

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
            reason = budget.exhausted(
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

            # ---- PLAN（critic 修订循环；注入记忆库检索与用户修改意见）
            plan, critique = plan_with_critic(
                client, project_id, state.goal, state.hypotheses,
                previous=state.previous_plan, decision_id=state.last_decision_id,
                memory_text=_memory_digest_text(event_log, project_id, memory_store,
                                                state, question),
                user_notes=state.user_notes,
                event_log=event_log, retries=retries,
            )
            state.user_notes = ""  # 意见只注入一轮
            plan.status = PlanStatus.AWAITING_APPROVAL
            storage.save(plan)
            event_log.append(Event(actor=Actor.SYSTEM, action="plan_ready",
                                   project_id=project_id, object_type="ResearchPlan",
                                   object_id=plan.plan_id,
                                   detail={"version": plan.version, "verdict": critique.verdict,
                                           "round": round_no,
                                           "issues": [i.model_dump() for i in critique.issues]}))
            # ---- 审批（人工触点 1）；计划文档每版都存 plans/（含 auto 存档）
            plan_doc_dir = Path(storage.path).parent
            if auto_approve:
                approve_plan(storage, event_log, plan, actor=Actor.SYSTEM,
                             note="auto-approve（演示/测试用，非人工）")
                save_plan_doc(plan_doc_dir, plan, goal=state.goal,
                              hypotheses=state.hypotheses,
                              critique_verdict=critique.verdict,
                              critique_issues=[i.model_dump() for i in critique.issues])
            else:
                plan = _interactive_approval(
                    storage, event_log, plan, client=client,
                    goal=state.goal, hypotheses=state.hypotheses,
                    retries=retries, plan_doc_dir=plan_doc_dir,
                    critique_verdict=critique.verdict,
                    critique_issues=[i.model_dump() for i in critique.issues])
            if plan.status != PlanStatus.APPROVED:
                return {"status": "plan_rejected", "round": round_no, "plan_id": plan.plan_id}

            for s in plan.steps:
                for t in s.tools:
                    state.tool_by_step.setdefault(s.step_id, t)

            def _tool_for(step_id: str) -> str | None:
                if step_id in state.tool_by_step:
                    return state.tool_by_step[step_id]
                # parameter_scan 子实验的 step_id 为 "<step_id>_<idx>"：回溯父步骤
                base = step_id
                while "_" in base:
                    base = base.rsplit("_", 1)[0]
                    if base in state.tool_by_step:
                        return state.tool_by_step[base]
                return None

            # ---- EXECUTE（max_workers>1 时实验并行计算，落账仍按序）
            _rows_before = len(state.all_rows)
            _ev_before = len(state.all_evidence)
            experiments = manager.execute_plan(plan, max_workers=max_parallel_experiments)

            # ---- VERIFY（证据资格门）
            eligible_ids: set[str] = set()
            for exp in experiments:
                tool = _tool_for(exp.step_id)
                if tool is None:
                    continue
                report = verifier.verify_experiment(exp, tool)
                if report.overall == VerificationStatus.PASSED:
                    eligible_ids.add(exp.experiment_id)

            # ---- ANALYZE（只允许引用通过验证的实验）
            eligible, rows = _experiment_rows(experiments, eligible_ids, _tool_for)
            state.all_rows.extend(rows)
            analysis, evidence_list = analyze(
                client, project_id, state.goal, state.hypotheses, eligible,
                event_log=event_log, retries=retries,
            )
            for ev in evidence_list:
                storage.save(ev)
            state.all_evidence.extend(evidence_list)
            state.all_analyses.append(analysis)

            # ---- DECIDE（人工触点 2：终止/宣布结论都 requires_human）
            decision = decide(
                client, project_id, state.goal, state.hypotheses, analysis,
                state.all_evidence, round_no=round_no, max_rounds=rounds,
                memory_text=_memory_digest_text(event_log, project_id, memory_store,
                                                state, question),
                event_log=event_log, retries=retries,
            )
            if round_no == rounds and decision.recommendation.value == "iterate":
                decision.rationale = (
                    f"[预算闸] 已达 {rounds} 轮上限，iterate 不被自动执行，转人工。"
                    f"原 rationale：{decision.rationale}"
                )
                decision.requires_human = True
            storage.save(decision)
            state.all_decisions.append(decision)
            state.last_decision_id = decision.decision_id

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


def _default_experiments_root(storage: Storage) -> Path:
    return Path(storage.path).parent / "experiments"


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
    from qresearch.experiments.manager import ExperimentManager
    from qresearch.verification.manager import VerificationManager

    budget = budget or Budget(max_rounds=rounds)
    pause_flag = Path(pause_flag) if pause_flag is not None else None
    needs_human: str | None = None
    t_start = time.monotonic()

    # Round 0：理解 + 假设
    project = Project(project_id=project_id, title=question[:40], question=question)
    storage.save(project)
    event_log.append(Event(actor=Actor.SYSTEM, action="create_project",
                           project_id=project_id, object_type="Project",
                           object_id=project_id, detail={}))
    state = _LoopState()
    outcome: dict = {}
    try:
        state.goal = understand(client, project_id, question, event_log=event_log,
                                retries=retries)
        storage.save(state.goal)
        event_log.append(Event(actor=Actor.SYSTEM, action="save",
                               project_id=project_id, object_type="Goal",
                               object_id=state.goal.goal_id, detail={}))
        state.hypotheses = hypothesize(client, project_id, state.goal, n=n_hypotheses,
                                       event_log=event_log, retries=retries)
        for h in state.hypotheses:
            storage.save(h)
    except NeedsHuman as e:
        needs_human = f"Round 0（理解/假设）：{e}"

    if needs_human is None:
        manager = ExperimentManager(
            storage, event_log,
            experiments_root=experiments_root or _default_experiments_root(storage))
        verifier = VerificationManager(storage, event_log)
        outcome = _rounds_loop(
            client, storage, event_log, project_id, state,
            start_round=1, rounds=rounds, manager=manager, verifier=verifier,
            retries=retries, auto_approve=auto_approve, memory_store=memory_store,
            budget=budget, pause_flag=pause_flag,
            max_parallel_experiments=max_parallel_experiments,
            round_callback=round_callback,
            t_start=t_start, question=question,
        )
        needs_human = outcome.get("needs_human")

    status, summary = _finalize(
        storage, event_log, project_id, project, state,
        needs_human=needs_human, outcome=outcome if needs_human is None else {},
        memory_store=memory_store, rounds=rounds,
    )
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
    from qresearch.experiments.manager import ExperimentManager
    from qresearch.verification.manager import VerificationManager

    budget = budget or Budget(max_rounds=rounds)
    pause_flag = Path(pause_flag) if pause_flag is not None else None
    t_start = time.monotonic()

    project = storage.get(Project, project_id)
    if project is None:
        raise KeyError(f"项目不存在，无法恢复: {project_id}")
    goals = storage.list(Goal, project_id=project_id)
    state = _LoopState()
    state.goal = goals[0] if goals else None
    state.hypotheses = storage.list(Hypothesis, project_id=project_id)
    plans = sorted(storage.list(ResearchPlan, project_id=project_id),
                   key=lambda p: p.version)
    state.previous_plan = plans[-1] if plans else None
    state.all_decisions = sorted(storage.list(Decision, project_id=project_id),
                                 key=lambda d: d.created_at)
    if state.all_decisions:
        state.last_decision_id = state.all_decisions[-1].decision_id
    state.all_evidence = storage.list(Evidence, project_id=project_id)

    # 工具映射：experiment.step_id → 工具名（tool_id 经 ToolRecord 解析）
    tool_name = {t.tool_id: t.name for t in storage.list(ToolRecord)}
    for exp in storage.list(Experiment, project_id=project_id):
        name = tool_name.get(exp.tool_id, exp.tool_id)
        if name:
            state.tool_by_step.setdefault(exp.step_id, name)

    start_round = len(state.all_decisions) + 1
    event_log.append(Event(
        actor=Actor.SYSTEM, action="resumed", project_id=project_id,
        object_type="Loop", object_id=project_id,
        detail={"start_round": start_round, "plans": len(plans),
                "decisions": len(state.all_decisions),
                "evidence": len(state.all_evidence)}))

    manager = ExperimentManager(
        storage, event_log,
        experiments_root=experiments_root or _default_experiments_root(storage))
    verifier = VerificationManager(storage, event_log)
    outcome = _rounds_loop(
        client, storage, event_log, project_id, state,
        start_round=start_round, rounds=rounds, manager=manager, verifier=verifier,
        retries=retries, auto_approve=auto_approve, memory_store=memory_store,
        budget=budget, pause_flag=pause_flag,
        max_parallel_experiments=max_parallel_experiments,
        round_callback=round_callback,
        t_start=t_start, question=project.question,
    )
    status, summary = _finalize(
        storage, event_log, project_id, project, state,
        needs_human=outcome.get("needs_human"), outcome=outcome,
        memory_store=memory_store, rounds=rounds,
    )
    return {"status": status, **summary}


def _finalize(
    storage: Storage,
    event_log: EventLog,
    project_id: str,
    project: Project,
    state: _LoopState,
    *,
    needs_human: str | None,
    outcome: dict,
    memory_store,
    rounds: int,
) -> tuple[str, dict]:
    """收尾：报告生成 + 记忆蒸馏 + 状态裁决（fresh/resume 共用）。"""
    plans = sorted(storage.list(ResearchPlan, project_id=project_id),
                   key=lambda p: p.version)
    # 计划版本历史：从事件重建每版的 critic 结论与审批者（可回放）
    plan_notes: dict[str, dict] = {}
    for e in event_log.events(project_id=project_id):
        if e.action == "plan_ready":
            plan_notes.setdefault(e.object_id, {}).update({
                "verdict": e.detail.get("verdict", "—"),
                "issues": e.detail.get("issues", []),
            })
        elif e.action == "approve":
            plan_notes.setdefault(e.object_id, {})["approved_by"] = e.actor.value
    report_md = generate_report(
        project, state.goal, state.hypotheses, plans,
        state.all_rows, state.all_decisions, state.all_analyses,
        status=needs_human if needs_human else outcome.get("status", "budget_exhausted"),
        plan_notes=plan_notes, all_evidence=state.all_evidence,
    )
    report_path = Path(storage.path).parent / "report.md"
    report_path.write_text(report_md, encoding="utf-8")
    event_log.append(Event(actor=Actor.SYSTEM, action="report_generated",
                           project_id=project_id, object_type="Report",
                           object_id=str(report_path),
                           detail={"rounds_used": len(state.all_decisions)}))

    _distill_to_memory(event_log, project_id, storage, memory_store)

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
