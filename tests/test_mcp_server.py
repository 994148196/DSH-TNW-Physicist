"""MCP 层测试（计划 v3 M2 / L2 + L4 离线面）。

覆盖：工具注册面（A1：plan_approve 无 actor 参数）/ 只读工具零写入（L2）/
A1 台账仲裁全链（MCP 纯查询 → CLI 审批台写 HUMAN 事件 → TTY 守卫拒无终端进程）/
异步 job 协议端到端（A2，open→understand→…→conclude→report 全链）/
adhoc 诚实边界经 MCP 面（D3/A6）。

进程内直调工具函数（@server.tool() 保留原函数）；stdio 传输契约由
examples/mcp_smoke.py 的真实子进程冒烟承担。
"""
import asyncio
import io
import json
import re
import time

import pytest
from conftest import Persistent

from qresearch import mcp_server as m
from qresearch.core.events import EventLog
from qresearch.core.models import PlanStep, ResearchPlan
from qresearch.core.status import Actor, PlanStatus
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient
from qresearch.engine import ResearchEngine
from qresearch.experiments.manager import InProcessToolRunner
from qresearch.tools.registry import load_seed_tools
from qresearch.ui import approvals

UNDERSTAND_OK = (
    '{"refined_question": "Heisenberg 链基态能量", "quantities": ["e0"],'
    ' "success_criteria": ["与 1/4-ln2 对照"]}'
)
HYP_OK = (
    '{"hypotheses": ['
    '{"statement": "外推与 BA 一致", "rationale": "解析",'
    ' "falsification_tests": ["偏差超 1e-3"], "discriminating_experiment": "ED"},'
    '{"statement": "有限尺寸效应小", "rationale": "文献",'
    ' "falsification_tests": ["两尺寸差超 1%"], "discriminating_experiment": "扫描"}]}'
)
PLAN_OK = (
    '{"steps": ['
    '{"action": "run_experiment", "purpose": "基准", "tools": ["simple_ed"],'
    ' "inputs": {"N": 4}, "expected_outputs": ["E0"]}],'
    ' "risks": [], "diff_summary": null}'
)
CRITIC_PASS = '{"verdict": "pass", "issues": []}'


def _analysis_response(prompt: str, session_id: str) -> str:
    """从 prompt 解析本轮通过验证的实验 id，只引用它们（资格门配合）。"""
    exp_ids = re.findall(r"\[(exp_[0-9a-f]+)\]", prompt)
    assert exp_ids, "分析阶段 prompt 中应包含已验证实验"
    return json.dumps({
        "observations": [{"claim": "能量与解析值一致", "experiment_ids": exp_ids[:1]}],
        "interpretations": [{"claim": "支持假设一", "experiment_ids": exp_ids[:1]}],
        "uncertainties": ["系统尺寸仍小"],
        "alternative_explanations": [],
        "recommended_next_steps": ["下一轮扩大 N"],
    }, ensure_ascii=False)


def _decide_factory(recommendation: str):
    def _resp(prompt: str, session_id: str) -> str:
        ev = re.findall(r"evidence_[0-9a-f]+", prompt)
        item = {"claim": "关键证据已核", "status": "passed" if ev else "untested",
                "evidence": ev[0] if ev else None}
        return json.dumps({"recommendation": recommendation, "checklist": [item],
                           "info_gain_estimate": "high", "rationale": "按证据决策"},
                          ensure_ascii=False)
    return _resp


class _TTY:
    """假 TTY：isatty()=True（守卫放行；input 由 monkeypatch 提供）。"""

    def isatty(self) -> bool:
        return True


class _StubClose:
    """缓存槽位的 client 占位（真实 client 由引擎自带）。"""

    def close(self) -> None:
        pass


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    """MCP 进程上下文隔离：projects_root 指向 tmp，缓存清空。"""
    load_seed_tools()
    monkeypatch.setattr(m._Ctx, "projects_root", tmp_path)
    monkeypatch.setattr(m._Ctx, "model", None)
    monkeypatch.setattr(m._Ctx, "_cache", {})
    yield tmp_path
    for eng, *_rest in m._Ctx._cache.values():
        try:
            eng.jobs._pool.shutdown(wait=False)
        except Exception:  # noqa: BLE001 —— 收尾尽力而为
            pass
    m._Ctx._cache.clear()


def _install(ctx, make_scripted_client=None, responses: dict | None = None) -> ResearchEngine:
    """在 ctx 下安装项目 proj 的引擎（目录契约同 orchestrator）。"""
    pdir = ctx / "proj"
    storage = Storage(pdir / "state.sqlite")
    log = EventLog(pdir / "events.jsonl")
    if responses is not None:
        assert make_scripted_client is not None
        client = make_scripted_client(responses)
    else:
        client = DSHClient(runner=lambda p, s: "{}")
    engine = ResearchEngine(storage, log, client,
                            experiments_root=pdir / "experiments",
                            runner=InProcessToolRunner())
    m._Ctx._cache["proj"] = (engine, storage, log, _StubClose())
    return engine


def _save_plan(engine: ResearchEngine) -> ResearchPlan:
    plan = ResearchPlan(
        project_id="proj", goal_id="goal_x", version=1,
        steps=[PlanStep(step_id="s1", action="run_experiment", purpose="基准",
                        tools=["simple_ed"], inputs={"N": 4, "want_gap": True})])
    engine.storage.save(plan)
    return plan


def _wait_job(engine: ResearchEngine, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    st: dict = {}
    while time.time() < deadline:
        st = engine.job_status(job_id)
        if st["state"] in ("done", "error", "cancelled"):
            return st
        time.sleep(0.02)
    raise AssertionError(f"job 超时未完成：{st}")


def _approve_via_cli(ctx, plan_id: str, monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    rc = approvals.do_approve(ctx / "proj", plan_id, "人工批准", stdin=_TTY())
    assert rc == 0, "CLI 审批应成功"


# ================================================================ 工具注册面
def test_tool_surface_and_a1_schema():
    tools = asyncio.run(m.server.list_tools())  # 服务端 API：直接返回 list[MCPTool]
    names = {t.name for t in tools}
    expected = {"research_open", "research_status", "research_list",
                "understand", "hypothesize", "plan_create", "plan_revise",
                "plan_show", "plan_approve", "experiments_run", "verify",
                "analyze", "decide", "adhoc_record", "adhoc_verify",
                "report_generate", "viz_plot", "ledger_query", "events_tail",
                "job_status", "job_result", "job_cancel"}
    assert expected <= names, f"缺工具：{sorted(expected - names)}"
    # A1：审批查询工具不收 actor 参数——agent 没有"自称人"的入口
    schema = next(t for t in tools if t.name == "plan_approve").input_schema
    assert set(schema["properties"]) == {"project_id", "plan_id"}


# ================================================================ L2 只读零写入
def test_readonly_zero_write(ctx):
    engine = _install(ctx)
    engine.open_project("proj", "问题")
    plan = _save_plan(engine)
    db = ctx / "proj" / "state.sqlite"
    log = ctx / "proj" / "events.jsonl"
    before = (db.read_bytes(), log.read_bytes())

    assert m.research_status(project_id="proj")["project_id"] == "proj"
    assert m.research_list()["projects"][0]["project_id"] == "proj"
    assert m.ledger_query(project_id="proj", kind="experiment")["items"] == []
    assert len(m.events_tail(project_id="proj")["events"]) >= 1
    out = m.plan_approve(project_id="proj", plan_id=plan.plan_id)
    assert out["approved_by_human"] is False and "how_to" in out
    assert "error" in m.job_status(job_id="job_nope")

    assert (db.read_bytes(), log.read_bytes()) == before, "只读工具改动台账！"


# ================================================================ A1 仲裁 + TTY 守卫
def test_a1_arbitration_and_tty_guard(ctx):
    engine = _install(ctx)
    engine.open_project("proj", "问题")
    plan = _save_plan(engine)

    # 无 TTY 的进程（agent 经 bash 起的子进程）：守卫直接拒绝，不碰台账
    rc = approvals.do_approve(ctx / "proj", plan.plan_id, stdin=io.StringIO("y"))
    assert rc == 2
    assert not any(e.action == "approve"
                   for e in engine.event_log.events(project_id="proj"))

    # MCP 侧仲裁：无人批准 → False（且不写事件）
    out = m.plan_approve(project_id="proj", plan_id=plan.plan_id)
    assert out["approved_by_human"] is False
    assert out["plan_status"] == PlanStatus.AWAITING_APPROVAL.value

    # 真实终端（TTY + 显式 y）：唯一的 HUMAN 审批写入口
    import builtins
    orig_input = builtins.input
    builtins.input = lambda *a, **k: "y"
    try:
        rc = approvals.do_approve(ctx / "proj", plan.plan_id, "人工批准", stdin=_TTY())
    finally:
        builtins.input = orig_input
    assert rc == 0

    evs = [e for e in engine.event_log.events(project_id="proj")
           if e.action == "approve"]
    assert len(evs) == 1 and evs[0].actor == Actor.HUMAN
    assert engine.storage.get(ResearchPlan, plan.plan_id).status == PlanStatus.APPROVED

    # MCP 侧仲裁：批准后查询为 True
    out = m.plan_approve(project_id="proj", plan_id=plan.plan_id)
    assert out["approved_by_human"] is True


# ================================================================ D5 未批禁执行
def test_experiments_run_requires_approval(ctx):
    engine = _install(ctx)
    engine.open_project("proj", "问题")
    plan = _save_plan(engine)
    out = m.experiments_run(project_id="proj", plan_id=plan.plan_id)
    assert "error" in out and "批准" in out["error"]


# ================================================================ A2 全链（L2 MCP 面）
def test_full_flow_through_mcp_tools(ctx, make_scripted_client, monkeypatch):
    """research_open → … → conclude → report：全部经 MCP 工具函数走通。"""
    engine = _install(ctx, make_scripted_client, {
        "understand": UNDERSTAND_OK,
        "hypothesize": HYP_OK,
        "plan": [PLAN_OK],
        "critic": [CRITIC_PASS],
        "analyze": [Persistent(_analysis_response)],
        "decide": [Persistent(_decide_factory("declare_result"))],
    })
    engine.open_project("proj", "Heisenberg 链基态研究")

    # --- 规划（异步站点）---
    job = m.understand(project_id="proj")
    assert _wait_job(engine, job["job_id"])["state"] == "done"
    job = m.hypothesize(project_id="proj", n=2)
    assert _wait_job(engine, job["job_id"])["state"] == "done"
    job = m.plan_create(project_id="proj")
    assert _wait_job(engine, job["job_id"])["state"] == "done"
    digest = engine.job_result(job["job_id"])["result"]
    assert digest["awaiting_approval"] is True
    assert digest["critic_verdict"] == "pass"
    plan_id = digest["plan_id"]
    assert engine.storage.get(ResearchPlan, plan_id).status == PlanStatus.AWAITING_APPROVAL

    # --- 审批（A1：CLI 写，MCP 查）---
    assert m.plan_approve(project_id="proj", plan_id=plan_id)["approved_by_human"] is False
    _approve_via_cli(ctx, plan_id, monkeypatch)
    assert m.plan_approve(project_id="proj", plan_id=plan_id)["approved_by_human"] is True

    # --- 执行（D4 幂等 + A2 轮询）---
    job = m.experiments_run(project_id="proj", plan_id=plan_id)
    job_again = m.experiments_run(project_id="proj", plan_id=plan_id)
    assert job["job_id"] == job_again["job_id"]  # 活跃 job 同 key 幂等
    assert _wait_job(engine, job["job_id"])["state"] == "done"
    run = engine.job_result(job["job_id"])["result"]
    assert run["n_completed"] == 1 and run["n_failed"] == 0

    # --- 验证（缺省=全部未验证已完成）---
    job = m.verify(project_id="proj")
    assert _wait_job(engine, job["job_id"])["state"] == "done"
    ver = engine.job_result(job["job_id"])["result"]
    assert ver["n_verified"] == 1
    assert ver["results"][0]["overall"] == "passed"

    # --- 台账投影 ---
    items = m.ledger_query(project_id="proj", kind="experiment")["items"]
    assert len(items) == 1 and items[0]["status"] == "completed"

    # --- 分析与决策（结轮宣告 → 无条件人工）---
    job = m.analyze(project_id="proj")
    assert _wait_job(engine, job["job_id"])["state"] == "done"
    assert engine.job_result(job["job_id"])["result"]["n_evidence"] == 2  # 观察+解读
    job = m.decide(project_id="proj")
    assert _wait_job(engine, job["job_id"])["state"] == "done"
    dec = engine.job_result(job["job_id"])["result"]
    assert dec["requires_human"] is True
    assert dec["decision_id"].startswith("dec_")

    # --- 结论确认（D6：只经 CLI conclude；重复幂等）---
    monkeypatch.setattr("builtins.input", lambda *a, **k: "y")
    rc = approvals.do_conclude(ctx / "proj", dec["decision_id"], stdin=_TTY())
    assert rc == 0
    rc = approvals.do_conclude(ctx / "proj", dec["decision_id"], stdin=_TTY())
    assert rc == 0  # 幂等返回
    evs = [e for e in engine.event_log.events(project_id="proj")
           if e.action == "conclude"]
    assert len(evs) == 1 and evs[0].actor == Actor.HUMAN

    # --- 报告：status 从台账推导（concluded），证据 id 可回溯 ---
    out = m.report_generate(project_id="proj")
    report = (ctx / "proj" / "report.md").read_text(encoding="utf-8")
    assert "`concluded`" in report
    assert "evidence_" in report and "提示" in report  # 关键证据 + 人工确认提示


# ================================================================ D3/A6 adhoc
def test_adhoc_discipline_via_mcp(ctx):
    engine = _install(ctx)
    engine.open_project("proj", "问题")
    out = m.adhoc_record(project_id="proj", kind="quick_calc",
                         summary="bash 现算 N=4", parameters={"N": 4})
    assert out["verified"] is False and out["step_id"].startswith("adhoc_")
    job = m.adhoc_verify(project_id="proj", experiment_id=out["experiment_id"])
    assert _wait_job(engine, job["job_id"])["state"] == "error"
    assert "未关联注册工具" in engine.job_result(job["job_id"])["error"]
    # 未验证 adhoc 不产生任何证据
    assert m.ledger_query(project_id="proj", kind="evidence")["items"] == []


# ================================================================ M3 plan_show markdown
def test_plan_show_markdown_card(ctx):
    """M3：plan_show 的 markdown 字段给 Web 渲染——标题/目标/步骤表/critic/blocker。"""
    engine = _install(ctx)
    engine.open_project("proj", "Heisenberg 链基态研究")
    plan = _save_plan(engine)

    # 无 critic 事件：基础卡片，无 blocker 节、无编辑提示（edit_hint=False）
    out = m.plan_show(project_id="proj", plan_id=plan.plan_id)
    md = out["markdown"]
    assert "# 研究计划 v1" in md and "Heisenberg 链基态研究" in md
    assert "## 步骤（1 步）" in md and "### s1 [run_experiment] 基准" in md
    assert "simple_ed" in md and "critic" not in md
    assert out["n_blockers"] == 0

    # 追加 plan_ready 事件（verdict=block）：critic 节与 blocker 计数出现
    from qresearch.core.events import Event
    engine.event_log.append(Event(
        actor=Actor.SYSTEM, action="plan_ready", project_id="proj",
        object_type="ResearchPlan", object_id=plan.plan_id,
        detail={"verdict": "block",
                "issues": [{"severity": "blocker", "description": "缺对照组"}]}))
    out = m.plan_show(project_id="proj", plan_id=plan.plan_id)
    assert out["critic_verdict"] == "block" and out["n_blockers"] == 1
    assert "## critic 审查结论：block" in out["markdown"]
    assert "缺对照组" in out["markdown"]
    # 编辑提示只属于 plan_doc 渲染入口，MCP 卡片不带（Web 端不显示修改说明）
    assert "两种用法" not in out["markdown"]
