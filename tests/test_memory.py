"""Research Memory 测试：四层蒸馏 / 检索 / prompt 注入 / 失败案例固定输入。"""
import json
import re

import pytest
from qresearch.core.events import EventLog
from qresearch.core.models import (
    Decision, Experiment, Evidence, Goal, Hypothesis, Project, VerificationReport,
    VerificationReportItem,
)
from qresearch.core.status import (
    DecisionRecommendation, DecisionType, EvidenceType, ExperimentStatus,
    VerificationStatus,
)
from qresearch.memory import MemoryEntry, MemoryLayer, MemoryStore, distill_project, memory_digest

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
    ' "inputs": {"N": 4}, "expected_outputs": ["E0"]},'
    '{"action": "run_experiment", "purpose": "奇数 N 误配", "tools": ["simple_ed"],'
    ' "inputs": {"N": 3}, "expected_outputs": ["E0"]}],'
    ' "risks": [], "diff_summary": null}'
)
CRITIC_PASS = '{"verdict": "pass", "issues": []}'


# ================================================================ 蒸馏
def _seed_project(storage, pid: str) -> None:
    """合成一个带失败实验与验证未通过实验的项目台账。"""
    storage.save(Project(project_id=pid, title="Heisenberg 研究", question="Heisenberg 链基态能量"))
    storage.save(Goal(project_id=pid, question="Heisenberg 链基态能量",
                      quantities=["e0"], success_criteria=["对照 1/4-ln2"]))
    storage.save(Hypothesis(project_id=pid, statement="外推与 BA 一致",
                            falsification_tests=["偏差超 1e-3"]))
    good = Experiment(project_id=pid, plan_id="plan_x", step_id="step_1",
                      tool_id="simple_ed", parameters={"N": 4},
                      status=ExperimentStatus.COMPLETED)
    bad = Experiment(project_id=pid, plan_id="plan_x", step_id="step_2",
                     tool_id="simple_ed", parameters={"N": 3},
                     status=ExperimentStatus.FAILED, error="simple_ed 要求偶数 N")
    shaky = Experiment(project_id=pid, plan_id="plan_x", step_id="step_3",
                       tool_id="simple_ed", parameters={"N": 6},
                       status=ExperimentStatus.COMPLETED)
    storage.save(good)
    storage.save(bad)
    storage.save(shaky)
    storage.save(Evidence(project_id=pid, type=EvidenceType.NUMERICAL,
                          claim="N=4 能量与解析值 -2J 一致", source_experiment=good.experiment_id))
    storage.save(VerificationReport(
        project_id=pid, experiment_id=good.experiment_id,
        items=[VerificationReportItem(claim="结果可读", layer="software",
                                      status=VerificationStatus.PASSED)],
        overall=VerificationStatus.PASSED,
    ))
    storage.save(VerificationReport(
        project_id=pid, experiment_id=shaky.experiment_id,
        items=[VerificationReportItem(claim="复跑不可重复", layer="software",
                                      status=VerificationStatus.FAILED,
                                      detail="两次结果超容差")],
        overall=VerificationStatus.FAILED,
    ))
    storage.save(Decision(project_id=pid, type=DecisionType.DECLARE_RESULT,
                          recommendation=DecisionRecommendation.ACCEPT,
                          rationale="证据充分"))


def test_distill_four_layers(db):
    _seed_project(db, "proj_old")
    entries = distill_project(db, "proj_old")
    layers = {e.layer for e in entries}
    assert layers == set(MemoryLayer)

    project = next(e for e in entries if e.layer is MemoryLayer.project)
    assert "Heisenberg 链基态能量" in project.content
    assert "accept" in project.content  # 决策轨迹

    method = [e for e in entries if e.layer is MemoryLayer.method]
    assert any("-2J 一致" in e.content for e in method)

    failures = [e for e in entries if e.layer is MemoryLayer.failure]
    # FAILED 实验（含错误信息）与"完成但验证未通过"各一条
    assert len(failures) == 2
    assert any("要求偶数 N" in e.content for e in failures)
    assert any("不得引用为证据" in e.content for e in failures)

    tool = next(e for e in entries if e.layer is MemoryLayer.tool)
    assert tool.tags[:1] == ["simple_ed"] and "使用 3 次" in tool.content
    assert "heisenberg" in tool.tags  # 问题域标签：相似问题可检索


def test_store_search_and_markdown(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite", markdown_path=tmp_path / "memory.md")
    store.add(MemoryEntry(layer=MemoryLayer.failure, title="失败案例：奇数 N",
                          content="simple_ed 要求偶数 N，奇数输入直接失败", tags=["simple_ed"],
                          source_project="proj_a"))
    store.add(MemoryEntry(layer=MemoryLayer.method, title="方法经验：1/N^2 外推",
                          content="e0(N) 用 1/N^2 修正外推，窗口 N>=8 稳定", tags=["heisenberg"],
                          source_project="proj_a"))

    hits = store.search("heisenberg 外推")
    assert hits and "1/N^2" in hits[0].content
    assert store.search("外推", layers=[MemoryLayer.failure]) == []
    assert len(store.list(MemoryLayer.failure)) == 1

    md = (tmp_path / "memory.md").read_text(encoding="utf-8")
    for header in ("项目层", "方法层", "工具层", "失败案例层"):
        assert header in md
    assert "失败案例：奇数 N" in md
    store.close()


# ================================================================ 闭环注入
def _analysis_response(prompt: str, session_id: str) -> str:
    exp_ids = re.findall(r"\[(exp_[0-9a-f]+)\]", prompt)
    return json.dumps({
        "observations": [{"claim": "能量与解析值一致", "experiment_ids": exp_ids[:1]}],
        "interpretations": [], "uncertainties": ["系统尺寸仍小"],
        "alternative_explanations": [], "recommended_next_steps": ["扩大 N"],
    }, ensure_ascii=False)


def _declare(prompt: str, captured: list[str]) -> str:
    captured.append(prompt)
    ev = re.findall(r"evidence_[0-9a-f]+", prompt)
    item = {"claim": "关键证据已核", "status": "passed" if ev else "untested",
            "evidence": ev[0] if ev else None}
    return json.dumps({"recommendation": "declare_result", "checklist": [item],
                       "info_gain_estimate": "high", "rationale": "证据充分"},
                      ensure_ascii=False)


def test_loop_injects_memory_and_distills(tmp_path, db, make_scripted_client):
    """记忆库经验进 PLAN/DECIDE prompt（失败案例优先）；项目结束蒸馏入库。"""
    from qresearch.research_loop import run_research_loop

    store = MemoryStore(tmp_path / "memory.sqlite", markdown_path=tmp_path / "memory.md")
    # 预置一条与本项目问题相关的失败案例（检索应命中）
    store.add(MemoryEntry(
        layer=MemoryLayer.failure, title="失败案例：parameter_scan 缺 inputs.scan",
        content="把单次计算误标为 parameter_scan 导致重试耗尽；scan 应为 {参数名: [取值]}",
        tags=["heisenberg"], source_project="proj_ancient",
    ))

    plan_prompts: list[str] = []
    decide_prompts: list[str] = []
    client = make_scripted_client({
        "understand": UNDERSTAND_OK,
        "hypothesize": HYP_OK,
        "plan": [lambda p, s: (plan_prompts.append(p), PLAN_OK)[1]],
        "critic": CRITIC_PASS,
        "analyze": [_analysis_response],
        "decide": [lambda p, s: _declare(p, decide_prompts)],
    })
    log = EventLog(tmp_path / "events.jsonl")
    pid = "proj_memory_loop"
    summary = run_research_loop(
        client, db, log, pid,
        "Heisenberg 链基态能量研究", rounds=1, auto_approve=True,
        memory_store=store,
    )
    assert summary["status"] == "terminated"

    # PLAN prompt 注入了检索到的失败案例
    assert plan_prompts and "相关历史经验" in plan_prompts[0]
    assert "失败案例：parameter_scan 缺 inputs.scan" in plan_prompts[0]
    # DECIDE prompt 也注入（失败案例优先）
    assert decide_prompts and "失败案例：parameter_scan 缺 inputs.scan" in decide_prompts[0]

    # 事件：每轮 2 次检索 + 结束时 1 次蒸馏入库
    actions = [e.action for e in log.events(project_id=pid)]
    assert actions.count("memory_retrieved") == 2
    assert actions.count("memory_written") == 1

    # 本项目经验已入库：四层齐备（奇数 N 失败也进了失败案例库）
    entries = [e for e in store.list() if e.source_project == pid]
    assert {e.layer for e in entries} == set(MemoryLayer)
    assert any("要求偶数 N" in e.content for e in entries if e.layer is MemoryLayer.failure)
    store.close()


def test_loop_without_memory_store(tmp_path, db, make_scripted_client):
    """未接记忆库：prompt 用（无）占位、无 memory 事件——闭环行为不变。"""
    from qresearch.research_loop import run_research_loop

    plan_prompts: list[str] = []
    decide_prompts: list[str] = []
    client = make_scripted_client({
        "understand": UNDERSTAND_OK,
        "hypothesize": HYP_OK,
        "plan": [lambda p, s: (plan_prompts.append(p), PLAN_OK)[1]],
        "critic": CRITIC_PASS,
        "analyze": [_analysis_response],
        "decide": [lambda p, s: _declare(p, decide_prompts)],
    })
    log = EventLog(tmp_path / "events.jsonl")
    summary = run_research_loop(
        client, db, log, "proj_nomem",
        "Heisenberg 链基态能量研究", rounds=1, auto_approve=True,
    )
    assert summary["status"] == "terminated"
    assert "相关历史经验" in plan_prompts[0] and "（无）" in plan_prompts[0]
    assert "（无）" in decide_prompts[0]
    actions = [e.action for e in log.events(project_id="proj_nomem")]
    assert "memory_retrieved" not in actions and "memory_written" not in actions


def test_memory_digest_empty_store(tmp_path):
    store = MemoryStore(tmp_path / "m.sqlite")
    digest, n = memory_digest(store, "heisenberg")
    assert n == 0 and "暂无相关历史经验" in digest
    digest, n = memory_digest(None, "heisenberg")
    assert n == 0
    store.close()
