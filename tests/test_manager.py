"""Experiment Manager 测试：建实验 → 执行 → 落账 → 批量扫描 → 失败路径。"""
import json
import pytest
from qresearch.core.events import EventLog
from qresearch.core.models import PlanStep, ResearchPlan
from qresearch.core.status import ExperimentStatus
from qresearch.core.storage import Storage
from qresearch.experiments.manager import (
    ExperimentManager, InProcessToolRunner, ToolRunError,
)
from qresearch.tools.registry import ToolSpec, get_tool, load_seed_tools, register


def _plan(project_id: str, steps: list[PlanStep]) -> ResearchPlan:
    return ResearchPlan(project_id=project_id, goal_id="g1", steps=steps)


@pytest.fixture()
def mgr(tmp_path):
    storage = Storage(tmp_path / "s.sqlite")
    log = EventLog(tmp_path / "e.jsonl")
    manager = ExperimentManager(
        storage, log, runner=InProcessToolRunner(),
        experiments_root=tmp_path / "experiments",
    )
    manager.storage, manager.event_log = storage, log
    yield manager
    storage.close()


def test_execute_step_completed(mgr, tmp_path):
    load_seed_tools()
    plan = _plan("p1", [PlanStep(
        step_id="step_1", action="run_experiment", purpose="N=4 基准",
        tools=["simple_ed"], inputs={"N": 4},
    )])
    exp = mgr.execute_step(plan, plan.steps[0])
    assert exp.status == ExperimentStatus.COMPLETED
    assert exp.tool_id  # ToolRecord 已入库
    result = json.loads((tmp_path / "experiments" / exp.experiment_id / "result.json")
                        .read_text(encoding="utf-8"))
    assert result["E0"] == pytest.approx(-2.0, abs=1e-10)
    assert exp.environment["qresearch"]
    actions = [e.action for e in mgr.event_log.events(project_id="p1")]
    assert actions == ["experiment_started", "experiment_finished"]


def test_execute_scan_expands_grid(mgr):
    load_seed_tools()
    plan = _plan("p1", [PlanStep(
        step_id="step_2", action="parameter_scan", purpose="尺寸扫描",
        tools=["simple_ed"], inputs={"J": 1.0, "scan": {"N": [4, 6]}},
    )])
    exps = mgr.execute_scan(plan, plan.steps[0])
    assert len(exps) == 2
    assert all(e.status == ExperimentStatus.COMPLETED for e in exps)
    assert [e.parameters["_scan_index"] for e in exps] == [0, 1]
    assert exps[0].parameters["N"] == 4 and exps[1].parameters["N"] == 6


def test_execute_plan_skips_approval_and_non_runnable(mgr):
    load_seed_tools()
    plan = _plan("p1", [
        PlanStep(step_id="s1", action="run_experiment", purpose="跑", tools=["simple_ed"],
                 inputs={"N": 4}),
        PlanStep(step_id="s2", action="run_experiment", purpose="需审批", tools=["simple_ed"],
                 inputs={"N": 6}, requires_approval=True),
        PlanStep(step_id="s3", action="plot", purpose="非实验动作"),
    ])
    exps = mgr.execute_plan(plan)
    assert [e.step_id for e in exps] == ["s1"]
    exps_all = mgr.execute_plan(plan, include_requires_approval=True)
    assert [e.step_id for e in exps_all] == ["s1", "s2"]


def test_unknown_tool_rejected(mgr):
    plan = _plan("p1", [PlanStep(
        step_id="s1", action="run_experiment", purpose="x",
        tools=["not_a_tool"], inputs={},
    )])
    with pytest.raises(KeyError, match="未注册的工具"):
        mgr.execute_step(plan, plan.steps[0])


def test_tool_failure_recorded(mgr):
    load_seed_tools()
    # 注册一个必然失败的工具（只在本测试内）
    spec = ToolSpec(
        name="_broken_tool", version="0", type="test",
        description="测试失败路径", input_model=get_tool("simple_ed").input_model,
        run=lambda inputs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    try:
        register(spec)
        plan = _plan("p1", [PlanStep(
            step_id="s1", action="run_experiment", purpose="x",
            tools=["_broken_tool"], inputs={"N": 4},
        )])
        exp = mgr.execute_step(plan, plan.steps[0])
        assert exp.status == ExperimentStatus.FAILED
        assert "boom" in exp.error
    finally:
        from qresearch.tools import registry
        del registry._REGISTRY["_broken_tool"]


def test_invalid_inputs_recorded_as_failure(mgr):
    load_seed_tools()
    plan = _plan("p1", [PlanStep(
        step_id="s1", action="run_experiment", purpose="x",
        tools=["simple_ed"], inputs={"N": 3},  # 奇数 N → 校验失败
    )])
    exp = mgr.execute_step(plan, plan.steps[0])
    assert exp.status == ExperimentStatus.FAILED


def test_tool_record_registered_once(mgr):
    load_seed_tools()
    spec = get_tool("simple_ed")
    r1 = mgr._ensure_tool_record(spec)
    r2 = mgr._ensure_tool_record(spec)
    assert r1.tool_id == r2.tool_id
