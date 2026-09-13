"""Tool Builder 测试：修复循环 / 三层验证 / 注册与防串通卫生（离线脚本 client）。"""
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from qresearch.core.events import EventLog
from qresearch.core.storage import Storage
from qresearch.tools.registry import get_tool, tool_names, unregister

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "tool_specs" / "tfim_ed.yaml"

GOOD_MODULE = '''\
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

# 横场偏移 0.01 的错误实现：数值自洽（residual 小）但物理错误。
# 注：不能用 +J 反铁磁当"错误"——偶数双分环上反铁磁 TFIM 与铁磁谱完全等价（规范变换），
# 那样的"破坏"会被 golden 全部放行。
BROKEN_MODULE = GOOD_MODULE.replace(
    "H[state, state] += -h * sum(1 - 2 * b for b in bits)",
    "H[state, state] += -(h + 0.01) * sum(1 - 2 * b for b in bits)",
)
assert BROKEN_MODULE != GOOD_MODULE

CRITIC_PASS = json.dumps({"verdict": "pass", "issues": []}, ensure_ascii=False)


def _spec_in_tmp(tmp_path: Path) -> Path:
    """spec 副本：fixtures 安装目标改指 tmp，避免测试写仓库 benchmarks/golden。"""
    raw = yaml.safe_load(SPEC.read_text(encoding="utf-8"))
    raw["fixtures_install_to"] = str(tmp_path / "installed" / "tfim_ed.yaml")
    out = tmp_path / "tfim_ed.yaml"
    out.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return out


def _writer(modules: list[str], prompts: list[str]):
    state = {"n": 0}

    def _resp(prompt: str, session_id: str) -> str:
        prompts.append(prompt)
        workspace = Path(prompt.split("本次构建的工作目录：")[1].split("\n")[0].strip())
        target = workspace / "tool_module.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(modules[min(state["n"], len(modules) - 1)], encoding="utf-8")
        state["n"] += 1
        return "done"

    return _resp


def test_build_success_after_repair(tmp_path, db, make_scripted_client):
    """符号写反的候选被 golden 抓住 → 回喂修复 → 三层验证 → 注册。"""
    from qresearch.tool_builder import build_tool

    spec_path = _spec_in_tmp(tmp_path)
    prompts: list[str] = []
    client = make_scripted_client({
        "build": [_writer([BROKEN_MODULE, GOOD_MODULE], prompts)],
        "tool_critic": CRITIC_PASS,
    })
    log = EventLog(tmp_path / "events.jsonl")
    try:
        result = build_tool(
            client, db, log, spec_path,
            builds_root=tmp_path / "builds", auto_approve=True,
        )
        assert result.status == "registered"
        assert result.attempts == 2
        assert result.verification_overall == "passed"
        assert result.critic_verdict == "pass"
        # 正式名已注册、候选名已清理
        from qresearch.verification.golden import dense_tfim_ground

        assert get_tool("tfim_ed").run({"N": 4, "h": 0.5, "J": 1.0})["E0"] == pytest.approx(
            dense_tfim_ground(4, 0.5)["E0"], abs=1e-9
        )
        assert "candidate_tfim_ed" not in tool_names()
        # fixtures 已安装
        assert (tmp_path / "installed" / "tfim_ed.yaml").exists()
        # 事件链
        actions = [e.action for e in log.events(project_id="toolbuild_tfim_ed")]
        assert actions.count("tool_build_attempt") == 2
        assert "tool_critic" in actions and "approve_tool" in actions
        assert actions[-1] == "tool_registered"
        # 构建报告
        assert "registered" in result.report_path.read_text(encoding="utf-8")
    finally:
        unregister("tfim_ed")
        unregister("candidate_tfim_ed")


def test_build_repair_exhausted(tmp_path, db, make_scripted_client):
    """始终错的实现：修复耗尽 → failed，不注册、不进批评环节。"""
    from qresearch.tool_builder import build_tool

    spec_path = _spec_in_tmp(tmp_path)
    prompts: list[str] = []
    client = make_scripted_client({"build": [_writer([BROKEN_MODULE] * 3, prompts)]})
    log = EventLog(tmp_path / "events.jsonl")
    result = build_tool(
        client, db, log, spec_path,
        max_repair_rounds=3, builds_root=tmp_path / "builds", auto_approve=True,
    )
    assert result.status == "failed"
    assert result.attempts == 3
    assert result.golden_passed is False
    assert result.failures
    assert "tfim_ed" not in tool_names()
    assert "candidate_tfim_ed" not in tool_names()
    assert "tool_critic" not in [e.action for e in log.events(project_id="toolbuild_tfim_ed")]
    assert "failed" in result.report_path.read_text(encoding="utf-8")


def test_build_prompt_contains_no_fixture_values(tmp_path, db, make_scripted_client):
    """防串通卫生：编码 prompt 不含任何基准数值/容差。"""
    from qresearch.tool_builder import build_tool

    spec_path = _spec_in_tmp(tmp_path)
    prompts: list[str] = []
    # 只交好实现，走通到批评者即可
    client = make_scripted_client({
        "build": [_writer([GOOD_MODULE], prompts)],
        "tool_critic": CRITIC_PASS,
    })
    try:
        build_tool(client, db, EventLog(tmp_path / "e2.jsonl"), spec_path,
                   builds_root=tmp_path / "builds", auto_approve=True)
        build_prompt = prompts[0]
        for leak in ("-4.0", "-8.0", "-1.2732395447351627", "1.0e-10", "-0.4431"):
            assert leak not in build_prompt, f"prompt 泄漏基准值: {leak}"
    finally:
        unregister("tfim_ed")
        unregister("candidate_tfim_ed")


def test_build_client_factory_per_attempt(tmp_path, db, make_scripted_client):
    """client_factory：每轮 attempt 以独立 client（cwd=workspace）编码，交付必落工作目录。"""
    from qresearch.tool_builder import build_tool

    spec_path = _spec_in_tmp(tmp_path)
    calls: list[Path] = []

    def factory(workspace: Path):
        calls.append(workspace)
        return make_scripted_client({"build": [_writer([GOOD_MODULE], [])]})

    client = make_scripted_client({"tool_critic": CRITIC_PASS})
    try:
        result = build_tool(
            client, db, EventLog(tmp_path / "e3.jsonl"), spec_path,
            builds_root=tmp_path / "builds", auto_approve=True,
            client_factory=factory,
        )
        assert result.status == "registered"
        assert result.attempts == 1
        assert len(calls) == 1
        assert (calls[0] / "tool_module.py").exists()
    finally:
        unregister("tfim_ed")
        unregister("candidate_tfim_ed")
