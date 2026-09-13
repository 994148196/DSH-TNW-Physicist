"""工具子进程入口：python -m qresearch.tools.cli run --tool NAME --inputs-file F --out F2。

ExperimentManager 的默认执行路径经由此模块——实验不经 LLM、崩溃与主进程隔离。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qresearch.tools")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="运行一个已注册工具")
    run_p.add_argument("--tool", required=True)
    run_p.add_argument("--inputs-file", required=True)
    run_p.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    from qresearch.tools.registry import get_tool, load_seed_tools

    load_seed_tools()
    try:
        spec = get_tool(args.tool)
        inputs = json.loads(Path(args.inputs_file).read_text(encoding="utf-8"))
        result = spec.run(inputs)
        Path(args.out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return 0
    except Exception as exc:  # noqa: BLE001 —— 错误文本交给调用方记录
        print(f"TOOL RUN FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
