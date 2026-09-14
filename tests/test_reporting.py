"""报告内容测试：置顶研究总结 + 计划版本历史 + 结论可追溯 + viz 冒烟（离线闭环）。"""
import json
import re

from qresearch.core.events import EventLog
from qresearch.core.storage import Storage
from qresearch.research_loop import run_research_loop
from qresearch.viz import plot_project
from conftest import Persistent
from test_research_loop import (
    HYP_OK,
    PLAN_OK,
    CRITIC_PASS,
    UNDERSTAND_OK,
    _analysis_response,
    _decide_factory,
    loop_client,
)


def _run(tmp_path, client, pid, rounds=3):
    storage = Storage(tmp_path / "state.sqlite")
    log = EventLog(tmp_path / "events.jsonl")
    summary = run_research_loop(client, storage, log, pid,
                                "Heisenberg 链基态研究", rounds=rounds,
                                auto_approve=True)
    return storage, log, summary


def test_report_has_top_summary(tmp_path, loop_client):
    """① 研究总结置顶：最终状态 / 结论（rationale 原文）/ 关键证据挂 id / 假设命运。"""
    storage, log, summary = _run(tmp_path, loop_client, "proj_report")
    assert summary["status"] == "terminated"
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    # 总结节在报告头部（先于假设/计划/实验各节）
    assert report.index("## 研究总结") < report.index("## 假设")
    assert "**最终状态**" in report and "`terminated`" in report
    # 结论 = 收束决策 rationale 原文；关键证据逐条挂 evidence id
    assert "按证据决策" in report
    assert "支撑结论的关键证据" in report
    assert re.search(r"evidence: `evidence_[0-9a-f]+`", report)
    # 假设命运（支持/反驳/未判定）
    assert "假设命运" in report and "证伪试验" in report
    # 规模统计
    assert "**规模**" in report


def test_report_has_plan_version_history(tmp_path, loop_client):
    """② 计划版本历史：每版一节（critic 判定、依据决策、步骤清单）。"""
    storage, log, summary = _run(tmp_path, loop_client, "proj_plans")
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## 计划版本历史" in report
    for v in (1, 2, 3):
        assert f"### 计划 v{v}" in report
    assert "critic 审查：pass" in report
    # replan 决策产生的后续版本标注依据决策
    assert "依据决策：`dec_" in report
    # 步骤行带工具名
    assert "工具：simple_ed" in report


def test_report_unconcluded_run_has_no_fake_conclusion(tmp_path, make_scripted_client):
    """未收束的运行（iterate 收尾）不得编造结论——总结如实标注。"""
    client = make_scripted_client({
        "understand": UNDERSTAND_OK, "hypothesize": HYP_OK,
        "plan": PLAN_OK, "critic": CRITIC_PASS,
        "analyze": [Persistent(_analysis_response)],
        "decide": [Persistent(_decide_factory(["iterate"]))],
    })
    storage, log, summary = _run(tmp_path, client, "proj_open", rounds=1)
    assert summary["status"] == "budget_exhausted"
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "本轮运行未给出最终结论" in report


def test_viz_plots_three_png(tmp_path, loop_client):
    """可视化冒烟：三张 PNG 落盘且非空（离线闭环的真实台账数据）。"""
    storage, log, summary = _run(tmp_path, loop_client, "proj_viz", rounds=2)
    plots = plot_project(storage, "proj_viz", tmp_path / "plots")
    assert [p.name for p in plots] == [
        "experiment_tools.png", "results_vs_param.png", "evidence_timeline.png"]
    for p in plots:
        assert p.exists() and p.stat().st_size > 0
    # 重跑覆盖同一路径（确定性、可重放）
    plots2 = plot_project(storage, "proj_viz", tmp_path / "plots")
    assert [p.name for p in plots2] == [p.name for p in plots]
