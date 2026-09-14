"""qresearch CLI 审批台（计划 v3 M2 / 评审修订 A1）。

写 ``actor=HUMAN`` 审批事件的入口只有"带在场证明的通道"。本模块提供 **TTY 通道**：
真实终端（TTY 守卫 ``sys.stdin.isatty()``，agent 经 DSH bash 起的子进程没有 TTY，
守卫即拒绝，fail-closed）+ 显式 y 确认。另一条是 localhost 浏览器通道
（``web_approvals.py``，带 cookie/CSRF 但在**同一信任域**内，保真度较低）。
两条通道共用 ``approval_ops`` 的写路径，避免写逻辑漂移。

台账是唯一真相源（D1）：MCP 侧的 plan_approve / 结论确认只**查询**这里写入的
事件，绝不代写。

用法（在真实终端里）：
    qresearch approvals                                  # 列出全部待办（只读）
    qresearch approve  <project_dir> <plan_id> [备注...]  # 人工批准计划
    qresearch reject   <project_dir> <plan_id> [备注...]  # 人工拒绝计划
    qresearch conclude <project_dir> <decision_id> [备注...]  # 人工确认结论（D6）
    qresearch approvals-web [--port N] [--data-root D]   # 浏览器审批台（保真度较低）

其中 <project_dir> 是项目数据目录（含 state.sqlite），即
<projects_root>/<project_id>。

诚实声明（不要把这个 if 当成密码学保证）：TTY 守卫是**善意边界**。拥有代码执行权
的进程理论上可以自行分配控制台并合成击键，使 ``isatty()`` 为真。它的价值在于让
"老实"成为默认路径、让伪造成为需要刻意为之的动作——真正的保证是"agent 不伪造
人类签字"这一约定，而不是这个检查本身。因此每条审批事件都记录 channel
（``ApprovalChannel``），让保真度可审计，而不是被 ``actor=HUMAN`` 三个字抹平。
"""
from __future__ import annotations

import sys
from pathlib import Path

from qresearch.core.models import Decision, ResearchPlan
from qresearch.core.status import ApprovalChannel
from qresearch.ui import approval_ops
from qresearch.ui.approval_ops import ApprovalError

_USAGE = """\
审批台用法：
  qresearch approvals
  qresearch approve  <project_dir> <plan_id> [备注...]
  qresearch reject   <project_dir> <plan_id> [备注...]
  qresearch conclude <project_dir> <decision_id> [备注...]
  qresearch approvals-web [--port N] [--data-root D]
（approve/reject/conclude 需要真实终端 TTY——agent 无法代批，A1）
（approvals-web 是同机浏览器通道，保真度低于 TTY，台账会如实记录 channel）
"""


def _require_tty(stdin=None) -> bool:
    """TTY 守卫（A1）：无终端即拒绝。stdin 参数仅供测试注入。"""
    stream = stdin if stdin is not None else sys.stdin
    if not stream.isatty():
        print("拒绝：审批台需要真实终端（TTY）。agent 经 bash 起的子进程没有 TTY，"
              "不得代批（A1 fail-closed）。")
        return False
    return True


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


# ---------------------------------------------------------------- 写操作（TTY 通道）
def do_approve(project_dir: Path, plan_id: str, note: str = "", *,
               stdin=None) -> int:
    return _do_plan_op(project_dir, plan_id, note, stdin=stdin, op="approve")


def do_reject(project_dir: Path, plan_id: str, note: str = "", *,
              stdin=None) -> int:
    return _do_plan_op(project_dir, plan_id, note, stdin=stdin, op="reject")


def _do_plan_op(project_dir: Path, plan_id: str, note: str, *,
                stdin, op: str) -> int:
    if not _require_tty(stdin):
        return 2
    try:
        project_dir = approval_ops.require_ledger(project_dir)
    except ApprovalError as exc:
        print(str(exc))
        return 1
    storage, _log = approval_ops.open_project(project_dir)
    try:
        plan = storage.get(ResearchPlan, plan_id)
    finally:
        storage.close()
    if plan is None:
        print(f"计划不存在: {plan_id}")
        return 1
    verb = "批准" if op == "approve" else "拒绝"
    _show_brief(plan)
    answer = input(f"{verb}计划 {plan_id}（v{plan.version}）？[y]{verb} / 其他取消：")
    if not answer.strip().lower().startswith("y"):
        print(f"（未{verb}）")
        return 1
    fn = (approval_ops.apply_approve if op == "approve"
          else approval_ops.apply_reject)
    try:
        result = fn(project_dir, plan_id, note=note,
                    channel=ApprovalChannel.TTY)
    except ApprovalError as exc:
        print(str(exc))
        return 1
    print(result.message)
    return 0


def do_conclude(project_dir: Path, decision_id: str, note: str = "", *,
                stdin=None) -> int:
    """人工确认收束决策（D6）：conclude 事件落账（actor=HUMAN）+ 报告刷新为
    concluded。只对 requires_human=True 的决策开放；重复确认幂等返回。"""
    if not _require_tty(stdin):
        return 2
    try:
        project_dir = approval_ops.require_ledger(project_dir)
    except ApprovalError as exc:
        print(str(exc))
        return 1
    storage, log = approval_ops.open_project(project_dir)
    try:
        decision = storage.get(Decision, decision_id)
        if decision is None:
            print(f"决策不存在: {decision_id}")
            return 1
        if not decision.requires_human:
            print(f"决策 {decision_id} 未标记需要人工确认（requires_human=False）——"
                  "无需 conclude。")
            return 1
        if approval_ops.find_conclude_event(log, decision.project_id,
                                            decision_id) is not None:
            print(f"决策 {decision_id} 已被人工确认过（台账为准，幂等返回）。")
            return 0
        print(f"\n== 待确认结论 {decision_id}（{decision.type.value}）==")
        print(f"  建议：{decision.recommendation.value}")
        print(f"  理由：{decision.rationale or '（未填写）'}")
        answer = input("确认该结论？[y]确认（conclude 落账）/ 其他取消：")
        if not answer.strip().lower().startswith("y"):
            print("（未确认——决策保持 requires_human，agent 只能继续建议）")
            return 1
    finally:
        storage.close()
    try:
        result = approval_ops.apply_conclude(project_dir, decision_id, note=note,
                                             channel=ApprovalChannel.TTY)
    except ApprovalError as exc:
        print(str(exc))
        return 1
    if result.report_path is not None:
        print(f"结论已确认落账（actor=HUMAN，channel={ApprovalChannel.TTY.value}）；"
              f"报告已刷新：{result.report_path}")
    else:
        print(result.message)
    return 0


# ---------------------------------------------------------------- 只读待办
def _p(msg: str) -> None:
    """编码安全打印：Windows 控制台常为 GBK，台账里的非 GBK 字符（emoji 等）
    会让 print 抛 UnicodeEncodeError 并崩掉整个审批台。宁可降级替换也不崩。"""
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(msg.encode(enc, errors="replace").decode(enc, errors="replace"))


def list_pending(data_root: Path) -> int:
    items = approval_ops.pending_in_root(data_root)
    if not items:
        _p(f"（{data_root} 下没有待办：无待审批计划、无待确认结论）")
        return 0
    by_project: dict[str, list] = {}
    for it in items:
        by_project.setdefault(it.project_id, []).append(it)
    for pid, group in by_project.items():
        _p(f"项目 {pid}")
        for it in group:
            verb = "待审批计划" if it.kind == "plan" else "待确认结论"
            _p(f"  {verb}：{it.cli_command(data_root / pid)}"
               f"   # {'，'.join(it.lines)}")
    return 0


def main(argv: list[str]) -> int:
    """审批台入口：argv[0] ∈ approvals/approve/reject/conclude/approvals-web
    （session.main 分发）。"""
    if not argv:
        print(_USAGE)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "approvals":
        return list_pending(Path(rest[0]) if rest else Path("research_data"))
    if cmd == "approvals-web":
        from qresearch.ui import web_approvals

        return web_approvals.main(rest)
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
