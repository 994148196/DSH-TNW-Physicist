"""qresearch CLI 审批台（计划 v3 M2 / 评审修订 A1）。

MCP 通道里 agent 无法证明"人在场"——所以写 `actor=HUMAN` 审批事件的唯一入口
是本模块：真实终端（TTY 守卫 `sys.stdin.isatty()`，agent 经 DSH bash 起的子进程
没有 TTY，守卫即拒绝，fail-closed）+ 显式 y 确认。台账是唯一真相源（D1）：MCP
侧的 plan_approve / 结论确认只**查询**这里写入的事件，绝不代写。

用法（在真实终端里）：
    qresearch approvals                                  # 列出全部待办（只读）
    qresearch approve  <project_dir> <plan_id> [备注...]  # 人工批准计划
    qresearch reject   <project_dir> <plan_id> [备注...]  # 人工拒绝计划
    qresearch conclude <project_dir> <decision_id> [备注...]  # 人工确认结论（D6）

其中 <project_dir> 是项目数据目录（含 state.sqlite），即
<projects_root>/<project_id>。
"""
from __future__ import annotations

import sys
from pathlib import Path

from qresearch.core.events import Event, EventLog
from qresearch.core.models import Decision, ResearchPlan
from qresearch.core.status import Actor, PlanStatus
from qresearch.core.storage import Storage
from qresearch.loop import approve_plan, reject_plan

_USAGE = """\
审批台用法：
  qresearch approvals
  qresearch approve  <project_dir> <plan_id> [备注...]
  qresearch reject   <project_dir> <plan_id> [备注...]
  qresearch conclude <project_dir> <decision_id> [备注...]
（approve/reject/conclude 需要真实终端 TTY——agent 无法代批，A1）
"""


def _require_tty(stdin=None) -> bool:
    """TTY 守卫（A1）：无终端即拒绝。stdin 参数仅供测试注入。"""
    stream = stdin if stdin is not None else sys.stdin
    if not stream.isatty():
        print("拒绝：审批台需要真实终端（TTY）。agent 经 bash 起的子进程没有 TTY，"
              "不得代批（A1 fail-closed）。")
        return False
    return True


def _open_project(project_dir: Path) -> tuple[Storage, EventLog]:
    return Storage(project_dir / "state.sqlite"), EventLog(project_dir / "events.jsonl")


def _show_brief(plan: ResearchPlan) -> None:
    print(f"\n== 计划 v{plan.version}（{len(plan.steps)} 步）{plan.plan_id} ==")
    for st in plan.steps:
        flag = " [需审批]" if st.requires_approval else ""
        tools = "、".join(st.tools) or "—"
        print(f"  {st.step_id} [{st.action}] {st.purpose}（工具：{tools}）{flag}")
    if plan.risks:
        print("风险：" + "；".join(plan.risks))
    if plan.diff_summary:
        print(f"与上一版差异：{plan.diff_summary}")


# ---------------------------------------------------------------- 写操作
def do_approve(project_dir: Path, plan_id: str, note: str = "", *,
               stdin=None) -> int:
    if not _require_tty(stdin):
        return 2
    if not (project_dir / "state.sqlite").exists():
        print(f"项目目录不存在或没有台账: {project_dir}")
        return 1
    storage, log = _open_project(project_dir)
    try:
        plan = storage.get(ResearchPlan, plan_id)
        if plan is None:
            print(f"计划不存在: {plan_id}")
            return 1
        if plan.status == PlanStatus.APPROVED:
            print(f"计划 {plan_id} 已是批准状态（无需重复批准）。")
            return 0
        _show_brief(plan)
        answer = input(f"批准计划 {plan_id}（v{plan.version}）？[y]批准 / 其他取消：")
        if not answer.strip().lower().startswith("y"):
            print("（未批准）")
            return 1
        approve_plan(storage, log, plan, actor=Actor.HUMAN, note=note)
        print(f"已批准：{plan_id}（actor=HUMAN 已落账，agent 侧 plan_approve 可查询到）")
        return 0
    finally:
        storage.close()


def do_reject(project_dir: Path, plan_id: str, note: str = "", *,
              stdin=None) -> int:
    if not _require_tty(stdin):
        return 2
    if not (project_dir / "state.sqlite").exists():
        print(f"项目目录不存在或没有台账: {project_dir}")
        return 1
    storage, log = _open_project(project_dir)
    try:
        plan = storage.get(ResearchPlan, plan_id)
        if plan is None:
            print(f"计划不存在: {plan_id}")
            return 1
        _show_brief(plan)
        answer = input(f"拒绝计划 {plan_id}（v{plan.version}）？[y]拒绝 / 其他取消：")
        if not answer.strip().lower().startswith("y"):
            print("（未拒绝）")
            return 1
        reject_plan(storage, log, plan, actor=Actor.HUMAN,
                    note=note or "人工拒绝")
        print(f"已拒绝：{plan_id}（actor=HUMAN 已落账；可在 agent 侧提修改意见重新出计划）")
        return 0
    finally:
        storage.close()


def do_conclude(project_dir: Path, decision_id: str, note: str = "", *,
                stdin=None) -> int:
    """人工确认收束决策（D6）：conclude 事件落账（actor=HUMAN）+ 报告刷新为
    concluded。只对 requires_human=True 的决策开放；重复确认幂等返回。"""
    if not _require_tty(stdin):
        return 2
    if not (project_dir / "state.sqlite").exists():
        print(f"项目目录不存在或没有台账: {project_dir}")
        return 1
    storage, log = _open_project(project_dir)
    try:
        decision = storage.get(Decision, decision_id)
        if decision is None:
            print(f"决策不存在: {decision_id}")
            return 1
        if not decision.requires_human:
            print(f"决策 {decision_id} 未标记需要人工确认（requires_human=False）——"
                  "无需 conclude。")
            return 1
        pid = decision.project_id
        if any(e.action == "conclude" and e.object_id == decision_id
               for e in log.events(project_id=pid)):
            print(f"决策 {decision_id} 已被人工确认过（台账为准，幂等返回）。")
            return 0
        print(f"\n== 待确认结论 {decision_id}（{decision.type.value}）==")
        print(f"  建议：{decision.recommendation.value}")
        print(f"  理由：{decision.rationale or '（未填写）'}")
        answer = input("确认该结论？[y]确认（conclude 落账）/ 其他取消：")
        if not answer.strip().lower().startswith("y"):
            print("（未确认——决策保持 requires_human，agent 只能继续建议）")
            return 1
        log.append(Event(actor=Actor.HUMAN, action="conclude", project_id=pid,
                         object_type="Decision", object_id=decision_id,
                         detail={"note": note}))
        # 报告刷新：status 由引擎从台账推导（有 conclude 事件 → concluded）
        from qresearch.engine import ResearchEngine

        engine = ResearchEngine(storage, log, client=None)
        path = engine.report_generate(pid)
        print(f"结论已确认落账（actor=HUMAN）；报告已刷新：{path}")
        return 0
    finally:
        storage.close()


# ---------------------------------------------------------------- 只读待办
def list_pending(data_root: Path) -> int:
    """列出数据根目录下所有项目的待审批计划与待确认结论（只读，无 TTY 要求）。"""
    dbs = sorted(data_root.glob("*/state.sqlite"))
    if not dbs:
        print(f"（{data_root} 下没有项目）")
        return 0
    found = False
    for db in dbs:
        pid = db.parent.name
        storage = Storage(db)
        try:
            plans = [p for p in storage.list(ResearchPlan, project_id=pid)
                     if p.status == PlanStatus.AWAITING_APPROVAL]
            log = EventLog(db.parent / "events.jsonl")
            confirmed = {e.object_id for e in log.events(project_id=pid)
                         if e.action == "conclude"}
            decisions = [d for d in storage.list(Decision, project_id=pid)
                         if d.requires_human and d.decision_id not in confirmed]
            if not plans and not decisions:
                continue
            found = True
            print(f"项目 {pid}（{db.parent}）")
            for p in plans:
                print(f"  待审批计划：qresearch approve {db.parent} {p.plan_id}"
                      f"   # v{p.version}，{len(p.steps)} 步")
            for d in decisions:
                print(f"  待确认结论：qresearch conclude {db.parent} {d.decision_id}"
                      f"   # {d.type.value}（{d.recommendation.value}）")
        finally:
            storage.close()
    if not found:
        print("（没有待办：无待审批计划、无待确认结论）")
    return 0


def main(argv: list[str]) -> int:
    """审批台入口：argv[0] ∈ approvals/approve/reject/conclude（session.main 分发）。"""
    if not argv:
        print(_USAGE)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "approvals":
        return list_pending(Path(rest[0]) if rest else Path("research_data"))
    if cmd in ("approve", "reject", "conclude"):
        if len(rest) < 2:
            print(_USAGE)
            return 2
        project_dir, obj_id = Path(rest[0]), rest[1]
        note = " ".join(rest[2:]).strip()
        fn = {"approve": do_approve, "reject": do_reject,
              "conclude": do_conclude}[cmd]
        return fn(project_dir, obj_id, note)
    print(_USAGE)
    return 2
