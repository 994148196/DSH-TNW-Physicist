"""dsh_client 测试：JSON 提取、schema 校验重试、转人工、事件留痕。"""
import pytest
from qresearch.core.events import EventLog
from qresearch.core.status import Actor
from qresearch.dsh_client import DSHClient, NeedsHuman, extract_json
from qresearch.stations.schemas import UnderstandOutput

VALID_UNDERSTAND = (
    '{"refined_question": "q", "quantities": ["E0"], "success_criteria": ["s1"]}'
)


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == '{"a": 1}'
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('说明文字 {"a": {"b": 2}} 结尾') == '{"a": {"b": 2}}'
    with pytest.raises(ValueError):
        extract_json("这段回复里没有任何 JSON")


def test_call_station_parses_and_logs(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    client = DSHClient(runner=lambda p, s: VALID_UNDERSTAND)
    out = client.call_station("understand", "p1", UnderstandOutput, "prompt", event_log=log)
    assert out.refined_question == "q"
    # P9：站点开始调用先发 station_started（进度条数据源），成功后 station_call
    assert [e.action for e in log.events(project_id="p1")] == ["station_started", "station_call"]
    assert log.events()[-1].actor == Actor.MODEL


def test_call_station_retries_with_error_feedback():
    responses = ["抱歉，我不能用 JSON 回答。", VALID_UNDERSTAND]
    prompts_seen: list[str] = []

    def runner(prompt: str, session_id: str) -> str:
        prompts_seen.append(prompt)
        return responses.pop(0)

    client = DSHClient(runner=runner)
    out = client.call_station("understand", "p1", UnderstandOutput, "base prompt", retries=1)
    assert out.quantities == ["E0"]
    assert len(prompts_seen) == 2
    assert "不合法" in prompts_seen[1]  # 错误信息回喂给下一次尝试


def test_call_station_escalates_after_retries():
    client = DSHClient(runner=lambda p, s: "仍然是散文回复")
    with pytest.raises(NeedsHuman) as exc:
        client.call_station("understand", "p1", UnderstandOutput, "prompt", retries=1)
    assert exc.value.station == "understand"


def test_session_id_encodes_station_and_attempt():
    """会话 id 契约：第二段是站点名（runner 与既有工具靠它取站点），末段是 attempt。"""
    sessions: list[str] = []

    def runner(prompt: str, session_id: str) -> str:
        sessions.append(session_id)
        return VALID_UNDERSTAND

    DSHClient(runner=runner).call_station("understand", "proj_x", UnderstandOutput, "p")
    assert sessions[0].split(":")[1] == "understand", "站点名必须仍在第二段"
    assert sessions[0].startswith("proj_x:understand:")
    assert sessions[0].endswith(":a0")


def test_repeated_station_call_gets_fresh_sessions():
    """回归：重复调用同一站点不得复用会话 id。

    复用会让 DSH **重放**上次回复——一次畸形输出被永久缓存，换 attempt 也逃不掉，
    只会一路 NeedsHuman。真实踩过：understand 连续两次失败，第二次 0.04s 返回
    （没发生 LLM 调用，只是重放）。
    """
    sessions: list[str] = []

    def runner(prompt: str, session_id: str) -> str:
        sessions.append(session_id)
        return VALID_UNDERSTAND

    client = DSHClient(runner=runner)
    for _ in range(3):
        client.call_station("understand", "proj_x", UnderstandOutput, "p")
    assert len(set(sessions)) == 3, f"会话 id 被复用：{sessions}"


def test_retry_attempts_within_one_call_stay_distinct():
    """同一次调用内的重试仍要换 session，且与其它调用互不重叠。"""
    sessions: list[str] = []

    def runner(prompt: str, session_id: str) -> str:
        sessions.append(session_id)
        return "散文" if len(sessions) == 1 else VALID_UNDERSTAND

    out = DSHClient(runner=runner).call_station(
        "understand", "p1", UnderstandOutput, "p", retries=1)
    assert out.quantities == ["E0"]
    assert len(sessions) == 2
    assert sessions[0].endswith(":a0") and sessions[1].endswith(":a1")
    assert sessions[0].rsplit(":a", 1)[0] == sessions[1].rsplit(":a", 1)[0]


# ================================================== 看门狗（runtime 挂起兜底）
class _StubHarness:
    """假 runtime：可编程 run 行为，记录 close（注入 _create_harness 用）。"""

    def __init__(self, behavior):
        self.behavior = behavior
        self.closed = False

    def run(self, prompt, session_id=None):
        from types import SimpleNamespace

        return SimpleNamespace(final_response=self.behavior(prompt, session_id))

    def close(self):
        self.closed = True


def test_station_timeout_restarts_runtime(monkeypatch):
    """run() 悬挂超过 wallclock 上限 → StationTimeout + runtime 已重启。"""
    import time

    from qresearch.dsh_client import StationTimeout

    created: list[_StubHarness] = []

    def fake_create(self, dsh_home, cwd, model, **kw):
        h = _StubHarness(lambda p, s: time.sleep(2) or "")
        created.append(h)
        return h, (dsh_home, cwd, model, kw)

    monkeypatch.setattr(DSHClient, "_create_harness", fake_create)
    client = DSHClient(station_timeout_s=0.3)
    with pytest.raises(StationTimeout):
        client._run("p", "s1")
    assert created[0].closed, "旧 runtime 应被关闭"
    assert client._harness is created[1], "应换上新 runtime"
    assert len(created) == 2


def test_call_station_retries_on_runtime_failure(monkeypatch, tmp_path):
    """runtime 层失败（超时/断链）→ station_retry 事件 + 换 session 重试成功。"""
    from qresearch.dsh_client import StationTimeout

    calls = {"n": 0}

    def fake_create(self, dsh_home, cwd, model, **kw):
        def behave(prompt, session_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise StationTimeout("s", 1.0)
            return VALID_UNDERSTAND

        return _StubHarness(behave), (dsh_home, cwd, model, kw)

    monkeypatch.setattr(DSHClient, "_create_harness", fake_create)
    client = DSHClient(station_timeout_s=5)
    log = EventLog(tmp_path / "e.jsonl")
    out = client.call_station("understand", "p1", UnderstandOutput, "prompt",
                              event_log=log)
    assert out.refined_question == "q"
    assert calls["n"] == 2
    actions = [e.action for e in log.events(project_id="p1")]
    # station_started 每次尝试各发一条（两次尝试）
    assert actions == ["station_started", "station_retry",
                       "station_started", "station_call"]
    assert log.events()[0].actor == Actor.SYSTEM


def test_call_station_needs_human_when_runtime_always_fails(monkeypatch):
    def fake_create(self, dsh_home, cwd, model, **kw):
        return _StubHarness(
            lambda p, s: (_ for _ in ()).throw(ConnectionError("runner 断链"))
        ), (dsh_home, cwd, model, kw)

    monkeypatch.setattr(DSHClient, "_create_harness", fake_create)
    client = DSHClient(station_timeout_s=5)
    with pytest.raises(NeedsHuman) as exc:
        client.call_station("understand", "p1", UnderstandOutput, "prompt",
                            retries=1)
    assert "ConnectionError" in str(exc.value)


# ================================================== runtime 明确报错（finish_reason=error）
class _ErrHarness:
    """假 runtime：返回 finish_reason=error 的结果，错误藏在 turn/end 事件里。"""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        self.calls = 0
        self.closed = False

    def run(self, prompt, session_id=None):
        from types import SimpleNamespace

        self.calls += 1
        events = [{"type": "turn/end",
                   "data": {"turn": 1,
                            "reason": {"kind": "error",
                                       "error": {"code": self.code,
                                                 "message": self.message}}}}]
        return SimpleNamespace(final_response="", finish_reason="error",
                               events=events)

    def close(self):
        self.closed = True


def _err_client(monkeypatch, harness) -> DSHClient:
    def fake_create(self, dsh_home, cwd, model, **kw):
        return harness, (dsh_home, cwd, model, kw)

    monkeypatch.setattr(DSHClient, "_create_harness", fake_create)
    return DSHClient(station_timeout_s=5)


def test_runtime_error_is_surfaced_not_masked(monkeypatch):
    """finish_reason=error 必须抬出 runtime 原文，不得伪装成"回复里没有 JSON"。

    真实踩过：station runtime 缺 API key（MISSING_CREDENTIAL），final_response 是空串，
    于是被 extract_json 报成 ``ValueError: 回复中未找到 JSON 对象``——把配置故障
    伪装成"模型输出不合法"，重试三轮永远修不好，人工介入也看不到线索。
    """
    from qresearch.dsh_client import StationRuntimeError

    harness = _ErrHarness("MISSING_CREDENTIAL",
                          'no API key for provider route "deepseek-official"')
    client = _err_client(monkeypatch, harness)
    with pytest.raises(StationRuntimeError) as exc:
        client._run("p", "s1")
    assert "MISSING_CREDENTIAL" in str(exc.value)
    assert "no API key" in str(exc.value)


def test_fatal_runtime_error_escalates_without_retry(monkeypatch, tmp_path):
    """凭据/配额类故障重试无意义：一次尝试即转人工，且带出真实原因。"""
    harness = _ErrHarness("MISSING_CREDENTIAL", "no API key for provider route")
    client = _err_client(monkeypatch, harness)
    log = EventLog(tmp_path / "e.jsonl")
    with pytest.raises(NeedsHuman) as exc:
        client.call_station("understand", "p1", UnderstandOutput, "prompt",
                            retries=2, event_log=log)
    assert harness.calls == 1, "凭据类故障不该空转重试"
    assert "MISSING_CREDENTIAL" in exc.value.reason
    assert "no API key" in exc.value.reason


def test_nonfatal_runtime_error_still_retries(monkeypatch):
    """非配置类 runtime 报错仍按原策略换 session 重试。"""
    harness = _ErrHarness("RUNTIME_CRASH", "runtime 崩了")
    client = _err_client(monkeypatch, harness)
    with pytest.raises(NeedsHuman):
        client.call_station("understand", "p1", UnderstandOutput, "prompt",
                            retries=1)
    assert harness.calls == 2, "非 fatal 错误应重试到上限"
