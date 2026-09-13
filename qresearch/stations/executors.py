"""站点执行器：prompt 投影（从状态对象现算）→ call_station → 转换为状态层对象。

上下文=状态的投影（计划 v2 §7.8）：每个函数从传入对象构造 prompt，不携带历史对话。
"""
from __future__ import annotations

import json

from qresearch.core.events import Event, EventLog
from qresearch.core.models import Goal, Hypothesis, PlanStep, ResearchPlan
from qresearch.core.status import Actor
from qresearch.dsh_client import DSHClient
from .prompts import ACTIONS, CRITIC, HYPOTHESIZE, PLAN, UNDERSTAND, actions_text, tools_text
from .schemas import CritiqueOutput, HypothesizeOutput, PlanOutput, UnderstandOutput


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
    out = client.call_station(
        "plan", project_id, PlanOutput,
        PLAN.format(
            version=version, goal=_goal_digest(goal), hypotheses=hypotheses_text,
            actions=actions_text(), tools=tools_text(), critic_notes=critic_notes or "（无）",
            diff_instruction=diff_instruction,
        ),
        retries=retries, event_log=event_log,
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
