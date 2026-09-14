"""Phase 9 交互层测试：station_started / 计划文档 / 多通道审批 / 进度显示 / CLI 会话。"""
from qresearch.core.events import Event, EventLog
from qresearch.core.models import Goal, PlanStep, ResearchPlan
from qresearch.core.status import Actor, PlanStatus
from qresearch.core.storage import Storage
from qresearch.research_loop import run_research_loop
from qresearch.ui.plan_doc import render_plan_markdown, save_plan_doc, split_user_notes
from qresearch.ui.progress import ConsoleProgress
from conftest import Persistent
from test_research_loop import (
    CRITIC_PASS,
    HYP_OK,
    PLAN_OK,
    UNDERSTAND_OK,
    _analysis_response,
    _decide_factory,
    loop_client,
)

PLAN_V2 = (
    '{"steps": ['
    '{"action": "run_experiment", "purpose": "基准（按意见加倍尺寸）", "tools": ["simple_ed"],'
    ' "inputs": {"N": 8}, "expected_outputs": ["E0"]}],'
    ' "risks": [], "diff_summary": "N 4→8"}'
)


# ---------------------------------------------------------------- 显示层基建
def test_event_subscription_swallows_subscriber_errors(tmp_path):
    """显示层异常绝不打断记账（铁律：显示是视图）。"""
    log = EventLog(tmp_path / "events.jsonl")
    seen = []

    def bad(_event):
        raise RuntimeError("显示层炸了")

    log.subscribe(bad)
    log.subscribe(seen.append)
    ev = log.append(Event(actor=Actor.SYSTEM, action="x", project_id="p"))
    assert seen == [ev]
    assert log.events(project_id="p")[0].action == "x"


def test_station_started_emitted(tmp_path, make_scripted_client):
    """站点调用开始即有 station_started 事件（进度显示的数据源）。"""
    client = make_scripted_client({"understand": UNDERSTAND_OK})
    log = EventLog(tmp_path / "events.jsonl")
    from qresearch.stations.executors import understand
    understand(client, "p", "q", event_log=log, retries=0)
    actions = [e.action for e in log.events(project_id="p")]
    assert actions[0] == "station_started"
    assert "station_call" in actions
    started = next(e for e in log.events() if e.action == "station_started")
    assert started.object_id == "understand"


def test_console_progress_counters(tmp_path):
    """进度条从事件流累计统计；rich/纯文本两种模式都不抛异常。"""
    log = EventLog(tmp_path / "events.jsonl")
    progress = ConsoleProgress(log)  # 不进 with（不起 Live），只测事件处理
    log.append(Event(actor=Actor.SYSTEM, action="station_started",
                     project_id="p", object_type="plan", detail={"attempt": 0}))
    log.append(Event(actor=Actor.SYSTEM, action="experiment_started",
                     project_id="p", object_id="exp_a", detail={"tool": "simple_ed"}))
    log.append(Event(actor=Actor.SYSTEM, action="experiment_finished",
                     project_id="p", object_id="exp_a",
                     detail={"tool": "simple_ed", "status": "completed", "elapsed_s": 0.1}))
    log.append(Event(actor=Actor.SYSTEM, action="verify_experiment",
                     project_id="p", object_id="vr1",
                     detail={"experiment": "exp_a", "overall": "passed"}))
    log.append(Event(actor=Actor.SYSTEM, action="plan_ready",
                     project_id="p", object_id="plan_x",
                     detail={"version": 1, "verdict": "pass", "round": 2}))
    assert progress._n_done == 1 and progress._n_verified == 1
    assert progress._round == 2
    # rich 可用时 Live 上下文可进可出（pytest 非 tty 环境也不炸）
    with ConsoleProgress(log):
        log.append(Event(actor=Actor.SYSTEM, action="station_call",
                         project_id="p", object_type="station", object_id="plan",
                         detail={"attempt": 0}))


# ---------------------------------------------------------------- 计划文档
def _make_plan(pid: str = "p_doc") -> ResearchPlan:
    return ResearchPlan(
        project_id=pid, goal_id="g", version=1,
        steps=[PlanStep(step_id="step_1", action="run_experiment", purpose="基准",
                        tools=["simple_ed"], inputs={"N": 4},
                        expected_outputs=["E0"])],
        risks=["小尺寸"], diff_summary=None,
    )


def test_plan_doc_render_and_split(tmp_path):
    plan = _make_plan()
    md = render_plan_markdown(
        plan, goal=Goal(project_id="p", question="q", success_criteria=["s"]),
        hypotheses=[], critique_verdict="pass", critique_issues=[])
    assert "# 研究计划 v1" in md and "step_1" in md and "simple_ed" in md
    assert "## 修改意见" in md and "critic 审查结论：pass" in md
    path = save_plan_doc(tmp_path, plan)
    assert path.exists() and "plan_v1" in path.name

    # 拆分：正文 + 修改意见
    edited = md.replace("（在此填写……）", "把 N 加倍\n再加验证步骤")
    body, notes = split_user_notes(edited)
    assert "把 N 加倍" in notes and "step_1" in body
    # 无意见（占位符未动）→ 空意见
    body2, notes2 = split_user_notes(md)
    assert notes2 == "" and "step_1" in body2


# ---------------------------------------------------------------- 多通道审批
def _approve_client(make_scripted_client, plan_queue, extra=None):
    responses = {"plan": plan_queue, "critic": [CRITIC_PASS] * 4}
    if extra:
        responses.update(extra)
    return make_scripted_client(responses)


def test_approval_free_text_is_feedback(tmp_path, make_scripted_client, monkeypatch):
    """审批处直接输入一段文字（口述）= 修改意见 → 重新制题出新版 → y 批准。"""
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    client = _approve_client(make_scripted_client, [PLAN_OK, PLAN_V2])
    goal = Goal(project_id="p_rev", question="q", success_criteria=["s"])
    plan = _make_plan("p_rev")
    plan.status = PlanStatus.AWAITING_APPROVAL
    storage.save(plan)

    from qresearch.loop import _interactive_approval
    ANS = iter(["把尺寸加倍并加一步验证", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(ANS))
    final = _interactive_approval(storage, log, plan, client=client, goal=goal,
                                  hypotheses=[], retries=0)

    assert final.status == PlanStatus.APPROVED and final.version == 2
    events = log.events(project_id="p_rev")
    fb = next(e for e in events if e.action == "plan_feedback")
    assert fb.actor == Actor.HUMAN and fb.detail["mode"] == "verbal"
    assert fb.detail["notes"] == "把尺寸加倍并加一步验证"
    assert (tmp_path / "plans" / "plan_v2.md").exists()


def test_approval_multiline_c(tmp_path, make_scripted_client, monkeypatch):
    """c 引导多行意见；意见整体注入。"""
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    client = _approve_client(make_scripted_client, [PLAN_OK, PLAN_V2])
    goal = Goal(project_id="p_ml", question="q", success_criteria=["s"])
    plan = _make_plan("p_ml")
    plan.status = PlanStatus.AWAITING_APPROVAL
    storage.save(plan)

    from qresearch.loop import _interactive_approval
    answers = iter(["c", "第一条：尺寸加倍", "第二条：加收敛检查", "", "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    final = _interactive_approval(storage, log, plan, client=client, goal=goal,
                                  hypotheses=[], retries=0)
    assert final.version == 2
    fb = next(e for e in log.events() if e.action == "plan_feedback")
    assert "第一条" in fb.detail["notes"] and "第二条" in fb.detail["notes"]


def test_approval_edit_doc_flow(tmp_path, make_scripted_client, monkeypatch):
    """e 通道：用户直接改文档 → transcribe 站点转录（过语义校验）→ critic 再审 → 新版审批。"""
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    client = _approve_client(make_scripted_client, [PLAN_OK],
                             extra={"transcribe": [PLAN_V2]})
    goal = Goal(project_id="p_edit", question="q", success_criteria=["s"])
    plan = _make_plan("p_edit")
    plan.status = PlanStatus.AWAITING_APPROVAL
    storage.save(plan)

    doc_path = tmp_path / "plans" / "plan_v1.md"
    save_plan_doc(tmp_path, plan, critique_verdict="pass", critique_issues=[])

    calls = {"n": 0}

    def fake_input(prompt=""):
        calls["n"] += 1
        if calls["n"] == 2:  # 第二次输入 = "编辑完回车"：顺手改掉文档
            text = doc_path.read_text(encoding="utf-8")
            text = text.replace("（在此填写……）", "请把基准尺寸加倍")
            doc_path.write_text(text, encoding="utf-8")
        return next(ANS)

    ANS = iter(["e", "", "y"])
    monkeypatch.setattr("builtins.input", fake_input)

    from qresearch.loop import _interactive_approval
    final = _interactive_approval(storage, log, plan, client=client, goal=goal,
                                  hypotheses=[], retries=0)

    assert final.status == PlanStatus.APPROVED and final.version == 2
    assert final.diff_summary == "N 4→8"
    actions = [e.action for e in log.events(project_id="p_edit")]
    assert actions.count("plan_transcribed") == 1
    fb = next(e for e in log.events() if e.action == "plan_feedback")
    assert fb.actor == Actor.HUMAN and fb.detail["mode"] == "edit_doc"
    pr = next(e for e in log.events() if e.action == "plan_ready"
              and e.detail.get("from_user_edit"))
    assert pr.detail["verdict"] == "pass"


def test_approval_eof_never_approves(tmp_path, make_scripted_client, monkeypatch):
    """EOF/无人在场：绝不默认批准。"""
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    client = _approve_client(make_scripted_client, [PLAN_OK])
    plan = _make_plan("p_eof")
    plan.status = PlanStatus.AWAITING_APPROVAL
    storage.save(plan)

    def _eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    from qresearch.loop import _interactive_approval
    final = _interactive_approval(storage, log, plan, client=client,
                                  goal=Goal(project_id="p_eof", question="q",
                                            success_criteria=["s"]),
                                  hypotheses=[], retries=0)
    assert final.status == PlanStatus.REJECTED
    assert not [e for e in log.events() if e.action == "approve"]


# ---------------------------------------------------------------- CLI 会话
def test_session_commands(tmp_path, loop_client):
    """会话命令：/projects、/status、/plots、/report、/quit、未知命令。"""
    from qresearch.ui.session import Session

    pid = "p_hist"
    storage = Storage(tmp_path / pid / "state.sqlite")
    log = EventLog(tmp_path / pid / "events.jsonl")
    run_research_loop(loop_client, storage, log, pid, "Heisenberg q",
                      rounds=3, auto_approve=True)
    storage.close()

    outputs: list[str] = []
    session = Session(tmp_path, print_fn=outputs.append)

    assert session.run_command("/projects") is True
    assert any("p_hist" in o for o in outputs)
    text = session.cmd_status(pid)
    assert "决策链" in text and "report.md" in text

    paths = session.cmd_plots(pid)
    assert len(paths) == 3 and all(p.exists() for p in paths)

    summary = session.cmd_report(pid)
    assert "研究总结" in summary

    assert session.run_command("/quit") is False
    session.run_command("/nope")
    assert any("未知命令" in o for o in outputs)


def test_session_start_project_end_to_end(tmp_path, make_scripted_client, monkeypatch):
    """自然语言开题全流程（离线）：确认 → 审批 v1 → 轮末回车 → 审批 v2 → terminated。"""
    import qresearch.ui.session as session_mod
    from qresearch.ui.session import Session

    client = make_scripted_client({
        "understand": UNDERSTAND_OK, "hypothesize": HYP_OK,
        "plan": [PLAN_OK] * 3, "critic": [CRITIC_PASS] * 3,
        "analyze": [Persistent(_analysis_response)],
        "decide": [Persistent(_decide_factory(["iterate", "declare_result"]))],
    })
    monkeypatch.setattr(session_mod, "DSHClient", lambda **kw: client)

    outputs: list[str] = []
    session = Session(tmp_path, rounds=2, print_fn=outputs.append)
    ANS = iter(["y",      # 以此问题开跑？（会话确认）
                "y",      # 第 1 轮计划审批
                "",       # 第 1 轮末：回车继续
                "y"])     # 第 2 轮计划审批
    monkeypatch.setattr("builtins.input", lambda prompt="": next(ANS))
    session._input = lambda prompt="": next(ANS)

    summary = session.start_project("Heisenberg 链基态研究（会话测试）", pid="p_new")
    assert summary is not None and summary["status"] == "terminated"
    assert (tmp_path / "p_new" / "report.md").exists()
    assert (tmp_path / "p_new" / "plans" / "plan_v1.md").exists()
    actions = [e.action for e in EventLog(tmp_path / "p_new" / "events.jsonl")
               .events(project_id="p_new")]
    assert actions.count("approve") == 2
    assert any("【节点汇报】第 1 轮" in o for o in outputs)


def test_session_pause_flag(tmp_path):
    """pause 命令只设旗标，不写台账。"""
    from qresearch.ui.session import Session
    outputs: list[str] = []
    session = Session(tmp_path, print_fn=outputs.append)
    (tmp_path / "p_x").mkdir()
    (tmp_path / "p_x" / "state.sqlite").touch()
    flag = session.cmd_pause("p_x")
    assert flag.exists()


def test_session_round_callback_stop(tmp_path):
    """轮末回调：stop_requested 置位 → 返回 stop（Ctrl+C 一次的优雅停语义）。"""
    from qresearch.ui.session import Session
    outputs: list[str] = []
    session = Session(tmp_path, print_fn=outputs.append,
                      input_fn=lambda prompt="": "")
    digest = {"round_no": 1, "decision": {"type": "iterate", "recommendation": "iterate",
                                          "rationale": "r"},
              "experiments_this_round": 2, "evidence_this_round": 2, "evidence_total": 2}
    assert session._round_callback(digest) is None
    session.stop_requested = True
    assert session._round_callback(digest) == "stop"
