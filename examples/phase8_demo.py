"""Phase 8 验收演示：并行实验 / 预算闸 / 暂停恢复 / Slurm dry-run / 多项目队列。

流程（计划 v2 Phase 8，全部离线脚本模型，秒级）：
1. 并行实验：4 个实验 max_workers=4，计时对比串行 + 落账顺序（started 全部
   先于 finished——记账只在主线程）；
2. 预算闸：实验数上限 2，第 1 轮跑满后第 2 轮前被代码层硬闸拦截；
3. 暂停恢复：跑 1 轮 → 写 PAUSE 旗标 → resume 优雅暂停 → 移除旗标 →
   resume 续跑到 terminated（计划版本/决策/证据跨恢复衔接）；
4. Slurm 后端：sbatch 脚本渲染（内容校验）+ dry-run runner——dry-run
   结果不是真实计算，三层验证不得放行为证据（诚实边界）；
5. 多项目队列：两个项目串行执行，目录/沙箱隔离，跨项目记忆共享滚动入库。

用法：
  .venv/Scripts/python.exe -X utf8 examples/phase8_demo.py           # 全离线
  .venv/Scripts/python.exe -X utf8 examples/phase8_demo.py --live    # 第 5 段接真实 LLM
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qresearch.core.budget import Budget
from qresearch.core.events import EventLog
from qresearch.core.models import Decision, PlanStep, ResearchPlan
from qresearch.core.status import ExperimentStatus, VerificationStatus
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient
from qresearch.experiments.manager import ExperimentManager, RunOutcome
from qresearch.hpc import SlurmConfig, SlurmRunner, render_sbatch
from qresearch.memory import MemoryLayer, MemoryStore
from qresearch.orchestrator import ProjectJob, run_projects
from qresearch.research_loop import resume_research_loop, run_research_loop
from qresearch.verification.manager import VerificationManager

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research_data" / "demo_phase8"

UNDERSTAND = (
    '{"refined_question": "一维自旋 1/2 Heisenberg 链基态能量（PBC，J=1）",'
    ' "quantities": ["e0(N)"],'
    ' "success_criteria": ["与 Bethe ansatz e0=1/4-ln2 对照"]}'
)
HYP_OK = (
    '{"hypotheses": ['
    '{"statement": "小尺寸 ED 外推与 Bethe ansatz 一致", "rationale": "解析对照",'
    ' "falsification_tests": ["偏差超 1e-3"], "discriminating_experiment": "ED"},'
    '{"statement": "有限尺寸效应随 N 单调减小", "rationale": "文献",'
    ' "falsification_tests": ["相邻尺寸差超 1%"], "discriminating_experiment": "扫描"}]}'
)
PLAN_OK = (
    '{"steps": ['
    '{"action": "run_experiment", "purpose": "N=4 基准", "tools": ["simple_ed"],'
    ' "inputs": {"N": 4}, "expected_outputs": ["E0"]},'
    '{"action": "run_experiment", "purpose": "N=6 对照", "tools": ["simple_ed"],'
    ' "inputs": {"N": 6}, "expected_outputs": ["E0"]}],'
    ' "risks": [], "diff_summary": null}'
)
CRITIC_PASS = '{"verdict": "pass", "issues": []}'


def _analysis_response(prompt: str, session_id: str) -> str:
    exp_ids = re.findall(r"\[(exp_[0-9a-f]+)\]", prompt)
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


def _make_client(responses: dict) -> DSHClient:
    """离线脚本客户端：str / callable 均一次性按序消费，耗尽即报错。"""
    queues = {k: (v if isinstance(v, list) else [v]) for k, v in responses.items()}

    def runner(prompt: str, session_id: str) -> str:
        station = session_id.split(":")[1]
        q = queues[station]
        if not q:
            raise AssertionError(f"站点 {station} 应答队列耗尽（演示编排少写一轮）")
        item = q.pop(0)
        if callable(item):
            return item(prompt, session_id)
        return item

    return DSHClient(runner=runner)


def _loop_client(decide_recs: list[str]) -> DSHClient:
    return _make_client({
        "understand": UNDERSTAND, "hypothesize": HYP_OK,
        "plan": [PLAN_OK] * 5, "critic": [CRITIC_PASS] * 5,
        "analyze": [_analysis_response] * 5,
        "decide": [_decide_response(r) for r in decide_recs],
    })


def _plan(storage, project_id: str, steps: list[dict], version: int = 1) -> ResearchPlan:
    plan = ResearchPlan(
        project_id=project_id, goal_id="goal_demo", version=version,
        steps=[PlanStep(**s) for s in steps], risks=[], diff_summary=None,
    )
    storage.save(plan)
    return plan


# ================================================================ 1. 并行实验
class _SlowRunner:
    """固定耗时的假 runner：记录并发峰值。"""

    def __init__(self, delay: float):
        self.delay = delay
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def run(self, spec, inputs, workspace, timeout_s) -> RunOutcome:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        with self._lock:
            self.active -= 1
        return RunOutcome(result={"E0": -2.0, "method": "slow"}, log_text="ok")


def demo_parallel() -> None:
    print("== 1. 并行实验：4 实验 max_workers=4 ==")
    storage = Storage(DATA / "parallel" / "state.sqlite")
    log = EventLog(DATA / "parallel" / "events.jsonl")
    runner = _SlowRunner(delay=0.25)
    mgr = ExperimentManager(storage, log, experiments_root=DATA / "parallel" / "exp",
                            runner=runner)
    steps = [{"action": "run_experiment", "purpose": f"p{i}", "tools": ["simple_ed"],
              "inputs": {"N": 4 + 2 * i}, "expected_outputs": ["E0"]} for i in range(4)]
    plan = _plan(storage, "proj_parallel", steps)
    t0 = time.monotonic()
    exps = mgr.execute_plan(plan, max_workers=4)
    elapsed = time.monotonic() - t0
    assert all(e.status == ExperimentStatus.COMPLETED for e in exps)
    assert elapsed < 0.25 * 4 * 0.8, f"并行未生效：{elapsed:.2f}s"
    assert runner.max_active > 1, "并发峰值应为 >1"
    events = [e.action for e in log.events(project_id="proj_parallel")]
    starts = [i for i, a in enumerate(events) if a == "experiment_started"]
    finishes = [i for i, a in enumerate(events) if a == "experiment_finished"]
    assert max(starts) < min(finishes), "落账应按提交顺序（记账只在主线程）"
    print(f"  4×0.25s 实验 {elapsed:.2f}s 完成（串行需 ~1.0s），"
          f"并发峰值 {runner.max_active}，落账顺序 ✔")


# ================================================================ 2. 预算闸
def demo_budget() -> None:
    print("== 2. 预算闸：实验数上限 2（每轮 2 个实验） ==")
    storage = Storage(DATA / "budget" / "state.sqlite")
    log = EventLog(DATA / "budget" / "events.jsonl")
    client = _loop_client(["iterate", "iterate", "iterate"])
    summary = run_research_loop(
        client, storage, log, "proj_budget", "Heisenberg 链基态能量研究",
        rounds=5, auto_approve=True, budget=Budget(max_rounds=5, max_experiments=2),
    )
    assert summary["status"] == "budget_exhausted", summary
    assert "实验数预算耗尽" in summary["reason"]
    assert summary["rounds_used"] == 1, "第 2 轮前应被拦停"
    actions = [e.action for e in log.events(project_id="proj_budget")]
    assert "budget_exhausted" in actions
    print(f"  status={summary['status']}，{summary['reason']}，rounds_used=1 ✔")


# ================================================================ 3. 暂停恢复
def demo_pause_resume() -> None:
    print("== 3. 暂停恢复：1 轮 → PAUSE → resume 暂停 → 移除旗标 → resume 终止 ==")
    pdir = DATA / "pause_resume"
    storage = Storage(pdir / "state.sqlite")
    log = EventLog(pdir / "events.jsonl")
    flag = pdir / "PAUSE"
    # decide 按序：run1=iterate，resume#2 的 R2=iterate、R3=terminate
    client = _loop_client(["iterate", "iterate", "terminate"])
    s1 = run_research_loop(client, storage, log, "proj_pause",
                           "Heisenberg 链基态能量研究", rounds=1,
                           auto_approve=True, pause_flag=flag)
    assert s1["status"] == "budget_exhausted" and s1["rounds_used"] == 1

    flag.write_text("pause", encoding="utf-8")
    s2 = resume_research_loop(client, storage, log, "proj_pause", rounds=3,
                              auto_approve=True, pause_flag=flag)
    assert s2["status"] == "paused" and flag.exists(), s2

    flag.unlink()
    s3 = resume_research_loop(client, storage, log, "proj_pause", rounds=3,
                              auto_approve=True, pause_flag=flag)
    assert s3["status"] == "terminated", s3
    assert s3["rounds_used"] == 3
    versions = sorted(p.version for p in storage.list(ResearchPlan,
                                                      project_id="proj_pause"))
    assert versions == [1, 2, 3], f"计划版本应跨恢复衔接：{versions}"
    actions = [e.action for e in log.events(project_id="proj_pause")]
    assert actions.count("resumed") == 2
    print(f"  版本 {versions}，决策 3 条，resumed×2，最终 status={s3['status']} ✔")


# ================================================================ 4. Slurm
def demo_slurm() -> None:
    print("== 4. Slurm 后端：sbatch 渲染 + dry-run（证据门拒绝） ==")
    cfg = SlurmConfig(partition="gpu", time_min=90, mem_gb=8, cpus=4, account="qm")
    script = render_sbatch("simple_ed", Path("/w/inputs.json"), Path("/w/result.json"),
                           cfg=cfg)
    for expect in ("#!/bin/bash", "#SBATCH --partition=gpu", "#SBATCH --time=01:30:00",
                   "#SBATCH --mem=8G", "#SBATCH --account=qm",
                   "--tool simple_ed", "qresearch.tools.cli run"):
        assert expect in script, f"sbatch 脚本缺少 {expect!r}"

    storage = Storage(DATA / "slurm" / "state.sqlite")
    log = EventLog(DATA / "slurm" / "events.jsonl")
    runner = SlurmRunner(cfg, dry_run=True)
    mgr = ExperimentManager(storage, log, experiments_root=DATA / "slurm" / "exp",
                            runner=runner)
    steps = [{"action": "run_experiment", "purpose": "集群单点", "tools": ["simple_ed"],
              "inputs": {"N": 8}, "expected_outputs": ["E0"]}]
    plan = _plan(storage, "proj_slurm", steps)
    exps = mgr.execute_plan(plan)
    assert all(e.status == ExperimentStatus.COMPLETED for e in exps)
    result = json.loads(Path(exps[0].artifacts[0]).read_text(encoding="utf-8"))
    assert result.get("dry_run") is True, "dry-run 结果应带 dry_run 标记"
    assert (Path(exps[0].artifacts[1])).read_text(encoding="utf-8").startswith(
        "#!/bin/bash")
    report = VerificationManager(storage, EventLog(DATA / "slurm" / "e2.jsonl")
                                 ).verify_experiment(exps[0], "simple_ed")
    assert report.overall is not VerificationStatus.PASSED, \
        "dry-run 结果不是真实计算，不得放行为证据"
    print("  sbatch 字段齐备；dry-run 产出脚本；验证 overall≠PASSED（证据门拒绝）✔")


# ================================================================ 5. 多项目队列
def demo_orchestrator(live: bool = False) -> None:
    mode = "真实 LLM（每项目 2 轮，分钟级）" if live else "离线脚本（秒级）"
    print(f"== 5. 多项目队列：两项目串行 + 沙箱隔离 + 记忆共享（{mode}） ==")
    store = MemoryStore(DATA / "memory.sqlite", markdown_path=DATA / "memory.md")

    if live:
        # 真实 runtime：每项目以 cwd=沙箱 独立启动（编排器接线点）；
        # retries=2 沿用 P5 live 教训（计划校验反馈重试上限）
        def factory(sandbox: Path) -> DSHClient:
            return DSHClient(cwd=sandbox, dsh_home=DATA / "dsh_home")
    else:
        def factory(sandbox: Path) -> DSHClient:
            return _make_client({
                "understand": UNDERSTAND, "hypothesize": HYP_OK,
                "plan": PLAN_OK, "critic": CRITIC_PASS,
                "analyze": [_analysis_response],
                "decide": [_decide_response("declare_result")],
            })

    rounds = 2 if live else 1
    results = run_projects(
        factory,
        [ProjectJob(project_id="proj_a", question="一维自旋 1/2 Heisenberg 链"
                    "基态能量与 Bethe ansatz 对照研究", rounds=rounds,
                    n_hypotheses=2),
         ProjectJob(project_id="proj_b", question="横场 Ising 模型临界区基态"
                    "能量密度研究", rounds=rounds, n_hypotheses=2)],
        data_root=DATA / "projects", memory_store=store, auto_approve=True,
        retries=2,
    )
    for pid in ("proj_a", "proj_b"):
        status = results[pid].get("status") or results[pid].get("needs_human", "")
        print(f"  {pid}: status={status}")
        # live 模式下模型可能 iterate 到轮上限（needs_human/budget_exhausted，
        # 均为合法收尾）；队列接线/隔离/记忆断言与状态无关，始终成立
        assert (DATA / "projects" / pid / "state.sqlite").exists()
        assert (DATA / "projects" / pid / "sandbox").is_dir()
        assert (DATA / "projects" / pid / "report.md").exists()
    if not live:
        assert all(r["status"] == "terminated" for r in results.values())
    projects = {e.source_project for e in store.list(MemoryLayer.project)}
    assert {"proj_a", "proj_b"} <= projects, "两个项目的经验都应入库（跨项目共享）"
    print(f"  目录+沙箱隔离 ✔；记忆库项目层含 {sorted(projects)} ✔")
    store.close()


def main() -> int:
    live = "--live" in sys.argv
    shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir(parents=True, exist_ok=True)
    demo_parallel()
    demo_budget()
    demo_pause_resume()
    demo_slurm()
    demo_orchestrator(live=live)
    print("\nPhase 8 验收演示：全部 PASS" + ("（live）" if live else ""))
    print("  （Slurm 诚实边界：本机无集群，dry_run 只生成/校验脚本；"
          "真实提交路径需集群环境验收）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
