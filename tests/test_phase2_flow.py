"""Phase 2 流程端到端测试（离线假模型）：问题 → Goal → 假设 → 计划 → 审批落账。"""
from qresearch.core.events import EventLog
from qresearch.core.models import PlanStep, ResearchPlan
from qresearch.core.status import Actor, PlanStatus
from qresearch.core.storage import Storage
from qresearch.loop import reject_plan, run_planning_phase

OFFLINE_RESPONSES = {
    "understand": (
        '{"refined_question": "一维 Heisenberg 基态能量收敛性",'
        ' "quantities": ["E0/L"],'
        ' "success_criteria": ["与 1/4-ln2 对照到 1e-3", "两个系统尺寸比较"]}'
    ),
    "hypothesize": (
        '{"hypotheses": ['
        '{"statement": "外推后与 Bethe ansatz 一致", "rationale": "有解析解",'
        ' "falsification_tests": ["L=8,12 偏差超过 1e-3"], "discriminating_experiment": "ED"},'
        '{"statement": "有限尺寸效应小于 1%", "rationale": "文献",'
        ' "falsification_tests": ["L=8 与 L=12 差异超过 1%"], "discriminating_experiment": "尺寸扫描"}]}'
    ),
    "plan": (
        '{"steps": ['
        '{"action": "run_experiment", "purpose": "L=8 基准", "tools": ["simple_ed"],'
        ' "inputs": {"L": 8}, "expected_outputs": ["energy.csv"]},'
        '{"action": "compare_benchmark", "purpose": "与 Bethe ansatz 对照",'
        ' "expected_outputs": ["comparison.md"]}],'
        ' "risks": ["有限尺寸效应"], "diff_summary": null}'
    ),
    "critic": '{"verdict": "pass", "issues": []}',
}


def test_planning_phase_end_to_end_offline(tmp_path, make_scripted_client):
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    client = make_scripted_client(OFFLINE_RESPONSES)

    plan = run_planning_phase(client, storage, log, "proj_flow",
                              "一维 Heisenberg 基态能量研究", auto_approve=True)

    assert plan.status == PlanStatus.APPROVED
    bundle = storage.load_bundle("proj_flow")
    assert len(bundle.goals) == 1
    assert len(bundle.hypotheses) == 2
    assert bundle.plans[0].plan_id == plan.plan_id
    # 每条假设都带证伪试验（铁律）
    assert all(h.falsification_tests for h in bundle.hypotheses)

    actions = [e.action for e in log.events(project_id="proj_flow")]
    assert actions[0] == "create_project"
    assert "critique" in actions and "plan_ready" in actions and "approve" in actions
    approve_event = next(e for e in log.events() if e.action == "approve")
    assert approve_event.actor == Actor.SYSTEM  # auto-approve 留痕，非人工


def test_reject_records_event(tmp_path):
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    plan = ResearchPlan(
        project_id="p1", goal_id="g1",
        steps=[PlanStep(action="run_experiment", purpose="p")],
    )
    storage.save(plan)
    reject_plan(storage, log, plan, actor=Actor.HUMAN, note="不做这个方向")
    assert storage.get(ResearchPlan, plan.plan_id).status == PlanStatus.REJECTED
    event = next(e for e in log.events(project_id="p1") if e.action == "reject")
    assert event.actor == Actor.HUMAN


PLAN_V2 = (
    '{"steps": ['
    '{"action": "run_experiment", "purpose": "L=8 基准（按意见修订）", "tools": ["simple_ed"],'
    ' "inputs": {"L": 8}, "expected_outputs": ["energy.csv"]}],'
    ' "risks": [], "diff_summary": "去掉对照步骤，只保留基准"}'
)


def test_interactive_approval_revise_then_approve(
        tmp_path, make_scripted_client, monkeypatch):
    """对话式审批：c 提意见 → plan_feedback 落账（actor=HUMAN）→ 新版再过 critic → y 批准。"""
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    responses = dict(OFFLINE_RESPONSES)
    responses["plan"] = [OFFLINE_RESPONSES["plan"], PLAN_V2]
    responses["critic"] = ['{"verdict": "pass", "issues": []}'] * 2
    client = make_scripted_client(responses)

    answers = iter(["c", "只保留基准步骤，去掉对照步骤", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    plan = run_planning_phase(client, storage, log, "proj_rev",
                              "一维 Heisenberg 基态能量研究", auto_approve=False)

    assert plan.status == PlanStatus.APPROVED
    assert plan.version == 2
    plans = storage.list(ResearchPlan, project_id="proj_rev")
    assert len(plans) == 2
    events = log.events(project_id="proj_rev")
    actions = [e.action for e in events]
    assert actions.count("plan_ready") == 2
    assert actions.count("plan_feedback") == 1
    assert actions.count("critique") == 2  # 新版重新过了 critic
    fb = next(e for e in events if e.action == "plan_feedback")
    assert fb.actor == Actor.HUMAN
    assert "基准" in fb.detail["notes"]
    approve = [e for e in events if e.action == "approve"]
    assert approve[-1].actor == Actor.HUMAN  # 人工批准留痕，非 system 代行


def test_interactive_approval_eof_never_approves(
        tmp_path, make_scripted_client, monkeypatch):
    """EOF（无人在场）按放弃处理——绝不默认批准。"""
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    client = make_scripted_client(OFFLINE_RESPONSES)

    def _eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    plan = run_planning_phase(client, storage, log, "proj_eof", "q",
                              auto_approve=False)
    assert plan.status == PlanStatus.REJECTED
    events = log.events(project_id="proj_eof")
    assert not [e for e in events if e.action == "approve"]
    reject = next(e for e in events if e.action == "reject")
    assert reject.actor == Actor.HUMAN
