"""Verification Manager 测试：三层验证、VerificationReport、证据资格门、错误工具捕获。"""
import json
import random
import pytest
from qresearch.core.events import EventLog
from qresearch.core.models import Experiment, VerificationReportItem
from qresearch.core.status import VerificationStatus
from qresearch.core.storage import Storage
from qresearch.tools import registry
from qresearch.tools.registry import ToolSpec, load_seed_tools, register
from qresearch.tools.simple_ed import SimpleEDInputs
from qresearch.verification.manager import VerificationManager


@pytest.fixture()
def vr(tmp_path):
    storage = Storage(tmp_path / "s.sqlite")
    log = EventLog(tmp_path / "e.jsonl")
    yield VerificationManager(storage, log)
    storage.close()


def _experiment(tmp_path, result: dict, params: dict | None = None) -> Experiment:
    out = tmp_path / "exp_ws"
    out.mkdir(parents=True, exist_ok=True)
    result_path = out / "result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    return Experiment(
        project_id="p1", plan_id="pl1", step_id="s1", tool_id="tool_x",
        parameters=params or {"N": 4}, artifacts=[str(result_path)],
    )


def _replace_simple_ed(transform) -> None:
    """同名替换已注册的 simple_ed 实现——真实威胁模型：已注册工具的代码被改错。"""
    def run(inputs: dict) -> dict:
        return transform(registry._REGISTRY["simple_ed_orig"].run(inputs))

    registry._REGISTRY["simple_ed"] = ToolSpec(
        name="simple_ed", version="1.0.0-broken", type="exact_diagonalization",
        description="被注入错误的 simple_ed", input_model=SimpleEDInputs, run=run,
    )


def _restore_simple_ed() -> None:
    registry._REGISTRY["simple_ed"] = registry._REGISTRY.pop("simple_ed_orig")


def test_verify_tool_three_layers_all_pass(vr):
    load_seed_tools()
    items = vr.verify_tool("simple_ed")
    layers = {i.layer for i in items}
    assert layers == {"software", "numerical", "physics"}
    assert all(i.status == VerificationStatus.PASSED for i in items), [
        (i.layer, i.claim, i.detail) for i in items if i.status != VerificationStatus.PASSED
    ]


def test_sabotaged_tool_caught(vr):
    load_seed_tools()
    registry._REGISTRY["simple_ed_orig"] = registry._REGISTRY["simple_ed"]
    try:
        _replace_simple_ed(lambda r: {**r, "E0": -r["E0"], "e0": -r["e0"]})
        items = vr.verify_tool("simple_ed")
        by_layer = {i.layer: i for i in items}
        assert by_layer["software"].status == VerificationStatus.FAILED  # 解析值不符
        assert by_layer["physics"].status == VerificationStatus.FAILED  # Bethe 极限/差分不符
    finally:
        _restore_simple_ed()


def test_subtle_offset_tool_caught(vr):
    load_seed_tools()
    registry._REGISTRY["simple_ed_orig"] = registry._REGISTRY["simple_ed"]
    try:
        _replace_simple_ed(lambda r: {**r, "E0": r["E0"] - 0.01, "e0": r["e0"] - 0.01})
        items = vr.verify_tool("simple_ed")
        software = next(i for i in items if i.layer == "software")
        assert software.status == VerificationStatus.FAILED
        assert "n2" in software.detail or "n4" in software.detail
    finally:
        _restore_simple_ed()


def test_experiment_pass_and_gate_open(vr, tmp_path):
    load_seed_tools()
    result = registry.get_tool("simple_ed").run({"N": 4})
    exp = _experiment(tmp_path, result)
    vr.storage.save(exp)
    report = vr.verify_experiment(exp, "simple_ed")
    assert report.overall == VerificationStatus.PASSED, [
        (i.layer, i.claim, i.detail) for i in report.items if i.status != VerificationStatus.PASSED
    ]
    ok, msg = vr.evidence_gate(report)
    assert ok
    # 实验验证状态已回写
    assert vr.storage.get(Experiment, exp.experiment_id).verification_status == VerificationStatus.PASSED


def test_sabotaged_experiment_gate_closed(vr, tmp_path):
    """验收条文：故意写错的工具被抓住，且其产出被拒绝出具证据。"""
    load_seed_tools()
    registry._REGISTRY["simple_ed_orig"] = registry._REGISTRY["simple_ed"]
    try:
        _replace_simple_ed(lambda r: {**r, "E0": r["E0"] - 0.01, "e0": r["e0"] - 0.01})
        result = registry.get_tool("simple_ed").run({"N": 4})
        exp = _experiment(tmp_path, result)
        vr.storage.save(exp)
        report = vr.verify_experiment(exp, "simple_ed")
        assert report.overall == VerificationStatus.FAILED
        ok, msg = vr.evidence_gate(report)
        assert not ok and "禁止出具证据" in msg
        assert vr.storage.get(Experiment, exp.experiment_id).verification_status == VerificationStatus.FAILED
    finally:
        _restore_simple_ed()


def _register_scratch(name: str, transform) -> None:
    """注册一次性测试工具（新名字，不与 golden 套件匹配）。"""
    def run(inputs: dict) -> dict:
        base = registry.get_tool("simple_ed").run(inputs)
        return transform(base)

    register(ToolSpec(
        name=name, version="0", type="test", description="测试用临时工具",
        input_model=SimpleEDInputs, run=run,
    ))


def test_nondeterministic_tool_caught(vr, tmp_path):
    load_seed_tools()
    # 每次调用累加确定性偏移：复跑必然超出容差，且测试无随机闪烁
    calls = {"n": 0}

    def jittered(r: dict) -> dict:
        calls["n"] += 1
        return {**r, "E0": r["E0"] + 1e-4 * calls["n"]}

    _register_scratch("_nondet_ed", jittered)
    try:
        first = registry.get_tool("_nondet_ed").run({"N": 4})
        exp = _experiment(tmp_path, first)
        vr.storage.save(exp)
        report = vr.verify_experiment(exp, "_nondet_ed")
        det = next(i for i in report.items if "复跑" in i.claim)
        assert det.status == VerificationStatus.FAILED
        assert report.overall == VerificationStatus.FAILED
    finally:
        del registry._REGISTRY["_nondet_ed"]


def test_missing_numerical_fields_uncertain_gate_closed(vr, tmp_path):
    load_seed_tools()
    _register_scratch("_no_residual", lambda r: {k: v for k, v in r.items() if k != "residual"})
    try:
        result = registry.get_tool("_no_residual").run({"N": 4})
        exp = _experiment(tmp_path, result)
        vr.storage.save(exp)
        report = vr.verify_experiment(exp, "_no_residual")
        assert report.overall == VerificationStatus.UNCERTAIN
        ok, msg = vr.evidence_gate(report)
        assert not ok and "人工复核" in msg
    finally:
        del registry._REGISTRY["_no_residual"]


def test_tool_without_benchmarks_uncertain(vr):
    _register_scratch("_unverified_tool", lambda r: r)
    try:
        items = vr.verify_tool("_unverified_tool")
        assert len(items) == 1
        assert items[0].status == VerificationStatus.UNCERTAIN
        assert "禁止出具物理证据" in items[0].detail
    finally:
        del registry._REGISTRY["_unverified_tool"]
