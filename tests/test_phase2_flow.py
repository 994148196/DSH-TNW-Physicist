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
