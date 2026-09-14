"""Phase 2 验收演示：科研问题 → 结构化 Goal → 候选假设 → plan v1（critic 通过）→ 人批 → 落账。

用法：
  .venv/Scripts/python.exe examples/phase2_demo.py                  # 真实 LLM + 交互审批
  .venv/Scripts/python.exe examples/phase2_demo.py --auto-approve   # 机器代批（仅演示）
  .venv/Scripts/python.exe examples/phase2_demo.py --offline        # 离线假模型（演示流程）
"""
import argparse
import shutil
import sys
from pathlib import Path

from qresearch.core.events import EventLog
from qresearch.core.storage import Storage
from qresearch.dsh_client import DSHClient, resolve_dsh_bin
from qresearch.loop import run_planning_phase

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "research_data" / "demo_phase2"

DEFAULT_QUESTION = (
    "计算一维自旋 1/2 Heisenberg 模型的基态能量密度与关联函数，"
    "分析有限尺寸效应，并与 Bethe ansatz 精确结果对照。"
)

OFFLINE_RESPONSES = {
    "understand": (
        '{"refined_question": "一维自旋 1/2 Heisenberg 模型的基态能量密度与关联函数及其收敛性",'
        ' "quantities": ["E0/L", "自旋关联函数 C(r)"],'
        ' "success_criteria": ["E0/L 与 1/4-ln2 对照到 1e-3", "至少两个系统尺寸比较", "关联函数收敛性检查"],'
        ' "constraints": {"max_L": 16},'
        ' "assumptions_made": ["自旋 1/2", "开放边界条件", "容差 1e-3 为合理默认"]}'
    ),
    "hypothesize": (
        '{"hypotheses": ['
        '{"statement": "有限尺寸外推后 E0/L 与 Bethe ansatz 值 1/4-ln2 一致到 1e-3",'
        ' "rationale": "一维 Heisenberg 链存在精确解，可作为对照锚点",'
        ' "falsification_tests": ["L=8 与 L=12 的 ED 结果与解析值偏差超过 1e-3"],'
        ' "discriminating_experiment": "simple_ed 在 L=8,12 上计算 E0"},'
        '{"statement": "关联函数随距离指数衰减且有限尺寸效应随 L 增大减小",'
        ' "rationale": "无能隙自旋液体图像",'
        ' "falsification_tests": ["L=8 与 L=12 的 C(r) 差异随 L 增大不减小"],'
        ' "discriminating_experiment": "parameter_scan 扫 L=8,12 的 C(r)"}]}'
    ),
    "plan": (
        '{"steps": ['
        '{"action": "run_experiment", "purpose": "L=8 小系统基准", "tools": ["simple_ed"],'
        ' "inputs": {"L": 8}, "expected_outputs": ["energy.csv"]},'
        '{"action": "parameter_scan", "purpose": "尺寸扫描 L=10,12 检查有限尺寸效应", "tools": ["simple_ed"],'
        ' "inputs": {"scan": {"L": [10, 12]}}, "expected_outputs": ["scan_energy.csv", "corr.csv"]},'
        '{"action": "compare_benchmark", "purpose": "与 Bethe ansatz 值 1/4-ln2 对照",'
        ' "expected_outputs": ["comparison.md"]}],'
        ' "risks": ["小系统有限尺寸效应可能掩盖真实偏差"], "diff_summary": null}'
    ),
    "critic": '{"verdict": "pass", "issues": []}',
}


def offline_runner(prompt: str, session_id: str) -> str:
    station = session_id.split(":")[1]
    return OFFLINE_RESPONSES[station]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="*", default=None)
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--offline", action="store_true", help="离线假模型，走通流程")
    parser.add_argument("--model", default=None)
    args = parser.parse_args()
    question = " ".join(args.question) if args.question else DEFAULT_QUESTION

    if DEMO_DIR.exists():
        shutil.rmtree(DEMO_DIR)
    DEMO_DIR.mkdir(parents=True)
    storage = Storage(DEMO_DIR / "state.sqlite")
    log = EventLog(DEMO_DIR / "events.jsonl")

    if args.offline:
        client = DSHClient(runner=offline_runner)
    else:
        print(f"dsh_bin: {resolve_dsh_bin()}")
        client = DSHClient(model=args.model)

    with client:
        plan = run_planning_phase(
            client, storage, log, project_id="proj_phase2_demo",
            question=question, auto_approve=args.auto_approve,
        )

    print("\n== 结果 ==")
    print(f"计划状态：{plan.status}（v{plan.version}）")
    print(f"事件日志：{log.path}（{len(log.events())} 条，Decision 链可追溯）")
    print(f"状态库  ：{storage.path}")
    storage.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
