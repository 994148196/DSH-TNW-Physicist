"""ResearchEngine 引擎层测试（计划 v3 M1）。

覆盖：只读工具零写入（L2 断言种子）/ adhoc 诚实边界（D3/A6：bash 现算永不可验证、
注册工具路径可升级）/ 计划审批事件 / 异步 job 协议（D4 幂等 + 取消语义）。
闭环全流程的回归由既有 104 项测试承担（M1 重构后它们经引擎执行，全部保持绿）。
"""
import json
import time

import pytest

from qresearch.core.events import EventLog
from qresearch.core.models import Experiment, PlanStep, ResearchPlan
from qresearch.core.status import Actor, VerificationStatus
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient
from qresearch.engine import ResearchEngine
from qresearch.experiments.manager import InProcessToolRunner
from qresearch.jobs import JobManager, UnknownJob
from qresearch.tools.registry import get_tool, load_seed_tools


@pytest.fixture()
def engine(tmp_path):
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    eng = ResearchEngine(
        storage, log, DSHClient(runner=lambda p, s: "{}"),
        experiments_root=tmp_path / "experiments", runner=InProcessToolRunner())
    yield eng
    storage.close()


def _plan(engine, project_id: str, step: dict) -> ResearchPlan:
    plan = ResearchPlan(
        project_id=project_id, goal_id="goal_test", version=1,
        steps=[PlanStep(**step)])
    engine.storage.save(plan)
    return plan


# ================================================================ 只读零写入
def test_status_events_ledger_readonly(engine, tmp_path):
    """L2 种子：只读工具不得改动 state.sqlite 与 events.jsonl（文件字节级比对）。"""
    load_seed_tools()
    engine.open_project("proj_ro", "问题")
    ev_path = tmp_path / "events.jsonl"
    db_path = tmp_path / "state.sqlite"
    before = (ev_path.read_bytes(), db_path.read_bytes())

    engine.status("proj_ro")
    engine.events("proj_ro")
    engine.ledger("proj_ro", "experiment")
    engine.ledger("proj_ro", "verification")

    after = (ev_path.read_bytes(), db_path.read_bytes())
    assert after == before


def test_status_shape(engine):
    load_seed_tools()
    engine.open_project("proj_st", "问题")
    st = engine.status("proj_st")
    assert st["project_id"] == "proj_st"
    assert st["rounds_used"] == 0 and st["evidence"] == 0
    assert st["latest_plan"] is None


# ================================================================ 执行与验证
def test_experiments_run_and_verify_flow(engine):
    load_seed_tools()
    engine.open_project("proj_flow", "Heisenberg 基态")
    plan = _plan(engine, "proj_flow", {
        "step_id": "s1", "action": "run_experiment", "purpose": "N=4 基准",
        "tools": ["simple_ed"], "inputs": {"N": 4, "want_gap": True}})
    run = engine.experiments_run(plan.plan_id)
    assert run["n_completed"] == 1 and run["n_failed"] == 0
    reports = engine.verify(run["experiment_ids"])
    assert len(reports) == 1
    assert reports[0].overall == VerificationStatus.PASSED
    # eligible 缺省过滤（MCP 路径）：analyze 的资格门由既有闭环测试覆盖
    eligible = {e.experiment_id for e in engine.storage.list(Experiment, "proj_flow")
                if e.verification_status == VerificationStatus.PASSED}
    assert eligible == set(run["experiment_ids"])


# ================================================================ adhoc 诚实边界（D3/A6）
def test_adhoc_bash_numbers_never_verifiable(engine):
    load_seed_tools()
    engine.open_project("proj_adhoc", "问题")
    exp = engine.adhoc_record("proj_adhoc", kind="quick_calc", summary="bash 现算",
                              parameters={"N": 4})
    assert exp.step_id.startswith("adhoc_")
    assert exp.plan_id == ""
    assert exp.verification_status == VerificationStatus.NOT_RUN
    with pytest.raises(ValueError, match="未关联注册工具"):
        engine.adhoc_verify(exp.experiment_id)
    evs = [e for e in engine.event_log.events(project_id="proj_adhoc")
           if e.action == "adhoc_recorded"]
    assert len(evs) == 1 and evs[0].detail["verified"] is False


def test_adhoc_registered_tool_can_upgrade(engine, tmp_path):
    """agent 用注册工具跑的临时计算：adhoc_verify 走同一套三层 golden 可升级。"""
    load_seed_tools()
    engine.open_project("proj_adhoc2", "Heisenberg")
    spec = get_tool("simple_ed")
    inputs = {"N": 4, "want_gap": True}
    out_dir = tmp_path / "adhoc_out"
    out_dir.mkdir()
    (out_dir / "result.json").write_text(
        json.dumps(spec.run(inputs), ensure_ascii=False), encoding="utf-8")
    exp = engine.adhoc_record("proj_adhoc2", kind="gap_check", summary="顺手算 N=4",
                              tool="simple_ed", parameters=inputs,
                              artifacts=[str(out_dir / "result.json")])
    report = engine.adhoc_verify(exp.experiment_id)
    assert report.overall == VerificationStatus.PASSED
    assert engine.storage.get(Experiment, exp.experiment_id) \
        .verification_status == VerificationStatus.PASSED


# ================================================================ 审批事件
def test_plan_approve_reject_events(engine):
    engine.open_project("proj_plan", "问题")
    plan = _plan(engine, "proj_plan", {
        "step_id": "s1", "action": "run_experiment", "purpose": "占位",
        "tools": ["simple_ed"], "inputs": {"N": 4}})
    engine.plan_approve(plan.plan_id, actor=Actor.HUMAN, note="人工批准")
    engine.plan_reject(plan.plan_id, actor=Actor.HUMAN, note="改判拒绝")
    evs = engine.event_log.events(project_id="proj_plan")
    assert any(e.action == "approve" and e.actor == Actor.HUMAN for e in evs)
    assert any(e.action == "reject" and e.actor == Actor.HUMAN for e in evs)


# ================================================================ job 协议（D4/A3）
def test_job_manager_lifecycle_and_idempotency():
    jm = JobManager(max_workers=1)

    def slow():
        time.sleep(0.25)
        return "ok"

    j1 = jm.submit(slow, key="experiments:plan1")
    j2 = jm.submit(slow, key="experiments:plan1")
    assert j1 == j2  # 活跃 job 同 key 幂等返回（D4 防重复实验）

    for _ in range(200):
        if jm.status(j1)["state"] in ("done", "error"):
            break
        time.sleep(0.02)
    st = jm.status(j1)
    assert st["state"] == "done"
    assert jm.result(j1)["result"] == "ok"
    # 终态后同 key 再提交 → 新 job（幂等只对活跃 job）
    assert jm.submit(slow, key="experiments:plan1") != j1


def test_job_manager_cancel_semantics():
    jm = JobManager(max_workers=1)
    j_running = jm.submit(lambda: time.sleep(0.4))
    time.sleep(0.05)  # 让它进入 running
    j_pending = jm.submit(lambda: 1)  # 单 worker：排队中
    assert jm.cancel(j_pending) is True  # pending 可直接取消
    assert jm.status(j_pending)["state"] == "cancelled"
    assert jm.cancel(j_running) is False  # running 只置取消请求，不谎报已取消
    assert jm.status(j_running)["cancel_requested"] is True
    with pytest.raises(UnknownJob):
        jm.status("job_nope")


def test_engine_experiments_run_async(engine):
    load_seed_tools()
    engine.open_project("proj_async", "问题")
    plan = _plan(engine, "proj_async", {
        "step_id": "s1", "action": "run_experiment", "purpose": "N=4",
        "tools": ["simple_ed"], "inputs": {"N": 4}})
    job_id = engine.experiments_run_async(plan.plan_id)
    for _ in range(300):
        st = engine.job_status(job_id)
        if st["state"] in ("done", "error"):
            break
        time.sleep(0.02)
    assert st["state"] == "done"
    res = engine.job_result(job_id)
    assert res["result"]["n_completed"] == 1
    assert engine.job_status(job_id)["total"] is None  # M1 无进度细粒度，如实为 None
