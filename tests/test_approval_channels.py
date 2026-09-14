"""审批通道与保真度审计测试（A1 补充：channel 字段）。

覆盖三件事：
1. **写路径唯一**：TTY 与 localhost 浏览器两条通道共用 approval_ops，落账结果
   只在 channel 上不同；
2. **结构性闸门**：不代表"人工在场"的通道（system-auto/unspecified）写
   actor=HUMAN 事件必须被拒——防止未来有人拿它伪造人工签字；
3. **保真度如实入账**：报告把 channel 渲染成独立一行并标注保真度，历史事件
   （无 channel）必须显示"未记录"而**不得**被反推成 tty。

另有两条通道自身的边界：TTY 守卫拒无终端进程（保留在 test_mcp_server）；
本文件补浏览器通道的 token/cookie/CSRF 与"只许回环"约束。
"""
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from qresearch.core.events import Event, EventLog
from qresearch.core.models import (CheckItem, Decision, PlanStep, Project,
                                   ResearchPlan)
from qresearch.core.status import (Actor, ApprovalChannel, DecisionRecommendation,
                                   DecisionType, PlanStatus)
from qresearch.core.storage import Storage
from qresearch.engine import ResearchEngine
from qresearch.ui import approval_ops, approvals, web_approvals
from qresearch.ui.approval_ops import ApprovalError

TOKEN = "test-token-abc123"


class _TTY:
    """假 TTY：isatty()=True（守卫放行；input 由 monkeypatch 提供）。"""

    def isatty(self) -> bool:
        return True


# ---------------------------------------------------------------- 夹具
def _project(tmp_path, pid: str = "proj"):
    """建一个最小项目（Project + 待确认的 declare_result 决策）。"""
    pdir = tmp_path / pid
    pdir.mkdir(parents=True, exist_ok=True)
    storage = Storage(pdir / "state.sqlite")
    log = EventLog(pdir / "events.jsonl")
    storage.save(Project(project_id=pid, title="Heisenberg 链基态能量",
                         question="e0(N) 随长度如何收敛"))
    dec = Decision(project_id=pid, type=DecisionType.DECLARE_RESULT,
                   recommendation=DecisionRecommendation.ACCEPT,
                   checklist=[CheckItem(claim="外推与 Bethe ansatz 一致",
                                        status="untested",
                                        reason="测试夹具：未接证据")],
                   rationale="夹具 rationale：结论可宣布，但须列明限制。")
    storage.save(dec)
    storage.close()
    return pdir, dec.decision_id


def _plan(tmp_path, pid: str = "proj"):
    pdir = tmp_path / pid
    pdir.mkdir(parents=True, exist_ok=True)
    storage = Storage(pdir / "state.sqlite")
    plan = ResearchPlan(project_id=pid, goal_id="goal_x", version=1,
                        status=PlanStatus.AWAITING_APPROVAL,
                        steps=[PlanStep(step_id="s1", action="run_experiment",
                                        purpose="基准", tools=["simple_ed"],
                                        inputs={"N": 4})])
    storage.save(plan)
    storage.close()
    return pdir, plan.plan_id


def _report(pdir) -> str:
    return (pdir / "report.md").read_text(encoding="utf-8")


def _events(pdir, pid="proj"):
    return EventLog(pdir / "events.jsonl").events(project_id=pid)


def _start_server(tmp_path, **kw):
    srv = web_approvals.serve(tmp_path, port=0, token=TOKEN, announce=False, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _get(url, *, cookie=None):
    req = urllib.request.Request(url)
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8"), r.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), e.headers.get("Set-Cookie")


def _post(url, data, *, cookie=None):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def _session_cookie(tmp_path):
    """起服务并完成带 token 的首次 GET，返回 (server, base, cookie)。"""
    srv, base = _start_server(tmp_path)
    status, _, set_cookie = _get(f"{base}/?t={TOKEN}")
    assert status == 200 and set_cookie, "带 token 的首次 GET 应成功并下发 cookie"
    return srv, base, set_cookie.split(";")[0]


# ================================================================ 结构性闸门
def test_apply_ops_refuse_non_human_channel(tmp_path):
    """不代表人工在场的通道不得写 actor=HUMAN 事件（防伪造人工签字）。"""
    pdir, dec_id = _project(tmp_path)
    for bad in (ApprovalChannel.SYSTEM_AUTO, ApprovalChannel.UNSPECIFIED):
        with pytest.raises(ApprovalError, match="不代表人工在场"):
            approval_ops.apply_conclude(pdir, dec_id, channel=bad)
        with pytest.raises(ApprovalError, match="不代表人工在场"):
            approval_ops.apply_approve(pdir, "plan_x", channel=bad)
    assert not [e for e in _events(pdir) if e.action == "conclude"], "被拒后不得落账"
    assert not (pdir / "report.md").exists(), "被拒后不得刷新报告"


def test_apply_conclude_validates_target(tmp_path):
    pdir, dec_id = _project(tmp_path)
    with pytest.raises(ApprovalError, match="决策不存在"):
        approval_ops.apply_conclude(pdir, "dec_nope",
                                    channel=ApprovalChannel.WEBUI_LOCAL)
    with pytest.raises(ApprovalError, match="没有台账"):
        approval_ops.apply_conclude(tmp_path / "nope", dec_id,
                                    channel=ApprovalChannel.WEBUI_LOCAL)


# ================================================================ TTY 通道
def test_tty_channel_recorded_and_rendered(tmp_path, monkeypatch):
    """TTY 通道落账 channel=tty，报告单列并标注"保真度最高"。"""
    pdir, dec_id = _project(tmp_path)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    assert approvals.do_conclude(pdir, dec_id, stdin=_TTY()) == 0

    evs = [e for e in _events(pdir) if e.action == "conclude"]
    assert len(evs) == 1
    assert evs[0].actor == Actor.HUMAN
    assert evs[0].detail["channel"] == "tty"

    report = _report(pdir)
    assert "`concluded`" in report
    assert "- **结论确认通道**：`tty`" in report
    assert "保真度最高" in report


def test_tty_guard_still_rejects_no_terminal(tmp_path):
    """TTY 守卫未因重构而放松：无终端拒绝且不碰台账。"""
    import io

    pdir, dec_id = _project(tmp_path)
    assert approvals.do_conclude(pdir, dec_id, stdin=io.StringIO("y")) == 2
    assert not [e for e in _events(pdir) if e.action == "conclude"]


# ================================================================ 浏览器通道
def test_web_get_requires_url_token(tmp_path):
    srv, base = _start_server(tmp_path)
    try:
        status, body, _ = _get(f"{base}/")
        assert status == 403 and "token" in body
        status, body, _ = _get(f"{base}/?t=wrong")
        assert status == 403
    finally:
        srv.shutdown()
        srv.server_close()


def test_web_post_requires_session_and_csrf(tmp_path):
    pdir, dec_id = _project(tmp_path)
    srv, base, cookie = _session_cookie(tmp_path)
    try:
        action = {"kind": "decision", "project_id": "proj", "obj_id": dec_id,
                  "op": "conclude", "csrf": TOKEN}
        status, body = _post(f"{base}/act", action)  # 无 cookie
        assert status == 403 and "session cookie" in body

        status, body = _post(f"{base}/act", {**action, "csrf": "bad"}, cookie=cookie)
        assert status == 403 and "CSRF" in body

        assert not [e for e in _events(pdir) if e.action == "conclude"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_web_conclude_lands_human_webui_local(tmp_path):
    """浏览器通道落账 channel=webui-local，报告如实标注保真度较低，且幂等。"""
    pdir, dec_id = _project(tmp_path)
    srv, base, cookie = _session_cookie(tmp_path)
    try:
        status, body, _ = _get(f"{base}/", cookie=cookie)
        assert status == 200
        assert "较低保真度通道" in body, "页面必须自陈通道保真度"
        assert dec_id in body and "夹具 rationale" in body, "页面应展示决策内容"

        action = {"kind": "decision", "project_id": "proj", "obj_id": dec_id,
                  "op": "conclude", "csrf": TOKEN, "note": "浏览器确认"}
        status, body = _post(f"{base}/act", action, cookie=cookie)
        assert status == 200 and "已落账" in body

        evs = [e for e in _events(pdir) if e.action == "conclude"]
        assert len(evs) == 1
        assert evs[0].actor == Actor.HUMAN, "通道较弱，但 actor 仍是发起审批的人"
        assert evs[0].detail["channel"] == "webui-local"
        assert evs[0].detail["note"] == "浏览器确认"

        report = _report(pdir)
        assert "`concluded`" in report
        assert "- **结论确认通道**：`webui-local`" in report
        assert "保真度较低" in report, "报告必须标注该通道保真度较低"

        # 幂等：重复 POST 不重复落账
        status, _ = _post(f"{base}/act", action, cookie=cookie)
        assert status == 200
        assert len([e for e in _events(pdir) if e.action == "conclude"]) == 1
    finally:
        srv.shutdown()
        srv.server_close()


def test_web_plan_approve_and_reject(tmp_path):
    """浏览器通道也能批计划；approve 落 channel，reject 落另一条计划。"""
    pdir, plan_id = _plan(tmp_path)
    srv, base, cookie = _session_cookie(tmp_path)
    try:
        status, body, _ = _get(f"{base}/", cookie=cookie)
        assert status == 200 and plan_id in body and "批准" in body

        status, _ = _post(f"{base}/act",
                          {"kind": "plan", "project_id": "proj", "obj_id": plan_id,
                           "op": "approve", "csrf": TOKEN},
                          cookie=cookie)
        assert status == 200
        ev = next(e for e in _events(pdir) if e.action == "approve")
        assert ev.actor == Actor.HUMAN and ev.detail["channel"] == "webui-local"

        storage = Storage(pdir / "state.sqlite")
        try:
            assert storage.get(ResearchPlan, plan_id).status is PlanStatus.APPROVED
        finally:
            storage.close()
    finally:
        srv.shutdown()
        srv.server_close()


def test_web_refuses_non_loopback_host(tmp_path):
    """审批口不得暴露到局域网——非回环一律拒绝启动。"""
    with pytest.raises(SystemExit, match="只允许监听回环地址"):
        web_approvals.serve(tmp_path, host="0.0.0.0", token=TOKEN, announce=False)


def test_web_pending_listing_empty(tmp_path):
    srv, base, cookie = _session_cookie(tmp_path)
    try:
        status, body, _ = _get(f"{base}/", cookie=cookie)
        assert status == 200 and "没有待办" in body
    finally:
        srv.shutdown()
        srv.server_close()


# ================================================================ 历史事件
def test_legacy_conclude_without_channel_not_read_as_tty(tmp_path):
    """channel 字段引入前的 conclude 事件显示"未记录"，不得反推为 tty。"""
    pdir, dec_id = _project(tmp_path)
    EventLog(pdir / "events.jsonl").append(Event(
        actor=Actor.HUMAN, action="conclude", project_id="proj",
        object_type="Decision", object_id=dec_id, detail={}))  # 旧格式：无 channel

    storage = Storage(pdir / "state.sqlite")
    try:
        engine = ResearchEngine(storage, EventLog(pdir / "events.jsonl"), client=None)
        assert engine.conclude_channel("proj") == "未记录"
        engine.report_generate("proj")
    finally:
        storage.close()

    report = _report(pdir)
    assert "- **结论确认通道**：`未记录`" in report
    assert "不得反推为 tty" in report


def test_status_without_conclude_has_no_channel_line(tmp_path):
    """未确认时报告不出现通道行（None ≠ 未记录）。"""
    pdir, _ = _project(tmp_path)
    storage = Storage(pdir / "state.sqlite")
    try:
        engine = ResearchEngine(storage, EventLog(pdir / "events.jsonl"), client=None)
        assert engine.conclude_channel("proj") is None
        engine.report_generate("proj")
    finally:
        storage.close()

    report = _report(pdir)
    assert "`terminated`" in report
    assert "结论确认通道" not in report


# ================================================================ 状态投影
def _status(pdir):
    storage = Storage(pdir / "state.sqlite")
    try:
        engine = ResearchEngine(storage, EventLog(pdir / "events.jsonl"), client=None)
        return engine.status("proj")
    finally:
        storage.close()


def test_status_projection_separates_required_from_confirmed(tmp_path):
    """requires_human（必须人工）≠ confirmed_by_human（人工真的确认了）。

    agent 只应据后者宣布结论成立——这正是它需要能区分两者的原因。
    """
    pdir, dec_id = _project(tmp_path)

    st = _status(pdir)
    assert st["conclusion_channel"] is None
    d = next(x for x in st["decisions"] if x["decision_id"] == dec_id)
    assert d["requires_human"] is True
    assert d["confirmed_by_human"] is False and d["channel"] is None

    approval_ops.apply_conclude(pdir, dec_id, channel=ApprovalChannel.WEBUI_LOCAL)

    st = _status(pdir)
    assert st["conclusion_channel"] == "webui-local"
    d = next(x for x in st["decisions"] if x["decision_id"] == dec_id)
    assert d["confirmed_by_human"] is True and d["channel"] == "webui-local"


# ================================================================ 待办收集
def test_pending_in_project_reports_decision_and_plan(tmp_path):
    pdir, dec_id = _project(tmp_path)
    _, plan_id = _plan(tmp_path)
    items = approval_ops.pending_in_project(pdir)
    kinds = {i.kind for i in items}
    assert kinds == {"decision", "plan"}
    dec_item = next(i for i in items if i.kind == "decision")
    assert any("无条件需要人工" in ln for ln in dec_item.lines)
    assert dec_item.cli_command(pdir).startswith("qresearch conclude")

    # 确认后该决策从待办里消失
    approval_ops.apply_conclude(pdir, dec_id, channel=ApprovalChannel.TTY)
    after = approval_ops.pending_in_project(pdir)
    assert not [i for i in after if i.kind == "decision"]
