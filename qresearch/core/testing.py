"""构造一份结构完整、引用自洽的示例项目状态（演示与测试共用）。

`id_suffix` 用于在多项目测试中生成互不冲突的对象 ID（所有固定 ID 统一加后缀）。
"""
from __future__ import annotations

from .models import (
    CheckItem,
    Decision,
    Evidence,
    Experiment,
    Goal,
    Hypothesis,
    PlanStep,
    Project,
    ProjectBundle,
    ResearchPlan,
    ToolRecord,
    VerificationReport,
    VerificationReportItem,
)
from .status import (
    Actor,
    DecisionRecommendation,
    DecisionType,
    EvidenceType,
    ExperimentStatus,
    VerificationStatus,
)


def make_demo_bundle(project_id: str = "proj_heisenberg_demo", id_suffix: str = "") -> ProjectBundle:
    """一维 Heisenberg 示例：含全部对象类型，引用自洽。"""
    s = id_suffix
    project = Project(
        project_id=project_id,
        title="1D Heisenberg 基态性质研究",
        question="计算一维自旋 1/2 Heisenberg 模型的基态能量与关联函数，并做收敛性分析。",
    )
    goal = Goal(
        goal_id=f"goal_001{s}",
        project_id=project_id,
        question="基态能量密度收敛到多少？有限尺寸效应多大？",
        success_criteria=[
            "E0/L 在系统尺寸上收敛并给出误差估计",
            "与 Bethe ansatz 解析值 1/4 - ln2 对照",
            "至少两个系统尺寸比较",
        ],
        constraints={"max_compute_hours": 1},
    )
    tool = ToolRecord(
        tool_id=f"tool_simple_ed{s}",
        project_id="global",
        name="simple_ed",
        version="0.1.0",
        type="numerical_solver",
        description="scipy 稀疏 Lanczos 精确对角化（L<=16）",
        applicable_domains=["quantum_many_body", "1d_spin_chain"],
        benchmark_refs=["Bethe ansatz: E/L = 1/4 - ln2", "L=4-16 golden fixtures"],
        known_limitations=["只适用于小系统", "无对称性投影时内存 O(4^L)"],
    )
    hypothesis = Hypothesis(
        hypothesis_id=f"hyp_001{s}",
        project_id=project_id,
        statement="有限尺寸外推后 E0/L 与 Bethe ansatz 值一致到 1e-3",
        falsification_tests=[
            "L=8,12 的 ED 结果与 1/4 - ln2 偏差超过容差",
        ],
        supporting_evidence=[f"evidence_001{s}"],
    )
    plan = ResearchPlan(
        plan_id=f"plan_001{s}",
        project_id=project_id,
        goal_id=f"goal_001{s}",
        version=1,
        steps=[
            PlanStep(
                step_id=f"step_1{s}",
                action="run_experiment",
                purpose="小系统基准（L=8）",
                tools=[f"tool_simple_ed{s}"],
                inputs={"L": 8},
            ),
            PlanStep(
                step_id=f"step_2{s}",
                action="parameter_scan",
                purpose="尺寸扫描（L=10,12）",
                tools=[f"tool_simple_ed{s}"],
                inputs={"L": [10, 12]},
            ),
            PlanStep(
                step_id=f"step_3{s}",
                action="compare_benchmark",
                purpose="与 Bethe ansatz 对照",
            ),
        ],
    )
    experiment = Experiment(
        experiment_id=f"exp_001{s}",
        project_id=project_id,
        plan_id=f"plan_001{s}",
        step_id=f"step_1{s}",
        tool_id=f"tool_simple_ed{s}",
        parameters={"L": 8, "model": "heisenberg_1d"},
        status=ExperimentStatus.COMPLETED,
        seed=42,
        code_version="demo",
        artifacts=["research_data/experiments/exp_001/energy.csv"],
    )
    evidence = Evidence(
        evidence_id=f"evidence_001{s}",
        project_id=project_id,
        type=EvidenceType.NUMERICAL,
        claim="L=8 的 ED 基态能量密度与 golden fixture 一致到 1e-12",
        source_experiment=f"exp_001{s}",
        confidence=0.99,
    )
    decision = Decision(
        decision_id=f"dec_001{s}",
        project_id=project_id,
        type=DecisionType.ITERATE_OR_TERMINATE,
        recommendation=DecisionRecommendation.ITERATE,
        checklist=[
            CheckItem(claim="E0/L 已收敛", status="passed", evidence=f"evidence_001{s}"),
            CheckItem(claim="hyp_001 已被检验", status="untested", reason="还需 L=10,12"),
        ],
        info_gain_estimate="high",
        rationale="小系统基准通过，尺寸扫描信息增益高，继续迭代。",
        made_by=Actor.MODEL,
        requires_human=True,
    )
    report = VerificationReport(
        report_id=f"vr_001{s}",
        project_id=project_id,
        experiment_id=f"exp_001{s}",
        overall=VerificationStatus.PASSED,
        items=[
            VerificationReportItem(
                claim="与 scipy eigsh 独立实现对拍",
                layer="numerical",
                status=VerificationStatus.PASSED,
                detail="max|ΔE| = 1e-12",
            )
        ],
    )
    return ProjectBundle(
        project=project,
        goals=[goal],
        hypotheses=[hypothesis],
        plans=[plan],
        experiments=[experiment],
        evidence=[evidence],
        decisions=[decision],
        tools=[tool],
        verification_reports=[report],
    )
