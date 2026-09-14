"""qresearch MCP server（计划 v3 M2）：把 ResearchEngine 能力暴露为受约束的 MCP 工具。

接入：DSH 是 MCP client（`@deepseek-ai/dsh-mcp-client`，stdio），工具自动名为
`mcp__qresearch__<tool>`。本进程内嵌自己的 DSHClient 跑站点（A7）——交互会话是壳，
引擎是智能层，出题人≠答题人与站点纪律原样保留。

约束（评审修订）：
- A1 人工审批走"台账仲裁 + CLI 审批台 + TTY 守卫"：本服务器的 plan_approve **不写**
  批准，只查台账 actor=HUMAN 事件；写入口 = `qresearch approve <project_dir> <plan_id>`。
- A2 长工具（站点调用与实验 alike）一律异步：返回 job_id，job_status/job_result 轮询；
  同 key 活跃 job 幂等（D4 防重复实验）。
- A3 引擎核心全同步；异步只是本层薄封装。
- D6 无独立 declare_result 工具：结论只能经 decide 站点产生（带证据 checklist 校验），
  其 declare_result 建议被引擎无条件置 requires_human=True——agent 没有"自己宣布结论"
  的旁路（比计划原设计更严：少一条可被滥用的工具面）。

目录契约（与 orchestrator 一致）：<project-root>/<project_id>/{state.sqlite, events.jsonl,
sandbox/, experiments/, report.md}。

启动（profile patch 调用形如）：
  python -m qresearch.mcp_server --project-root D:/AI/Agent/Try/research-projects
"""
from __future__ import annotations

import argparse
import atexit
from functools import wraps
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from qresearch.core.events import EventLog
from qresearch.core.models import Decision, Experiment, Project, ResearchPlan
from qresearch.core.status import Actor, ExperimentStatus, PlanStatus, VerificationStatus
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient
from qresearch.engine import ResearchEngine
from qresearch.ui.plan_doc import render_plan_markdown

server = MCPServer(
    name="qresearch",
    instructions=(
        "qresearch：量子多体自主科研引擎。铁律：实验不经 LLM、出题人≠答题人、"
        "结论确认无条件人工、一切可回放。标准流程：research_open → understand → "
        "hypothesize → plan_create → plan_show → (人工批准) → experiments_run → "
        "verify → analyze → decide → report_generate。长工具返回 job_id，"
        "用 job_status 轮询、job_result 取果。产生数值的计划外动作必须先 "
        "adhoc_record 登记（默认未验证）；引用前先 adhoc_verify。"
    ),
)


# ================================================================ 进程级上下文
class _Ctx:
    projects_root: Path | None = None
    model: str | None = None
    # project_id → (engine, storage, event_log, lazy_client)
    _cache: dict[str, tuple] = {}


def _err(e: Exception) -> dict:
    return {"error": f"{type(e).__name__}: {e}"}


def _tool(fn):
    """统一错误包装：异常转可读 error dict（模型能看到原因并自行纠正）。"""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 —— 工具面不抛裸异常
            return _err(e)
    return wrapper


class _LazyClient:
    """按项目惰性创建 DSHClient（读工具不 spawn runtime 子进程）。"""

    def __init__(self, project_dir: Path, model: str | None):
        self._project_dir = project_dir
        self._model = model
        self._client: DSHClient | None = None

    def _get(self) -> DSHClient:
        if self._client is None:
            sandbox = self._project_dir / "sandbox"
            sandbox.mkdir(parents=True, exist_ok=True)
            self._client = DSHClient(cwd=sandbox, model=self._model)
        return self._client

    def __getattr__(self, name):
        return getattr(self._get(), name)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def _open(project_id: str, *, must_exist: bool = True) -> ResearchEngine:
    """打开（或复用）项目引擎：storage/log/sandbox 按 orchestrator 目录契约。"""
    if _Ctx.projects_root is None:
        raise RuntimeError("server 未初始化（缺 --project-root）")
    project_dir = _Ctx.projects_root / project_id
    cached = _Ctx._cache.get(project_id)
    if cached is not None:
        return cached[0]
    if must_exist and not (project_dir / "state.sqlite").exists():
        raise FileNotFoundError(
            f"项目 {project_id} 不存在（{project_dir}）；先 research_open 创建")
    project_dir.mkdir(parents=True, exist_ok=True)
    storage = Storage(project_dir / "state.sqlite")
    event_log = EventLog(project_dir / "events.jsonl")
    client = _LazyClient(project_dir, _Ctx.model)
    engine = ResearchEngine(
        storage, event_log, client,
        experiments_root=project_dir / "experiments")
    _Ctx._cache[project_id] = (engine, storage, event_log, client)
    return engine


def _close_all() -> None:
    for _engine, storage, _log, client in _Ctx._cache.values():
        try:
            client.close()
        except Exception:  # noqa: BLE001 —— 收尾尽力而为
            pass
        try:
            storage.close()
        except Exception:  # noqa: BLE001
            pass
    _Ctx._cache.clear()


def _submit(engine: ResearchEngine, fn, *args, key: str | None = None, **kwargs) -> dict:
    job_id = engine.jobs.submit(fn, *args, key=key, **kwargs)
    return {"job_id": job_id, "state": "pending",
            "hint": "长任务已提交：轮询 job_status(job_id) 至 done/error，"
                    "再 job_result(job_id) 取结果"}


def _plan_digest(engine: ResearchEngine, plan: ResearchPlan, critique) -> dict:
    issues = [i.model_dump() for i in critique.issues]
    return {
        "plan_id": plan.plan_id, "version": plan.version,
        "status": plan.status.value, "n_steps": len(plan.steps),
        "steps": [{"step_id": s.step_id, "action": s.action, "purpose": s.purpose,
                   "tools": s.tools, "inputs": s.inputs,
                   "requires_approval": s.requires_approval} for s in plan.steps],
        "risks": plan.risks, "diff_summary": plan.diff_summary,
        "critic_verdict": critique.verdict, "critic_issues": issues,
        "awaiting_approval": plan.status == PlanStatus.AWAITING_APPROVAL,
    }


# ================================================================ 项目工具
@server.tool()
@_tool
def research_open(project_id: str, question: str) -> dict:
    """打开或创建研究项目（幂等）。question 为自然语言科研问题。返回项目状态。"""
    if _Ctx.projects_root is None:
        raise RuntimeError("server 未初始化")
    engine = _open(project_id, must_exist=False)
    if engine.storage.get(Project, project_id) is None:
        engine.open_project(project_id, question)
    return engine.status(project_id)


@server.tool()
@_tool
def research_status(project_id: str) -> dict:
    """项目状态投影（只读）：目标量/轮次/计划/实验/证据/决策。会话开始先调它对齐。"""
    return _open(project_id).status(project_id)


@server.tool()
@_tool
def research_list() -> dict:
    """列出 project-root 下全部项目（只读）。"""
    if _Ctx.projects_root is None:
        raise RuntimeError("server 未初始化")
    items = []
    for d in sorted(_Ctx.projects_root.iterdir()):
        if (d / "state.sqlite").exists():
            items.append({"project_id": d.name, "dir": str(d)})
    return {"projects": items}


# ================================================================ 站点工具（异步，A2）
@server.tool()
@_tool
def understand(project_id: str) -> dict:
    """UNDERSTAND 站点（异步 job）：把研究问题打磨成结构化 Goal（目标量+成功判据）。"""
    eng = _open(project_id)
    return _submit(eng, eng.understand, project_id, key=f"understand:{project_id}")


@server.tool()
@_tool
def hypothesize(project_id: str, n: int = 3) -> dict:
    """HYPOTHESIZE 站点（异步 job）：提出 n 条可证伪假设。"""
    eng = _open(project_id)
    return _submit(eng, eng.hypothesize, project_id, n=n,
                   key=f"hypothesize:{project_id}")


@server.tool()
@_tool
def plan_create(project_id: str, user_notes: str = "",
                previous_plan_id: str | None = None) -> dict:
    """PLAN 站点（异步 job）：制定计划并过 critic。user_notes=研究者的修改意见
    （最高优先级注入）。返回 job，job_result 含 plan 摘要 + critic issues。"""
    eng = _open(project_id)
    return _submit(eng, _plan_job, eng, project_id,
                   user_notes=user_notes or "", previous_plan_id=previous_plan_id,
                   key=f"plan:{project_id}")


@server.tool()
@_tool
def plan_revise(project_id: str, user_notes: str,
                previous_plan_id: str | None = None) -> dict:
    """按研究者意见修订计划（异步 job）：previous_plan_id 缺省取最新版。"""
    eng = _open(project_id)
    if previous_plan_id is None:
        plans = sorted(eng.storage.list(ResearchPlan, project_id=project_id),
                       key=lambda p: p.version)
        if not plans:
            raise ValueError("项目尚无计划可修订")
        previous_plan_id = plans[-1].plan_id
    return _submit(eng, _plan_job, eng, project_id, user_notes=user_notes,
                   previous_plan_id=previous_plan_id,
                   key=f"plan:{project_id}:{previous_plan_id}")


def _plan_job(engine: ResearchEngine, project_id: str, *, user_notes: str,
              previous_plan_id: str | None) -> dict:
    plan, critique = engine.plan_create(
        project_id, user_notes=user_notes, previous_plan_id=previous_plan_id)
    return _plan_digest(engine, plan, critique)


# ================================================================ 审批（A1 台账仲裁）
@server.tool()
@_tool
def plan_show(project_id: str, plan_id: str | None = None) -> dict:
    """计划卡片（只读，给人看）：步骤表/风险/critic blocker/版本 + markdown 字段
    （M3：目标量/步骤表/风险/blocker/diff，Web UI 直接渲染）。缺省最新版。"""
    eng = _open(project_id)
    plan = _latest_plan(eng, project_id, plan_id)
    verdict: str | None = None
    issues: list[dict] = []
    for e in eng.event_log.events(project_id=project_id):
        if e.action == "plan_ready" and e.object_id == plan.plan_id:
            verdict = e.detail.get("verdict")
            issues = e.detail.get("issues", [])
    blockers = [i for i in issues if i.get("severity") == "blocker"]
    st = eng.state(project_id)
    eng._sync_state_from_ledger(project_id, st)
    project = eng.storage.get(Project, project_id)
    markdown = render_plan_markdown(
        plan, goal=st.goal, hypotheses=st.hypotheses,
        critique_verdict=verdict, critique_issues=issues,
        project_title=project.title if project else None, edit_hint=False)
    return {
        "plan_id": plan.plan_id, "version": plan.version, "status": plan.status.value,
        "based_on_decision": plan.based_on_decision, "diff_summary": plan.diff_summary,
        "risks": plan.risks,
        "steps": [{"step_id": s.step_id, "action": s.action, "purpose": s.purpose,
                   "tools": s.tools, "inputs": s.inputs,
                   "expected_outputs": s.expected_outputs,
                   "requires_approval": s.requires_approval} for s in plan.steps],
        "critic_verdict": verdict,
        "critic_issues": issues, "n_blockers": len(blockers),
        "human_approval": _human_approval_state(eng, project_id, plan.plan_id),
        "markdown": markdown,
    }


def _latest_plan(engine: ResearchEngine, project_id: str, plan_id: str | None) -> ResearchPlan:
    if plan_id:
        plan = engine.storage.get(ResearchPlan, plan_id)
        if plan is None:
            raise FileNotFoundError(f"计划不存在: {plan_id}")
        return plan
    plans = sorted(engine.storage.list(ResearchPlan, project_id=project_id),
                   key=lambda p: p.version)
    if not plans:
        raise FileNotFoundError("项目尚无计划（先 plan_create）")
    return plans[-1]


def _human_approval_state(engine: ResearchEngine, project_id: str, plan_id: str) -> dict:
    """A1：人工审批状态只看台账 actor=HUMAN 的 approve 事件。"""
    for e in engine.event_log.events(project_id=project_id):
        if e.action == "approve" and e.object_id == plan_id and e.actor == Actor.HUMAN:
            return {"approved_by_human": True, "note": e.detail.get("note", "")}
    return {"approved_by_human": False,
            "how_to": f"人工批准：终端运行 qresearch approve <project_dir> {plan_id}"
                      "（agent 无法代批——A1 台账仲裁）"}


@server.tool()
@_tool
def plan_approve(project_id: str, plan_id: str) -> dict:
    """计划审批（A1 台账仲裁，不写批准）：查该计划是否已被人工批准。
    未批准时返回待办指引——请在终端用 qresearch CLI 批准后再来查询。"""
    eng = _open(project_id)
    plan = eng.storage.get(ResearchPlan, plan_id)
    if plan is None:
        raise FileNotFoundError(f"计划不存在: {plan_id}")
    state = _human_approval_state(eng, project_id, plan_id)
    return {"plan_id": plan_id, "plan_status": plan.status.value, **state}


# ================================================================ 执行与验证（异步）
@server.tool()
@_tool
def experiments_run(project_id: str, plan_id: str, max_workers: int = 1) -> dict:
    """执行已批准计划的全部实验（异步 job）。实验不经 LLM；同 plan 活跃 job 幂等。"""
    eng = _open(project_id)
    plan = eng.storage.get(ResearchPlan, plan_id)
    if plan is None:
        raise FileNotFoundError(f"计划不存在: {plan_id}")
    if plan.status != PlanStatus.APPROVED:
        raise PermissionError(
            f"计划 {plan_id} 状态为 {plan.status.value}，未经人工批准不得执行（A1/D5）")
    return _submit(eng, _experiments_job, eng, plan_id, max_workers=max_workers,
                   key=f"experiments:{plan_id}")


def _experiments_job(engine: ResearchEngine, plan_id: str, *, max_workers: int) -> dict:
    result = engine.experiments_run(plan_id, max_workers=max_workers)
    result["experiments"] = [e.model_dump() for e in result["experiments"]]
    return result


@server.tool()
@_tool
def verify(project_id: str, experiment_ids: list[str] | None = None) -> dict:
    """三层验证 + 证据资格门（异步 job）。experiment_ids 缺省=全部未验证的已完成实验。"""
    eng = _open(project_id)
    if experiment_ids is None:
        experiment_ids = [e.experiment_id for e in eng.storage.list(Experiment, project_id)
                          if e.status == ExperimentStatus.COMPLETED
                          and e.verification_status == VerificationStatus.NOT_RUN]
    if not experiment_ids:
        return {"job_id": None, "note": "没有待验证的实验", "n_verified": 0}
    return _submit(eng, _verify_job, eng, experiment_ids,
                   key=f"verify:{project_id}:{len(experiment_ids)}")


def _verify_job(engine: ResearchEngine, experiment_ids: list[str]) -> dict:
    reports = engine.verify(experiment_ids)
    return {"n_verified": len(reports),
            "results": [{"experiment_id": r.experiment_id, "overall": r.overall.value,
                         "report_id": r.report_id,
                         "failed": [i.claim for i in r.items
                                    if i.status == VerificationStatus.FAILED]}
                        for r in reports]}


# ================================================================ 分析与决策（异步）
@server.tool()
@_tool
def analyze(project_id: str) -> dict:
    """ANALYZE 站点（异步 job）：五段式分析，只允许引用通过验证的实验（资格门）。"""
    eng = _open(project_id)
    eligible = [e.experiment_id for e in eng.storage.list(Experiment, project_id)
                if e.verification_status == VerificationStatus.PASSED]
    if not eligible:
        raise ValueError("没有通过验证的实验可分析（先 experiments_run + verify）")
    return _submit(eng, _analyze_job, eng, project_id, eligible,
                   key=f"analyze:{project_id}:{len(eligible)}")


def _analyze_job(engine: ResearchEngine, project_id: str, eligible: list[str]) -> dict:
    _analysis, evidence = engine.analyze(project_id, experiment_ids=eligible)
    return {"n_evidence": len(evidence),
            "evidence_ids": [ev.evidence_id for ev in evidence],
            "note": "Evidence 已落账；下一步 decide"}


@server.tool()
@_tool
def decide(project_id: str, max_rounds: int = 3) -> dict:
    """DECIDE 站点（异步 job）：决策建议（iterate/terminate/replan/declare_result）。
    结论类决策被引擎无条件置 requires_human=True（D6）——人工经 CLI 确认。"""
    eng = _open(project_id)
    round_no = len(eng.storage.list(Decision, project_id)) + 1
    return _submit(eng, _decide_job, eng, project_id, round_no=round_no,
                   max_rounds=max_rounds, key=f"decide:{project_id}:{round_no}")


def _decide_job(engine: ResearchEngine, project_id: str, *, round_no: int,
                max_rounds: int) -> dict:
    decision = engine.decide(project_id, round_no=round_no, max_rounds=max_rounds)
    return {"decision_id": decision.decision_id, "type": decision.type.value,
            "recommendation": decision.recommendation.value,
            "requires_human": decision.requires_human, "round_no": round_no,
            "rationale": decision.rationale,
            "checklist": [c.model_dump() for c in decision.checklist]}


# ================================================================ adhoc（D3/A6）
@server.tool()
@_tool
def adhoc_record(project_id: str, kind: str, summary: str, tool: str | None = None,
                 parameters: dict | None = None, artifacts: list[str] | None = None) -> dict:
    """登记计划外已发生的动作（bash 现算等）。默认未验证、不得引用（D3/A6）。
    要能升级为证据：必须用注册工具跑（tool=注册名）并给 result.json 产物路径，
    再 adhoc_verify。"""
    eng = _open(project_id)
    exp = eng.adhoc_record(project_id, kind=kind, summary=summary, tool=tool,
                           parameters=parameters, artifacts=artifacts)
    return {"experiment_id": exp.experiment_id, "step_id": exp.step_id,
            "verified": False,
            "note": "未验证记录：报告与决策不得引用；要引用先 adhoc_verify"}


@server.tool()
@_tool
def adhoc_verify(project_id: str, experiment_id: str) -> dict:
    """adhoc 记录走三层 golden（异步 job）。仅注册工具路径可能通过（A6）。"""
    eng = _open(project_id)
    return _submit(eng, _adhoc_verify_job, eng, experiment_id,
                   key=f"adhoc_verify:{experiment_id}")


def _adhoc_verify_job(engine: ResearchEngine, experiment_id: str) -> dict:
    report = engine.adhoc_verify(experiment_id)
    return {"experiment_id": experiment_id, "overall": report.overall.value,
            "report_id": report.report_id,
            "failed": [i.claim for i in report.items
                       if i.status == VerificationStatus.FAILED]}


# ================================================================ 产出与只读
@server.tool()
@_tool
def report_generate(project_id: str) -> dict:
    """生成/刷新研究报告（置顶总结+计划版本历史，全部可回溯）。返回报告路径。"""
    eng = _open(project_id)
    path = eng.report_generate(project_id)
    return {"report": str(path)}


@server.tool()
@_tool
def viz_plot(project_id: str) -> dict:
    """三张结果变化图（工具×状态 / 数值vs参数 / 证据累计）。需要 [viz]。"""
    eng = _open(project_id)
    out = Path(eng.storage.path).parent / "plots"
    paths = eng.plot(project_id, out)
    return {"plots": [str(p) for p in paths]}


@server.tool()
@_tool
def ledger_query(project_id: str, kind: str, filters: dict | None = None) -> dict:
    """台账查询（只读）。kind ∈ project/goal/hypothesis/plan/experiment/evidence/
    decision/tool/verification；filters 为字段等值过滤（如 {"status": "completed"}）。"""
    eng = _open(project_id)
    return {"items": eng.ledger(project_id, kind, **(filters or {}))}


@server.tool()
@_tool
def events_tail(project_id: str, since: int = 0, limit: int = 200) -> dict:
    """事件流尾部（只读审计入口）。"""
    eng = _open(project_id)
    return {"events": eng.events(project_id, since=since, limit=limit)}


# ================================================================ job 协议
@server.tool()
@_tool
def job_status(job_id: str) -> dict:
    """查询 job 状态：state ∈ pending/running/done/error/cancelled。"""
    for engine, _s, _l, _c in list(_Ctx._cache.values()):
        try:
            return engine.jobs.status(job_id)
        except KeyError:
            continue
    raise KeyError(f"job 不存在: {job_id}")


@server.tool()
@_tool
def job_result(job_id: str) -> dict:
    """取 job 结果（未完成时返回提示继续轮询）。"""
    for engine, _s, _l, _c in list(_Ctx._cache.values()):
        try:
            return engine.jobs.result(job_id)
        except KeyError:
            continue
    raise KeyError(f"job 不存在: {job_id}")


@server.tool()
@_tool
def job_cancel(job_id: str) -> dict:
    """取消 job：pending 直接取消；running 置取消请求（M1 不谎报已取消）。"""
    for engine, _s, _l, _c in list(_Ctx._cache.values()):
        try:
            return {"job_id": job_id, "cancelled": engine.jobs.cancel(job_id)}
        except KeyError:
            continue
    raise KeyError(f"job 不存在: {job_id}")


# ================================================================ 入口
def main() -> None:
    parser = argparse.ArgumentParser(description="qresearch MCP server (stdio)")
    parser.add_argument("--project-root", required=True,
                        help="研究项目根目录（每项目一个子目录，orchestrator 同契约）")
    parser.add_argument("--model", default=None, help="站点调用的 DSH 模型（缺省用 SDK 默认）")
    args = parser.parse_args()
    _Ctx.projects_root = Path(args.project_root).resolve()
    _Ctx.projects_root.mkdir(parents=True, exist_ok=True)
    _Ctx.model = args.model
    atexit.register(_close_all)
    server.run(transport="stdio")


if __name__ == "__main__":
    # profile patch 以文件路径直启（python <repo>/qresearch/mcp_server.py）：
    # 把仓库根加进 sys.path，保证 `qresearch` 包可导入（与 cwd 无关）
    import sys

    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
