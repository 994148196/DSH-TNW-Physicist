"""golden 基准检查器：跑工具 → 对照基准 → 机器可判定报告（计划 v2 §8.2）。

基准文件在 benchmarks/golden/*.yaml：数值来自解析 / 文献 / 独立实现，
出题人≠答题人。本模块只解释基准，不改基准。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from qresearch.tools.registry import ToolSpec, get_tool, load_seed_tools

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- 独立 oracle
def dense_exact_ground(N: int, J: float = 1.0) -> dict[str, float]:
    """全 2^N 希尔伯特空间稠密对角化——与 Sz=0 扇区 Lanczos 完全独立的实现。"""
    dim = 2**N
    sz = np.array([[0.5, 0.0], [0.0, -0.5]])
    sx = np.array([[0.0, 0.5], [0.5, 0.0]])
    sy = np.array([[0.0, -0.5j], [0.5j, 0.0]])
    I = np.eye(2)
    H = np.zeros((dim, dim), dtype=complex)
    for i in range(N):
        j = (i + 1) % N
        ops = {"z": sz, "x": sx, "y": sy}
        for name, op in ops.items():
            M = np.array([[1.0 + 0j]])
            for site in range(N):
                if site in (i, j):
                    M = np.kron(M, op)
                else:
                    M = np.kron(M, I)
            H += J * M
    evals = np.linalg.eigvalsh(H)
    return {"E0": float(evals[0]), "gap": float(evals[1] - evals[0])}


def dense_tfim_ground(N: int, h: float, J: float = 1.0) -> dict[str, float]:
    """横场 Ising 链（PBC 单计数）稠密对角化——独立实现。

    约定（与基准文件一致）：H = -J Σ_i σ^x_i σ^x_{i+1} - h Σ_i σ^z_i（泡利矩阵）。
    基用 z 方向计算基：σ^x 项翻转两比特（无符号），σ^z 本征值 = 1-2b（b∈{0,1}）。
    解析锚点：h=0 → E0=-NJ；h=J 临界 → e0(∞)=-4J/π（Lieb-Schultz-Mattis）。
    """
    dim = 2**N
    H = np.zeros((dim, dim))
    for state in range(dim):
        bits = [(state >> i) & 1 for i in range(N)]
        H[state, state] += -h * sum(1 - 2 * b for b in bits)
        for i in range(N):
            j = (i + 1) % N
            H[state ^ (1 << i) ^ (1 << j), state] += -J
    evals = np.linalg.eigvalsh(H)
    return {"E0": float(evals[0]), "gap": float(evals[1] - evals[0])}


# ---------------------------------------------------------------- 报告对象
@dataclass
class CheckResult:
    case: str
    check: dict[str, Any]
    passed: bool
    detail: str
    layer: str = "physics"


@dataclass
class GoldenReport:
    benchmark: str
    tool: str
    results: list[CheckResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    def summary(self) -> str:
        lines = [f"golden: {self.benchmark}（工具 {self.tool}）"]
        for r in self.results:
            mark = "✅" if r.passed else "❌"
            lines.append(f"  {mark} {r.case}: {r.detail}")
        lines.append(f"{'PASS' if self.all_passed else 'FAIL'}（{len(self.results)} 项检查）")
        return "\n".join(lines)


# ---------------------------------------------------------------- 检查器
def _approx(value: float, target: float, tol: float) -> tuple[bool, str]:
    diff = abs(value - target)
    ok = diff <= tol
    return ok, f"|{value!r} - {target!r}| = {diff:.3e} {'≤' if ok else '>'} {tol:.1e}"


def run_case(
    spec: ToolSpec, case: dict[str, Any], against_spec: ToolSpec | None = None
) -> list[CheckResult]:
    """执行一个 case（含 sweep 展开），返回逐检查结果；单 case 异常不炸套件。"""
    results: list[CheckResult] = []
    name = case["name"]
    layer = case.get("layer", "physics")
    try:
        sweep = case.get("sweep") or {}
        sweep_keys = sorted(sweep)
        combos: list[dict[str, Any]] = [{}]
        for key in sweep_keys:
            combos = [dict(c, **{key: v}) for c in combos for v in sweep[key]]

        runs: list[dict[str, Any]] = []
        for combo in combos:
            inputs = {**case.get("inputs", {}), **combo}
            out = spec.run(inputs)
            runs.append({"inputs": inputs, "output": out})
            if against_spec is not None:
                a_inputs = {**case.get("against_inputs", {}), **combo}
                a_map = {"L": "N", "bc": None}
                a_inputs = {a_map.get(k, k): v for k, v in a_inputs.items() if a_map.get(k, k)}
                runs[-1]["against_output"] = against_spec.run(a_inputs)
    except Exception as exc:  # noqa: BLE001 —— 工具崩溃 = 该 case 所有检查 fail
        err = f"{type(exc).__name__}: {exc}"
        return [
            CheckResult(case=name, check=check, passed=False,
                        detail=f"case 执行异常：{err}", layer=layer)
            for check in case.get("checks", [])
        ]

    for check in case.get("checks", []):
        op = check["op"]
        passed_all, detail_all = True, []
        try:
            for run in runs:
                ok, detail = _apply_check(op, check, run)
                passed_all &= ok
                detail_all.append(detail)
                if not ok:
                    break
        except Exception as exc:  # noqa: BLE001 —— 检查器异常也按 fail 处理
            passed_all = False
            detail_all.append(f"检查执行异常：{type(exc).__name__}: {exc}")
        results.append(CheckResult(
            case=name, check=check, passed=passed_all,
            detail="; ".join(detail_all), layer=layer,
        ))
    return results


def _apply_check(op: str, check: dict[str, Any], run: dict[str, Any]) -> tuple[bool, str]:
    out = run["output"]
    if op == "approx":
        value = out[check["field"]]
        target = check.get("value")
        if target is None and "op_param_field" in check:
            target = out[check["op_param_field"]]
        return _approx(float(value), float(target), float(check["tol"]))
    if op == "oracle_dense":
        oracle_name = check.get("oracle", "heisenberg")
        if oracle_name == "heisenberg":
            oracle = dense_exact_ground(run["inputs"]["N"])
        elif oracle_name == "tfim":
            oracle = dense_tfim_ground(
                run["inputs"]["N"], run["inputs"]["h"], run["inputs"].get("J", 1.0)
            )
        else:
            raise ValueError(f"未知 oracle: {oracle_name}")
        field_name = check.get("oracle_field", "E0")
        return _approx(float(out[check["field"]]), oracle[field_name], float(check["tol"]))
    if op == "diff":
        mine = float(out[check["field"]])
        theirs = float(run["against_output"][check["against_field"]])
        return _approx(mine, theirs, float(check["tol"]))
    if op == "increasing_toward":
        raise ValueError("increasing_toward 只能用于 sweep case（多组输出）")
    raise ValueError(f"未知检查 op: {op}")


def _apply_sweep_check(op: str, check: dict[str, Any], runs: list[dict[str, Any]]) -> tuple[bool, str]:
    if op != "increasing_toward":
        return _apply_check(op, check, runs[0])
    target = float(check["value"])
    values = [float(r["output"][check["field"]]) for r in runs]
    dists = [abs(v - target) for v in values]
    mono = all(d2 < d1 for d1, d2 in zip(dists, dists[1:]))
    inc = all(v2 > v1 for v1, v2 in zip(values, values[1:]))
    ok = mono and inc
    detail = (
        f"e0 序列 {[round(v, 6) for v in values]}"
        f"{'单调递增' if inc else '非单调'}、"
        f"{'单调趋近' if mono else '未单调趋近'}目标 {target}"
    )
    return ok, detail


def run_benchmark(
    path: str | Path, tool_override: str | None = None
) -> GoldenReport:
    """跑一个基准文件。tool_override：候选工具名（Tool Builder 构建期用，
    工具注册后以文件内 tool 名运行）。"""
    path = Path(path)
    spec_raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    load_seed_tools()
    tool = get_tool(tool_override or spec_raw["tool"])
    against = None
    if spec_raw.get("against"):
        against = get_tool(spec_raw["against"])

    report = GoldenReport(benchmark=path.name, tool=tool.name)
    for case in spec_raw["cases"]:
        sweep = case.get("sweep") or {}
        has_sweep_op = any(c["op"] == "increasing_toward" for c in case.get("checks", []))
        if has_sweep_op:
            combos = [dict(zip(sorted(sweep), vals)) for vals in _product(sweep)]
            try:
                runs = []
                for combo in combos:
                    inputs = {**case.get("inputs", {}), **combo}
                    runs.append({"inputs": inputs, "output": tool.run(inputs)})
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                for check in case.get("checks", []):
                    report.results.append(CheckResult(
                        case["name"], check, False, f"case 执行异常：{err}",
                        layer=case.get("layer", "physics"),
                    ))
                continue
            for check in case.get("checks", []):
                ok, detail = _apply_sweep_check(check["op"], check, runs)
                report.results.append(CheckResult(
                    case["name"], check, ok, detail, layer=case.get("layer", "physics"),
                ))
        else:
            report.results.extend(run_case(tool, case, against))
    return report


def _product(sweep: dict[str, list]) -> list[tuple]:
    import itertools

    keys = sorted(sweep)
    return list(itertools.product(*(sweep[k] for k in keys)))


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    paths = argv or [str(p) for p in sorted((ROOT / "benchmarks" / "golden").glob("*.yaml"))]
    failed = False
    for p in paths:
        report = run_benchmark(p)
        print(report.summary())
        failed |= not report.all_passed
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
