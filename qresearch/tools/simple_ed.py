"""simple_ed：自旋 1/2 Heisenberg 链精确对角化（scipy 稀疏 Lanczos）。

约定（与 benchmarks/golden/simple_ed.yaml 一致，勿改任一侧）：
- H = J Σ_{i=1}^{N} S_i·S_{i+1}，PBC 每键计一次；N=2 时单键计两次 → E0=-1.5J；
- Sz=0 扇区（偶数 N），基态用 Lanczos（eigsh）；
- 能隙：同扇区最低两本征值之差（S=1 三重态的 Sz=0 分量在此扇区）。
"""
from __future__ import annotations

from itertools import combinations
from typing import Any

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import eigsh
from pydantic import BaseModel, Field

from .registry import ToolSpec, register


class SimpleEDInputs(BaseModel):
    N: int = Field(ge=2, le=20, description="链长（位点数，偶数）")
    J: float = Field(default=1.0, description="耦合常数")
    want_gap: bool = Field(default=False, description="是否计算能隙 Δ=E1−E0")
    want_corr: bool = Field(default=False, description="是否计算近邻关联 <S_i·S_{i+1}>")


def _build_sector(N: int) -> tuple[np.ndarray, dict[int, int]]:
    """Sz=0 扇区基：N/2 个上自旋的位串。返回 (states, index)。"""
    states = np.array(
        [sum(1 << i for i in occ) for occ in combinations(range(N), N // 2)],
        dtype=np.int64,
    )
    states.sort()
    index = {int(s): k for k, s in enumerate(states)}
    return states, index


def _build_H(N: int, J: float, states: np.ndarray, index: dict[int, int]):
    """Sz=0 扇区内的 Heisenberg 环（PBC，N 条键）。"""
    dim = len(states)
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    diag = np.zeros(dim)
    for k, s in enumerate(states):
        for i in range(N):
            j = (i + 1) % N
            bi = (int(s) >> i) & 1
            bj = (int(s) >> j) & 1
            if bi == bj:
                diag[k] += 0.25
            else:
                diag[k] -= 0.25
                t = int(s) ^ ((1 << i) | (1 << j))  # 翻转近邻对
                rows.append(k)
                cols.append(index[t])
                vals.append(0.5)
    H = coo_matrix(
        (
            vals + diag.tolist(),
            (rows + list(range(dim)), cols + list(range(dim))),
        ),
        shape=(dim, dim),
    ).tocsr()
    return J * H


def _apply_Splus(psi: np.ndarray, states: np.ndarray, N: int,
                 index_p1: dict[int, int]) -> np.ndarray:
    """总自旋升算符 S⁺ = Σ_i S_i⁺：把 Sz=0 扇区的态映射到 Sz=+1 扇区。"""
    out = np.zeros(len(index_p1))
    for k, s in enumerate(states):
        a = psi[k]
        if a == 0.0:
            continue
        s = int(s)
        for i in range(N):
            if not (s >> i) & 1:
                out[index_p1[s | (1 << i)]] += a
    return out


def _apply_Sminus(psi_p1: np.ndarray, states_p1: np.ndarray, N: int,
                  index0: dict[int, int]) -> np.ndarray:
    """总自旋降算符 S⁻ = Σ_i S_i⁻：从 Sz=+1 扇区映射回 Sz=0 扇区。"""
    out = np.zeros(len(index0))
    for k, s in enumerate(states_p1):
        b = psi_p1[k]
        if b == 0.0:
            continue
        s = int(s)
        for i in range(N):
            if (s >> i) & 1:
                out[index0[s & ~(1 << i)]] += b
    return out


def _Sz2_expectation(
    psi: np.ndarray, states: np.ndarray, N: int,
    index0: dict[int, int], states_p1: np.ndarray, index_p1: dict[int, int],
) -> float:
    """<S_tot²>。Sz_tot=0 扇区：S² = S⁻S⁺（Sz 项为 0），经 Sz=+1 扇区中转。"""
    sp = _apply_Splus(psi, states, N, index_p1)
    sm_sp = _apply_Sminus(sp, states_p1, N, index0)
    return float(np.dot(psi, sm_sp))


def _nn_corr(psi: np.ndarray, states: np.ndarray, N: int) -> float:
    """<S_i·S_{i+1}>（平移平均）= 3<S^z_i S^z_{i+1}>（SU(2)），纯对角路径。"""
    acc = 0.0
    norm2 = float(np.dot(psi, psi))
    for k, s in enumerate(states):
        w = psi[k] ** 2 / norm2
        s = int(s)
        sgn = 0
        for i in range(N):
            bi = (s >> i) & 1
            bj = (s >> ((i + 1) % N)) & 1
            sgn += 1 if bi == bj else -1
        acc += w * sgn * 0.25
    return float(3.0 * acc / N)


def run_simple_ed(inputs: dict[str, Any]) -> dict[str, Any]:
    p = SimpleEDInputs(**inputs)
    if p.N % 2 != 0:
        raise ValueError("simple_ed 要求偶数 N（Sz=0 扇区非空）")
    states, index = _build_sector(p.N)
    dim = len(states)
    # Sz=+1 扇区：计算 <S²> 时 S⁻S⁺ 的中间跃迁扇区
    states_p1 = np.array(
        [sum(1 << i for i in occ) for occ in combinations(range(p.N), p.N // 2 + 1)],
        dtype=np.int64,
    )
    states_p1.sort()
    index_p1 = {int(s): k for k, s in enumerate(states_p1)}
    H = _build_H(p.N, p.J, states, index)

    out: dict[str, Any] = {
        "N": p.N, "J": p.J, "bc": "PBC", "dim": dim,
        "method": "scipy.sparse.linalg.eigsh (Lanczos, Sz=0 sector)",
    }

    if dim <= 8:  # 小扇区直接稠密对角化，规避 eigsh 的 k<dim 限制
        evals, evecs = np.linalg.eigh(H.toarray())
        E0 = float(evals[0])
        psi = evecs[:, 0]
        resid = float(np.linalg.norm(H @ psi - E0 * psi))
        out["gap"] = float(evals[1] - evals[0]) if p.want_gap else None
    else:
        k = 2 if p.want_gap else 1
        # 固定初始向量：ARPACK 默认随机 v0 会导致机器精度级不可复现。
        # Marshall 符号规则保证 Sz=0 基态振幅全正，与全 1 向量必有重叠。
        v0 = np.ones(dim) / np.sqrt(dim)
        evals, evecs = eigsh(H, k=k, which="SA", v0=v0, tol=0, maxiter=10000)
        order = np.argsort(evals)
        evals, evecs = evals[order], evecs[:, order]
        E0 = float(evals[0])
        psi = evecs[:, 0]
        resid = float(np.linalg.norm(H @ psi - E0 * psi))
        out["gap"] = float(evals[1] - evals[0]) if p.want_gap else None

    out["E0"] = E0
    out["e0"] = E0 / p.N
    out["residual"] = resid
    out["Sz2"] = _Sz2_expectation(psi, states, p.N, index, states_p1, index_p1)
    if p.want_corr:
        out["nn_corr"] = _nn_corr(psi, states, p.N)
        out["nn_corr_times_N"] = out["nn_corr"] * p.N
    return out


register(ToolSpec(
    name="simple_ed",
    version="1.0.0",
    type="exact_diagonalization",
    description="自旋 1/2 Heisenberg 链 ED：基态能量/能隙/近邻关联（PBC，Sz=0 扇区，Lanczos）",
    input_model=SimpleEDInputs,
    run=run_simple_ed,
    applicable_domains=["自旋 1/2 链", "N≤20 精确基态"],
    benchmark_refs=["benchmarks/golden/simple_ed.yaml"],
    known_limitations=[
        "只支持偶数 N 与 PBC",
        "N=2 的 PBC 单键双计（E0=-1.5J 是约定而非错误）",
        "dim=C(N,N/2) 指数增长，N>20 不可用",
    ],
))
