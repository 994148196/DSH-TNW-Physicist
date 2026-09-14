"""进程内异步 job 注册表（计划 v3 M1 / 评审修订 A2+A3）。

只服务 MCP 层：长工具（站点调用与实验 alike，A2）提交后立即返回 job_id，
调用方用 status/result 轮询——MCP 工具调用默认 60s 超时，同步返回必超时，
agent 会误判失败并重跑，台账出现重复实验（D4）。

边界（A3）：壳 B（run_research_loop / run_projects）**不经过**本模块——引擎核心
方法全同步，事件时序与重构前严格一致。job 生命周期是进程内状态（不落台账）；
台账事件由 job 内部执行的引擎方法照常落账，不因异步化而改变。

取消（D4/M3）：pending 直接取消；running 置 cancel_requested 并如实上报，
实验级协作式取消在 M3 落地（experiments_run 的 cancel_check 检查点：
取消后不再新建实验账目，已建账的照常完成）——不谎报"已取消"。
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

# job 状态机：pending → running → done | error；pending/running → cancelled
PENDING, RUNNING, DONE, ERROR, CANCELLED = "pending", "running", "done", "error", "cancelled"
_ACTIVE = (PENDING, RUNNING)


@dataclass
class Job:
    job_id: str
    fn: Callable
    args: tuple
    kwargs: dict
    key: str | None = None           # 幂等键（如 "experiments:<plan_id>"，D4）
    state: str = PENDING
    result: Any = None
    error: str | None = None
    cancel_requested: bool = False
    progress_done: int | None = None
    progress_total: int | None = None
    last_event: str | None = None
    submitted_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    finished_at: float | None = None


class UnknownJob(KeyError):
    """job_id 不存在（从未提交或注册表已清理）。"""


class JobManager:
    """同 key 活跃 job 重复提交幂等返回既有 id（防重复实验，D4）。"""

    def __init__(self, max_workers: int = 2):
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix="qresearch-job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._seq = 0

    # -------------------------------------------------- 提交与轮询
    def submit(self, fn: Callable, *args, key: str | None = None,
               total: int | None = None, **kwargs) -> str:
        with self._lock:
            if key is not None:
                for job in self._jobs.values():
                    if job.key == key and job.state in _ACTIVE:
                        return job.job_id
            self._seq += 1
            job = Job(job_id=f"job_{self._seq:06d}_{uuid.uuid4().hex[:6]}",
                      fn=fn, args=args, kwargs=kwargs, key=key, progress_total=total)
            self._jobs[job.job_id] = job
        self._pool.submit(self._run, job)
        return job.job_id

    def status(self, job_id: str) -> dict:
        j = self._get(job_id)
        elapsed_to = j.finished_at if j.finished_at is not None else time.monotonic()
        return {
            "job_id": j.job_id, "state": j.state,
            "cancel_requested": j.cancel_requested,
            "done": j.progress_done, "total": j.progress_total,
            "last_event": j.last_event,
            "elapsed_s": round(elapsed_to - j.submitted_at, 3),
            "error": j.error,
        }

    def result(self, job_id: str) -> dict:
        j = self._get(job_id)
        if j.state in _ACTIVE:
            return {"job_id": j.job_id, "state": j.state,
                    "note": "job 未完成：请继续 job_status 轮询"}
        return {"job_id": j.job_id, "state": j.state, "result": j.result, "error": j.error}

    # -------------------------------------------------- 协作式取消（M3）
    def cancel_requested(self, job_id: str) -> bool:
        """长任务在检查点轮询此方法（实验级协作式取消：只停"未开工"的）。"""
        return self._get(job_id).cancel_requested

    def find_active(self, key: str) -> str | None:
        """按幂等键找活跃 job 的 id（找不到返回 None）。"""
        with self._lock:
            for job in self._jobs.values():
                if job.key == key and job.state in _ACTIVE:
                    return job.job_id
        return None

    # -------------------------------------------------- 取消与进度
    def cancel(self, job_id: str) -> bool:
        """pending → 取消成功（True）；running → 置取消请求（返回 False，如实上报）；
        终态 → False。"""
        with self._lock:
            j = self._jobs.get(job_id)
            if j is None:
                raise UnknownJob(job_id)
            if j.state == PENDING:
                j.cancel_requested = True
                j.state = CANCELLED
                j.finished_at = time.monotonic()
                return True
            if j.state == RUNNING:
                j.cancel_requested = True
                return False
        return False

    def set_progress(self, job_id: str, *, done: int | None = None,
                     total: int | None = None, last_event: str | None = None) -> None:
        """供长任务汇报进度（M3 协作式取消的检查点也读这里）。"""
        j = self._get(job_id)
        if done is not None:
            j.progress_done = done
        if total is not None:
            j.progress_total = total
        if last_event is not None:
            j.last_event = last_event

    # -------------------------------------------------- internals
    def _get(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise UnknownJob(job_id)
        return job

    def _run(self, job: Job) -> None:
        with self._lock:  # 与 cancel 串行化 pending→running 的迁移，防取消竞态
            if job.cancel_requested:
                job.state = CANCELLED
                job.finished_at = time.monotonic()
                return
            job.state = RUNNING
            job.started_at = time.monotonic()
        try:
            job.result = job.fn(*job.args, **job.kwargs)
            job.state = DONE
        except BaseException as e:  # noqa: BLE001 —— 失败也要可查询，不在 worker 里炸
            job.error = f"{type(e).__name__}: {e}"
            job.state = ERROR
        finally:
            job.finished_at = time.monotonic()
