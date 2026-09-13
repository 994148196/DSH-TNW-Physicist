"""Phase 4 验收演示：三层验证 + VerificationReport + 证据资格门。

验收条文：注入一个故意写错的工具能被抓住并出具报告。
流程：
1. 正常工具跑实验 → 三层验证 → 报告 PASSED → 证据门放行；
2. 同名注入偏移 -0.01J 的错误实现 → 再跑实验 → 验证抓住 → 报告 FAILED → 证据门关闭。

用法：.venv/Scripts/python.exe -X utf8 examples/phase4_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qresearch.core.events import Event, EventLog
from qresearch.core.models import Experiment, PlanStep, Project, ResearchPlan
from qresearch.core.status import Actor, VerificationStatus
from qresearch.core.storage import Storage
from qresearch.experiments.manager import ExperimentManager
from qresearch.tools import registry
from qresearch.tools.registry import ToolSpec
from qresearch.tools.simple_ed import SimpleEDInputs
from qresearch.verification.manager import VerificationManager

import shutil

DATA = Path(__file__).resolve().parents[1] / "research_data" / "demo_phase4"


def print_report(report, gate_ok: bool, gate_msg: str) -> None:
    print(f"\n报告 {report.report_id}（实验 {report.experiment_id[:16]}…）：overall = {report.overall.value}")
    for item in report.items:
        mark = {"passed": "✅", "failed": "❌", "uncertain": "⚠️ ", "not_run": "➖"}[item.status.value]
        print(f"  {mark} [{item.layer:9s}] {item.claim}：{item.detail[:110]}")
    print(f"  证据资格门：{'放行' if gate_ok else '关闭'}（{gate_msg}）")


def main() -> int:
    shutil.rmtree(DATA, ignore_errors=True)  # 演示每次全新
    DATA.mkdir(parents=True, exist_ok=True)
    storage = Storage(DATA / "state.sqlite")
    log = EventLog(DATA / "events.jsonl")
    verifier = VerificationManager(storage, log)

    project = Project(project_id="proj_phase4_demo", title="验证层验收",
                      question="验证三层协议与证据资格门")
    storage.save(project)
    log.append(Event(actor=Actor.SYSTEM, action="create_project",
                     project_id=project.project_id, object_type="Project",
                     object_id=project.project_id, detail={}))
    plan = ResearchPlan(
        project_id=project.project_id, goal_id="g1", version=1,
        steps=[
            PlanStep(step_id="step_1", action="run_experiment", purpose="N=4",
                     tools=["simple_ed"], inputs={"N": 4, "want_gap": True}),
            PlanStep(step_id="step_2", action="run_experiment", purpose="N=8",
                     tools=["simple_ed"], inputs={"N": 8}),
            PlanStep(step_id="step_3_sabotaged", action="run_experiment", purpose="注入错误实现",
                     tools=["simple_ed"], inputs={"N": 6}),
        ],
    )
    storage.save(plan)
    manager = ExperimentManager(storage, log, experiments_root=DATA / "experiments")

    # ---- 1. 正常工具：实验 → 验证 → 门放行
    good_reports = []
    for step in plan.steps[:2]:
        exp = manager.execute_step(plan, step)
        report = verifier.verify_experiment(exp, "simple_ed")
        ok, msg = verifier.evidence_gate(report)
        print_report(report, ok, msg)
        good_reports.append((report, ok))

    # ---- 2. 注入故意写错的实现（同名替换，模拟编码 agent 改错代码）
    original = registry._REGISTRY["simple_ed"]

    def broken(inputs: dict) -> dict:
        result = dict(original.run(inputs))
        result["E0"] -= 0.01  # 系统性偏移：比符号错更隐蔽
        result["e0"] -= 0.01
        return result

    registry._REGISTRY["simple_ed"] = ToolSpec(
        name="simple_ed", version="1.0.0-broken", type="exact_diagonalization",
        description="注入 -0.01J 偏移的错误实现", input_model=SimpleEDInputs, run=broken,
    )
    try:
        exp_bad = manager.execute_step(plan, plan.steps[2])
        report_bad = verifier.verify_experiment(exp_bad, "simple_ed")
        ok_bad, msg_bad = verifier.evidence_gate(report_bad)
        print_report(report_bad, ok_bad, msg_bad)
    finally:
        registry._REGISTRY["simple_ed"] = original  # 还原，避免污染后续运行

    caught = (
        report_bad.overall == VerificationStatus.FAILED
        and not ok_bad
        and any(i.layer == "physics" and i.status == VerificationStatus.FAILED
                for i in report_bad.items)
        and any(i.layer == "software" and i.status == VerificationStatus.FAILED
                for i in report_bad.items)
    )
    all_good = all(r.overall == VerificationStatus.PASSED and ok for r, ok in good_reports)

    print(f"\n故意写错的工具：{'被 golden 基准抓住并出具报告' if caught else '未被抓住！！'}")
    print(f"事件日志：{DATA / 'events.jsonl'}（{len(log.events(project_id=project.project_id))} 条）")
    print(f"\nPHASE 4 DEMO: {'PASS' if (caught and all_good) else 'FAIL'}")
    return 0 if (caught and all_good) else 1


if __name__ == "__main__":
    raise SystemExit(main())
