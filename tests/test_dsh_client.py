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
    assert [e.action for e in log.events(project_id="p1")] == ["station_call"]
    assert log.events()[0].actor == Actor.MODEL


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
    sessions: list[str] = []

    def runner(prompt: str, session_id: str) -> str:
        sessions.append(session_id)
        return VALID_UNDERSTAND

    DSHClient(runner=runner).call_station("understand", "proj_x", UnderstandOutput, "p")
    assert sessions[0] == "proj_x:understand:a0"
