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
