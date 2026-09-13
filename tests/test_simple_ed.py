"""simple_ed 单元测试：解析值 + 独立 oracle 差分。"""
import numpy as np
import pytest
from qresearch.tools.registry import get_tool, load_seed_tools

BETHE_E0 = 0.25 - np.log(2)  # 1/4 − ln 2 ≈ -0.4431472


@pytest.fixture(scope="module", autouse=True)
def _seed_tools():
    load_seed_tools()


def test_n2_analytic():
    out = get_tool("simple_ed").run({"N": 2, "want_gap": True})
    assert out["E0"] == pytest.approx(-1.5, abs=1e-10)
    assert out["gap"] == pytest.approx(2.0, abs=1e-10)
    assert out["Sz2"] == pytest.approx(0.0, abs=1e-8)


def test_n4_analytic():
    out = get_tool("simple_ed").run({"N": 4})
    assert out["E0"] == pytest.approx(-2.0, abs=1e-10)


def test_nn_corr_self_consistency():
    out = get_tool("simple_ed").run({"N": 8, "want_corr": True})
    # E0 = N·J·<S_i·S_{i+1}>（PBC N 键）——两条独立计算路径必须一致
    assert out["nn_corr_times_N"] == pytest.approx(out["E0"], abs=1e-8)


def test_odd_n_rejected():
    with pytest.raises(ValueError):
        get_tool("simple_ed").run({"N": 3})


def test_bethe_limit():
    out = get_tool("simple_ed").run({"N": 16})
    assert out["e0"] == pytest.approx(BETHE_E0, abs=5e-3)
    assert out["residual"] < 1e-10


def test_monotonic_convergence():
    tool = get_tool("simple_ed")
    e0s = [tool.run({"N": n})["e0"] for n in (4, 6, 8, 10, 12)]
    assert e0s == sorted(e0s)
    dists = [abs(e - BETHE_E0) for e in e0s]
    assert dists == sorted(dists, reverse=True)
