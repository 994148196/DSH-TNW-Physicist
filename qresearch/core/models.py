"""核心科研对象（Pydantic v2，计划 v2 §5）。

内建的规则（铁律的模型层体现）：
- 每条假设必须自带证伪试验（falsification_tests 非空）；
- Decision checklist 中 status=passed 的项必须引用 Evidence；
- declare_result 决策无条件需要人工（requires_human 强制为 True）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from .status import (
    Actor,
    DecisionRecommendation,
    DecisionType,
    EvidenceType,
    ExperimentStatus,
    HypothesisStatus,
    PlanStatus,
    ProjectStatus,
    StepStatus,
    VerificationStatus,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


class Project(BaseModel):
    project_id: str = Field(default_factory=lambda: _new_id("proj"))
    title: str
    domain: str = "quantum_many_body"
    question: str
    status: ProjectStatus = ProjectStatus.ACTIVE
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Goal(BaseModel):
    goal_id: str = Field(default_factory=lambda: _new_id("goal"))
    project_id: str
    question: str
    success_criteria: list[str] = Field(min_length=1)
    constraints: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Hypothesis(BaseModel):
    hypothesis_id: str = Field(default_factory=lambda: _new_id("hyp"))
    project_id: str
    statement: str
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    confidence: float | None = None
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)
    # 铁律：无证伪方式的假设不入库
    falsification_tests: list[str] = Field(min_length=1)
    source: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PlanStep(BaseModel):
    step_id: str = Field(default_factory=lambda: _new_id("step"))
    action: str
    purpose: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    tools: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    success_criteria: str | None = None
    risks: list[str] = Field(default_factory=list)
    cost_estimate: str | None = None
    requires_approval: bool = False
    status: StepStatus = StepStatus.PENDING


class ResearchPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: _new_id("plan"))
    project_id: str
    goal_id: str
    version: int = 1
    status: PlanStatus = PlanStatus.AWAITING_APPROVAL
    steps: list[PlanStep] = Field(min_length=1)
    based_on_decision: str | None = None
    diff_summary: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Experiment(BaseModel):
    experiment_id: str = Field(default_factory=lambda: _new_id("exp"))
    project_id: str
    plan_id: str
    step_id: str
    tool_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: ExperimentStatus = ExperimentStatus.CREATED
    seed: int | None = None
    code_version: str | None = None
    environment: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    log_path: str | None = None
    verification_status: VerificationStatus = VerificationStatus.NOT_RUN
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Evidence(BaseModel):
    evidence_id: str = Field(default_factory=lambda: _new_id("evidence"))
    project_id: str
    type: EvidenceType
    claim: str
    source_experiment: str | None = None
    confidence: float | None = None
    artifacts: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class CheckItem(BaseModel):
    """Decision checklist 的一项判断。"""

    claim: str
    status: Literal["passed", "failed", "untested"]
    evidence: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _passed_requires_evidence(self) -> "CheckItem":
        if self.status == "passed" and not self.evidence:
            raise ValueError("checklist 项 status=passed 必须引用 evidence")
        return self


class Decision(BaseModel):
    decision_id: str = Field(default_factory=lambda: _new_id("dec"))
    project_id: str
    type: DecisionType
    recommendation: DecisionRecommendation
    checklist: list[CheckItem] = Field(default_factory=list)
    info_gain_estimate: Literal["low", "moderate", "high"] | None = None
    alternatives_considered: list[str] = Field(default_factory=list)
    rationale: str | None = None
    made_by: Actor = Actor.MODEL
    requires_human: bool = True
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _declare_result_requires_human(self) -> "Decision":
        if self.type is DecisionType.DECLARE_RESULT and not self.requires_human:
            raise ValueError("declare_result 决策无条件需要人工（计划 v2 §6.4）")
        return self


class ToolRecord(BaseModel):
    """注册工具的描述。project_id 默认 'global'：工具注册表跨项目共享。"""

    tool_id: str = Field(default_factory=lambda: _new_id("tool"))
    project_id: str = "global"
    name: str
    version: str
    type: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    applicable_domains: list[str] = Field(default_factory=list)
    benchmark_refs: list[str] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class VerificationReportItem(BaseModel):
    claim: str
    layer: Literal["software", "numerical", "physics"]
    status: VerificationStatus
    detail: str | None = None
    artifacts: list[str] = Field(default_factory=list)


class VerificationReport(BaseModel):
    report_id: str = Field(default_factory=lambda: _new_id("vr"))
    project_id: str
    experiment_id: str
    items: list[VerificationReportItem] = Field(min_length=1)
    overall: VerificationStatus
    created_at: datetime = Field(default_factory=utcnow)


class ProjectBundle(BaseModel):
    """一个科研项目的完整状态聚合（导入导出与恢复的单元）。"""

    project: Project
    goals: list[Goal] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    plans: list[ResearchPlan] = Field(default_factory=list)
    experiments: list[Experiment] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    tools: list[ToolRecord] = Field(default_factory=list)
    verification_reports: list[VerificationReport] = Field(default_factory=list)
