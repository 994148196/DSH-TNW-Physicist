"""终端进度显示（Phase 9.1）：订阅 EventLog，实时渲染"正在干什么"。

rich 可用时用 Live 单行刷新（spinner + 计时 + 当前动作 + 统计行）；
缺失时退化为纯文本刷新。显示层零写账——只读事件流，任何错误
都被 EventLog.subscribe 吞掉，绝不干扰研究闭环（铁律：显示是视图）。

用法：
    with ConsoleProgress(event_log):
        run_research_loop(client, storage, log, pid, question, ...)
"""
from __future__ import annotations

import time
from datetime import datetime

from qresearch.core.events import Event, EventLog

# 持久打印的里程碑事件（其余事件只刷新状态行）
_MILESTONES = {"plan_ready", "approve", "reject", "plan_feedback", "user_feedback",
               "user_stop", "decide", "needs_human", "paused", "resumed",
               "budget_exhausted", "report_generated", "station_retry"}


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


class ConsoleProgress:
    """事件驱动的终端进度条。attach(event_log) 后用 with 进入/退出。"""

    def __init__(self, event_log: EventLog | None = None):
        self._t0 = time.monotonic()
        self._status = ""
        self._round = 0
        self._running: dict[str, str] = {}      # exp_id -> tool（并行中的实验）
        self._n_done = 0
        self._n_failed = 0
        self._n_verified = 0
        self._live = None                        # rich Live
        self._plain_last_len = 0
        try:
            from rich.console import Console
            from rich.live import Live
            self._console = Console()
            self._live_cls = Live
            self._rich = True
        except ImportError:
            self._console = None
            self._rich = False
        if event_log is not None:
            self.attach(event_log)

    # ---------------------------------------------------------------- 生命周期
    def attach(self, event_log: EventLog) -> None:
        event_log.subscribe(self._on_event)

    def __enter__(self) -> "ConsoleProgress":
        self._t0 = time.monotonic()
        if self._rich:
            self._live = self._live_cls(
                _Dynamic(self), refresh_per_second=8, transient=False,
                console=self._console)
            self._live.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        if self._live is not None:
            self._live.__exit__(*exc)
            self._live = None
        self._clear_plain()

    # ---------------------------------------------------------------- 事件处理
    def _on_event(self, event: Event) -> None:
        a = event.action
        d = event.detail
        if a == "station_started":
            self._status = (f"站点 {event.object_id} 调用中"
                            f"（第 {d.get('attempt', 0) + 1} 次尝试）")
        elif a == "station_retry":
            self._status = f"站点 {event.object_id} 异常重试：{d.get('error', '')[:70]}"
        elif a == "station_call":
            self._status = ""
        elif a == "experiment_started":
            self._running[event.object_id or "?"] = d.get("tool", "?")
            self._status = f"实验运行中 ×{len(self._running)}（最近：{event.object_id}）"
        elif a == "experiment_finished":
            self._running.pop(event.object_id, None)
            if d.get("status") == "failed":
                self._n_failed += 1
            else:
                self._n_done += 1
            self._print(f"  [{_ts()}] 实验 {event.object_id}"
                        f"（{d.get('tool', '?')}）→ {d.get('status')}"
                        f"（{d.get('elapsed_s', '?')}s）")
        elif a == "verify_experiment":
            self._n_verified += 1
            if d.get("overall") != "passed":
                self._print(f"  [{_ts()}] 验证未通过：{d.get('experiment')}"
                            f"（overall={d.get('overall')}）")
        elif a == "plan_ready" or a == "decide":
            self._round = d.get("round", self._round)
        if a in _MILESTONES:
            line = self._milestone_line(event)
            if line:
                self._print(line)
        self._refresh()

    def _milestone_line(self, event: Event) -> str | None:
        """里程碑事件 → 一行持久文本（None=只刷状态行）。"""
        a, d = event.action, event.detail
        if a == "plan_ready":
            return f"[{_ts()}] 计划 v{d.get('version', '?')} 已生成（critic：{d.get('verdict', '—')}）"
        if a == "approve":
            return f"[{_ts()}] 计划已批准（by {event.actor.value}）"
        if a == "decide":
            return (f"[{_ts()}] 决策：{d.get('recommendation', '?')}"
                    f"——{str(d.get('rationale', ''))[:80]}")
        if a == "station_retry":
            return f"[{_ts()}] 站点重试（{event.object_id}）：{d.get('error', '')[:90]}"
        if a in _MILESTONES:
            return f"[{_ts()}] {a}"
        return None

    # ---------------------------------------------------------------- 渲染
    def _stats(self) -> str:
        elapsed = int(time.monotonic() - self._t0)
        mm, ss = divmod(elapsed, 60)
        head = f"第 {self._round} 轮" if self._round else "准备中"
        run = f" | 运行中 {len(self._running)}" if self._running else ""
        return (f"{head} | 实验 完成 {self._n_done} / 失败 {self._n_failed}{run}"
                f" | 验证 {self._n_verified} | 已用 {mm:02d}:{ss:02d}")

    def _build_renderable(self):
        from rich.console import Group
        from rich.spinner import Spinner
        from rich.text import Text
        status = self._status or "等待事件…"
        return Group(
            Spinner("dots", text=Text(" " + status)),
            Text(self._stats(), style="dim"),
        )

    def _refresh(self) -> None:
        if self._live is not None:
            return  # rich Live 按 refresh_per_second 自刷
        # 纯文本退化：单行 \r 刷新
        import sys
        line = f"… {self._status or '运行中'}  |  {self._stats()}"
        pad = max(0, self._plain_last_len - len(line))
        sys.stdout.write("\r" + line + " " * pad)
        sys.stdout.flush()
        self._plain_last_len = len(line)

    def _print(self, text: str) -> None:
        """持久打印一行（rich 模式下打印在 Live 区域上方）。"""
        self._clear_plain()
        if self._console is not None and self._live is not None:
            self._console.print(text)
        else:
            print(text)

    def _clear_plain(self) -> None:
        if self._live is None and self._plain_last_len:
            import sys
            sys.stdout.write("\r" + " " * self._plain_last_len + "\r")
            sys.stdout.flush()
            self._plain_last_len = 0


class _Dynamic:
    """rich 的动态渲染包装：每次刷新都重新取当前状态（计时器随之跳动）。"""

    def __init__(self, progress: "ConsoleProgress"):
        self._p = progress

    def __rich_console__(self, console, options):
        yield self._p._build_renderable()

