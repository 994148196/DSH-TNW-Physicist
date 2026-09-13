"""站点执行器测试：状态转换正确性 + 铁律在站点层的执行。"""
import pytest
from qresearch.core.models import Goal
from qresearch.dsh_client import NeedsHuman
from qresearch.stations.executors import (
    critique, hypothesize, make_plan, plan_with_critic, understand,
)

UNDERSTAND_OK = (
    '{"refined_question": "q", "quantities": ["E0"], "success_criteria": ["s1"]}'
)
HYPOTHESIZE_OK = (
    '{"hypotheses": ['
    '{"statement": "s1", "rationale": "r1", "falsification_tests": ["f1"], "discriminating_experiment": "e1"},'
    '{"statement": "s2", "rationale": "r2", "falsification_tests": ["f2"], "discriminating_experiment": "e2"}]}'
)
HYPOTHESIZE_UNFALSIFIABLE = (
    '{"hypotheses": ['
    '{"statement": "a", "rationale": "r", "falsification_tests": ["x"], "discriminating_experiment": "e"},'
    '{"statement": "b", "rationale": "r", "falsification_tests": [], "discriminating_experiment": "e"}]}'
)
PLAN_OK = (
    '{"steps": [{"action": "run_experiment", "purpose": "p1", "tools": ["simple_ed"],'
    ' "inputs": {"L": 8}, "expected_outputs": ["o"]}], "risks": ["小系统效应"], "diff_summary": null}'
)
CRITIC_PASS = '{"verdict": "pass", "issues": []}'
CRITIC_BLOCK = (
    '{"verdict": "revise", "issues": ['
    '{"step_id": "step_1", "severity": "blocker", "description": "缺少与解析解对照的步骤"}]}'
)


def _goal() -> Goal:
    return Goal(project_id="p1", question="q", success_criteria=["s"])


def test_understand_builds_goal(make_scripted_client):
    client = make_scripted_client({"understand": [UNDERSTAND_OK]})
    goal = understand(client, "p1", "原始问题")
    assert isinstance(goal, Goal)
    assert goal.project_id == "p1"
    assert goal.quantities == ["E0"]


def test_hypothesize_maps_to_hypotheses(make_scripted_client):
    client = make_scripted_client({"hypothesize": [HYPOTHESIZE_OK]})
    hyps = hypothesize(client, "p1", _goal(), n=2)
    assert [h.statement for h in hyps] == ["s1", "s2"]
    assert hyps[0].source == "r1"
    assert hyps[0].falsification_tests == ["f1"]


def test_hypothesize_escalates_on_unfalsifiable(make_scripted_client):
    client = make_scripted_client({"hypothesize": [HYPOTHESIZE_UNFALSIFIABLE]})
    with pytest.raises(NeedsHuman):
        hypothesize(client, "p1", _goal(), retries=0)


def test_make_plan_versions(make_scripted_client):
    client = make_scripted_client({"plan": [PLAN_OK, PLAN_OK]})
    goal = _goal()
    v1 = make_plan(client, "p1", goal, [], version=1)
    assert v1.version == 1
    assert v1.steps[0].step_id == "step_1"
    assert v1.risks == ["小系统效应"]
    v2 = make_plan(client, "p1", goal, [], version=2, previous=v1, decision_id="dec_9")
    assert v2.version == 2 and v2.based_on_decision == "dec_9"


def test_plan_with_critic_revises_until_pass(make_scripted_client):
    client = make_scripted_client({"plan": [PLAN_OK, PLAN_OK],
                                   "critic": [CRITIC_BLOCK, CRITIC_PASS]})
    plan, c = plan_with_critic(client, "p1", _goal(), [])
    assert plan.version == 2
    assert c.verdict == "pass"


def test_plan_with_critic_gives_up_gracefully(make_scripted_client):
    client = make_scripted_client({"plan": [PLAN_OK, PLAN_OK],
                                   "critic": [CRITIC_BLOCK, CRITIC_BLOCK]})
    plan, c = plan_with_critic(client, "p1", _goal(), [], max_rounds=2)
    assert plan.version == 2
    assert c.verdict == "revise"


def test_critique_logged_as_event(tmp_path, make_scripted_client):
    from qresearch.core.events import EventLog

    log = EventLog(tmp_path / "e.jsonl")
    client = make_scripted_client({"critic": [CRITIC_BLOCK]})
    plan = make_plan(make_scripted_client({"plan": [PLAN_OK]}), "p1", _goal(), [])
    out = critique(client, "p1", _goal(), plan, event_log=log)
    assert out.verdict == "revise"
    events = log.events(project_id="p1")
    assert events[-1].action == "critique"
    assert events[-1].detail["issues"][0]["severity"] == "blocker"


PLAN_BAD_TOOL = (
    '{"steps": [{"action": "run_experiment", "purpose": "p1", "tools": ["run_experiment"],'
    ' "inputs": {"N": 4}, "expected_outputs": ["o"]}], "risks": [], "diff_summary": null}'
)


def test_make_plan_rejects_unregistered_tool(make_scripted_client):
    """plan 语义校验：tools 引用未注册名（如把动作名当工具名）→ 重试耗尽转人工。"""
    client = make_scripted_client({"plan": PLAN_BAD_TOOL})
    with pytest.raises(NeedsHuman, match="未注册"):
        make_plan(client, "p1", _goal(), [], retries=0)


PLAN_NO_TOOL = (
    '{"steps": [{"action": "run_experiment", "purpose": "p1", "tools": [],'
    ' "inputs": {"N": 4}, "expected_outputs": ["o"]}], "risks": [], "diff_summary": null}'
)


def test_make_plan_requires_tool_for_runnable_action(make_scripted_client):
    """可执行实验动作必须恰好引用 1 个注册工具，否则重试耗尽转人工。"""
    client = make_scripted_client({"plan": PLAN_NO_TOOL})
    with pytest.raises(NeedsHuman, match="恰好引用 1 个"):
        make_plan(client, "p1", _goal(), [], retries=0)
