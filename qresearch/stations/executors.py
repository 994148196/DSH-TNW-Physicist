"""站点执行器：prompt 投影（从状态对象现算）→ call_station → 转换为状态层对象。

上下文=状态的投影（计划 v2 §7.8）：每个函数从传入对象构造 prompt，不携带历史对话。
"""
from __future__ import annotations

import json

from qresearch.core.events import Event, EventLog
from qresearch.core.models import Decision, Evidence, Goal, Hypothesis, PlanStep, ResearchPlan
from qresearch.core.status import Actor, DecisionRecommendation, DecisionType, EvidenceType
from qresearch.dsh_client import DSHClient
from .prompts import ACTIONS, ANALYZE, CRITIC, DECIDE, HYPOTHESIZE, PLAN, UNDERSTAND, actions_text, tools_text
from .schemas import (
    AnalyzeOutput, CritiqueOutput, DecideOutput, HypothesizeOutput, PlanOutput, UnderstandOutput,
)


def _goal_digest(goal: Goal) -> str:
    return (
        f"问题：{goal.question}\n"
        f"目标量：{goal.quantities}\n"
        f"成功标准：{goal.success_criteria}\n"
        f"约束：{json.dumps(goal.constraints, ensure_ascii=False)}\n"
        f"默认假设：{goal.assumptions}"
    )


def _plan_digest(plan: ResearchPlan) -> str:
    steps = "\n".join(
        f"  - {s.step_id} [{s.action}] {s.purpose}（工具：{s.tools or '无'}）"
        for s in plan.steps
    )
    return f"v{plan.version}：\n{steps}"


def understand(
    client: DSHClient,
    project_id: str,
    question: str,
    *,
    event_log: EventLog | None = None,
    retries: int = 1,
) -> Goal:
    out = client.call_station(
        "understand", project_id, UnderstandOutput,
        UNDERSTAND.format(question=question), retries=retries, event_log=event_log,
    )
    return Goal(
        project_id=project_id,
        question=out.refined_question,
        quantities=out.quantities,
        success_criteria=out.success_criteria,
        constraints=out.constraints,
        assumptions=out.assumptions_made,
    )


def hypothesize(
    client: DSHClient,
    project_id: str,
    goal: Goal,
    *,
    n: int = 3,
    event_log: EventLog | None = None,
    retries: int = 1,
) -> list[Hypothesis]:
    out = client.call_station(
        "hypothesize", project_id, HypothesizeOutput,
        HYPOTHESIZE.format(goal=_goal_digest(goal), actions=actions_text()),
        retries=retries, event_log=event_log,
    )
    return [
        Hypothesis(
            project_id=project_id,
            statement=h.statement,
            source=h.rationale,
            falsification_tests=h.falsification_tests,
        )
        for h in out.hypotheses[:n]
    ]


def make_plan(
    client: DSHClient,
    project_id: str,
    goal: Goal,
    hypotheses: list[Hypothesis],
    *,
    version: int = 1,
    previous: ResearchPlan | None = None,
    critic_notes: str = "",
    decision_id: str | None = None,
    event_log: EventLog | None = None,
    retries: int = 1,
) -> ResearchPlan:
    hypotheses_text = "\n".join(
        f"- {h.statement}（证伪：{'；'.join(h.falsification_tests)}）" for h in hypotheses
    )
    diff_instruction = ""
    if previous is not None:
        diff_instruction = (
            f"\n这是对 v{previous.version} 的修订。当前上一版计划：\n{_plan_digest(previous)}"
            f"\ndiff_summary 必须概述与上一版的差异（改了哪些步骤、为什么）。"
        )
    from qresearch.experiments.manager import RUNNABLE_ACTIONS
    from qresearch.tools.registry import load_seed_tools, tool_names as registered_tools

    load_seed_tools()
    known_tools = set(registered_tools())

    def _check_tools(out: PlanOutput) -> None:
        for s in out.steps:
            # tools 字段填的是注册工具名，不是动作名
            unknown = [t for t in s.tools if t not in known_tools]
            if unknown:
                raise ValueError(
                    f"步骤（{s.purpose}）的 tools {unknown} 未注册；"
                    f"tools 只能从注册工具清单中选择：{sorted(known_tools)}"
                )
            # 可执行实验动作必须恰好引用 1 个注册工具
            if s.action in RUNNABLE_ACTIONS and len(s.tools) != 1:
                raise ValueError(
                    f"步骤（{s.purpose}）动作 {s.action} 必须恰好引用 1 个注册工具"
                    f"（实际 {len(s.tools)}）"
                )

    out = client.call_station(
        "plan", project_id, PlanOutput,
        PLAN.format(
            version=version, goal=_goal_digest(goal), hypotheses=hypotheses_text,
            actions=actions_text(), tools=tools_text(), critic_notes=critic_notes or "（无）",
            diff_instruction=diff_instruction,
        ),
        retries=retries, event_log=event_log, validator=_check_tools,
    )
    steps = [
        PlanStep(
            step_id=f"step_{i}",
            action=s.action, purpose=s.purpose, tools=s.tools,
            inputs=s.inputs, expected_outputs=s.expected_outputs,
            requires_approval=s.requires_approval,
        )
        for i, s in enumerate(out.steps, start=1)
    ]
    return ResearchPlan(
        project_id=project_id, goal_id=goal.goal_id, version=version,
        steps=steps, risks=out.risks, diff_summary=out.diff_summary,
        based_on_decision=decision_id,
    )


def critique(
    client: DSHClient,
    project_id: str,
    goal: Goal,
    plan: ResearchPlan,
    *,
    event_log: EventLog | None = None,
    retries: int = 1,
) -> CritiqueOutput:
    out = client.call_station(
        "critic", project_id, CritiqueOutput,
        CRITIC.format(goal=_goal_digest(goal), plan=_plan_digest(plan), actions=actions_text()),
        retries=retries, event_log=event_log,
    )
    if event_log is not None:
        event_log.append(Event(
            actor=Actor.MODEL, action="critique",
            project_id=project_id, object_type="ResearchPlan", object_id=plan.plan_id,
            detail={
                "verdict": out.verdict,
                "issues": [i.model_dump() for i in out.issues],
            },
        ))
    return out


def plan_with_critic(
    client: DSHClient,
    project_id: str,
    goal: Goal,
    hypotheses: list[Hypothesis],
    *,
    previous: ResearchPlan | None = None,
    decision_id: str | None = None,
    max_rounds: int = 2,
    event_log: EventLog | None = None,
    retries: int = 1,
) -> tuple[ResearchPlan, CritiqueOutput]:
    """制定计划并交批评者攻击；存在 blocker 则修订再评（上限 max_rounds）。

    即使未获 pass 也返回——阻塞项交由审批人裁决（人审是最终防线）。
    """
    version = previous.version + 1 if previous else 1
    plan = make_plan(
        client, project_id, goal, hypotheses, version=version, previous=previous,
        decision_id=decision_id, event_log=event_log, retries=retries,
    )
    critique_out: CritiqueOutput | None = None
    for round_no in range(max_rounds):
        critique_out = critique(client, project_id, goal, plan, event_log=event_log, retries=retries)
        has_blocker = any(i.severity == "blocker" for i in critique_out.issues)
        if critique_out.verdict == "pass" and not has_blocker:
            return plan, critique_out
        if round_no == max_rounds - 1:
            break  # 最后一轮不再重生成，阻塞项交由审批人裁决
        notes = "\n".join(
            f"- [{i.severity}] {i.step_id or '整体'}：{i.description}" for i in critique_out.issues
        )
        previous = plan
        plan = make_plan(
            client, project_id, goal, hypotheses, version=plan.version + 1,
            previous=previous, critic_notes=notes, decision_id=decision_id,
            event_log=event_log, retries=retries,
        )
    assert critique_out is not None
    return plan, critique_out


# ================================================================ ANALYZE
def _hypotheses_text(hypotheses: list[Hypothesis]) -> str:
    return "\n".join(
        f"- {h.statement}（证伪：{'；'.join(h.falsification_tests)}）" for h in hypotheses
    ) or "（无）"


def _eligible_digest(eligible: list[dict]) -> str:
    exp_lines = []
    for e in eligible:
        keys = {k: v for k, v in e["key_results"].items()
                if isinstance(v, (int, float, str))}
        exp_lines.append(
            f"- [{e['experiment_id']}] 步骤 {e['step_id']}，工具 {e['tool']}，输入 {e['inputs']}：{keys}"
        )
    return "\n".join(exp_lines) or "（本轮无已通过验证的实验）"


def analyze(
    client: DSHClient,
    project_id: str,
    goal: Goal,
    hypotheses: list[Hypothesis],
    eligible: list[dict],
    *,
    event_log: EventLog | None = None,
    retries: int = 1,
) -> tuple[AnalyzeOutput, list[Evidence]]:
    """ANALYZE 站点：五段式分析。只允许引用已通过验证的实验 id（资格门在代码层）。"""
    eligible_ids = {e["experiment_id"] for e in eligible}

    def _check_citations(out: AnalyzeOutput) -> None:
        for section in ("observations", "interpretations"):
            for c in getattr(out, section):
                unknown = [i for i in c.experiment_ids if i not in eligible_ids]
                if unknown:
                    raise ValueError(
                        f"证据资格门：analysis 引用了未通过验证的实验 {unknown}；"
                        f"experiment_ids 只能从以下 id 中选取：{sorted(eligible_ids)}"
                    )

    out = client.call_station(
        "analyze", project_id, AnalyzeOutput,
        ANALYZE.format(goal=_goal_digest(goal),
                       hypotheses=_hypotheses_text(hypotheses),
                       eligible_experiments=_eligible_digest(eligible)),
        retries=retries, event_log=event_log, validator=_check_citations,
    )
    evidence: list[Evidence] = []
    for c in out.observations:
        for exp_id in c.experiment_ids:
            evidence.append(Evidence(
                project_id=project_id, type=EvidenceType.NUMERICAL,
                claim=c.claim, source_experiment=exp_id,
            ))
    for c in out.interpretations:
        for exp_id in c.experiment_ids:
            evidence.append(Evidence(
                project_id=project_id, type=EvidenceType.CONSISTENCY_CHECK,
                claim=c.claim, source_experiment=exp_id,
            ))
    if event_log is not None:
        event_log.append(Event(
            actor=Actor.MODEL, action="analyze", project_id=project_id,
            object_type="Analysis", object_id=project_id,
            detail={
                "n_observations": len(out.observations),
                "n_interpretations": len(out.interpretations),
                "n_evidence": len(evidence),
                "uncertainties": out.uncertainties,
            },
        ))
    return out, evidence


# ================================================================ DECIDE
_REC_MAP = {
    # (DecisionType, DecisionRecommendation)
    "iterate": (DecisionType.ITERATE_OR_TERMINATE, DecisionRecommendation.ITERATE),
    "terminate": (DecisionType.ITERATE_OR_TERMINATE, DecisionRecommendation.TERMINATE),
    "replan": (DecisionType.REPLAN, DecisionRecommendation.REPLAN),
    "declare_result": (DecisionType.DECLARE_RESULT, DecisionRecommendation.ACCEPT),
}


def _hypothesis_digest(hypotheses: list[Hypothesis]) -> str:
    lines = [f"- {h.statement}" for h in hypotheses]
    return "\n".join(lines) or "（无）"


def decide(
    client: DSHClient,
    project_id: str,
    goal: Goal,
    hypotheses: list[Hypothesis],
    analysis: AnalyzeOutput,
    evidence: list[Evidence],
    *,
    round_no: int,
    max_rounds: int,
    event_log: EventLog | None = None,
    retries: int = 1,
) -> Decision:
    """DECIDE 站点：LLM 提议决策建议，代码校验 checklist 并落账。"""
    budget_text = (
        f"第 {round_no} 轮 / 预算上限 {max_rounds} 轮；"
        + ("尚有剩余预算" if round_no < max_rounds else "预算已耗尽：不得建议继续大规模实验")
    )
    evidence_text = "\n".join(
        f"- [{ev.evidence_id}] {ev.claim}（实验 {ev.source_experiment}）"
        for ev in evidence
    ) or "（无）"
    def _check_evidence(o: DecideOutput) -> None:
        valid = {ev.evidence_id for ev in evidence}
        for item in o.checklist:
            if item.status == "passed" and item.evidence not in valid:
                raise ValueError(
                    f"checklist passed 项引用了不存在的 evidence {item.evidence!r}；"
                    f"只能引用以下 evidence id：{sorted(valid)}"
                )

    out = client.call_station(
        "decide", project_id, DecideOutput,
        DECIDE.format(
            goal=_goal_digest(goal),
            hypotheses=_hypothesis_digest(hypotheses),
            analysis=(
                f"观察：{[c.claim for c in analysis.observations]}；"
                f"解读：{[c.claim for c in analysis.interpretations]}；"
                f"不确定：{analysis.uncertainties}；"
                f"替代解释：{analysis.alternative_explanations}；"
                f"建议下一步：{analysis.recommended_next_steps}"
            ),
            evidence=evidence_text,
            budget=budget_text,
        ),
        retries=retries, event_log=event_log, validator=_check_evidence,
    )
    from qresearch.core.models import CheckItem

    decision = Decision(
        project_id=project_id,
        type=_REC_MAP[out.recommendation][0],
        recommendation=_REC_MAP[out.recommendation][1],
        checklist=[
            CheckItem(claim=i.claim, status=i.status, evidence=i.evidence, reason=i.reason)
            for i in out.checklist
        ],
        info_gain_estimate=out.info_gain_estimate,
        alternatives_considered=out.alternatives_considered,
        rationale=out.rationale,
        made_by=Actor.MODEL,
        requires_human=True,  # Phase 5 默认全人工确认；自动化放开是后续按类授权
    )
    if event_log is not None:
        event_log.append(Event(
            actor=Actor.MODEL, action="decide", project_id=project_id,
            object_type="Decision", object_id=decision.decision_id,
            detail={
                "recommendation": out.recommendation, "round": round_no,
                "checklist": [i.model_dump() for i in decision.checklist],
                "rationale": out.rationale,
            },
        ))
    return decision
