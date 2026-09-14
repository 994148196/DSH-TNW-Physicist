"""Phase 7 验收演示：新项目能检索复用旧项目经验（含"哪些实验没信息增益"）。

流程（计划 v2 §9 Phase 7）：
1. 旧项目（Heisenberg 基态，离线脚本 1 轮闭环，含一个必然失败的实验）→
   项目结束确定性蒸馏四层经验入跨项目记忆库；
2. 新项目（Heisenberg 关联函数，同问题域）→ 每轮 PLAN/DECIDE prompt 注入
   记忆库检索结果（失败案例优先）→ 断言旧项目教训出现在 prompt 里；
3. 新项目结束同样蒸馏入库（经验滚动积累）。

离线脚本模型（确定性、秒级）。记忆注入是 prompt 级机制，P5 live 已验证
真实 LLM 全流程；本演示验证的是记忆层的蒸馏/检索/注入与落账。

用法：
  .venv/Scripts/python.exe -X utf8 examples/phase7_demo.py
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
from qresearch.memory import MemoryStore, MemoryLayer, memory_digest
from qresearch.research_loop import run_research_loop

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research_data" / "demo_phase7"

UNDERSTAND = (
    '{"refined_question": "一维自旋 1/2 Heisenberg 链基态性质（PBC，J=1）",'
    ' "quantities": ["e0(N)", "关联函数"],'
    ' "success_criteria": ["与解析/文献值对照到给定容差", "两个以上系统尺寸比较"]}'
)
HYP_OK = (
    '{"hypotheses": ['
    '{"statement": "e0(N) 以 1/N^2 修正收敛到 Bethe 值", "rationale": "CFT 标度",'
    ' "falsification_tests": ["外推偏差超过 1e-3"], "discriminating_experiment": "ED 尺寸扫描"},'
    '{"statement": "近邻关联随 N 缓慢变化", "rationale": "准长程序",'
    ' "falsification_tests": ["两尺寸差异超过 5%"], "discriminating_experiment": "ED 关联"}]}'
)
CRITIC_PASS = '{"verdict": "pass", "issues": []}'

# 旧项目计划：step_2 故意用奇数 N（simple_ed 要求偶数）——必然失败，进失败案例库
PLAN_OLD = (
    '{"steps": ['
    '{"action": "run_experiment", "purpose": "基态基准", "tools": ["simple_ed"],'
    ' "inputs": {"N": 4, "want_gap": true}, "expected_outputs": ["E0", "gap"]},'
    '{"action": "run_experiment", "purpose": "奇数尺寸试探", "tools": ["simple_ed"],'
    ' "inputs": {"N": 3, "want_gap": true}, "expected_outputs": ["E0"]}],'
    ' "risks": ["有限尺寸效应"], "diff_summary": null}'
)
# 新项目计划：避开奇数 N（吸收记忆库教训），要关联函数
PLAN_NEW = (
    '{"steps": ['
    '{"action": "run_experiment", "purpose": "关联基准（小系统）", "tools": ["simple_ed"],'
    ' "inputs": {"N": 4, "want_corr": true}, "expected_outputs": ["nn_corr"]},'
    '{"action": "run_experiment", "purpose": "关联尺寸对照", "tools": ["simple_ed"],'
    ' "inputs": {"N": 6, "want_corr": true}, "expected_outputs": ["nn_corr"]}],'
    ' "risks": ["关联收敛慢"], "diff_summary": null}'
)


def _analysis_response(prompt: str, session_id: str) -> str:
    exp_ids = re.findall(r"\[(exp_[0-9a-f]+)\]", prompt)
    assert exp_ids, "分析阶段 prompt 中应包含已验证实验"
    return json.dumps({
        "observations": [{"claim": "能量/关联与解析趋势一致", "experiment_ids": exp_ids[:1]}],
        "interpretations": [{"claim": "支持 1/N^2 收敛假设", "experiment_ids": exp_ids[:1]}],
        "uncertainties": ["系统尺寸小"],
        "alternative_explanations": [],
        "recommended_next_steps": ["扩大 N 复用同一路线"],
    }, ensure_ascii=False)


def _declare_response(prompt: str, session_id: str) -> str:
    ev = re.findall(r"evidence_[0-9a-f]+", prompt)
    item = {"claim": "关键证据已核", "status": "passed" if ev else "untested",
            "evidence": ev[0] if ev else None}
    return json.dumps({"recommendation": "declare_result", "checklist": [item],
                       "info_gain_estimate": "high", "rationale": "证据支持结论"},
                      ensure_ascii=False)


def _make_client(responses: dict, plan_prompts: list[str], decide_prompts: list[str]):
    from qresearch.dsh_client import DSHClient

    queues = {k: (v if isinstance(v, list) else [v]) for k, v in responses.items()}

    def runner(prompt: str, session_id: str) -> str:
        station = session_id.split(":")[1]
        if station == "plan":
            plan_prompts.append(prompt)
        if station == "decide":
            decide_prompts.append(prompt)
        item = queues[station].pop(0)
        if callable(item):
            queues[station].insert(0, item)  # callable 常驻：按 prompt 现生成
            return item(prompt, session_id)
        return item

    return DSHClient(runner=runner)


def main() -> int:
    shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir(parents=True, exist_ok=True)
    store = MemoryStore(DATA / "memory.sqlite", markdown_path=DATA / "memory.md")

    plan_prompts: list[str] = []
    decide_prompts: list[str] = []

    # ============ 旧项目：基态研究（含必然失败的实验）========================
    print("== 旧项目：Heisenberg 基态（含奇数 N 失败实验） ==")
    old_dir = DATA / "proj_old"
    old_dir.mkdir()
    client = _make_client(
        {"understand": UNDERSTAND, "hypothesize": HYP_OK, "plan": [PLAN_OLD],
         "critic": CRITIC_PASS, "analyze": [_analysis_response], "decide": [_declare_response]},
        plan_prompts, decide_prompts,
    )
    old_storage = Storage(old_dir / "state.sqlite")
    log_old = EventLog(old_dir / "events.jsonl")
    try:
        summary = run_research_loop(
            client, old_storage, log_old, "proj_old",
            "一维自旋 1/2 Heisenberg 链基态能量与 Bethe ansatz 对照研究",
            rounds=1, auto_approve=True, memory_store=store,
        )
    finally:
        client.close()
        old_storage.close()
    assert summary["status"] == "terminated", summary
    print(f"  status={summary['status']}，蒸馏入库 …")
    for layer in MemoryLayer:
        print(f"  {layer.value} 层：{len(store.list(layer))} 条")
    assert all(store.list(l) for l in MemoryLayer), "四层记忆应全部非空"
    assert "要求偶数 N" in " ".join(
        e.content for e in store.list(MemoryLayer.failure)), "失败案例应记录奇数 N 教训"

    # ============ 新项目：关联函数（检索复用旧经验）==========================
    print("\n== 新项目：Heisenberg 关联函数（检索记忆库） ==")
    query = "一维 Heisenberg 链自旋关联函数的幂律衰减研究"
    digest, n_hits = memory_digest(store, query)
    print(f"  检索命中 {n_hits} 条，prompt 将注入：")
    for line in digest.splitlines()[:6]:
        print(f"    {line}")

    new_dir = DATA / "proj_new"
    new_dir.mkdir()
    client = _make_client(
        {"understand": UNDERSTAND, "hypothesize": HYP_OK, "plan": [PLAN_NEW],
         "critic": CRITIC_PASS, "analyze": [_analysis_response], "decide": [_declare_response]},
        plan_prompts, decide_prompts,
    )
    new_storage = Storage(new_dir / "state.sqlite")
    log_new = EventLog(new_dir / "events.jsonl")
    try:
        summary = run_research_loop(
            client, new_storage, log_new, "proj_new", query,
            rounds=1, auto_approve=True, memory_store=store,
        )
    finally:
        client.close()
        new_storage.close()
    assert summary["status"] == "terminated", summary

    # ============ 验收断言 ==================================================
    ok = True
    def _check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        ok = ok and cond

    print("\n== 验收 ==")
    _check("PLAN prompt 注入旧项目失败案例（教训：偶数 N）",
           "相关历史经验" in plan_prompts[-1]
           and "要求偶数 N" in plan_prompts[-1])
    _check("PLAN prompt 注入方法/工具经验", "方法经验" in plan_prompts[-1])
    _check("DECIDE prompt 注入失败案例（固定输入）",
           "失败案例" in decide_prompts[-1] and "要求偶数 N" in decide_prompts[-1])
    actions = [e.action for e in log_new.events(project_id="proj_new")]
    _check("memory_retrieved 每轮落账（PLAN+DECIDE）", actions.count("memory_retrieved") == 2)
    _check("memory_written 项目结束落账", actions.count("memory_written") == 1)
    # 新项目干净收尾：不产失败案例（失败层不增长才是对的），但方法/工具/项目层入库
    _check("新项目经验滚动入库（项目/方法/工具层）",
           any(e.source_project == "proj_new" for e in store.list(MemoryLayer.tool))
           and any(e.source_project == "proj_new" for e in store.list(MemoryLayer.method)))
    _check("失败案例层如实不增长（新项目无失败）",
           len(store.list(MemoryLayer.failure)) == 1)
    _check("工具名人类可读（非 ToolRecord 哈希）",
           "工具 simple_ed" in " ".join(e.content for e in store.list(MemoryLayer.tool)))
    _check("Markdown 镜像可读", (DATA / "memory.md").exists()
           and "失败案例层" in (DATA / "memory.md").read_text(encoding="utf-8"))

    print(f"\n记忆库：{DATA / 'memory.sqlite'}（镜像 {DATA / 'memory.md'}）")
    print(f"\nPHASE 7 DEMO: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
