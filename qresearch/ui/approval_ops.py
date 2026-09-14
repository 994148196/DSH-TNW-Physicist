"""审批写路径的通道无关内核（A1 审批台 + 通道保真度审计）。

职责边界（改这个文件前先读）：

- 本模块**不含在场证明**。它只负责"给定一次已被真人确认的审批，正确地写台账"。
  在场证明由各通道自己提供：
    * TTY 通道   → ``sys.stdin.isatty()``（approvals.py）
    * 浏览器通道 → session cookie + CSRF token（web_approvals.py）
- 因此本模块**绝不可**被 MCP 层或 research_loop 用来伪造 ``actor=HUMAN`` 事件。
  它只接受"人工在场"通道（``_require_human_channel`` 结构性拒绝其它通道），
  以保证任何一条 ``actor=HUMAN`` 事件都至少经由某个在场证明才能写出。
- 写路径唯一：TTY 与浏览器两条通道共用这里的函数，避免两套写逻辑漂移。

保真度分级见 ``qresearch.core.status.ApprovalChannel``。核心事实：TTY 是**进程能力**
（agent 起的子进程结构性拿不到），而同机 localhost 浏览器通道的凭据落在 agent 同用户
可读的信任域内，**保真度较低**。本模块不假装两者等价——它把通道如实写进事件，
让读报告的人自行折价。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from qresearch.core.events import Event, EventLog
from qresearch.core.models import Decision, ResearchPlan
from qresearch.core.status import Actor, ApprovalChannel, PlanStatus
from qresearch.core.storage import Storage
from qresearch.loop import approve_plan, reject_plan

#: 允许写 actor=HUMAN 审批事件的通道（必须蕴含"真人在场"）。
HUMAN_CHANNELS: tuple[ApprovalChannel, ...] = (
    ApprovalChannel.TTY,
    ApprovalChannel.WEBUI_LOCAL,
)


class ApprovalError(Exception):
    """审批操作不可执行：对象不存在／前置不满足／通道不代表人工在场。"""


@dataclass
class OpResult:
    """一次审批写操作的结果（两条通道共用的回执，便于统一渲染）。"""

    message: str
    report_path: Path | None = None
    already: bool = False


@dataclass
class PendingItem:
    """一条待人工处理的事项（计划待批 / 结论待确认）。"""

    kind: str            # "plan" | "decision"
    project_id: str
    obj_id: str
    title: str
    lines: list[str] = field(default_factory=list)

    @property
    def actions(self) -> list[str]:
        return ["approve", "reject"] if self.kind == "plan" else ["conclude"]

    def cli_command(self, project_dir: Path) -> str:
        return f"qresearch {self.actions[0]} {project_dir} {self.obj_id}"


# ---------------------------------------------------------------- 基础
def to_channel(channel: str | ApprovalChannel) -> ApprovalChannel:
    return channel if isinstance(channel, ApprovalChannel) else ApprovalChannel(channel)


def _require_human_channel(channel: str | ApprovalChannel) -> ApprovalChannel:
    """结构性闸门：只放行蕴含"真人在场"的通道。"""
    ch = to_channel(channel)
    if ch not in HUMAN_CHANNELS:
        allowed = "、".join(c.value for c in HUMAN_CHANNELS)
        raise ApprovalError(
            f"通道 {ch.value!r} 不代表人工在场，不得写 actor=HUMAN 审批事件"
            f"（允许：{allowed}）。"
        )
    return ch


def open_project(project_dir: Path) -> tuple[Storage, EventLog]:
    project_dir = Path(project_dir)
    return (Storage(project_dir / "state.sqlite"),
            EventLog(project_dir / "events.jsonl"))


def require_ledger(project_dir: Path) -> Path:
    project_dir = Path(project_dir)
    if not (project_dir / "state.sqlite").exists():
        raise ApprovalError(f"项目目录不存在或没有台账: {project_dir}")
    return project_dir


def find_conclude_event(log: EventLog, project_id: str,
                        decision_id: str) -> Event | None:
    for e in log.events(project_id=project_id):
        if e.action == "conclude" and e.object_id == decision_id:
            return e
    return None


def conclude_channel(log: EventLog, project_id: str,
                     decision_id: str) -> str | None:
    """已确认结论的取得通道；None = 尚未确认。历史事件无 channel → "未记录"。"""
    e = find_conclude_event(log, project_id, decision_id)
    if e is None:
        return None
    return e.detail.get("channel") or "未记录"


# ---------------------------------------------------------------- 写操作
def apply_conclude(project_dir: Path, decision_id: str, *,
                   note: str = "",
                   channel: str | ApprovalChannel) -> OpResult:
    """人工确认收束决策（D6）：写 conclude 事件（actor=HUMAN）+ 刷新报告。

    幂等：已确认过则跳过落账、仅刷新报告并置 ``already=True``。
    """
    ch = _require_human_channel(channel)
    project_dir = require_ledger(project_dir)
    storage, log = open_project(project_dir)
    try:
        decision = storage.get(Decision, decision_id)
        if decision is None:
            raise ApprovalError(f"决策不存在: {decision_id}")
        if not decision.requires_human:
            raise ApprovalError(
                f"决策 {decision_id} 未标记需要人工确认（requires_human=False）"
                "——无需 conclude。")
        pid = decision.project_id
        already = find_conclude_event(log, pid, decision_id) is not None
        if not already:
            log.append(Event(actor=Actor.HUMAN, action="conclude", project_id=pid,
                             object_type="Decision", object_id=decision_id,
                             detail={"note": note, "channel": ch.value}))
        from qresearch.engine import ResearchEngine

        engine = ResearchEngine(storage, log, client=None)
        report = engine.report_generate(pid)
        msg = (f"决策 {decision_id} 已被确认过（台账为准，幂等返回）。"
               if already else
               f"结论已确认落账（actor=HUMAN，channel={ch.value}）；"
               f"报告已刷新：{report}")
        return OpResult(message=msg, report_path=report, already=already)
    finally:
        storage.close()


def _apply_plan_op(project_dir: Path, plan_id: str, *, note: str,
                   channel: str | ApprovalChannel, op: str) -> OpResult:
    ch = _require_human_channel(channel)
    project_dir = require_ledger(project_dir)
    storage, log = open_project(project_dir)
    try:
        plan = storage.get(ResearchPlan, plan_id)
        if plan is None:
            raise ApprovalError(f"计划不存在: {plan_id}")
        if op == "approve" and plan.status == PlanStatus.APPROVED:
            return OpResult(message=f"计划 {plan_id} 已是批准状态（无需重复批准）。",
                            already=True)
        if op == "reject" and plan.status == PlanStatus.REJECTED:
            return OpResult(message=f"计划 {plan_id} 已是拒绝状态（无需重复拒绝）。",
                            already=True)
        fn = approve_plan if op == "approve" else reject_plan
        fn(storage, log, plan, actor=Actor.HUMAN, note=note, channel=ch)
        verb = "批准" if op == "approve" else "拒绝"
        tail = ("agent 侧 plan_approve 可查询到"
                if op == "approve" else "可在 agent 侧提修改意见重新出计划")
        return OpResult(
            message=f"已{verb}：{plan_id}（actor=HUMAN，channel={ch.value} 已落账，{tail}）")
    finally:
        storage.close()


def apply_approve(project_dir: Path, plan_id: str, *, note: str = "",
                  channel: str | ApprovalChannel) -> OpResult:
    return _apply_plan_op(project_dir, plan_id, note=note, channel=channel,
                          op="approve")


def apply_reject(project_dir: Path, plan_id: str, *, note: str = "",
                 channel: str | ApprovalChannel) -> OpResult:
    return _apply_plan_op(project_dir, plan_id, note=note, channel=channel,
                          op="reject")


# ---------------------------------------------------------------- 只读待办
def pending_in_project(project_dir: Path) -> list[PendingItem]:
    """单个项目里的待办（计划待批 + 结论待确认）。只读，无在场证明要求。

    "已被采纳执行"的决策不计入待办：若某条决策被后续计划经
    ``plan.based_on_decision`` 采纳（例如 iterate 已经真的迭代了下一轮），
    它就**不再是"待人工确认"的事项**——继续列出来只会让审批台堆满无效按钮，
    掩盖真正需要人看的收束决策。这类决策仍完整留在决策记录里可查。

    历史事件无 channel / 无 based_on_decision 的旧台账同样按上述规则处理。
    """
    project_dir = Path(project_dir)
    if not (project_dir / "state.sqlite").exists():
        return []
    pid = project_dir.name
    storage, log = open_project(project_dir)
    try:
        events = log.events(project_id=pid)
        confirmed = {e.object_id for e in events if e.action == "conclude"}
        plans = storage.list(ResearchPlan, project_id=pid)
        acted_on = {p.based_on_decision for p in plans if p.based_on_decision}
        out: list[PendingItem] = []
        for p in plans:
            if p.status is not PlanStatus.AWAITING_APPROVAL:
                continue
            lines = [f"v{p.version}，{len(p.steps)} 步"]
            verdict = next((e.detail.get("verdict") for e in events
                            if e.action == "plan_ready" and e.object_id == p.plan_id),
                           None)
            if verdict:
                lines.append(f"critic: {verdict}")
            blockers = [i for e in events if e.action == "plan_ready"
                        and e.object_id == p.plan_id
                        for i in e.detail.get("issues", [])
                        if isinstance(i, dict) and i.get("severity") == "blocker"]
            if blockers:
                lines.append(f"[blocker] {len(blockers)} 条")
            out.append(PendingItem(kind="plan", project_id=pid, obj_id=p.plan_id,
                                   title=f"计划 {p.plan_id}", lines=lines))
        for d in storage.list(Decision, project_id=pid):
            if not d.requires_human or d.decision_id in confirmed:
                continue
            if d.decision_id in acted_on:
                continue  # 已被后续计划采纳执行，不再待确认
            n_pass = sum(1 for c in d.checklist if c.status == "passed")
            n_open = sum(1 for c in d.checklist if c.status != "passed")
            out.append(PendingItem(
                kind="decision", project_id=pid, obj_id=d.decision_id,
                title=f"结论 {d.decision_id}",
                lines=[f"{d.type.value} → 建议 {d.recommendation.value}",
                       f"checklist：{n_pass} passed / {n_open} 未闭合",
                       "[注意] declare_result 类结论无条件需要人工（D6）"]))
        return out
    finally:
        storage.close()


def pending_in_root(data_root: Path) -> list[PendingItem]:
    """数据根目录下所有项目的待办（``*/state.sqlite``）。只读。"""
    data_root = Path(data_root)
    out: list[PendingItem] = []
    for db in sorted(data_root.glob("*/state.sqlite")):
        out.extend(pending_in_project(db.parent))
    return out
