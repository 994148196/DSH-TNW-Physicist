"""golden 基准检查器：跑工具 → 对照基准 → 机器可判定报告（计划 v2 §8.2）。

基准文件在 benchmarks/golden/*.yaml：数值来自解析 / 文献 / 独立实现，
出题人≠答题人。本模块只解释基准，不改基准。
"""
from __future__ import annotations

import re
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



def dense_hubbard_ground(
    L: int, t: float, U: float, n_up: int, n_down: int
) -> dict[str, float]:
    """一维单带 Hubbard 链（开放边界）稠密对角化——独立实现。

    约定（与 tool_specs/hubbard_ed.yaml 逐条一致）：
        H = -t * sum_{i=0}^{L-2} sum_{sigma} (c^dag_{i,sigma} c_{i+1,sigma} + h.c.)
            + U * sum_{i=0}^{L-1} n_{i,up} n_{i,dn}
        全局模排序 0up,1up,...,(L-1)up,0dn,1dn,...,(L-1)dn；
        c_m |n> = (-1)^{sum_{k<m} n_k} * n_m * |..., b_m = 0, ...>

    本函数在 **2^{2L} 全 Fock 空间**上枚举，再按 (n_up, n_down) 取子集建矩阵，
    不做任何对称性约化、不手工推导 Jordan-Wigner 串——与工具实现路径无关。
    粒子数筛选只是选基，不构成物理简化的捷径。
    """
    n_modes = 2 * L
    full_dim = 1 << n_modes

    basis: list[int] = []
    for state in range(full_dim):
        up = sum((state >> m) & 1 for m in range(L))
        dn = sum((state >> (L + m)) & 1 for m in range(L))
        if up == n_up and dn == n_down:
            basis.append(state)

    dim = len(basis)
    if dim == 0:
        raise ValueError(
            f"空扇区：L={L}, n_up={n_up}, n_down={n_down}（要求 0 <= n_sigma <= L）"
        )
    index = {state: k for k, state in enumerate(basis)}
    H = np.zeros((dim, dim), dtype=float)

    for k, state in enumerate(basis):
        occ = [(state >> m) & 1 for m in range(n_modes)]
        # ---- 在位相互作用 U * n_{i,up} n_{i,dn}
        if U:
            for i in range(L):
                if occ[i] and occ[L + i]:
                    H[k, k] += U
        # ---- 跃迁 -t (c^dag_p c_q + c^dag_q c_p)，逐个费米子算符按右到左作用
        if t:
            for i in range(L - 1):
                for spin in (0, 1):
                    q = i + spin * L
                    p = (i + 1) + spin * L
                    for src, dst in ((q, p), (p, q)):
                        if not occ[src]:
                            continue
                        # 第一步：c_src
                        sign = -1.0 if (sum(occ[:src]) % 2) else 1.0
                        mid = state & ~(1 << src)
                        occ_mid = list(occ)
                        occ_mid[src] = 0
                        if occ_mid[dst]:
                            continue
                        # 第二步：c^dag_dst（相位按去掉 src 之后的占据数算）
                        sign *= -1.0 if (sum(occ_mid[:dst]) % 2) else 1.0
                        new = mid | (1 << dst)
                        H[index[new], k] += -t * sign

    # 数值安全：跃迁项成对写入，理论上严格厄米；显式对称化以消除累加舍入
    H = 0.5 * (H + H.T)
    evals = np.linalg.eigvalsh(H)
    return {
        "E0": float(evals[0]),
        "gap": float(evals[1] - evals[0]) if dim > 1 else float("nan"),
        "dim": dim,
    }

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
                # a_inputs 以 case 的 inputs 为**默认基底**，再被 against_inputs 覆盖，
                # 最后叠加 sweep 维度。之所以要继承：a_inputs 原实现只取
                # against_inputs + combo，于是「同一个物理系统两条实现路径对拍」这个
                # 最常见的情形反而必须把 inputs 逐字抄一遍；漏抄就静默退化成
                # {"": ...} 空输入（实测表现为 pydantic 报 L/n_up/n_down 缺失，
                # 看起来像实现缺陷，其实是接线错误）。更危险的是抄错一个数：
                # 两条路径会去算**不同的系统**，diff 却照样给出一个数。
                # 显式 against_inputs 仍然优先，因此需要对拍不同参数时照旧可写。
                a_inputs = {
                    **case.get("inputs", {}),
                    **case.get("against_inputs", {}),
                    **combo,
                }
                # 按 against 工具的实际输入字段推导（不再写死 Heisenberg 的 L->N）：
                # 仅当对方模型有 N 而没有 L 时才做 L->N，且丢弃对方不认识的键。
                a_fields = set(against_spec.input_model.model_fields)
                if "N" in a_fields and "L" not in a_fields:
                    a_inputs = {("N" if k == "L" else k): v for k, v in a_inputs.items()}
                a_inputs = {k: v for k, v in a_inputs.items() if k in a_fields}
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
        elif oracle_name == "hubbard":
            inp = run["inputs"]
            oracle = dense_hubbard_ground(
                inp["L"], inp.get("t", 1.0), inp.get("U", 4.0),
                inp["n_up"], inp["n_down"],
            )
        else:
            raise ValueError(f"未知 oracle: {oracle_name}")
        field_name = check.get("oracle_field", "E0")
        return _approx(float(out[check["field"]]), oracle[field_name], float(check["tol"]))
    if op == "diff":
        mine = float(out[check["field"]])
        theirs = float(run["against_output"][check["against_field"]])
        return _approx(mine, theirs, float(check["tol"]))
    if op in ("le", "ge"):
        # 不等式单侧比较：value 取标量界，或 other_field 取**另一个输出字段**作界。
        # 例：bond_dimension_max <= chi_max_requested —— 这类不变式用 approx 表达不了，
        # 之前只能退化成"回传请求值"的弱检查，等于名不副实。
        value = float(out[check["field"]])
        if "other_field" in check:
            bound = float(out[check["other_field"]])
            rhs = f"{check['other_field']}={bound!r}"
        else:
            bound = float(check["value"])
            rhs = repr(bound)
        ok = value <= bound if op == "le" else value >= bound
        sign = "<=" if op == "le" else ">="
        return ok, f"{check['field']}={value!r} {sign} {rhs} → {'成立' if ok else '不成立'}"
    if op == "field_present":
        ok = check["field"] in out and out[check["field"]] is not None
        return ok, f"字段 {check['field']} {'存在' if ok else '缺失或为 null'}"
    if op == "field_matches":
        text = str(out[check["field"]])
        ok = re.search(check["pattern"], text) is not None
        return ok, (f"{check['field']}={text[:80]!r} 匹配 /{check['pattern']}/ "
                    f"→ {'是' if ok else '否'}")
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
