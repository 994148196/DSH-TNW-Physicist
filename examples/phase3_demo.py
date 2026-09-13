"""Phase 3 验收演示：批准的计划 → 自动建实验 → 批量跑 → 落账 → 实验摘要。

不调 LLM（计划生成与审批是 Phase 2 的职责，这里从一份已批准计划开始），
实验经子进程执行（崩溃隔离、不经 LLM），产物落盘 research_data/demo_phase3/。

用法：.venv/Scripts/python.exe -X utf8 examples/phase3_demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qresearch.core.events import Event, EventLog
from qresearch.core.models import Goal, PlanStep, Project, ResearchPlan
from qresearch.core.status import Actor, PlanStatus
from qresearch.core.storage import Storage
from qresearch.experiments.manager import ExperimentManager
from qresearch.verification import golden

DATA = Path(__file__).resolve().parents[1] / "research_data" / "demo_phase3"


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    storage = Storage(DATA / "state.sqlite")
    log = EventLog(DATA / "events.jsonl")

    project = Project(
        project_id="proj_phase3_demo",
        title="Heisenberg 链基准研究",
        question="一维自旋 1/2 反铁磁 Heisenberg 链基态能量密度与 Bethe ansatz 对照",
    )
    storage.save(project)
    log.append(Event(actor=Actor.SYSTEM, action="create_project",
                     project_id=project.project_id, object_type="Project",
                     object_id=project.project_id, detail={}))
    goal = Goal(
        project_id=project.project_id,
        question="一维自旋 1/2 反铁磁 Heisenberg 链基态能量密度与 Bethe ansatz 对照",
        quantities=["e0(N)"], success_criteria=["e0 与 1/4−ln2 对照"],
    )
    storage.save(goal)

    # 已批准计划（LLM 生成/审批在 Phase 2 演示过，这里直接以 APPROVED 起步）
    plan = ResearchPlan(
        project_id=project.project_id, goal_id=goal.goal_id, version=1,
        status=PlanStatus.APPROVED,
        steps=[
            PlanStep(step_id="step_1", action="run_experiment", purpose="N=4 基准锚点（解析值 -2J）",
                     tools=["simple_ed"], inputs={"N": 4, "want_gap": True, "want_corr": True}),
            PlanStep(step_id="step_2", action="parameter_scan", purpose="有限尺寸扫描 N=6..12",
                     tools=["simple_ed"], inputs={"J": 1.0, "scan": {"N": [6, 8, 10, 12]}}),
            PlanStep(step_id="step_3", action="run_experiment", purpose="quimb 独立实现交叉验证 L=6",
                     tools=["dmrg_adapter"], inputs={"L": 6}),
            PlanStep(step_id="step_4", action="compare_benchmark", purpose="与 golden 基准对照（Phase 4）",
                     tools=[]),
        ],
    )
    storage.save(plan)
    print(f"计划 {plan.plan_id}（{len(plan.steps)} 步，状态 {plan.status.value}）")

    manager = ExperimentManager(storage, log, experiments_root=DATA / "experiments")
    experiments = manager.execute_plan(plan)

    print("\n== 实验摘要 ==")
    print(manager.summarize(experiments))

    print("\n== golden 基准 ==")
    golden_failed = golden.main() != 0

    events = log.events(project_id=project.project_id)
    print(f"\n事件日志：{DATA / 'events.jsonl'}（本项目 {len(events)} 条）")
    print(f"状态库  ：{DATA / 'state.sqlite'}")
    print(f"实验产物：{DATA / 'experiments'}/<exp_id>/result.json")

    ok = (
        all(e.status.value == "completed" for e in experiments)
        and len(experiments) == 6  # 1 基准 + 4 扫描点 + 1 交叉验证（step_4 非实验动作被跳过）
        and not golden_failed
    )
    print(f"\nPHASE 3 DEMO: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
