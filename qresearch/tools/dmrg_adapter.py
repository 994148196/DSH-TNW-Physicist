"""dmrg_adapter：接入 quimb 的 DMRG（计划 v2 §8.3）。

主路径 quimb DMRG2；若触发本平台已知的 quimb/numba 局部本征求解器 NaN 缺陷
（DMRG 与 DMRG2 均复现，见 PROGRESS.md），回退为 quimb MPO → 稠密对角化。
输出 `method` 字段如实记录实际路径——回退结果仍是与 simple_ed 独立的实现
（MPO 构造 + numpy eigvalsh），差分基准（benchmarks/golden/dmrg_vs_simple_ed.yaml）
的价值不受影响。
"""
from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from .registry import ToolSpec, register


class DMRGInputs(BaseModel):
    L: int = Field(ge=2, le=14, description="链长（偶数；PBC 单键计一次，L=2 双计）")
    J: float = Field(default=1.0, description="耦合常数")
    bc: str = Field(default="PBC", pattern="^(PBC|OBC)$")
    bond_dim: int = Field(default=32, ge=4, le=512)
    want_gap: bool = Field(default=False)


def _run_dmrg2(L: int, J: float, cyclic: bool, bond_dim: int) -> dict[str, Any]:
    import quimb.tensor as qtn

    ham = qtn.MPO_ham_heis(L, j=J, cyclic=cyclic)
    dmrg = qtn.DMRG2(ham, bond_dims=[bond_dim, 2 * bond_dim], cutoffs=1e-10)
    dmrg.solve(verbosity=0, tol=1e-10)
    out = {"E0": float(dmrg.energy), "method": "quimb.DMRG2"}
    if L >= 2:
        out["e0"] = out["E0"] / L
    return out


def _run_mpo_dense(L: int, J: float, cyclic: bool) -> dict[str, Any]:
    """回退路径：quimb MPO 构造哈密顿量，稠密对角化（独立于 simple_ed 的基构造）。"""
    import quimb.tensor as qtn

    ham = qtn.MPO_ham_heis(L, j=J, cyclic=cyclic)
    H = ham.to_dense()
    evals, evecs = np.linalg.eigh(H)
    out = {
        "E0": float(evals[0]),
        "e0": float(evals[0]) / L,
        "method": "quimb.MPO_dense_fallback",
        "dim": int(H.shape[0]),
    }
    out["gap"] = float(evals[1] - evals[0])
    return out


def run_dmrg(inputs: dict[str, Any]) -> dict[str, Any]:
    p = DMRGInputs(**inputs)
    if p.L % 2 != 0:
        raise ValueError("dmrg_adapter 当前只支持偶数 L（与 simple_ed 差分需同扇区约定）")
    cyclic = p.bc == "PBC"
    try:
        out = _run_dmrg2(p.L, p.J, cyclic, p.bond_dim)
    except Exception as exc:  # noqa: BLE001 —— 平台缺陷回退，method 字段留痕
        fallback_note = f"DMRG2 failed ({type(exc).__name__}: {exc}); fell back to MPO dense"
        out = _run_mpo_dense(p.L, p.J, cyclic)
        out["fallback_note"] = fallback_note
    out.update({"L": p.L, "J": p.J, "bc": p.bc})
    return out


register(ToolSpec(
    name="dmrg_adapter",
    version="0.1.0",
    type="tensor_network",
    description="自旋 1/2 Heisenberg 链 DMRG（quimb）：基态能量；平台缺陷时回退 MPO 稠密对角化",
    input_model=DMRGInputs,
    run=run_dmrg,
    applicable_domains=["自旋 1/2 链", "L≤14（回退路径）", "更大 L 需 DMRG2 可用的环境"],
    benchmark_refs=["benchmarks/golden/dmrg_vs_simple_ed.yaml"],
    known_limitations=[
        "本机 quimb 1.15 DMRG/DMRG2 局部本征求解器产生 NaN（LinalgError），自动回退 MPO 稠密对角化",
        "回退路径 O(4^L)，L>14 不可用",
        "只支持偶数 L",
    ],
))
