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


class ApprovalChannel(StrEnum):
    """人工审批事件（approve/reject/conclude）的取得通道——台账如实记账用。

    保真度分级（读报告的人据此折价，而不是靠约定俗成）：

    - ``TTY``：审批进程持有控制终端。控制终端是**进程能力**而非秘密——agent 经
      DSH 起的子进程结构性拿不到（``sys.stdin.isatty()`` 为假），故保真度最高。
    - ``WEBUI_LOCAL``：同机 localhost 浏览器点击。凭据（cookie/CSRF token）落在
      agent 同用户可读的信任域内，本地进程理论上可自行完成同一请求；因此这是
      **较低保真度**通道，台账必须如实记录，不得与 TTY 等同视之。
    - ``SYSTEM_AUTO``：system 代行（auto_approve 演示模式），**非人工**。
    - ``UNSPECIFIED``：历史事件或非交互调用，通道未记录——不得反推为 TTY。
    """

    TTY = "tty"
    WEBUI_LOCAL = "webui-local"
    SYSTEM_AUTO = "system-auto"
    UNSPECIFIED = "unspecified"
