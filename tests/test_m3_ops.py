"""M3 运维补齐测试：协作式取消（诚实落账）/ 混跑版本连续 / adhoc 蒸馏 / viz 冒烟。

混跑 = "交互式 1 轮（人工批准，actor=HUMAN）+ 无人值守续跑 2 轮（actor=SYSTEM）"，
断言计划版本连续递增、证据可累计、报告含全部轮次（计划 §4.3 M3 检验 2）。
"""
import json
import re
import time

import pytest
from conftest import Persistent

from qresearch.core.events import EventLog
from qresearch.core.models import Evidence, Experiment, PlanStep, ResearchPlan
from qresearch.core.status import ExperimentStatus
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient
from qresearch.engine import ResearchEngine
from qresearch.experiments.manager import InProcessToolRunner
from qresearch.memory.distill import distill_project
from qresearch.research_loop import resume_research_loop, run_research_loop
from qresearch.tools.registry import load_seed_tools

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
    ' "inputs": {"N": 4}, "expected_outputs": ["E0"]}],'
    ' "risks": [], "diff_summary": null}'
)
CRITIC_PASS = '{"verdict": "pass", "issues": []}'


def _analysis_response(prompt: str, session_id: str) -> str:
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


class _SlowRunner(InProcessToolRunner):
    """慢速进程内 runner：让"取消请求发生在执行途中"可复现。"""

    def run(self, spec, inputs, workspace, timeout_s):
        time.sleep(0.08)
        return super().run(spec, inputs, workspace, timeout_s)


def _engine(tmp_path, runner=None) -> tuple[Storage, EventLog, ResearchEngine]:
    load_seed_tools()
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    engine = ResearchEngine(storage, log, DSHClient(runner=lambda p, s: "{}"),
                            experiments_root=tmp_path / "experiments",
                            runner=runner or InProcessToolRunner())
    return storage, log, engine


def _multi_step_plan(engine: ResearchEngine, n: int = 4) -> ResearchPlan:
    plan = ResearchPlan(
        project_id="proj", goal_id="goal_x", version=1,
        steps=[PlanStep(step_id=f"s{i}", action="run_experiment", purpose=f"点{i}",
                        tools=["simple_ed"], inputs={"N": 4, "want_gap": False})
               for i in range(1, n + 1)])
    engine.storage.save(plan)
    return plan


# ================================================================ 协作式取消（M3）
def test_cancel_requested_before_run(tmp_path):
    """取消先于执行：一个实验账目都不产生（未开工的步骤没有台账记录）。"""
    storage, log, engine = _engine(tmp_path)
    engine.open_project("proj", "问题")
    plan = _multi_step_plan(engine)
    result = engine.experiments_run(plan.plan_id, cancel_check=lambda: True)
    assert storage.list(Experiment, "proj") == []
    assert result["n_completed"] == 0 and result["n_failed"] == 0


def test_cooperative_cancel_via_job(tmp_path):
    """job_cancel → cancel_requested：在跑的完成，未开的按取消落账（诚实边界）。"""
    storage, log, engine = _engine(tmp_path, runner=_SlowRunner())
    engine.open_project("proj", "问题")
    plan = _multi_step_plan(engine)

    job_id = engine.experiments_run_async(plan.plan_id)
    time.sleep(0.12)  # 让第一批实验进入执行
    assert engine.jobs.cancel(job_id) is False  # running：只置请求，不谎报已取消
    assert engine.jobs.cancel_requested(job_id) is True
    for _ in range(300):
        if engine.job_status(job_id)["state"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.02)
    assert engine.job_status(job_id)["state"] == "done"
    result = engine.job_result(job_id)["result"]

    exps = storage.list(Experiment, "proj")
    assert len(exps) == 4  # 账目在建账段已全部落账
    completed = [e for e in exps if e.status is ExperimentStatus.COMPLETED]
    cancelled = [e for e in exps
                 if e.status is ExperimentStatus.FAILED and "取消" in (e.error or "")]
    assert 1 <= len(completed) <= 3
    assert len(completed) + len(cancelled) == 4
    assert result["n_completed"] == len(completed)
    assert result["n_failed"] == len(cancelled)
    # 事件诚实：cancelled 实验的 finished 事件带 status=cancelled 标注
    finished = [e for e in log.events(project_id="proj")
                if e.action == "experiment_finished"]
    assert sum(1 for e in finished if e.detail.get("status") == "cancelled") \
        == len(cancelled)


# ================================================================ 混跑（M3 检验 2）
def test_mixed_interactive_then_unattended(tmp_path, make_scripted_client,
                                           monkeypatch):
    """交互式 1 轮 + 无人值守 2 轮：版本连续、证据累计、审批 actor 留痕分明。"""
    client = make_scripted_client({
        "understand": UNDERSTAND_OK,
        "hypothesize": HYP_OK,
        "plan": [PLAN_OK] * 3,
        "critic": [CRITIC_PASS] * 3,
        "analyze": [Persistent(_analysis_response)],
        "decide": [Persistent(_decide_factory(["iterate", "iterate", "declare_result"]))],
    })
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")

    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")  # 交互批准 v1
    s1 = run_research_loop(client, storage, log, "proj_mix", "Heisenberg 链基态研究",
                           rounds=1, auto_approve=False,
                           experiments_root=tmp_path / "experiments")
    assert s1["rounds_used"] == 1  # 预算闸：1 轮后转人工

    s2 = resume_research_loop(client, storage, log, "proj_mix", rounds=3,
                              auto_approve=True, retries=1,
                              experiments_root=tmp_path / "experiments")
    assert s2["status"] == "terminated" and s2["rounds_used"] == 3

    # 计划版本连续递增（v1 交互 + v2/v3 无人值守）
    plans = sorted(storage.list(ResearchPlan, project_id="proj_mix"),
                   key=lambda p: p.version)
    assert [p.version for p in plans] == [1, 2, 3]
    # 证据累计（每轮 2 条：观察+解读）
    assert len(storage.list(Evidence, project_id="proj_mix")) == 6
    # 审批 actor 分明：v1 人工批准，v2/v3 无人值守
    approves = [e.actor.value for e in log.events(project_id="proj_mix")
                if e.action == "approve"]
    assert approves == ["human", "system", "system"]
    # 报告含全部轮次的计划版本历史
    s2_report = (tmp_path / "report.md")
    if not s2_report.exists():  # resume 收尾已生成；防御性再生成
        from qresearch.core.models import Project
        engine = ResearchEngine(storage, log, DSHClient(runner=lambda p, s: "{}"),
                                experiments_root=tmp_path / "experiments")
        engine.report_generate("proj_mix")
        s2_report = tmp_path / "report.md"
    report = s2_report.read_text(encoding="utf-8")
    for v in (1, 2, 3):
        assert f"计划 v{v}" in report
    assert "`concluded`" not in report  # 未 conclude：如实为 terminated


# ================================================================ adhoc 蒸馏（M3）
def test_distill_adhoc_unverified_becomes_failure_lesson(tmp_path):
    storage, log, engine = _engine(tmp_path)
    engine.open_project("proj_d", "问题")
    engine.adhoc_record("proj_d", kind="quick_calc", summary="bash 现算 N=4",
                        parameters={"N": 4})
    entries = distill_project(storage, "proj_d")
    failures = [e for e in entries if e.layer.value == "failure"]
    assert any("adhoc 未验证" in e.title for e in failures)
    lesson = next(e for e in failures if "adhoc 未验证" in e.title)
    assert "注册工具" in lesson.content and "adhoc_verify" in lesson.content
    # 其余层照常：确定性蒸馏行为不变
    assert any(e.layer.value == "project" for e in entries)


# ================================================================ viz 冒烟（M3 检验 3）
def test_viz_on_ledger_with_plan_and_adhoc(tmp_path):
    matplotlib = pytest.importorskip("matplotlib")
    matplotlib.use("Agg")
    from qresearch.viz import plot_project

    storage, log, engine = _engine(tmp_path)
    engine.open_project("proj", "问题")
    plan = _multi_step_plan(engine, n=2)
    engine.experiments_run(plan.plan_id)
    engine.adhoc_record("proj", kind="quick_calc", summary="顺手算",
                        parameters={"N": 4})
    paths = plot_project(storage, "proj", tmp_path / "plots")
    assert len(paths) == 3 and all(p.exists() for p in paths)
