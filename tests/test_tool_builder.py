"""Tool Builder 测试：修复循环 / 三层验证 / 注册与防串通卫生（离线脚本 client）。"""
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from conftest import Persistent
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
        "build": [Persistent(_writer([BROKEN_MODULE, GOOD_MODULE], prompts))],
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
    client = make_scripted_client({"build": [Persistent(_writer([BROKEN_MODULE] * 3, prompts))]})
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
        "build": [Persistent(_writer([GOOD_MODULE], prompts))],
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
        return make_scripted_client({"build": [Persistent(_writer([GOOD_MODULE], []))]})

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


# ------------------------------------------------- 站点故障必须可恢复（不得吞掉成果）
STATION_FAILURE = "模拟 runtime 断链：站点调用未干净返回"


def _writer_then_raise(module: str, prompts: list[str]):
    """先写出交付物、**再抛错** —— 精确复刻本平台实测到的失败形态。

    实测两次 hubbard 构建就是这么丢的：编码站其实已经把 tool_module.py 写进了
    workspace，但站点调用随后抛错（看门狗耗尽 StationTimeout），异常穿透
    build_tool 使整次构建崩掉，事件流里连 tool_build_attempt 都没有，
    已产出的交付物从此无人问津。
    """

    def _resp(prompt: str, session_id: str) -> str:
        prompts.append(prompt)
        workspace = Path(prompt.split("本次构建的工作目录：")[1].split("\n")[0].strip())
        target = workspace / "tool_module.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(module, encoding="utf-8")
        raise RuntimeError(STATION_FAILURE)

    return _resp


def test_build_station_failure_keeps_deliverable(tmp_path, db, make_scripted_client):
    """编码站调用抛错、但交付物已落盘 → 不崩，照常评测并走完注册。"""
    from qresearch.tool_builder import build_tool

    spec_path = _spec_in_tmp(tmp_path)
    prompts: list[str] = []
    client = make_scripted_client({
        "build": [_writer_then_raise(GOOD_MODULE, prompts)],
        "tool_critic": CRITIC_PASS,
    })
    log = EventLog(tmp_path / "events.jsonl")
    try:
        result = build_tool(
            client, db, log, spec_path,
            max_repair_rounds=1, builds_root=tmp_path / "builds", auto_approve=True,
        )
        actions = [e.action for e in log.events(project_id="toolbuild_tfim_ed")]
        assert "tool_build_station_failed" in actions, "站点故障必须如实落账"
        assert "tool_build_attempt" in actions, "已产出的交付物必须仍然被评测"
        assert result.status == "registered"
        assert result.attempts == 1
        # 注意：不能用 `"tfim_ed" in tool_names()` 断言注册成功——run_benchmark 会
        # load_seed_tools() 把真正的 tfim_ed 种子注册进来，那条断言恒真。
        # tool_registered 事件只在 status=="registered" 时发出，才是真凭据。
        assert "tool_registered" in actions
        assert "candidate_tfim_ed" not in tool_names()
    finally:
        unregister("tfim_ed")
        unregister("candidate_tfim_ed")


def test_build_station_failure_without_deliverable_fails(tmp_path, db, make_scripted_client):
    """编码站抛错且**没有**交付物 → 这一轮真的失败（不注册、不进批评环节）。"""
    from qresearch.tool_builder import build_tool

    def _raise(prompt, session_id):
        raise RuntimeError(STATION_FAILURE)

    spec_path = _spec_in_tmp(tmp_path)
    client = make_scripted_client({"build": [_raise]})
    log = EventLog(tmp_path / "events.jsonl")
    result = build_tool(
        client, db, log, spec_path,
        max_repair_rounds=1, builds_root=tmp_path / "builds", auto_approve=True,
    )
    actions = [e.action for e in log.events(project_id="toolbuild_tfim_ed")]
    assert result.status == "failed"
    assert result.golden_passed is False
    assert "tool_build_station_failed" in actions
    assert "tool_critic" not in actions
    assert "tool_registered" not in actions
    assert "candidate_tfim_ed" not in tool_names()


def test_critic_failure_does_not_register(tmp_path, db, make_scripted_client):
    """批评者站点故障 → 三道关缺一不可 → 不注册、不进审批、不留含糊的审批事件。"""
    from qresearch.tool_builder import build_tool

    def _critic_raise(prompt, session_id):
        raise RuntimeError(STATION_FAILURE)

    spec_path = _spec_in_tmp(tmp_path)
    prompts: list[str] = []
    client = make_scripted_client({
        "build": [Persistent(_writer([GOOD_MODULE], prompts))],
        "tool_critic": [_critic_raise],
    })
    log = EventLog(tmp_path / "events.jsonl")
    result = build_tool(
        client, db, log, spec_path,
        max_repair_rounds=1, builds_root=tmp_path / "builds", auto_approve=True,
    )
    actions = [e.action for e in log.events(project_id="toolbuild_tfim_ed")]
    assert "tool_critic_failed" in actions
    assert "tool_build_aborted_no_critic" in actions
    # 批评者缺席时不得伪造任何人/模型的审批动作
    assert "approve_tool" not in actions
    assert "reject_tool" not in actions
    assert "tool_critic" not in actions
    assert result.status == "rejected"
    assert result.critic_verdict is None
    assert result.golden_passed is True, "golden 与三层验证的成果必须保住"
    assert "tool_registered" not in actions, "批评者缺席却发生了注册"
    # 被拒路径也必须清理候选名（原实现只在"修复耗尽"分支清理，会漏）
    assert "candidate_tfim_ed" not in tool_names()


# ------------------------------------------- 重复构建的 session 名必须按运行隔离
MINIMAL_SPEC = {
    "tool": "trivial_probe",
    "version": "0.0.1",
    "type": "unit_test_double",
    "purpose": "测试替身：返回固定常量，仅用于验证构建流程本身",
    "physics_convention": "y = x（无物理含义）",
    "inputs": {"x": {"type": "int", "bounds": "0 <= x <= 10", "desc": "输入"}},
    "outputs": {"y": "输出 y = x"},
    "invariants": ["y == x"],
    "falsification_basis": ["恒等式 y = x"],
    "anti_collusion": "测试替身，不涉及真实工具构建",
    "known_limitations": ["仅用于流程测试"],
}

TRIVIAL_MODULE = '''\
from pydantic import BaseModel, Field


class Inputs(BaseModel):
    x: int = Field(ge=0, le=10)


def run(inputs: dict) -> dict:
    p = Inputs(**inputs)
    return {"y": p.x}
'''

TRIVIAL_GOLDEN = {
    "tool": "trivial_probe",
    "cases": [{
        "name": "identity",
        "layer": "software",
        "inputs": {"x": 3},
        "checks": [{"op": "approx", "field": "y", "value": 3.0, "tol": 0.0}],
    }],
}


def _minimal_spec(tmp_path: Path) -> Path:
    spec_dir = tmp_path / "tool_specs"
    spec_dir.mkdir(exist_ok=True)
    golden = spec_dir / "trivial_probe.golden.yaml"
    golden.write_text(yaml.safe_dump(TRIVIAL_GOLDEN, allow_unicode=True), encoding="utf-8")
    spec = dict(MINIMAL_SPEC)
    spec["fixtures_ref"] = str(golden)
    spec["fixtures_install_to"] = str(tmp_path / "installed" / "trivial_probe.yaml")
    spec_path = spec_dir / "trivial_probe.yaml"
    spec_path.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    return spec_path


def test_build_session_ids_are_run_scoped(tmp_path, db, make_scripted_client):
    """同一工具重复构建不得复用 DSH session 名（否则第二次构建 2 秒内就失败）。

    DSH 的 session 是**持久化**的：同名再建会抛
    ``JsonRpcError("session ... already exists")``。实测 2026-09-15 重跑 hubbard_ed
    时 ``...:build:a0`` 冲突，编码站调用当场报错。``call_station`` 早有实例级
    ``_session_nonce``，``run_agent`` 这条路径原本漏了——本测试守住它。

    用极小的测试替身 spec，不跑稠密对角化，故耗时约 1 秒级。
    """
    from qresearch.tool_builder import build_tool

    spec_path = _minimal_spec(tmp_path)
    seen: list[str] = []

    def _token(prompt: str, session_id: str) -> str:
        seen.append(session_id)
        workspace = Path(prompt.split("本次构建的工作目录：")[1].split("\n")[0].strip())
        target = workspace / "tool_module.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(TRIVIAL_MODULE, encoding="utf-8")
        return "done"

    def _client():
        return make_scripted_client({
            "build": [Persistent(_token)],
            "tool_critic": CRITIC_PASS,
        })

    try:
        for i in range(2):
            # 不要求 registered：测试替身的 golden 只有 software 层，三层验证会给出
            # uncertain（这是引擎的正确行为，与本测试要守的 session 隔离无关）。
            # 只需确认构建确实跑到了评测环节、且两轮的 session 名不同。
            result = build_tool(
                _client(), db, EventLog(tmp_path / f"e_{i}.jsonl"), spec_path,
                max_repair_rounds=1, builds_root=tmp_path / "builds", auto_approve=True,
            )
            assert result.golden_passed is True, result.failures

        build_ids = [s for s in seen if s.split(":")[1] == "build"]
        assert len(build_ids) == 2, build_ids
        # 两次构建的 session 名必须不同（运行期 nonce）
        assert build_ids[0] != build_ids[1], f"重复构建复用了 session 名：{build_ids}"
        # 且每次都是 project:build:<nonce>:aN 形态，nonce 非空
        for sid in build_ids:
            parts = sid.split(":")
            assert parts[1] == "build" and len(parts) >= 4 and parts[2], sid
    finally:
        unregister("trivial_probe")
        unregister("candidate_trivial_probe")
