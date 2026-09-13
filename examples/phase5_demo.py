"""Phase 5 验收演示：MVP 问题全自动 3 轮研究闭环，全程可回放。

默认使用离线脚本模型（确定性、秒级完成，验证机制本身）；
`--live` 经 DSH 调真实 LLM 跑同样流程（每轮数分钟）。
人工触点：计划审批与终止确认——演示用 --auto 标记 actor=system 留痕。

用法：
  .venv/Scripts/python.exe -X utf8 examples/phase5_demo.py           # 离线
  .venv/Scripts/python.exe -X utf8 examples/phase5_demo.py --live    # 真实 LLM
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qresearch.core.events import EventLog
from qresearch.core.storage import Storage
from qresearch.research_loop import run_research_loop

DATA = Path(__file__).resolve().parents[1] / "research_data" / "demo_phase5"

# ---- 离线脚本模型（与 tests/test_research_loop.py 同机制）------------------
UNDERSTAND_OK = (
    '{"refined_question": "一维自旋 1/2 Heisenberg 链基态能量密度收敛性",'
    ' "quantities": ["e0(N)", "e0 = 1/4 - ln 2"],'
    ' "success_criteria": ["外推与 Bethe ansatz 一致到 1e-3", "两个以上系统尺寸比较"]}'
)
HYP_OK = (
    '{"hypotheses": ['
    '{"statement": "e0(N) 以 1/N^2 修正收敛到 Bethe 值", "rationale": "CFT 标度",'
    ' "falsification_tests": ["外推偏差超过 1e-3"], "discriminating_experiment": "ED 尺寸扫描"},'
    '{"statement": "小系统 N<=8 即可达到 1e-3 精度", "rationale": "待检验的乐观估计",'
    ' "falsification_tests": ["N=8 与 N=12 差异超过 1%"], "discriminating_experiment": "双尺寸对照"}]}'
)
PLAN_OK = (
    '{"steps": ['
    '{"action": "run_experiment", "purpose": "小系统基准", "tools": ["simple_ed"],'
    ' "inputs": {"N": 4, "want_gap": true}, "expected_outputs": ["E0", "gap"]},'
    '{"action": "run_experiment", "purpose": "尺寸对照", "tools": ["simple_ed"],'
    ' "inputs": {"N": 8}, "expected_outputs": ["E0"]}],'
    ' "risks": ["有限尺寸效应"], "diff_summary": null}'
)
CRITIC_PASS = '{"verdict": "pass", "issues": []}'


def _analysis_response(prompt: str, session_id: str) -> str:
    exp_ids = re.findall(r"\[(exp_[0-9a-f]+)\]", prompt)
    assert exp_ids, "分析阶段 prompt 中应包含已验证实验"
    return json.dumps({
        "observations": [{
            "claim": "N=4 基态能量与解析值 -2J 一致；e0(N) 随 N 上升趋向 Bethe 值",
            "experiment_ids": exp_ids[:1],
        }],
        "interpretations": [{
            "claim": "支持假设一（1/N^2 收敛），数值上无异常",
            "experiment_ids": exp_ids[:1],
        }],
        "uncertainties": ["系统尺寸小，外推未经大 N 验证"],
        "alternative_explanations": [],
        "recommended_next_steps": ["扩大 N 扫描并复用同一路线"],
    }, ensure_ascii=False)


def _decide_factory(recommendations: list[str]):
    state = {"call": 0}

    def _resp(prompt: str, session_id: str) -> str:
        rec = recommendations[state["call"]]
        state["call"] += 1
        ev = re.findall(r"evidence_[0-9a-f]+", prompt)
        item = {"claim": "关键证据已核", "status": "passed" if ev else "untested",
                "evidence": ev[0] if ev else None}
        return json.dumps({"recommendation": rec, "checklist": [item],
                           "info_gain_estimate": "high",
                           "rationale": "证据支持当前路线，继续推进"},
                          ensure_ascii=False)

    return _resp


def main() -> int:
    live = "--live" in sys.argv
    shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir(parents=True, exist_ok=True)
    storage = Storage(DATA / "state.sqlite")
    log = EventLog(DATA / "events.jsonl")

    from qresearch.dsh_client import DSHClient

    if live:
        client = DSHClient()  # 真实 DSH runtime + DeepSeek 模型
    else:
        queues = {
            "understand": [UNDERSTAND_OK], "hypothesize": [HYP_OK],
            "plan": [PLAN_OK] * 3, "critic": [CRITIC_PASS] * 3,
            "analyze": [_analysis_response], "decide": [_decide_factory(
                ["iterate", "iterate", "declare_result"])],
        }

        def scripted(prompt: str, session_id: str) -> str:
            station = session_id.split(":")[1]
            q = queues[station]
            item = q.pop(0)
            if callable(item):
                q.insert(0, item)
                return item(prompt, session_id)
            return item

        client = DSHClient(runner=scripted)

    summary = run_research_loop(
        client, storage, log, "proj_phase5_demo",
        "一维自旋 1/2 反铁磁 Heisenberg 链基态能量密度与 Bethe ansatz 对照研究",
        rounds=3, auto_approve=True,
    )

    print("\n== 闭环摘要 ==")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    print("\n== 决策链 ==")
    from qresearch.core.models import Decision

    for d_id in summary["decisions"]:
        d = storage.get(Decision, d_id)
        print(f"  [{d.type.value}] {d.recommendation.value} — {(d.rationale or '')[:80]}")
    print(f"\n事件回放：{DATA / 'events.jsonl'}（{len(log.events(project_id='proj_phase5_demo'))} 条）")
    print(f"研究报告：{summary['report']}")

    ok = summary["status"] == "terminated" and summary["rounds_used"] == 3
    print(f"\nPHASE 5 DEMO: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
