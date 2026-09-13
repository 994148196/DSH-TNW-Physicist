"""golden 基准检查器测试：基准文件必须全过（出题人≠答题人）。"""
from pathlib import Path

import pytest
from qresearch.verification.golden import run_benchmark

GOLDEN = Path(__file__).resolve().parents[1] / "benchmarks" / "golden"


def test_simple_ed_golden():
    report = run_benchmark(GOLDEN / "simple_ed.yaml")
    assert report.all_passed, report.summary()


def test_dmrg_differential_golden():
    """dmrg_adapter 与 simple_ed 独立实现差分（本平台经 MPO 稠密回退路径）。"""
    pytest.importorskip("quimb")
    report = run_benchmark(GOLDEN / "dmrg_vs_simple_ed.yaml")
    assert report.all_passed, report.summary()


def test_unknown_check_op_reported_as_failure(tmp_path):
    """未知 op 不炸套件：该检查判 fail，其余检查照常。"""
    import yaml

    bad = {
        "tool": "simple_ed",
        "cases": [{
            "name": "bad", "layer": "software", "inputs": {"N": 4},
            "checks": [{"field": "E0", "op": "nonsense_op", "value": -2.0, "tol": 1e-9}],
        }],
    }
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    report = run_benchmark(p)
    assert not report.all_passed
    assert "检查执行异常" in report.results[0].detail


def test_tfim_oracle_anchors():
    """出题人自检：TFIM 独立 oracle 的解析锚点（h=0 铁磁、临界 -4/pi）。"""
    import math

    from qresearch.verification.golden import dense_tfim_ground

    for N in (4, 6, 8):
        r = dense_tfim_ground(N, 0.0)
        assert abs(r["E0"] - (-N)) < 1e-10
        assert abs(r["gap"]) < 1e-8  # 两重简并
    target = -4.0 / math.pi
    e12 = dense_tfim_ground(12, 1.0)["E0"] / 12
    assert abs(e12 - target) < 1e-2


def test_tfim_fixture_spec_first():
    """spec 先行：tfim_ed golden 在工具实现之前定稿，结构合法、锚点已锁。"""
    import yaml

    path = Path(__file__).resolve().parents[1] / "tool_specs" / "tfim_ed.golden.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw["tool"] == "tfim_ed"
    known_ops = {"approx", "oracle_dense", "diff", "increasing_toward"}
    for case in raw["cases"]:
        assert case["layer"] in ("software", "numerical", "physics")
        for check in case["checks"]:
            assert check["op"] in known_ops
