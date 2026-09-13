"""子进程执行路径测试：CLI 与 SubprocessToolRunner。"""
import pytest
from qresearch.experiments.manager import SubprocessToolRunner, ToolRunError
from qresearch.tools.registry import get_tool, load_seed_tools


def test_subprocess_runner_roundtrip(tmp_path):
    load_seed_tools()
    spec = get_tool("simple_ed")
    runner = SubprocessToolRunner()
    outcome = runner.run(spec, {"N": 4, "J": 1.0}, tmp_path / "ws", timeout_s=120)
    assert outcome.result["E0"] == pytest.approx(-2.0, abs=1e-10)
    assert (tmp_path / "ws" / "inputs.json").exists()
    assert (tmp_path / "ws" / "result.json").exists()


def test_subprocess_runner_failure(tmp_path):
    load_seed_tools()
    spec = get_tool("simple_ed")
    runner = SubprocessToolRunner()
    with pytest.raises(ToolRunError):
        runner.run(spec, {"N": 3}, tmp_path / "ws2", timeout_s=120)  # 奇数 N → 非零退出
