"""同机浏览器审批台（``qresearch approvals-web``）——**保真度低于 TTY 的通道**。

为什么存在：终端审批（``approvals.py``）要人在真实终端前敲 y，对日常使用不方便。
本模块把同一件事搬到 localhost 网页上：浏览器里点"批准/确认"。

**它不假装和 TTY 一样强**。安全性质的差别是结构性的，不是实现细节：

- TTY 守卫检查的是 ``sys.stdin.isatty()``——控制终端是**进程能力**，agent 经 DSH
  起的子进程结构性拿不到，没有任何文件可读、没有请求可发能把 TTY 变出来。
- 本通道的凭据（URL token + session cookie + CSRF）全部落在**同一个 OS 用户可读的
  信任域**内：启动时 token 打印到 stdout（agent 可读），服务只监听 127.0.0.1
  （agent 可访问）。也就是说，同用户的本地进程**理论上可以自行完成同一串请求**。
  因此本地 token/cookie 挡得住跨站请求与局域网访问，但**挡不住同机 agent**。

结论：本通道是"便利换保真度"。补偿措施是**如实记账**——本通道写的事件带
``channel="webui-local"``，报告里以独立一行渲染并标注保真度较低，读报告的人可以
自行折价。这正是它不叫 ``tty`` 的原因：审计要能区分"真人守在终端前"与
"有人在这台机器上点了按钮"。

只监听回环地址是硬约束：``--host`` 非回环一律拒绝启动，避免把审批口暴露到局域网。
"""
from __future__ import annotations

import argparse
import html
import secrets
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from qresearch.core.models import Decision, ResearchPlan
from qresearch.core.status import ApprovalChannel
from qresearch.ui import approval_ops
from qresearch.ui.approval_ops import ApprovalError, PendingItem

CHANNEL = ApprovalChannel.WEBUI_LOCAL
_COOKIE = "qr_session"

_CSS = """
:root { color-scheme: light dark; }
body { font-family: ui-sans-serif, system-ui, "Segoe UI", sans-serif;
       max-width: 60rem; margin: 2rem auto; padding: 0 1rem; line-height: 1.5; }
h1 { font-size: 1.35rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
code { background: rgba(127,127,127,.18); padding: .1rem .3rem; border-radius: .2rem; }
.card { border: 1px solid rgba(127,127,127,.4); border-radius: .5rem;
        padding: 1rem; margin: 1rem 0; }
.warn { border-left: .35rem solid #d97706; padding: .6rem .9rem;
        background: rgba(217,119,6,.10); border-radius: .25rem; }
.ok { border-left: .35rem solid #16a34a; padding: .6rem .9rem;
      background: rgba(22,163,74,.10); border-radius: .25rem; }
.bad { border-left: .35rem solid #dc2626; padding: .6rem .9rem;
       background: rgba(220,38,38,.10); border-radius: .25rem; }
ul.check { padding-left: 1.2rem; } li { margin: .25rem 0; }
.pass { color: #16a34a; } .untested { color: #d97706; } .failed { color: #dc2626; }
button { font: inherit; padding: .45rem .9rem; border-radius: .35rem;
         border: 1px solid rgba(127,127,127,.5); cursor: pointer; margin-right: .5rem; }
button.primary { background: #16a34a; color: #fff; border-color: #16a34a; }
button.danger { background: #dc2626; color: #fff; border-color: #dc2626; }
textarea { width: 100%; min-height: 3.5rem; font: inherit; margin: .5rem 0; }
.muted { opacity: .75; font-size: .9rem; }
"""


def _page(title: str, body: str) -> bytes:
    doc = (f"<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{html.escape(title)}</title><style>{_CSS}</style>"
           f"<body>{body}</body></html>")
    return doc.encode("utf-8")


def _fidelity_banner() -> str:
    return (
        "<div class='warn'><strong>这是较低保真度通道（channel=webui-local）。</strong>"
        "它与终端审批的差别是结构性的：TTY 是 agent 结构性拿不到的进程能力，"
        "而本页面的凭据位于 agent 同用户可读的信任域内。批准会如实落账为 "
        "<code>webui-local</code>，报告里单列并标注保真度较低。"
        "需要最高保真度时请用终端 <code>qresearch conclude</code>。</div>"
    )


def _esc(v) -> str:
    return html.escape(str(v))


# ---------------------------------------------------------------- 细节渲染
def _decision_card(data_root: Path, it: PendingItem) -> str:
    project_dir = data_root / it.project_id
    storage, _ = approval_ops.open_project(project_dir)
    try:
        d = storage.get(Decision, it.obj_id)
    finally:
        storage.close()
    if d is None:
        return f"<div class='card'>决策 {_esc(it.obj_id)} 已不存在。</div>"
    rows = []
    for c in d.checklist:
        cls = {"passed": "pass", "failed": "failed"}.get(c.status, "untested")
        ev = f"（evidence: <code>{_esc(c.evidence)}</code>）" if c.evidence else ""
        reason = f"<br><span class='muted'>{_esc(c.reason)}</span>" if c.reason else ""
        rows.append(f"<li class='{cls}'><strong>{_esc(c.status)}</strong> "
                    f"{_esc(c.claim)}{ev}{reason}</li>")
    rationale = _esc(d.rationale or "（未填写）")
    return f"""
<div class='card'>
  <h2>{_esc(it.title)}</h2>
  <p>类型 <code>{_esc(d.type.value)}</code>　建议
     <strong>{_esc(d.recommendation.value)}</strong>　
     人工确认 <strong>是</strong>（D6 无条件）</p>
  <h3>checklist（{len(d.checklist)} 项）</h3>
  <ul class='check'>{''.join(rows) or '<li>（无）</li>'}</ul>
  <h3>rationale</h3>
  <p class='muted'>{rationale}</p>
  <form method='post' action='/act'>
    <input type='hidden' name='csrf' value='{_esc(_csrf_placeholder())}'>
    <input type='hidden' name='kind' value='decision'>
    <input type='hidden' name='project_id' value='{_esc(it.project_id)}'>
    <input type='hidden' name='obj_id' value='{_esc(it.obj_id)}'>
    <input type='hidden' name='op' value='conclude'>
    <label>备注（可选，落账进 conclude 事件）<br>
      <textarea name='note' placeholder='例如：已核对 checklist，接受该结论'></textarea></label>
    <button class='primary' type='submit'>确认结论（conclude）</button>
    <span class='muted'>确认后报告刷新为 concluded（channel=webui-local）</span>
  </form>
</div>"""


def _plan_card(data_root: Path, it: PendingItem) -> str:
    project_dir = data_root / it.project_id
    storage, log = approval_ops.open_project(project_dir)
    try:
        p = storage.get(ResearchPlan, it.obj_id)
        events = log.events(project_id=it.project_id)
    finally:
        storage.close()
    if p is None:
        return f"<div class='card'>计划 {_esc(it.obj_id)} 已不存在。</div>"
    steps = "".join(
        f"<li><code>{_esc(s.step_id)}</code> [{_esc(s.action)}] {_esc(s.purpose)}"
        f"<span class='muted'>（工具：{_esc('、'.join(s.tools) or '—')}）"
        f"{' <strong>[需审批]</strong>' if s.requires_approval else ''}</span></li>"
        for s in p.steps)
    verdict = next((e.detail.get("verdict") for e in events
                    if e.action == "plan_ready" and e.object_id == p.plan_id), None)
    issues = [i for e in events if e.action == "plan_ready"
              and e.object_id == p.plan_id
              for i in e.detail.get("issues", []) if isinstance(i, dict)]
    issue_html = "".join(
        f"<li>{'⛔' if i.get('severity') == 'blocker' else '·'} "
        f"[{_esc(i.get('severity'))}] {_esc(i.get('step_id') or '整体')}："
        f"{_esc(i.get('description', ''))}</li>" for i in issues)
    risks = "".join(f"<li>{_esc(r)}</li>" for r in p.risks)
    diff = f"<h3>与上一版差异</h3><p class='muted'>{_esc(p.diff_summary)}</p>" \
        if p.diff_summary else ""
    return f"""
<div class='card'>
  <h2>{_esc(it.title)}（v{p.version}，{len(p.steps)} 步）</h2>
  <p>状态 <code>{_esc(p.status.value)}</code>　critic 审查：
     <strong>{_esc(verdict or '—')}</strong></p>
  <h3>步骤</h3><ul class='check'>{steps or '<li>（无）</li>'}</ul>
  {'<h3>critic 问题</h3><ul class="check">' + issue_html + '</ul>' if issue_html else ''}
  {'<h3>风险</h3><ul class="check">' + risks + '</ul>' if risks else ''}
  {diff}
  <form method='post' action='/act'>
    <input type='hidden' name='csrf' value='{_esc(_csrf_placeholder())}'>
    <input type='hidden' name='kind' value='plan'>
    <input type='hidden' name='project_id' value='{_esc(it.project_id)}'>
    <input type='hidden' name='obj_id' value='{_esc(it.obj_id)}'>
    <label>备注（可选）<br><textarea name='note'></textarea></label>
    <button class='primary' type='submit' name='op' value='approve'>批准</button>
    <button class='danger' type='submit' name='op' value='reject'>拒绝</button>
  </form>
</div>"""


# 每次渲染时填入真实 token（由 Handler 在发送前替换）。
_CSRF_SENTINEL = "\x00CSRF\x00"


def _csrf_placeholder() -> str:
    return _CSRF_SENTINEL


# ---------------------------------------------------------------- 请求处理
class _Handler(BaseHTTPRequestHandler):
    server_version = "qresearch-approvals-web"
    #: 由 main() 注入
    data_root: Path = Path(".")
    token: str = ""
    restrict_project: str | None = None
    open_browser: bool = False

    # -------------------------------------------------- 工具
    def log_message(self, *args) -> None:  # 静音默认访问日志
        pass

    def _pending(self) -> list[PendingItem]:
        if self.restrict_project:
            return approval_ops.pending_in_project(self.data_root
                                                   / self.restrict_project)
        return approval_ops.pending_in_root(self.data_root)

    def _session_ok(self) -> bool:
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == _COOKIE and secrets.compare_digest(v, self.token):
                return True
        return False

    def _send(self, body: bytes, *, status=HTTPStatus.OK, set_cookie=False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        if set_cookie:
            self.send_header("Set-Cookie",
                             f"{_COOKIE}={self.token}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, msg: str, status=HTTPStatus.FORBIDDEN) -> None:
        self._send(_page("拒绝", f"<h1>拒绝</h1><div class='bad'>{_esc(msg)}</div>"),
                   status=status)

    def _render(self, body_html: str, title: str, *, set_cookie=False) -> None:
        self._send(_page(title, body_html.replace(_CSRF_SENTINEL, self.token)),
                   set_cookie=set_cookie)

    # -------------------------------------------------- GET
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(parsed.query)
        if parsed.path not in ("/", "/index.html"):
            self._fail("未知路径。", HTTPStatus.NOT_FOUND)
            return
        # 首次进入必须带 URL token（= 启动时打印的那个），随后靠 session cookie。
        supplied = (q.get("t") or [""])[0]
        if not self._session_ok():
            if not (supplied and secrets.compare_digest(supplied, self.token)):
                self._fail("缺少或错误的访问 token。请使用启动时打印的完整 URL 打开本页。")
                return
        items = self._pending()
        cards = []
        for it in items:
            cards.append(_decision_card(self.data_root, it) if it.kind == "decision"
                         else _plan_card(self.data_root, it))
        body = [f"<h1>qresearch 审批台（localhost）</h1>", _fidelity_banner()]
        if not items:
            body.append("<div class='ok'>当前没有待办：无待审批计划、无待确认结论。</div>")
        else:
            body.append(f"<p>待办 <strong>{len(items)}</strong> 项。"
                        f"数据根目录 <code>{_esc(self.data_root)}</code></p>")
            body.extend(cards)
        body.append("<p class='muted'>本页只读渲染台账；批准动作由本服务写入 "
                    "<code>actor=HUMAN, channel=webui-local</code> 事件。"
                    "刷新本页即可看到最新待办。已由后续计划采纳执行（"
                    "<code>based_on_decision</code>）的决策不再是待办，"
                    "它们仍留在决策记录中可查。</p>")
        self._render("".join(body), "qresearch 审批台", set_cookie=True)

    # -------------------------------------------------- POST
    def do_POST(self) -> None:  # noqa: N802
        # 必须先把请求体读完再回包：body 未读完就响应，客户端会看到连接被重置
        # （Windows WinError 10053）而不是我们的 403——拒绝路径也会走这里，
        # 所以排空放在所有校验之前。
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(length) if length > 0 else b""

        if self.path != "/act":
            self._fail("未知路径。", HTTPStatus.NOT_FOUND)
            return
        if not self._session_ok():
            self._fail("缺少 session cookie。请从启动时打印的 URL 重新打开本页。")
            return
        try:
            form = urllib.parse.parse_qs(raw.decode("utf-8"))
        except UnicodeDecodeError:
            self._fail("非法请求体。", HTTPStatus.BAD_REQUEST)
            return
        get = lambda k: (form.get(k) or [""])[0]  # noqa: E731

        csrf = get("csrf")
        if not (csrf and secrets.compare_digest(csrf, self.token)):
            self._fail("CSRF token 不匹配。")
            return
        op, kind, pid = get("op"), get("kind"), get("project_id")
        obj_id, note = get("obj_id"), get("note").strip()
        project_dir = self.data_root / pid
        try:
            if op == "conclude":
                result = approval_ops.apply_conclude(project_dir, obj_id, note=note,
                                                     channel=CHANNEL)
            elif op in ("approve", "reject"):
                fn = (approval_ops.apply_approve if op == "approve"
                      else approval_ops.apply_reject)
                result = fn(project_dir, obj_id, note=note, channel=CHANNEL)
            else:
                self._fail("未知操作。", HTTPStatus.BAD_REQUEST)
                return
        except ApprovalError as exc:
            self._fail(str(exc), HTTPStatus.BAD_REQUEST)
            return
        print(f"[approvals-web] {kind} {obj_id} op={op} → channel={CHANNEL.value}")
        body = (f"<h1>已落账</h1><div class='ok'>{_esc(result.message)}</div>"
                f"<p><a href='/'>← 返回待办</a></p>"
                f"<p class='muted'>台账事件：actor=<code>human</code>，"
                f"channel=<code>{CHANNEL.value}</code>。"
                f"该通道保真度低于 TTY，报告会如此标注。</p>")
        self._send(_page("已落账", body))


# ---------------------------------------------------------------- 入口
def serve(data_root: Path, *, host: str = "127.0.0.1", port: int = 8765,
          project: str | None = None, open_browser: bool = False,
          token: str | None = None, announce: bool = True) -> ThreadingHTTPServer:
    """启动审批台（返回 server；调用方负责 serve_forever / shutdown）。

    只允许回环地址：审批口暴露到局域网等于把人工确认权交给网络。
    """
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(f"拒绝：审批台只允许监听回环地址（收到 host={host!r}）。"
                         "把审批口暴露到局域网会绕开人工在场要求。")
    data_root = Path(data_root)
    if not data_root.exists():
        raise SystemExit(f"数据根目录不存在: {data_root}")
    tok = token or secrets.token_urlsafe(24)

    handler = type("_BoundHandler", (_Handler,), {
        "data_root": data_root, "token": tok,
        "restrict_project": project, "open_browser": open_browser,
    })
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    url = f"http://{host}:{httpd.server_address[1]}/?t={tok}"
    if announce:
        # flush=True：URL 里有 token，且 stdout 被重定向时是块缓冲——
        # 不刷出来操作者就看不到入口，只能重启。
        print(f"审批台已启动：{url}", flush=True)
        print(f"  数据根目录：{data_root}"
              + (f"（仅项目 {project}）" if project else ""), flush=True)
        print(f"  通道：{CHANNEL.value}（保真度低于 TTY——台账如实记录）", flush=True)
        print("  Ctrl+C 停止。", flush=True)
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    return httpd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="qresearch approvals-web",
        description="同机浏览器审批台（localhost；保真度低于 TTY，台账记录 channel）")
    parser.add_argument("--data-root", default="research_data",
                        help="项目数据根目录（默认 ./research_data）")
    parser.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址（只允许回环：127.0.0.1/localhost/::1）")
    parser.add_argument("--project", default=None,
                        help="只服务单个 project_id（默认服务数据根下全部项目）")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    httpd = serve(Path(args.data_root), host=args.host, port=args.port,
                  project=args.project, open_browser=args.open)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n（审批台已停止；台账不变的部分保持原状）")
    finally:
        httpd.server_close()
    return 0
