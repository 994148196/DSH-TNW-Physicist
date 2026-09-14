"""研究闭环测试：3 轮全自动（离线脚本模型），全程落账可回放 + 预算闸 + 资格门。"""
import json
import re

import pytest
from qresearch.core.events import EventLog
from qresearch.core.models import Decision, Evidence, ResearchPlan
from qresearch.core.status import Actor, DecisionType, PlanStatus
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient
from qresearch.research_loop import run_research_loop
from conftest import Persistent
from qresearch.stations.executors import analyze

UNDERSTAND_OK = (
    '{"refined_question": "Heisenberg 链基态能量", "quantities": ["e0"],'
    ' "success_criteria": ["与 1/4-ln2 对照"]}'
)
HYP_OK = (
    '{"hypotheses": ['
    '{"statement": "外推与 BA 一致", "rationale": "解析",'
    ' "falsification_tests": ["偏差超 1e-3"], "discriminating_experiment": "ED"},'
    '{"statement": "有限尺寸效应小", "rationale": "文献",'
    ' "falsification_tests": ["两尺寸差超 1%"], "discriminating_experiment": "扫描"}]}'
)
PLAN_OK = (
    '{"steps": ['
    '{"action": "run_experiment", "purpose": "基准", "tools": ["simple_ed"],'
    ' "inputs": {"N": 4}, "expected_outputs": ["E0"]},'
    '{"action": "run_experiment", "purpose": "扫描", "tools": ["simple_ed"],'
    ' "inputs": {"N": 6}, "expected_outputs": ["E0"]}],'
    ' "risks": [], "diff_summary": null}'
)
CRITIC_PASS = '{"verdict": "pass", "issues": []}'


def _analysis_response(prompt: str, session_id: str) -> str:
    """从 prompt 里解析本轮真正通过验证的实验 id，并只引用它们。"""
    exp_ids = re.findall(r"\[(exp_[0-9a-f]+)\]", prompt)
    assert exp_ids, "分析阶段 prompt 中应包含已验证实验"
    return json.dumps({
        "observations": [{"claim": "能量与解析值一致", "experiment_ids": exp_ids[:1]}],
        "interpretations": [{"claim": "支持假设一", "experiment_ids": exp_ids[:1]}],
        "uncertainties": ["系统尺寸仍小"],
        "alternative_explanations": [],
        "recommended_next_steps": ["下一轮扩大 N"],
    }, ensure_ascii=False)


def _decide_factory(recommendations: list[str]):
    state = {"call": 0}

    def _resp(prompt: str, session_id: str) -> str:
        rec = recommendations[state["call"]]
        state["call"] += 1
        ev = re.findall(r"evidence_[0-9a-f]+", prompt)
        item = {"claim": "关键证据已核", "status": "passed" if ev else "untested",
                "evidence": ev[0] if ev else None}
        return json.dumps({"recommendation": rec, "checklist": [item],
                           "info_gain_estimate": "high", "rationale": "按证据决策"},
                          ensure_ascii=False)

    return _resp


@pytest.fixture()
def loop_client(make_scripted_client):
    responses = {
        "understand": UNDERSTAND_OK,
        "hypothesize": HYP_OK,
        "plan": [PLAN_OK] * 3,
        "critic": [CRITIC_PASS] * 3,
        "analyze": [Persistent(_analysis_response)],
        "decide": [Persistent(_decide_factory(["iterate", "replan", "declare_result"]))],
    }
    return make_scripted_client(responses)


def test_three_round_loop_end_to_end(tmp_path, loop_client):
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")

    summary = run_research_loop(loop_client, storage, log, "proj_loop",
                                "Heisenberg 链基态研究", rounds=3, auto_approve=True)

    assert summary["status"] == "terminated"
    assert summary["rounds_used"] == 3
    assert summary["evidence"] >= 3

    # 三版计划，逐版递增，replan 决策与下一版计划挂链
    plans = sorted(storage.list(ResearchPlan, project_id="proj_loop"),
                   key=lambda p: p.version)
    assert [p.version for p in plans] == [1, 2, 3]
    assert plans[1].based_on_decision == summary["decisions"][0]
    assert plans[2].based_on_decision == summary["decisions"][1]

    # 决策落账：R1 iterate / R2 replan / R3 declare_result（无条件人工）
    decisions = [storage.get(Decision, d) for d in summary["decisions"]]
    assert [d.type for d in decisions] == [
        DecisionType.ITERATE_OR_TERMINATE, DecisionType.REPLAN, DecisionType.DECLARE_RESULT,
    ]
    assert decisions[2].requires_human is True
    assert all(d.checklist[0].evidence for d in decisions)

    # 证据落账且挂真实实验
    evidences = storage.list(Evidence, project_id="proj_loop")
    assert evidences
    assert all(e.source_experiment.startswith("exp_") for e in evidences)

    # 全程可回放：事件链完整
    actions = [e.action for e in log.events(project_id="proj_loop")]
    assert actions.count("plan_ready") == 3
    assert actions.count("approve") == 3
    assert actions.count("experiment_started") == 6
    assert actions.count("verify_experiment") == 6
    assert actions.count("analyze") == 3
    assert actions.count("decide") == 3
    assert actions[-1] == "report_generated"

    # 报告生成
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "研究报告" in report and "决策记录" in report and "exp_" in report


def test_budget_gate_forces_human(tmp_path, make_scripted_client):
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    client = make_scripted_client({
        "understand": UNDERSTAND_OK, "hypothesize": HYP_OK,
        "plan": PLAN_OK, "critic": CRITIC_PASS,
        "analyze": [Persistent(_analysis_response)],
        "decide": [Persistent(_decide_factory(["iterate"]))],
    })
    summary = run_research_loop(client, storage, log, "proj_budget",
                                "q", rounds=1, auto_approve=True)
    assert summary["status"] == "budget_exhausted"
    decision = storage.get(Decision, summary["decisions"][0])
    assert decision.requires_human is True
    assert "[预算闸]" in decision.rationale


def test_analyze_rejects_unverified_experiment(tmp_path, make_scripted_client):
    """证据资格门：分析引用未通过验证的实验 → 校验失败（带反馈重试），耗尽后转人工。"""
    from qresearch.core.models import Goal
    from qresearch.dsh_client import NeedsHuman

    bad = json.dumps({
        "observations": [{"claim": "c", "experiment_ids": ["exp_deadbeef"]}],
        "interpretations": [], "uncertainties": ["u"],
        "alternative_explanations": [], "recommended_next_steps": [],
    })
    client = make_scripted_client({"analyze": bad})
    goal = Goal(project_id="p", question="q", success_criteria=["s"])
    with pytest.raises(NeedsHuman, match="证据资格门"):
        analyze(client, "p", goal, [], [], retries=0)


PLAN_SCAN = (
    '{"steps": ['
    '{"action": "parameter_scan", "purpose": "尺寸扫描", "tools": ["simple_ed"],'
    ' "inputs": {"scan": {"N": [4, 6]}, "J": 1.0}, "expected_outputs": ["E0"]}],'
    ' "risks": [], "diff_summary": null}'
)


def test_scan_children_verified(tmp_path, make_scripted_client):
    """parameter_scan 子实验（step_id 带 _idx 后缀）必须进入验证与资格门。"""
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    client = make_scripted_client({
        "understand": UNDERSTAND_OK, "hypothesize": HYP_OK,
        "plan": PLAN_SCAN, "critic": CRITIC_PASS,
        "analyze": [Persistent(_analysis_response)],
        "decide": [Persistent(_decide_factory(["declare_result"]))],
    })
    summary = run_research_loop(client, storage, log, "proj_scan",
                                "Heisenberg 链尺寸扫描", rounds=1, auto_approve=True)
    assert summary["status"] == "terminated"
    actions = [e.action for e in log.events(project_id="proj_scan")]
    assert actions.count("experiment_started") == 2
    assert actions.count("verify_experiment") == 2
    evidences = storage.list(Evidence, project_id="proj_scan")
    assert evidences
    assert all(e.source_experiment.startswith("exp_") for e in evidences)


def test_scan_without_scan_key_recorded_not_crash(tmp_path, make_scripted_client):
    """parameter_scan 缺 inputs.scan：规划期校验会拦（重试转人工），
    执行期兜底按 FAILED 落账而不是 ValueError 炸闭环（live 教训）。"""
    from qresearch.experiments.manager import ExperimentManager
    from qresearch.core.models import PlanStep, ResearchPlan

    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    mgr = ExperimentManager(storage, log, experiments_root=tmp_path / "ex")
    plan = ResearchPlan(
        project_id="p", goal_id="g", version=1,
        steps=[PlanStep(step_id="s1", action="parameter_scan", purpose="x",
                        tools=["simple_ed"], inputs={"N": [4, 6]}, expected_outputs=[])],
        risks=[], diff_summary=None,
    )
    exps = mgr.execute_scan(plan, plan.steps[0])
    assert len(exps) == 1 and exps[0].status.value == "failed"
    assert "inputs.scan" in (exps[0].error or "")


def test_round_callback_injects_user_notes(tmp_path, make_scripted_client):
    """③ 轮末回调：digest 汇报节点；返回 str=修改意见 → 注入下一版计划 prompt 并落账。"""
    plan_prompts: list[str] = []

    def plan_recorder(prompt: str, session_id: str) -> str:
        plan_prompts.append(prompt)
        return PLAN_OK

    digests: list[dict] = []

    def callback(digest: dict):
        digests.append(digest)
        return "请把系统尺寸加倍" if digest["round_no"] == 1 else None

    client = make_scripted_client({
        "understand": UNDERSTAND_OK, "hypothesize": HYP_OK,
        "plan": [Persistent(plan_recorder)],
        "critic": [CRITIC_PASS] * 3,
        "analyze": [Persistent(_analysis_response)],
        "decide": [Persistent(_decide_factory(["iterate", "iterate", "declare_result"]))],
    })
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    summary = run_research_loop(client, storage, log, "proj_cb", "Heisenberg q",
                                rounds=3, auto_approve=True, round_callback=callback)
    assert summary["status"] == "terminated"
    # digest 只在"还要继续"的轮末汇报（收束轮结论走 declare_result 人工确认）
    assert [d["round_no"] for d in digests] == [1, 2]
    assert digests[0]["decision"]["recommendation"] == "iterate"
    assert digests[0]["experiments_this_round"] == 2
    # 意见只进第二轮计划 prompt，第三轮已清除（注入只生效一轮）
    assert len(plan_prompts) == 3
    assert "请把系统尺寸加倍" not in plan_prompts[0]
    assert "请把系统尺寸加倍" in plan_prompts[1]
    assert "研究者本人在上一轮结束时给出的修改意见" in plan_prompts[1]
    assert "请把系统尺寸加倍" not in plan_prompts[2]
    # 反馈以 actor=HUMAN 落账
    feedbacks = [e for e in log.events(project_id="proj_cb")
                 if e.action == "user_feedback"]
    assert len(feedbacks) == 1
    assert feedbacks[0].actor == Actor.HUMAN
    assert feedbacks[0].detail["notes"] == "请把系统尺寸加倍"


def test_round_callback_stop(tmp_path, make_scripted_client):
    """③ 轮末回调返回 "stop"：当轮收尾即停（stopped_by_user），事件落账，可 resume。"""
    digests: list[dict] = []

    def callback(digest: dict):
        digests.append(digest)
        return "stop"

    client = make_scripted_client({
        "understand": UNDERSTAND_OK, "hypothesize": HYP_OK,
        "plan": PLAN_OK, "critic": CRITIC_PASS,
        "analyze": [Persistent(_analysis_response)],
        "decide": [Persistent(_decide_factory(["iterate"]))],
    })
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    summary = run_research_loop(client, storage, log, "proj_stop", "q",
                                rounds=3, auto_approve=True, round_callback=callback)
    assert summary["status"] == "stopped_by_user"
    assert "resume_research_loop" in summary["hint"]
    assert len(digests) == 1 and digests[0]["round_no"] == 1
    actions = [e.action for e in log.events(project_id="proj_stop")]
    assert actions.count("user_stop") == 1
    # 中途停止也出报告（部分结果不丢），事件链以 report_generated 收尾
    assert actions[-1] == "report_generated"
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "`stopped_by_user`" in report
    # 停在第 1 轮末：只进行了 1 轮的实验
    assert actions.count("experiment_started") == 2
