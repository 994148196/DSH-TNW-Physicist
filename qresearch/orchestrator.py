"""多项目编排（Phase 8）：项目队列 + 每项目沙箱隔离 + 暂停旗标。

队列语义：项目按序执行（LLM 站点是瓶颈，串行队列即可饱和）；
项目内实验可并行（max_parallel_experiments）。每个项目独立
state.sqlite / events.jsonl / sandbox（DSH 站内 agent 的写入沙箱收口——
Phase 2 发现的"站内 agent 写仓库根"问题在此默认关闭）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from qresearch.core.budget import Budget
from qresearch.core.events import EventLog
from qresearch.core.storage import Storage
from qresearch.research_loop import run_research_loop


@dataclass
class ProjectJob:
    """一个研究项目的队列条目。"""

    project_id: str
    question: str
    rounds: int = 3
    max_parallel_experiments: int = 1
    budget: Budget | None = None
    n_hypotheses: int = 3
    extra: dict = field(default_factory=dict)  # 预留（Phase 9+ 的调度参数）


def run_projects(
    client_factory: Callable[[Path], object],
    jobs: list[ProjectJob],
    *,
    data_root: str | Path,
    memory_store=None,
    retries: int = 1,
    auto_approve: bool = False,
) -> dict[str, dict]:
    """按队列执行项目；返回 {project_id: summary}。

    client_factory(sandbox_dir) -> DSHClient：每项目以 cwd=沙箱 新建 runtime
    （真实 LLM）或离线脚本客户端（测试）。auto_approve 默认 False——
    长期运行的队列不默认越过人工审批（actor 留痕规则不变）。
    每项目目录布局：<data_root>/<project_id>/{state.sqlite, events.jsonl,
    sandbox/, experiments/, report.md, PAUSE}
    """
    results: dict[str, dict] = {}
    for job in jobs:
        pdir = Path(data_root) / job.project_id
        pdir.mkdir(parents=True, exist_ok=True)
        sandbox = pdir / "sandbox"
        sandbox.mkdir(exist_ok=True)
        storage = Storage(pdir / "state.sqlite")
        log = EventLog(pdir / "events.jsonl")
        client = client_factory(sandbox)
        try:
            results[job.project_id] = run_research_loop(
                client, storage, log, job.project_id, job.question,
                rounds=job.rounds, auto_approve=auto_approve,
                n_hypotheses=job.n_hypotheses, retries=retries,
                memory_store=memory_store, budget=job.budget,
                pause_flag=pdir / "PAUSE",
                max_parallel_experiments=job.max_parallel_experiments,
            )
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                close()
            storage.close()
    return results
