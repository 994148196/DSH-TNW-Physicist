"""Phase 0 smoke test：确认 SDK 能启动 runtime 并完成一次真实轮次。

用法：.venv/Scripts/python.exe examples/dsh_smoke_test.py
dsh_bin 解析顺序：环境变量 QRESEARCH_DSH_BIN → 项目隔离安装的 npm dsh → SDK 内置 runtime。
验收：finish_reason == completed 且回复包含 OK（见 PROGRESS.md Phase 0 清单）
"""
import os
import sys
from pathlib import Path

from deepseek_harness import DeepSeekHarness

ROOT = Path(__file__).resolve().parents[1]


def resolve_dsh_bin() -> str | None:
    override = os.environ.get("QRESEARCH_DSH_BIN")
    if override:
        return override
    local = ROOT / ".dsh-runtime" / "node_modules" / ".bin" / "dsh.cmd"
    return str(local) if local.exists() else None


def main() -> int:
    dsh_home = ROOT / "research_data" / "dsh_home"
    dsh_home.mkdir(parents=True, exist_ok=True)
    with DeepSeekHarness(
        dsh_home=str(dsh_home),
        cwd=str(ROOT),
        dsh_bin=resolve_dsh_bin(),
        initialize_timeout_seconds=120,
    ) as harness:
        result = harness.run("Reply with exactly: OK", session_id="smoke-001")
    print("finish_reason:", result.finish_reason)
    print("final_response:", result.final_response.strip()[:200])
    ok = result.finish_reason == "completed" and "OK" in result.final_response
    print("SMOKE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
