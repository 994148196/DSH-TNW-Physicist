"""站点 LLM 输出契约（Pydantic schema）。

这些 schema 是"状态层对象"的草案形态：执行器校验后转换为 storage 模型落账。
铁律在 schema 层同样生效（如 HypothesisDraft.falsification_tests 非空）。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class UnderstandOutput(BaseModel):
    """UNDERSTAND：把自然语言科研问题翻译成结构化研究目标。"""

    refined_question: str
    quantities: list[str] = Field(description="目标物理量", min_length=1)
    success_criteria: list[str] = Field(min_length=1)
    constraints: dict = Field(default_factory=dict)
    assumptions_made: list[str] = Field(
        default_factory=list, description="识别出的默认假设与不确定性"
    )


class HypothesisDraft(BaseModel):
    statement: str
    rationale: str
    falsification_tests: list[str] = Field(min_length=1)
    discriminating_experiment: str


class HypothesizeOutput(BaseModel):
    hypotheses: list[HypothesisDraft] = Field(min_length=2)


class PlanStepDraft(BaseModel):
    action: str
    purpose: str
    tools: list[str] = Field(default_factory=list)
    inputs: dict = Field(default_factory=dict)
    expected_outputs: list[str] = Field(default_factory=list)
    requires_approval: bool = False


class PlanOutput(BaseModel):
    steps: list[PlanStepDraft] = Field(min_length=1)
    risks: list[str] = Field(default_factory=list)
    diff_summary: str | None = None


class CriticIssue(BaseModel):
    step_id: str | None = None
    severity: Literal["blocker", "concern", "minor"]
    description: str


class CritiqueOutput(BaseModel):
    verdict: Literal["pass", "revise"]
    issues: list[CriticIssue] = Field(default_factory=list)
