"""状态机枚举：所有科研对象的生命周期状态（计划 v2 §5.1）。"""
from enum import StrEnum


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class HypothesisStatus(StrEnum):
    PROPOSED = "proposed"
    UNDER_TEST = "under_test"
    SUPPORTED = "supported"
    WEAKENED = "weakened"
    REJECTED = "rejected"
    ACCEPTED_PROVISIONALLY = "accepted_provisionally"


class PlanStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class StepStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExperimentStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VerificationStatus(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class EvidenceType(StrEnum):
    LITERATURE = "literature"
    NUMERICAL = "numerical"
    ANALYTIC = "analytic"
    SOFTWARE_TEST = "software_test"
    CONVERGENCE = "convergence"
    CONTROL_EXPERIMENT = "control_experiment"
    COUNTEREXAMPLE = "counterexample"
    CONSISTENCY_CHECK = "consistency_check"


class DecisionType(StrEnum):
    ITERATE_OR_TERMINATE = "iterate_or_terminate"
    REPLAN = "replan"
    NEW_PROPOSAL = "new_proposal"
    PROPOSAL_REVIEW = "proposal_review"
    DECLARE_RESULT = "declare_result"


class DecisionRecommendation(StrEnum):
    ITERATE = "iterate"
    TERMINATE = "terminate"
    REPLAN = "replan"
    PROPOSE = "propose"
    ACCEPT = "accept"
    ESCALATE = "escalate"


class Actor(StrEnum):
    MODEL = "model"
    HUMAN = "human"
    RULE = "rule"
    SYSTEM = "system"
