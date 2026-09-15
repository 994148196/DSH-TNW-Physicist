"""qresearch CLI 会话（Phase 9.3）：像 Code Agent 一样，敲 `qresearch` 进入交互。

用法：
    qresearch                          # 数据目录默认 ./research_data
    qresearch --data-root D:/path      # 指定数据根目录
    qresearch --model deepseek-v4-flash

会话内：
    直接输入研究问题（自然语言）→ 确认后自动开跑完整闭环；
    运行中每个交互点（计划审批 / 轮末汇报）直接输入文字即可"插话"
    （计划审批处的自由文本=修改意见；轮末=注入下一轮计划；stop=叫停）；
    斜杠命令：/help /projects /status /report /plots /pause /resume /quit。

设计边界：会话层零研究决策——它只把你的话路由成 参数/反馈/命令；
研究决策仍在六站点闭环里，台账仍是唯一真相。单线程 + 终端 typeahead：
实验执行期间敲的字会在下一个交互点被读到，无需并发。
"""
from __future__ import annotations

import argparse
import signal
import sys
from datetime import datetime
from pathlib import Path

from qresearch.core.budget import Budget
from qresearch.core.events import EventLog
from qresearch.core.models import Decision, Experiment, ResearchPlan
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient
from qresearch.memory import MemoryStore
from qresearch.research_loop import resume_research_loop, run_research_loop
from qresearch.ui.progress import ConsoleProgress
from qresearch.ui.prompt import framed_prompt

_BANNER = r"""
  ____                                 __
 / __ \____  ____________      _______/  |________
/ /_/ \__  \/ ___\___  /______/ ___/\   __\_  __ \
\__, \ / __ \  \__/ /_\ \\___ \  \   |  |  |  | \/
/____/ \____/\___/_____ /____/ >   |  |__|  |__|
                       \/    \/    \  ...

qresearch —— 量子多体自主科研闭环（DSH 驱动）。直接输入研究问题即可开跑；
/help 看命令；/quit 退出。
"""

_HELP = """\
命令：
  /projects            列出数据目录下已有项目
  /status <pid>        项目进度（轮次/决策/实验/证据/报告路径）
  /report <pid>        打印研究报告的"研究总结"节
  /plots <pid>         生成三张结果变化图（需要 [viz]）
  /pause <pid>         轮间优雅暂停（PAUSE 旗标；resume 续跑）
  /resume <pid>        从台账续跑项目
  /quit                退出
开新研究：直接输入研究问题（自然语言，写清模型/观测量/对照判据）。
运行中的交互点：计划审批 y/c/e/s/q（自由文本=修改意见）；
轮末 回车继续 / 输入意见 / stop 叫停。Ctrl+C 一次=轮末优雅停，两次=立即中断。
"""


class Session:
    """交互会话：REPL + 项目运行。逻辑拆成小方法便于测试。"""

    def __init__(self, data_root: str | Path, *, model: str | None = None,
                 rounds: int = 3, max_experiments: int = 48,
                 max_parallel: int = 4, retries: int = 2,
                 n_hypotheses: int = 3, input_fn=None, print_fn=print):
        self.data_root = Path(data_root)
        self.model = model
        self.rounds = rounds
        self.max_experiments = max_experiments
        self.max_parallel = max_parallel
        self.retries = retries
        self.n_hypotheses = n_hypotheses
        self._input = input_fn or input
        self._print = print_fn
        self.stop_requested = False     # 轮末优雅停（Ctrl+C 一次）
        self.in_run = False             # 是否处于 run_research_loop 内

    # ---------------------------------------------------------------- 项目布局
    def project_dir(self, pid: str) -> Path:
        return self.data_root / pid

    def _open(self, pid: str) -> tuple[DSHClient, Storage, EventLog, MemoryStore]:
        d = self.project_dir(pid)
        (d / "sandbox").mkdir(parents=True, exist_ok=True)
        client = DSHClient(cwd=d / "sandbox", dsh_home=d / "dsh_home", model=self.model)
        storage = Storage(d / "state.sqlite")
        log = EventLog(d / "events.jsonl")
        memory = MemoryStore(self.data_root / "memory.sqlite",
                             markdown_path=self.data_root / "memory.md")
        return client, storage, log, memory

    @staticmethod
    def _close(client, storage, memory) -> None:
        client.close()
        storage.close()
        memory.close()

    # ---------------------------------------------------------------- 命令
    def cmd_projects(self) -> list[str]:
        pids = sorted({p.parent.name for p in self.data_root.glob("*/state.sqlite")})
        self._print("已有项目：" + ("、".join(pids) if pids else "（无）"))
        return pids

    def cmd_status(self, pid: str) -> str:
        storage = Storage(self.project_dir(pid) / "state.sqlite")
        try:
            plans = sorted(storage.list(ResearchPlan, project_id=pid),
                           key=lambda p: p.version)
            experiments = storage.list(Experiment, project_id=pid)
            decisions = storage.list(Decision, project_id=pid)
            from qresearch.core.models import Evidence
            n_ev = len(storage.list(Evidence, project_id=pid))
        finally:
            storage.close()
        done = sum(1 for e in experiments if e.status.value == "completed")
        failed = sum(1 for e in experiments if e.status.value == "failed")
        rec = " → ".join(d.recommendation.value for d in decisions) or "（无决策）"
        report = self.project_dir(pid) / "report.md"
        lines = [
            f"项目 {pid}：计划 v{[p.version for p in plans] or '—'}，"
            f"决策链 {rec}（{len(decisions)} 轮）",
            f"实验 {len(experiments)} 个（完成 {done} / 失败 {failed}），证据 {n_ev} 条",
            f"报告：{report}{'（存在）' if report.exists() else '（尚未生成）'}",
        ]
        text = "\n".join(lines)
        self._print(text)
        return text

    def cmd_report(self, pid: str) -> str:
        path = self.project_dir(pid) / "report.md"
        if not path.exists():
            self._print(f"（{pid} 还没有报告——先跑完或 /status 看进度）")
            return ""
        text = path.read_text(encoding="utf-8")
        start = text.index("## 研究总结")
        nxt = text.find("\n## ", start + 1)
        summary = text[start:nxt if nxt > 0 else None].rstrip()
        self._print(summary)
        return summary

    def cmd_plots(self, pid: str) -> list[Path]:
        from qresearch.viz import plot_project
        storage = Storage(self.project_dir(pid) / "state.sqlite")
        try:
            paths = plot_project(storage, pid, self.project_dir(pid) / "plots")
        finally:
            storage.close()
        self._print("已生成：" + "\n  ".join(str(p) for p in paths))
        return paths

    def cmd_pause(self, pid: str) -> Path:
        flag = self.project_dir(pid) / "PAUSE"
        flag.touch()
        self._print(f"已设 PAUSE 旗标：{flag}（当前轮做完即暂停；/resume {pid} 续跑）")
        return flag

    def cmd_resume(self, pid: str) -> dict:
        rounds = self._ask_int(f"总轮数上限（含已完成，回车默认 {self.rounds + 2}）：",
                               default=self.rounds + 2)
        client, storage, log, memory = self._open(pid)
        try:
            with ConsoleProgress(log):
                summary = resume_research_loop(
                    client, storage, log, pid,
                    rounds=rounds, auto_approve=False, retries=self.retries,
                    experiments_root=self.project_dir(pid) / "experiments",
                    memory_store=memory,
                    budget=Budget(max_rounds=rounds,
                                  max_experiments=self.max_experiments),
                    max_parallel_experiments=self.max_parallel,
                )
        finally:
            self._close(client, storage, memory)
        self._print_summary(summary)
        return summary

    # ---------------------------------------------------------------- 运行
    def _ask_int(self, prompt: str, *, default: int) -> int:
        raw = self._input(framed_prompt(prompt)).strip()
        return int(raw) if raw.isdigit() else default

    def _round_callback(self, digest: dict) -> str | None:
        if self.stop_requested:
            return "stop"
        d = digest["decision"]
        self._print(f"\n【节点汇报】第 {digest['round_no']} 轮收尾：决策 "
                    f"{d['recommendation']}（本轮实验 {digest['experiments_this_round']}"
                    f" 个、新证据 {digest['evidence_this_round']} 条，"
                    f"累计 {digest['evidence_total']} 条）")
        try:
            ans = self._input(framed_prompt(
                "回车继续 / 输入修改意见（注入下一轮计划）/ stop 叫停")).strip()
        except KeyboardInterrupt:
            self._print("（轮末停止；resume_research_loop 可续跑）")
            return "stop"
        return ans or None

    def start_project(self, question: str, pid: str | None = None) -> dict | None:
        """确认问题 → 完整交互式闭环（计划文档/审批/轮末插话全部在线）。

        确认环（2026-09-15 实测教训）：在确认提示符处输入的非 y 文本**就地成为
        新的研究问题**并重新确认——而不是被当成对 y/n 的无效回答丢掉。此前 'y'
        这类碎片在主提示符被当问题建了项目，真实问题却在确认提示符处被吞。
        """
        if len(question.strip()) < 8:
            self._print(f"（这不像完整的研究问题：{question!r}——请重新描述）")
            question = ""
        pid = pid or f"proj_{datetime.now():%Y%m%d_%H%M%S}"
        self._print(f"项目 id：{pid}（数据目录 {self.project_dir(pid)}）")
        while True:
            if not question:
                question = self._input(framed_prompt(
                    "研究问题（自然语言，写清模型/观测量/对照判据）")).strip()
                if not question:
                    self._print("（已取消——未创建任何项目）")
                    return None
            self._print(f"研究问题：{question}")
            ans = self._input(framed_prompt(
                "[回车]取消 / [y]以此问题开跑 / 直接输入修正后的问题")).strip()
            if not ans:
                self._print("（已取消——未创建任何项目）")
                return None
            if ans.lower().startswith("y"):
                break
            question = ans  # 就地修正，重新确认
        client, storage, log, memory = self._open(pid)
        self.in_run = True
        self.stop_requested = False
        try:
            with ConsoleProgress(log):
                summary = run_research_loop(
                    client, storage, log, pid, question,
                    rounds=self.rounds,
                    auto_approve=False,          # 交互式：计划等你审批
                    n_hypotheses=self.n_hypotheses,
                    retries=self.retries,
                    experiments_root=self.project_dir(pid) / "experiments",
                    memory_store=memory,
                    budget=Budget(max_rounds=self.rounds,
                                  max_experiments=self.max_experiments),
                    max_parallel_experiments=self.max_parallel,
                    round_callback=self._round_callback,
                )
        finally:
            self.in_run = False
            self._close(client, storage, memory)
        self._print_summary(summary)
        return summary

    def _print_summary(self, summary: dict) -> None:
        self._print("=== SUMMARY ===")
        for k, v in summary.items():
            self._print(f"{k}: {v}")

    # ---------------------------------------------------------------- REPL
    def run_command(self, line: str) -> bool:
        """斜杠命令。返回 False 表示退出会话。"""
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""
        if cmd in ("/quit", "/exit", "/q"):
            return False
        if cmd == "/help":
            self._print(_HELP)
        elif cmd == "/projects":
            self.cmd_projects()
        elif cmd == "/status":
            pid = self._require_pid(arg)
            if pid:
                self.cmd_status(pid)
        elif cmd == "/report":
            pid = self._require_pid(arg)
            if pid:
                self.cmd_report(pid)
        elif cmd == "/plots":
            pid = self._require_pid(arg)
            if pid:
                self.cmd_plots(pid)
        elif cmd == "/pause":
            pid = self._require_pid(arg)
            if pid:
                self.cmd_pause(pid)
        elif cmd == "/resume":
            pid = self._require_pid(arg)
            if pid:
                self.cmd_resume(pid)
        else:
            self._print(f"未知命令 {cmd}；/help 看命令列表。")
        return True

    def _install_sigint(self) -> None:
        try:
            signal.signal(signal.SIGINT, self._on_sigint)
        except ValueError:  # 非主线程（测试环境）——跳过
            pass

    def _on_sigint(self, signum, frame) -> None:
        if self.in_run and not self.stop_requested:
            self.stop_requested = True
            self._print("（收到 Ctrl+C：将在轮末优雅停止；再按一次立即中断）")
        else:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            raise KeyboardInterrupt

    def _require_pid(self, arg: str) -> str:
        if arg:
            return arg
        pids = sorted({p.parent.name for p in self.data_root.glob("*/state.sqlite")})
        if len(pids) == 1:
            return pids[0]
        if not pids:
            return ""
        self._print(f"多个项目，请指定：{'、'.join(pids)}")
        return ""

    def repl(self) -> None:
        self._print(_BANNER)
        while True:
            try:
                line = self._input(framed_prompt(
                    "qresearch —— 输入研究问题，或 / 命令")).strip()
            except (EOFError, KeyboardInterrupt):
                self._print()
                return
            if not line:
                continue
            if line.startswith("/"):
                if not self.run_command(line):
                    return
                continue
            if len(line) < 8:
                # 2026-09-15 实测：主提示符的碎片输入（如 'y'）会被当成研究问题
                # 建项目开跑——垃圾进、垃圾出，烧掉整轮站点调用。宁多问一句。
                self._print("（这不像研究问题（太短）——直接输入完整问题；"
                            "/help 看命令）")
                continue
            try:
                self._install_sigint()
                self.start_project(line)
            except KeyboardInterrupt:
                self._print("\n（已中断——台账安全，/resume <pid> 可续跑）")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 审批台子命令（A1：actor=HUMAN 审批事件的写入口）优先分发。
    # approvals/approve/reject/conclude 走 TTY 通道；approvals-web 走 localhost
    # 浏览器通道（保真度较低，台账如实记录 channel）。MCP 侧只查台账，不代写。
    if argv and argv[0] in ("approvals", "approve", "reject", "conclude",
                            "approvals-web"):
        from qresearch.ui import approvals

        return approvals.main(argv)
    parser = argparse.ArgumentParser(
        prog="qresearch", description="量子多体自主科研闭环——交互式会话")
    parser.add_argument("--data-root", default="research_data",
                        help="项目数据根目录（默认 ./research_data）")
    parser.add_argument("--model", default=None, help="模型覆盖（默认 deepseek-v4-flash）")
    parser.add_argument("--rounds", type=int, default=3, help="默认总轮数")
    parser.add_argument("--max-experiments", type=int, default=48,
                        help="实验数预算（每项目）")
    parser.add_argument("--max-parallel", type=int, default=4, help="并行实验数")
    args = parser.parse_args(argv)

    session = Session(args.data_root, model=args.model, rounds=args.rounds,
                      max_experiments=args.max_experiments,
                      max_parallel=args.max_parallel)

    session.repl()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
