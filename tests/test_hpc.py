"""Phase 8 测试：并行实验 / 预算闸 / 暂停恢复 / Slurm 后端 / 多项目队列。"""
import json
import re
import time
from pathlib import Path

from qresearch.core.budget import Budget
from qresearch.core.events import EventLog
from qresearch.core.status import ExperimentStatus, VerificationStatus
from qresearch.hpc import SlurmConfig, SlurmRunner, render_sbatch
from qresearch.orchestrator import ProjectJob, run_projects
from qresearch.research_loop import resume_research_loop, run_research_loop

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
    '{"action": "run_experiment", "purpose": "对照", "tools": ["simple_ed"],'
    ' "inputs": {"N": 6}, "expected_outputs": ["E0"]}],'
    ' "risks": [], "diff_summary": null}'
)
CRITIC_PASS = '{"verdict": "pass", "issues": []}'


def _analysis_response(prompt: str, session_id: str) -> str:
    exp_ids = re.findall(r"\[(exp_[0-9a-f]+)\]", prompt)
    assert exp_ids, "分析阶段 prompt 中应包含已验证实验"
    return json.dumps({
        "observations": [{"claim": "能量与解析值一致", "experiment_ids": exp_ids[:1]}],
        "interpretations": [], "uncertainties": ["系统尺寸仍小"],
        "alternative_explanations": [], "recommended_next_steps": ["扩大 N"],
    }, ensure_ascii=False)


def _decide_response(rec: str):
    def _resp(prompt: str, session_id: str) -> str:
        ev = re.findall(r"evidence_[0-9a-f]+", prompt)
        item = {"claim": "关键证据已核", "status": "passed" if ev else "untested",
                "evidence": ev[0] if ev else None}
        return json.dumps({"recommendation": rec, "checklist": [item],
                           "info_gain_estimate": "high", "rationale": "按证据决策"},
                          ensure_ascii=False)
    return _resp


# ================================================================ 并行实验
class _SlowRunner:
    """假 runner：固定耗时，记录并发峰值（Phase 8 并行实验）。"""

    def __init__(self, delay: float = 0.3):
        self.delay = delay
        self.active = 0
        self.max_active = 0

    def run(self, spec, inputs, workspace, timeout_s):
        import threading
        from qresearch.experiments.manager import RunOutcome

        self.active += 1
        self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        self.active -= 1
        return RunOutcome(result={"E0": -2.0, "method": "slow"}, log_text="ok")


def _plan_object(storage, steps_json: str = PLAN_OK):
    from qresearch.core.models import Goal, PlanStep, ResearchPlan

    plan = ResearchPlan(
        project_id="proj_p8", goal_id="goal_x", version=1,
        steps=[PlanStep(**s) for s in json.loads(steps_json)["steps"]],
        risks=[], diff_summary=None,
    )
    storage.save(plan)
    return plan


def test_execute_plan_parallel(tmp_path, db):
    from qresearch.experiments.manager import ExperimentManager

    runner = _SlowRunner(delay=0.25)
    mgr = ExperimentManager(db, EventLog(tmp_path / "e.jsonl"),
                            experiments_root=tmp_path / "exp", runner=runner)
    plan = _plan_object(db, json.dumps({"steps": [
        {"action": "run_experiment", "purpose": f"p{i}", "tools": ["simple_ed"],
         "inputs": {"N": 4 + 2 * i}, "expected_outputs": ["E0"]} for i in range(4)
    ]}))
    t0 = time.monotonic()
    exps = mgr.execute_plan(plan, max_workers=4)
    elapsed = time.monotonic() - t0

    assert all(e.status == ExperimentStatus.COMPLETED for e in exps)
    assert len(exps) == 4
    assert elapsed < 0.25 * 4 * 0.8, f"并行未生效：{elapsed:.2f}s"
    assert runner.max_active > 1, "并发峰值应为 >1"
    # 落账按提交顺序（started 全部先于 finished）
    events = [e.action for e in EventLog(tmp_path / "e.jsonl").events()]
    starts = [i for i, a in enumerate(events) if a == "experiment_started"]
    finishes = [i for i, a in enumerate(events) if a == "experiment_finished"]
    assert max(starts) < min(finishes)


# ================================================================ 预算闸
def _loop_client(make_scripted_client, decide_recs: list[str]):
    from conftest import Persistent

    return make_scripted_client({
        "understand": UNDERSTAND_OK,
        "hypothesize": HYP_OK,
        "plan": [PLAN_OK] * 5,
        "critic": [Persistent(lambda p, s: CRITIC_PASS)],  # 常驻：每轮一次
        "analyze": [Persistent(_analysis_response)],
        # decide 一次性：按序消费 iterate → iterate → …（跨多次 loop 调用衔接）
        "decide": [_decide_response(r) for r in decide_recs],
    })


def test_budget_experiments_gate(tmp_path, db, make_scripted_client):
    """实验数预算：第 2 轮结束累计 4 个 ≥ 上限 3 → 第 3 轮前强制停。"""
    client = _loop_client(make_scripted_client, ["iterate", "iterate", "iterate"])
    log = EventLog(tmp_path / "e.jsonl")
    summary = run_research_loop(
        client, db, log, "proj_budget",
        "Heisenberg 链基态能量研究", rounds=5, auto_approve=True,
        budget=Budget(max_rounds=5, max_experiments=3),
    )
    assert summary["status"] == "budget_exhausted"
    assert summary["rounds_used"] == 2
    assert "实验数预算耗尽" in summary["reason"]
    actions = [e.action for e in log.events(project_id="proj_budget")]
    assert "budget_exhausted" in actions


def test_budget_wallclock_gate(tmp_path, db, make_scripted_client):
    """墙钟预算：0 分钟上限 → 第 1 轮后停。"""
    client = _loop_client(make_scripted_client, ["iterate", "iterate"])
    summary = run_research_loop(
        client, db, EventLog(tmp_path / "e.jsonl"), "proj_wc",
        "Heisenberg 链基态能量研究", rounds=5, auto_approve=True,
        budget=Budget(max_rounds=5, wallclock_min=0.0),
    )
    assert summary["status"] == "budget_exhausted"
    assert "墙钟预算耗尽" in summary["reason"]


# ================================================================ 暂停恢复
def test_pause_and_resume(tmp_path, db, make_scripted_client):
    """跑 1 轮 → 暂停（旗标）→ 移除旗标 resume 续跑：版本/决策/证据衔接。"""
    flag = tmp_path / "PAUSE"
    client = _loop_client(make_scripted_client, ["iterate", "iterate", "terminate"])
    log = EventLog(tmp_path / "e.jsonl")
    summary = run_research_loop(
        client, db, log, "proj_pause",
        "Heisenberg 链基态能量研究", rounds=1, auto_approve=True,
        pause_flag=flag,
    )
    # rounds=1 且 iterate：轮数预算闸收尾
    assert summary["status"] == "budget_exhausted"
    assert summary["rounds_used"] == 1

    # —— 暂停路径：旗标存在 → resume 在轮间优雅暂停
    flag.write_text("pause", encoding="utf-8")
    paused = resume_research_loop(
        client, db, log, "proj_pause", rounds=3, auto_approve=True, pause_flag=flag,
    )
    assert paused["status"] == "paused"
    assert flag.exists()
    actions = [e.action for e in log.events(project_id="proj_pause")]
    assert "resumed" in actions and "paused" in actions

    # —— 移除旗标 → 续跑到终止
    flag.unlink()
    final = resume_research_loop(
        client, db, log, "proj_pause", rounds=3, auto_approve=True, pause_flag=flag,
    )
    assert final["status"] == "terminated"
    assert final["rounds_used"] == 3
    from qresearch.core.models import ResearchPlan

    versions = sorted(p.version for p in db.list(ResearchPlan, project_id="proj_pause"))
    assert versions == [1, 2, 3]  # 版本跨恢复衔接
    actions = [e.action for e in log.events(project_id="proj_pause")]
    assert actions.count("resumed") == 2


# ================================================================ Slurm
def test_slurm_render_and_dry_run(tmp_path, db):
    """sbatch 脚本渲染（内容校验）+ dry-run runner；dry-run 结果过不了证据门。"""
    cfg = SlurmConfig(partition="gpu", time_min=90, mem_gb=8, cpus=4, account="qm")
    script = render_sbatch("simple_ed", Path("/w/inputs.json"), Path("/w/result.json"),
                           cfg=cfg)
    assert "#!/bin/bash" in script
    assert "#SBATCH --partition=gpu" in script
    assert "#SBATCH --time=01:30:00" in script
    assert "#SBATCH --mem=8G" in script
    assert "#SBATCH --account=qm" in script
    assert "--tool simple_ed" in script
    assert "qresearch.tools.cli run" in script

    runner = SlurmRunner(cfg, dry_run=True)
    outcome = runner.run.__self__  # noqa: F841 —— 占位避免误用
    from qresearch.experiments.manager import ExperimentManager

    mgr = ExperimentManager(db, EventLog(tmp_path / "e.jsonl"),
                            experiments_root=tmp_path / "exp", runner=runner)
    plan = _plan_object(db)
    exps = mgr.execute_plan(plan)
    assert all(e.status == ExperimentStatus.COMPLETED for e in exps)
    assert (Path(exps[0].artifacts[1])).read_text(encoding="utf-8").startswith("#!/bin/bash")

    # dry-run 结果不是真实计算：三层验证不得放行为证据
    from qresearch.verification.manager import VerificationManager

    report = VerificationManager(db, EventLog(tmp_path / "e2.jsonl")).verify_experiment(
        exps[0], "simple_ed")
    assert report.overall is not VerificationStatus.PASSED


# ================================================================ 多项目队列
def test_orchestrator_two_projects(tmp_path, make_scripted_client, db):
    """队列串行跑两个项目：目录隔离、沙箱就位、记忆共享滚动入库。"""
    from qresearch.memory import MemoryStore, MemoryLayer

    store = MemoryStore(tmp_path / "memory.sqlite", markdown_path=tmp_path / "memory.md")
    clients: list = []

    def factory(sandbox: Path):
        from conftest import Persistent

        client = make_scripted_client({
            "understand": UNDERSTAND_OK,
            "hypothesize": HYP_OK,
            "plan": [PLAN_OK],
            "critic": [Persistent(lambda p, s: CRITIC_PASS)],
            "analyze": [_analysis_response],
            "decide": [_decide_response("declare_result")],
        })
        clients.append((sandbox, client))
        return client

    results = run_projects(
        factory,
        [ProjectJob(project_id="proj_a", question="Heisenberg 链基态能量研究", rounds=1),
         ProjectJob(project_id="proj_b", question="Heisenberg 链基态能量复核", rounds=1)],
        data_root=tmp_path / "projects", memory_store=store, auto_approve=True,
    )
    assert results["proj_a"]["status"] == "terminated"
    assert results["proj_b"]["status"] == "terminated"
    # 目录隔离 + 沙箱就位
    for pid in ("proj_a", "proj_b"):
        assert (tmp_path / "projects" / pid / "state.sqlite").exists()
        assert (tmp_path / "projects" / pid / "sandbox").is_dir()
        assert (tmp_path / "projects" / pid / "report.md").exists()
    # 沙箱作为 client cwd（DSH 站内 agent 写入隔离的收口）
    assert all(s.name == "sandbox" for s, _ in clients)
    # 记忆共享：两个项目的经验都在库
    projects = {e.source_project for e in store.list(MemoryLayer.project)}
    assert {"proj_a", "proj_b"} <= projects
    store.close()
