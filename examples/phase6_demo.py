"""Phase 6 验收演示：真实工具缺口（横场 Ising 链 ED）被自动补齐并通过三层验证。

流程（计划 v2 §9 Phase 6）：缺口 → Tool Spec（tool_specs/tfim_ed.yaml，出题侧先定稿）
→ DSH 编码（隔离 workspace，演示中注入"横场偏移 0.01"的带错首版）
→ golden 自动跑（抓住错误 → 回喂修复）→ 三层验证 → 批评者审查 → 注册 → fixtures 安装。

默认离线脚本模型（确定性、秒级）；`--live` 经 DSH 调真实 LLM 编码（每轮数分钟）。

用法：
  .venv/Scripts/python.exe -X utf8 examples/phase6_demo.py           # 离线
  .venv/Scripts/python.exe -X utf8 examples/phase6_demo.py --live    # 真实 LLM
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qresearch.core.events import EventLog
from qresearch.core.storage import Storage
from qresearch.tools.registry import get_tool, tool_names

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research_data" / "demo_phase6"

GOOD_MODULE = '''\
"""一维横场 Ising 链精确对角化（PBC 单计数）。
约定：H = -J Σ_i σ^x_i σ^x_{i+1} - h Σ_i σ^z_i（泡利矩阵）。
"""
import numpy as np
from pydantic import BaseModel, Field

class Inputs(BaseModel):
    N: int = Field(ge=2, le=20)
    J: float = 1.0
    h: float = Field(default=1.0, ge=0)
    want_gap: bool = True

def run(inputs: dict) -> dict:
    p = Inputs(**inputs)
    N, J, h = p.N, p.J, p.h
    dim = 2 ** N
    H = np.zeros((dim, dim))
    for state in range(dim):
        bits = [(state >> i) & 1 for i in range(N)]
        H[state, state] += -h * sum(1 - 2 * b for b in bits)
        for i in range(N):
            j = (i + 1) % N
            H[state ^ (1 << i) ^ (1 << j), state] += -J
    evals, evecs = np.linalg.eigh(H)
    psi0 = evecs[:, 0]
    out = {
        "E0": float(evals[0]), "e0": float(evals[0] / N),
        "residual": float(np.linalg.norm(H @ psi0 - evals[0] * psi0)),
        "dim": dim, "method": "dense_eigh",
    }
    if p.want_gap:
        out["gap"] = float(evals[1] - evals[0])
    return out
'''

# 带错首版：横场偏移 0.01（数值自洽但物理错误——golden 的解析锚点应当场抓住）
BROKEN_MODULE = GOOD_MODULE.replace(
    "H[state, state] += -h * sum(1 - 2 * b for b in bits)",
    "H[state, state] += -(h + 0.01) * sum(1 - 2 * b for b in bits)",
)
assert BROKEN_MODULE != GOOD_MODULE


def _offline_client():
    from qresearch.dsh_client import DSHClient

    state = {"n": 0}

    def runner(prompt: str, session_id: str) -> str:
        station = session_id.split(":")[1]
        if station == "build":
            workspace = Path(prompt.split("本次构建的工作目录：")[1].split("\n")[0].strip())
            module = BROKEN_MODULE if state["n"] == 0 else GOOD_MODULE
            (workspace / "tool_module.py").write_text(module, encoding="utf-8")
            state["n"] += 1
            return "已实现并自测。"
        if station == "tool_critic":
            return json.dumps({"verdict": "pass", "issues": []}, ensure_ascii=False)
        raise AssertionError(f"意外的站点: {station}")

    return DSHClient(runner=runner)


def main() -> int:
    live = "--live" in sys.argv
    shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir(parents=True, exist_ok=True)
    storage = Storage(DATA / "state.sqlite")
    log = EventLog(DATA / "events.jsonl")

    if live:
        sandbox = DATA / "sandbox"
        sandbox.mkdir(parents=True, exist_ok=True)
        from qresearch.dsh_client import DSHClient

        client = DSHClient(
            cwd=sandbox, dsh_home=DATA / "dsh_home",  # 独立 home：不与其他运行争用会话存储
        )  # 草稿写沙箱；交付写 prompt 里的工作目录
    else:
        client = _offline_client()

    from qresearch.tool_builder import build_tool

    print("== 缺口 ==")
    assert "tfim_ed" not in tool_names(), "tfim_ed 不应已注册（否则不是缺口）"
    print("  注册表:", ", ".join(tool_names()), "→ 缺 tfim_ed（横场 Ising 链 ED）")
    print("  spec 先行:", ROOT / "tool_specs" / "tfim_ed.yaml",
          "+ golden 定值（解析/文献/独立 oracle）")

    try:
        result = build_tool(
            client, storage, log, ROOT / "tool_specs" / "tfim_ed.yaml",
            auto_approve=True,  # 演示用；真实流程为人工审批（actor 留痕）
            builds_root=DATA / "builds",
        )
    finally:
        client.close()

    print("\n== 构建结果 ==")
    print(f"  status: {result.status}（尝试 {result.attempts} 轮）")
    print(f"  三层验证: {result.verification_overall}  批评者: {result.critic_verdict}")
    print(f"  报告: {result.report_path}")

    ok = result.status == "registered" and result.verification_overall == "passed"
    if ok:
        # 注册后：golden 套件可跑、研究闭环可引用（证据资格门放行）。
        # 注意：子进程 runner 只认种子工具——构建工具经人工批准"晋升"进
        # qresearch/tools/ 后才可用于子进程模式；此处用进程内 runner 验证接入。
        from qresearch.experiments.manager import ExperimentManager, InProcessToolRunner
        from qresearch.core.status import VerificationStatus
        from qresearch.verification.manager import VerificationManager

        mgr = ExperimentManager(storage, log, experiments_root=DATA / "experiments",
                                runner=InProcessToolRunner())
        exp = mgr.execute_step(_mini_plan(storage), _mini_step())
        report = VerificationManager(storage, log).verify_experiment(exp, "tfim_ed")
        ok = report.overall == VerificationStatus.PASSED
        print(f"\n== 闭环接入 == 实验 {exp.experiment_id} 验证 {report.overall.value}"
              f"（证据资格门{'放行' if ok else '关闭'}）")
        print(f"  E0(N=6, h=2) = {get_tool('tfim_ed').run({'N': 6, 'h': 2.0})['E0']:.10f}")

    print(f"\nPHASE 6 DEMO: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def _mini_plan(storage):
    from qresearch.core.models import PlanStep, ResearchPlan

    return ResearchPlan(
        project_id="proj_phase6_accept", goal_id="goal_phase6_accept", version=1,
        steps=[PlanStep(
            step_id="step_1", action="run_experiment", purpose="缺口工具验收",
            tools=["tfim_ed"], inputs={"N": 6, "h": 2.0, "want_gap": True},
            expected_outputs=["E0", "gap"],
        )],
        risks=[], diff_summary=None,
    )


def _mini_step():
    from qresearch.core.models import PlanStep

    return PlanStep(
        step_id="step_1", action="run_experiment", purpose="缺口工具验收",
        tools=["tfim_ed"], inputs={"N": 6, "h": 2.0, "want_gap": True},
        expected_outputs=["E0", "gap"],
    )


if __name__ == "__main__":
    raise SystemExit(main())
