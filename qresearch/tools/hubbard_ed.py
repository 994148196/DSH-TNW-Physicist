"""hubbard_ed v1.0.0 -- exact diagonalization of the 1D single-band Hubbard chain.

Model and conventions (verbatim from ``tool_specs/hubbard_ed.yaml``)
-------------------------------------------------------------------
::

    H = -t * sum_{i=0}^{L-2} sum_{sigma in {up,dn}}
            ( c^dag_{i,sigma} c_{i+1,sigma} + h.c. )
        + U * sum_{i=0}^{L-1} n_{i,up} n_{i,dn}

* **Open boundary conditions (OBC).**  Hopping acts only on the ``L-1``
  neighbouring pairs ``(i, i+1)``, ``i = 0 .. L-2``; there is no ``i = L-1``
  wrap-around, so no boundary bond is double counted.  PBC is deliberately
  *not* implemented (a different bond convention would need its own spec).
* **No chemical potential, no interaction shift.**  The returned number is the
  absolute energy of the Hamiltonian written above.  Nothing is rewritten in a
  particle-hole symmetric form such as ``(n_up - 1/2)(n_dn - 1/2)``; such a
  form would shift the energy by ``-(U/2)(n_up + n_dn) + U L / 4`` and is never
  used here, so the output is directly comparable to the expression above.
* **Fermionic ordering.**  Global mode order ``0up, 1up, ..., (L-1)up,
  0dn, 1dn, ..., (L-1)dn`` (mode ``m`` with ``0 <= m < L`` is spin up at site
  ``m``; mode ``L + i`` is spin down at site ``i``).  A basis state is the
  occupation bitmask ``n`` with
  ``c_m |n> = (-1)^{sum_{k<m} n_k} n_m |..., n_m = 0, ...>`` and
  ``c^dag_m |n> = (-1)^{sum_{k<m} n_k} (1 - n_m) |..., n_m = 1, ...>``.
  Because the two modes of a hopping bond are *adjacent* in this ordering and
  the two spins anticommute, every adjacent hopping matrix element is exactly
  ``-t`` with no Jordan-Wigner string.  The self-tests at the bottom of this
  file rebuild the same operator from the raw ``c^dag c`` algebra and confirm
  the coefficient, so the shortcut is verified, not assumed.

Delivered quantities
--------------------
``E0``        ground-state energy (absolute value; **not** a density)
``e0``        energy density ``E0 / L``
``residual``  ``|| H psi0 - E0 psi0 || / max(1, |E0|)`` with ``||psi0|| = 1``
``dim``       sector dimension ``C(L, n_up) * C(L, n_down)``
``gap``       first excitation gap ``E1 - E0`` in the *same* sector
``method``    description of the solver actually used

Solver paths (all act on the same fixed-``(n_up, n_down)`` sector)
------------------------------------------------------------------
The Hamiltonian is real symmetric.  Four interchangeable routes are used, and
the one taken is always stated in ``method``:

1. ``t == 0`` -- **atomic limit, solved exactly, not iteratively.**
   ``H`` is diagonal in the occupation basis, its eigenvalues are
   ``U * (double occupancy)`` and its zero space is enormous (at half filling
   with even ``L`` the minimum double occupancy is zero and the ground level is
   massively degenerate).  A plain ``eigsh(which='SA')`` Krylov run can lose the
   zero-space component and return the *second* level (observed: ``E0 = U``
   instead of ``0`` at ``L = 8``).  Here the ground level is obtained by exact
   minimisation of the diagonal, which is the same answer for a dense
   ``eigh``, for a sparse Lanczos run, and for dimension above any threshold.
   ``gap`` is then the first *distinct* diagonal level.
2. ``U == 0`` -- **free fermions: the two spin sectors decouple exactly.**
   ``H = H_up + H_dn`` with ``[H_up, H_dn] = 0``, each ``H_sigma`` acting on
   ``C(L, n_sigma)`` spinless states; the exact ground state is the product of
   the two sector ground states, which are obtained by full dense
   diagonalization of the (small) sector matrices.  The result is checked
   against the analytic open-chain spectrum ``eps_k = -2 t cos(k pi / (L+1))``
   in the self-tests.  For moderate dimension it is also cross-checked against
   the Lanczos route below.
3. ``dim <= 2048`` -- the matrix is materialised densely and fully
   diagonalised with ``numpy.linalg.eigh`` (exact ``E0`` and ``E1``).
4. otherwise -- Lanczos (``scipy.sparse.linalg.eigsh``, ``which='SA'``,
   ``tol=0``, deterministic start vector) on the sector Hamiltonian, given either
   as an explicit CSR matrix (when the memory budget allows, which makes the
   ``matvec`` about an order of magnitude faster) or as a matrix-free
   ``LinearOperator`` whose ``matvec`` applies the diagonal interaction plus the
   ``2(L-1)`` hopping bonds via precomputed index maps.  A physics-informed
   second start (uniform over the minimal-double-occupancy subspace, which is
   the exact ground space of the atomic limit) is used as a fallback, and a
   LOBPCG block solve is the last resort; candidates are ranked by residual and
   then by Rayleigh quotient (every Rayleigh quotient is an upper bound on the
   ground energy, so the smallest one is the best estimate).

The largest admitted sector is ``L = 12``, ``n_up = n_down = 6`` with
``dim = C(12,6)^2``; no ``O(dim^2)`` object is ever stored.

``E0`` is recomputed as the Rayleigh quotient of the returned eigenvector and
``residual`` is evaluated with that same ``E0`` and the same ``matvec``, so
eigenpair and residual are mutually consistent; ``residual <= 1e-10`` is the
numerical invariant.  No solver failure is allowed to escape: every iterative
call is wrapped, and the last candidate is reported with its measured residual
rather than raising.

Known limitations (stated, not hidden)
--------------------------------------
* ``dim = C(L, L/2)^2`` grows exponentially; ``L > 12`` is rejected, and even
  inside the accepted range ED is a finite-Hilbert-space method -- it does not
  remove floating-point error, and the iterative (Lanczos) path is checked by
  the reported residual.
* OBC only.  PBC changes the boundary-bond convention and must not be faked
  with this tool.
* Fixed ``(n_up, n_down)`` sector only: no cross-sector global ground state,
  no finite temperature and no finite chemical potential.
* ``t < 0`` or ``U < 0`` are mathematically computable here but are outside the
  covered fixture range (``t > 0``, ``U >= 0``); at ``U < 0`` the interaction
  is attractive and the ground-state degeneracy can differ.
* ``gap`` is the gap inside the chosen sector, not a spin or charge gap of the
  full Hilbert space.  For a sector with a single state (``dim == 1``) there is
  no excitation and ``gap = 0.0``.  On the iterative path ``gap`` uses the few
  lowest Ritz values that were computed (``k`` of them), so a ground manifold
  wider than ``k`` can make the reported gap smaller than the true
  one-particle gap; on the dense, atomic and free-fermion paths it is exact.
* ``t = 0`` atomic limit in general: ``E0 = U * max(0, n_up + n_down - L)``,
  i.e. ``0`` whenever the particles fit without double occupancy (in
  particular at half filling).  The spec's ``t = 0 -> E0 = 0`` invariant is
  that case; the general minimisation is what the code returns.
"""

from __future__ import annotations

import math
from itertools import combinations
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import scipy.sparse as _sp
import scipy.sparse.linalg as _spla
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

__version__ = "1.0.0"
__all__ = ["Inputs", "run", "__version__"]

# Full-spectrum dense diagonalization for dim <= this.
_DENSE_MAX_DIM = 2048
# Numerical invariant: residual <= 1e-10.
_RESIDUAL_TOL = 1e-10
# Level splittings below this (relative) count as degeneracy of a manifold.
_DEGENERACY_TOL = 1e-9
# Above this dimension the cheap cross-checks are skipped to bound run time.
_CROSSCHECK_DIM = 200000
# Number of lowest Ritz values requested on the iterative path.
_LANCZOS_K = 4
# Fixed seed -> reproducible start vectors (determinism is part of the spec).
_V0_SEED = 20240617
# Above this number of non-zeros the explicit sparse matrix is not materialised
# (the matrix-free index-map matvec is used instead); keeps memory bounded.
_CSR_MAX_NNZ = 20000000


# --------------------------------------------------------------------------- #
# input validation
# --------------------------------------------------------------------------- #
class Inputs(BaseModel):
    """Validated input record for one Hubbard-chain ED request."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    L: int = Field(
        ...,
        ge=2,
        le=12,
        description="Number of sites of the open chain (2 <= L <= 12).",
    )
    t: float = Field(
        default=1.0,
        allow_inf_nan=False,
        description="Nearest-neighbour hopping amplitude (finite real; t > 0 covered).",
    )
    U: float = Field(
        default=4.0,
        allow_inf_nan=False,
        description="On-site Coulomb repulsion (finite real; U >= 0 covered).",
    )
    n_up: int = Field(
        ...,
        ge=0,
        description="Number of spin-up particles (fixed sector, 0 <= n_up <= L).",
    )
    n_down: int = Field(
        ...,
        ge=0,
        description="Number of spin-down particles (fixed sector, 0 <= n_down <= L).",
    )

    @field_validator("L", "n_up", "n_down", mode="before")
    @classmethod
    def _integral(cls, value: Any) -> Any:
        """Accept integral values only (rejects bool/str/fractional numbers)."""
        if isinstance(value, bool):
            raise ValueError("expected an integer, not a bool")
        if isinstance(value, (int, np.integer)):
            return int(value)
        if isinstance(value, (float, np.floating)):
            value = float(value)
            if not math.isfinite(value) or value != int(value):
                raise ValueError("expected an integral value")
            return int(value)
        raise ValueError("expected an integer")

    @field_validator("t", "U", mode="before")
    @classmethod
    def _real_scalar(cls, value: Any) -> Any:
        """Accept real scalars only (rejects bool/str/complex/None)."""
        if isinstance(value, bool):
            raise ValueError("value must be a real number, not a bool")
        if isinstance(value, (int, float, np.integer, np.floating)):
            return float(value)
        if isinstance(value, np.ndarray) and value.ndim == 0:
            return float(value)
        raise ValueError("value must be a real number")

    @model_validator(mode="after")
    def _sector_bounds(self) -> "Inputs":
        """n_up / n_down must lie in [0, L]."""
        if not 0 <= self.n_up <= self.L:
            raise ValueError(
                f"n_up must satisfy 0 <= n_up <= L={self.L}, got {self.n_up}"
            )
        if not 0 <= self.n_down <= self.L:
            raise ValueError(
                f"n_down must satisfy 0 <= n_down <= L={self.L}, got {self.n_down}"
            )
        return self


# --------------------------------------------------------------------------- #
# sector basis and Hamiltonian representation
# --------------------------------------------------------------------------- #
def _site_masks(L: int, n: int) -> np.ndarray:
    """Sorted bitmasks (bits ``0..L-1``) with exactly ``n`` occupied sites."""
    masks = [sum(1 << i for i in combo) for combo in combinations(range(L), n)]
    if not masks:
        return np.zeros(0, dtype=np.int64)
    return np.sort(np.asarray(masks, dtype=np.int64))


def _sector_basis(L: int, n_up: int, n_down: int) -> np.ndarray:
    """Sorted bitmasks of the fixed-``(n_up, n_down)`` sector.

    Mode order is ``0up..(L-1)up, 0dn..(L-1)dn``; spin-down bits are therefore
    shifted left by ``L``.  Sorting gives a canonical, search-sortable order.
    """
    up = _site_masks(L, n_up)
    dn = _site_masks(L, n_down)
    basis = np.bitwise_or(up[:, None], np.left_shift(dn[None, :], L)).ravel()
    basis.sort()
    return basis


def _double_occupancy(basis: np.ndarray, L: int) -> np.ndarray:
    """Diagonal of ``sum_i n_{i,up} n_{i,dn}`` (integer counts, float64)."""
    count = np.zeros(basis.shape[0], dtype=np.int64)
    for i in range(L):
        count += ((basis >> i) & 1) & ((basis >> (L + i)) & 1)
    return count.astype(np.float64)


def _bond_blocks(
    basis: np.ndarray, L: int, offset: int
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Index maps for the hopping bonds of one spin species.

    ``offset`` is ``0`` for spin up and ``L`` for spin down.  For a bond
    ``(m, m+1)`` the forward block maps a state with ``n_m = 1``,
    ``n_{m+1} = 0`` to the state with those occupations exchanged.  Its matrix
    element is ``-t``: the two modes are adjacent in the global ordering, so
    the two Jordan-Wigner phases cancel exactly (see the module docstring; the
    self-tests rebuild the same operator from the raw ``c^dag_m c_{m+1} +
    h.c.`` algebra and confirm the coefficient).  The reverse direction is the
    transpose of the same block, so only the forward map is stored.
    """
    blocks: List[Tuple[np.ndarray, np.ndarray]] = []
    for i in range(L - 1):
        m = offset + i
        valid = (((basis >> m) & 1) == 1) & (((basis >> (m + 1)) & 1) == 0)
        src = np.nonzero(valid)[0].astype(np.int32)
        target = (basis[valid] & ~np.int64(1 << m)) | np.int64(1 << (m + 1))
        dst = np.searchsorted(basis, target).astype(np.int32)
        if not np.array_equal(basis[dst], target):  # pragma: no cover - internal
            raise RuntimeError("internal error: sector is not closed under hopping")
        blocks.append((src, dst))
    return blocks


def _hopping_blocks(
    basis: np.ndarray, L: int
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Hopping blocks for both spins (spin up first, then spin down)."""
    return _bond_blocks(basis, L, 0) + _bond_blocks(basis, L, L)


def _matvec_factory(
    diag: np.ndarray, blocks: Sequence[Tuple[np.ndarray, np.ndarray]], t: float
) -> Any:
    """Return ``x -> H @ x`` for the fixed-sector Hamiltonian."""

    def matvec(x: np.ndarray) -> np.ndarray:
        x = np.ascontiguousarray(x, dtype=np.float64).reshape(-1)
        y = diag * x
        for src, dst in blocks:
            y[dst] -= t * x[src]
            y[src] -= t * x[dst]
        return y

    return matvec


def _dense_hamiltonian(
    dim: int,
    diag: np.ndarray,
    blocks: Sequence[Tuple[np.ndarray, np.ndarray]],
    t: float,
) -> np.ndarray:
    """Explicit dense Hamiltonian (small sectors only)."""
    H = np.zeros((dim, dim), dtype=np.float64)
    H[np.diag_indices(dim)] = diag
    for src, dst in blocks:
        if src.size:
            H[dst, src] -= t
            H[src, dst] -= t
    return H


def _csr_hamiltonian(
    dim: int,
    diag: np.ndarray,
    blocks: Sequence[Tuple[np.ndarray, np.ndarray]],
    t: float,
) -> Optional[Any]:
    """Explicit CSR Hamiltonian for the iterative path, or ``None``.

    ``None`` is returned for small sectors (the dense path is used anyway) and
    when the matrix would exceed the non-zero budget; the caller then keeps the
    matrix-free index-map ``matvec``.  The CSR form makes the ARPACK ``matvec``
    roughly an order of magnitude faster than the generic index-map version.
    """
    if dim <= _DENSE_MAX_DIM:
        return None
    nnz = int(dim) + 2 * sum(int(s.size) for s, _ in blocks)
    if nnz > _CSR_MAX_NNZ:
        return None
    try:
        diag_idx = np.arange(dim, dtype=np.int32)
        rows = np.concatenate(
            [diag_idx] + [b[1].astype(np.int32) for b in blocks]
            + [b[0].astype(np.int32) for b in blocks]
        )
        cols = np.concatenate(
            [diag_idx] + [b[0].astype(np.int32) for b in blocks]
            + [b[1].astype(np.int32) for b in blocks]
        )
        data = np.concatenate(
            [np.asarray(diag, dtype=np.float64)]
            + [np.full(int(b[0].size), -float(t)) for b in blocks] * 2
        )
        H = _sp.csr_matrix((data, (rows, cols)), shape=(dim, dim))
        H.sum_duplicates()
        return H
    except Exception:  # noqa: BLE001 - memory/format problem -> matrix-free
        return None


def _make_matvec(
    dim: int,
    diag: np.ndarray,
    blocks: Sequence[Tuple[np.ndarray, np.ndarray]],
    t: float,
) -> Tuple[Any, Optional[Any]]:
    """Return ``(matvec, csr_or_None)`` for the sector Hamiltonian."""
    csr = _csr_hamiltonian(dim, diag, blocks, t)
    if csr is not None:
        return (lambda x: csr @ np.asarray(x, dtype=np.float64).reshape(-1)), csr
    return _matvec_factory(diag, blocks, t), None


def _as_operator(matvec: Any, dim: int, csr: Optional[Any]) -> Any:
    """A scipy-linear-operator view of the Hamiltonian for eigsh / lobpcg."""
    if csr is not None:
        return csr
    return _spla.LinearOperator(
        (dim, dim), matvec=matvec, rmatvec=matvec, dtype=np.float64
    )


# --------------------------------------------------------------------------- #
# candidate eigenpairs and their self-consistency data
# --------------------------------------------------------------------------- #
class _Candidate:
    """One candidate ground state: vector, Rayleigh quotient, residual, method."""

    __slots__ = ("rq", "residual", "vec", "evals", "method")

    def __init__(
        self,
        rq: float,
        residual: float,
        vec: np.ndarray,
        evals: np.ndarray,
        method: str,
    ) -> None:
        self.rq = float(rq)
        self.residual = float(residual)
        self.vec = np.asarray(vec, dtype=np.float64).reshape(-1)
        self.evals = np.asarray(evals, dtype=np.float64).reshape(-1)
        self.method = str(method)


def _evaluate(
    matvec: Any,
    vec: Any,
    dim: int,
    evals: Optional[Sequence[float]] = None,
    method: str = "",
) -> Optional[_Candidate]:
    """Rayleigh quotient and residual of ``vec`` for the operator ``matvec``.

    ``residual = || H v - E v || / max(1, |E|)`` with ``v`` normalised and
    ``E = <v|H|v>``; eigenpair and residual are therefore mutually consistent.
    """
    v = np.asarray(vec, dtype=np.float64).reshape(-1)
    if v.size != dim:
        return None
    nrm = float(np.linalg.norm(v))
    if not math.isfinite(nrm) or nrm <= 0.0:
        return None
    v = v / nrm
    Hv = np.asarray(matvec(v), dtype=np.float64).reshape(-1)
    if Hv.size != dim or not np.all(np.isfinite(Hv)):
        return None
    rq = float(v @ Hv)
    if not math.isfinite(rq):
        return None
    residual = float(np.linalg.norm(Hv - rq * v) / max(1.0, abs(rq)))
    if evals is None:
        levels = np.asarray([rq], dtype=np.float64)
    else:
        levels = np.asarray(evals, dtype=np.float64).reshape(-1)
    return _Candidate(rq, residual, v, levels, method)


def _best_candidate(cands: Sequence[_Candidate]) -> Optional[_Candidate]:
    """Best estimate: smallest Rayleigh quotient among the residual-valid ones.

    Every Rayleigh quotient is an upper bound on the ground energy, so the
    smallest one is the best estimate available; the residual is used only to
    prefer numerically valid eigenpairs and to break exact ties.
    """
    if not cands:
        return None
    good = [c for c in cands if c.residual <= _RESIDUAL_TOL]
    pool = good if good else list(cands)
    return min(pool, key=lambda c: (c.rq, c.residual))


def _gap_from_levels(levels: Any, e0: float) -> float:
    """First level strictly above ``e0``, clamped to be non-negative."""
    lv = np.asarray(levels, dtype=np.float64).reshape(-1)
    if lv.size < 2:
        return 0.0
    above = lv[lv > e0 + _DEGENERACY_TOL * max(1.0, abs(e0))]
    if above.size == 0:
        return 0.0
    gap = float(above.min() - e0)
    return gap if gap > 0.0 else 0.0


def _normalized(v: np.ndarray) -> np.ndarray:
    nrm = float(np.linalg.norm(v))
    if not math.isfinite(nrm) or nrm <= 0.0:
        v = np.ones_like(v, dtype=np.float64)
        nrm = float(np.linalg.norm(v))
    return np.asarray(v, dtype=np.float64) / nrm


def _atomic_start_vector(diag: np.ndarray, dim: int, rng: Any, noise: float = 0.1) -> np.ndarray:
    """Start vector biased towards the atomic-limit ground manifold.

    The minimal-double-occupancy subspace is the exact ground space at
    ``t = 0``; for small ``t`` the true ground state is dominated by it.  A
    small random component keeps the Krylov space from being trapped in a
    single symmetry sector.
    """
    d = np.asarray(diag, dtype=np.float64)
    dmin = float(d.min())
    mask = d <= dmin + _DEGENERACY_TOL * max(1.0, abs(dmin))
    v = mask.astype(np.float64)
    if float(np.linalg.norm(v)) <= 0.0:  # pragma: no cover - dim >= 1 always
        v = np.ones(dim, dtype=np.float64)
    v = _normalized(v)
    w = _normalized(rng.standard_normal(dim))
    return _normalized(v + noise * w)


# --------------------------------------------------------------------------- #
# iterative back-end
# --------------------------------------------------------------------------- #
def _safe_eigsh(
    op: Any, dim: int, k: int, v0: np.ndarray, ncv: int
) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    """Lowest ``k`` eigenpairs, or ``None`` if ARPACK cannot deliver them.

    All ARPACK failures (including ``ArpackError -9: Starting vector is
    zero`` for a degenerate start) are absorbed here so that no legal input can
    produce an uncaught exception.
    """
    k = int(min(max(k, 1), max(dim - 1, 1)))
    if dim <= 2 or k < 1 or k >= dim:
        return None
    ncv = int(min(dim, max(ncv, k + 2)))
    try:
        vals, vecs = _spla.eigsh(
            op,
            k=k,
            which="SA",
            v0=np.asarray(v0, dtype=np.float64).reshape(-1),
            ncv=ncv,
            tol=0.0,
            maxiter=max(1000, 20 * dim),
        )
    except _spla.ArpackNoConvergence as exc:  # partial results are still useful
        vals = getattr(exc, "eigenvalues", None)
        vecs = getattr(exc, "eigenvectors", None)
        if vals is None or vecs is None or len(vals) == 0:
            return None
        return np.asarray(vals, dtype=np.float64), np.asarray(vecs, dtype=np.float64)
    except Exception:  # noqa: BLE001 - any ARPACK/ LAPACK failure -> fall back
        return None
    if vals is None or vecs is None or len(vals) == 0:
        return None
    return np.asarray(vals, dtype=np.float64), np.asarray(vecs, dtype=np.float64)


def _lobpcg_candidates(
    op: Any, matvec: Any, dim: int, k: int, diag: np.ndarray
) -> List[_Candidate]:
    """Block LOBPCG fallback with a Jacobi-type preconditioner (never raises)."""
    try:
        from scipy.sparse.linalg import lobpcg
    except Exception:  # pragma: no cover - scipy always ships lobpcg
        return []
    block = int(min(dim, max(2, min(k, 6))))
    rng = np.random.default_rng(_V0_SEED + 101)
    X = rng.standard_normal((dim, block))
    d = np.abs(np.asarray(diag, dtype=np.float64))
    shift = max(1.0, float(d.max()) if d.size else 1.0)
    inv = 1.0 / (d + shift)
    prec = _spla.LinearOperator(
        (dim, dim),
        matvec=lambda x: np.asarray(x, dtype=np.float64).reshape(-1) * inv,
        matmat=lambda x: np.asarray(x, dtype=np.float64) * inv[:, None],
        dtype=np.float64,
    )
    vals = vecs = None
    for kwargs in ({"M": prec}, {}):
        try:
            vals, vecs = lobpcg(
                op, X, largest=False, tol=1e-13, maxiter=2000, **kwargs
            )
            break
        except Exception:  # noqa: BLE001 - fall back to the next variant
            vals = vecs = None
    if vals is None or vecs is None:
        return []
    out: List[_Candidate] = []
    vecs = np.asarray(vecs, dtype=np.float64)
    for j in range(vecs.shape[1]):
        cand = _evaluate(
            matvec, vecs[:, j], dim, evals=vals, method="lobpcg"
        )
        if cand is not None:
            out.append(cand)
    return out


def _needs_second_opinion(cands: Sequence[_Candidate], dim: int) -> bool:
    """Whether a second, differently started Lanczos run is worth its cost."""
    if not cands:
        return True
    good = [c for c in cands if c.residual <= _RESIDUAL_TOL]
    if not good:
        return True
    if dim <= _CROSSCHECK_DIM:
        return True
    best = min(good, key=lambda c: c.rq)
    lv = np.sort(best.evals)
    if lv.size >= 2 and float(lv[1] - lv[0]) <= _DEGENERACY_TOL * max(1.0, abs(best.rq)):
        # A near-degenerate lowest level is exactly where a naive Krylov run
        # can collapse onto part of a large manifold and miss the true bottom.
        return True
    return False


# --------------------------------------------------------------------------- #
# solver paths
# --------------------------------------------------------------------------- #
def _conventions(L: int, n_up: int, n_down: int, dim: int) -> str:
    return (
        f"H = -t*sum_{{i=0}}^{{L-2}} sum_{{sigma in (up,dn)}} "
        f"(c^dag_{{i,sigma}} c_{{i+1,sigma}} + h.c.) "
        f"+ U*sum_{{i=0}}^{{L-1}} n_{{i,up}} n_{{i,dn}}; "
        f"open boundary conditions (bond sum stops at i = L-2, no PBC wrap); "
        f"fermionic modes 0up..(L-1)up,0dn..(L-1)dn; fixed sector "
        f"(n_up={n_up}, n_down={n_down}) of the 2L={2 * L}-mode Fock space; "
        f"dim = C({L},{n_up})*C({L},{n_down}) = {dim}"
    )


def _atomic_solution(
    dim: int, diag: np.ndarray, matvec: Any, L: int, U: float, n_up: int, n_down: int
) -> Tuple[_Candidate, float]:
    """``t = 0``: H is diagonal -- solve it exactly, never iteratively."""
    j = int(np.argmin(diag))
    vec = np.zeros(dim, dtype=np.float64)
    vec[j] = 1.0
    method = (
        f"atomic limit t = 0 solved exactly: H is diagonal in the occupation "
        f"basis with eigenvalues U*(double occupancy), so E0 = U*min(double "
        f"occupancy) is evaluated by direct minimisation over all {dim} sector "
        f"states (no Lanczos/ARPACK, hence the large zero space of the atomic "
        f"limit cannot be missed); gap = first distinct diagonal level; "
        + _conventions(L, n_up, n_down, dim)
    )
    cand = _evaluate(matvec, vec, dim, evals=np.unique(diag), method=method)
    if cand is None:  # pragma: no cover - cannot happen for a basis vector
        raise RuntimeError("internal error: atomic-limit eigenvector rejected")
    gap = _gap_from_levels(np.unique(diag), cand.rq)
    return cand, gap


def _dense_solution(
    dim: int,
    diag: np.ndarray,
    blocks: Sequence[Tuple[np.ndarray, np.ndarray]],
    t: float,
    matvec: Any,
    L: int,
    U: float,
    n_up: int,
    n_down: int,
) -> Tuple[_Candidate, float]:
    """Full dense spectrum (exact E0 and E1)."""
    H = _dense_hamiltonian(dim, diag, blocks, t)
    vals, vecs = np.linalg.eigh(H)
    order = np.argsort(vals)
    evals = np.asarray(vals, dtype=np.float64)[order]
    method = (
        f"dense exact diagonalization: numpy.linalg.eigh (full {dim}x{dim} "
        f"spectrum); " + _conventions(L, n_up, n_down, dim)
    )
    cand = _evaluate(
        matvec, np.asarray(vecs)[:, order[0]], dim, evals=evals, method=method
    )
    if cand is None:  # pragma: no cover - cannot happen for eigh output
        raise RuntimeError("internal error: dense eigenvector rejected")
    gap = _gap_from_levels(evals, cand.rq)
    return cand, gap


def _spin_sector_solution(L: int, n: int, t: float) -> Tuple[np.ndarray, float, np.ndarray]:
    """Exact ground eigenpair of the spinless hopping Hamiltonian on ``n`` sites.

    ``H_sigma = -t sum_{i=0}^{L-2} (c^dag_i c_{i+1} + h.c.)`` restricted to the
    ``n``-particle sector (dimension ``C(L, n) <= C(12, 6)``), diagonalised
    densely -- no Jordan-Wigner string, the single species is already
    fermionic with the same sign convention.
    """
    dim = int(math.comb(L, n))
    basis = _site_masks(L, n)
    if dim <= 1:
        return np.ones(1, dtype=np.float64), 0.0, np.zeros(1, dtype=np.float64)
    diag = np.zeros(dim, dtype=np.float64)
    blocks = _bond_blocks(basis, L, 0)
    H = _dense_hamiltonian(dim, diag, blocks, t)
    vals, vecs = np.linalg.eigh(H)
    order = np.argsort(vals)
    evals = np.asarray(vals, dtype=np.float64)[order]
    return np.asarray(vecs, dtype=np.float64)[:, order[0]], float(evals[0]), evals


def _product_levels(ev_up: np.ndarray, ev_dn: np.ndarray) -> np.ndarray:
    """Lowest few levels of ``H_up + H_dn`` (product spectrum)."""
    a = np.asarray(ev_up, dtype=np.float64).reshape(-1)[:4]
    b = np.asarray(ev_dn, dtype=np.float64).reshape(-1)[:4]
    if a.size == 0 or b.size == 0:
        return np.zeros(1, dtype=np.float64)
    return np.sort((a[:, None] + b[None, :]).ravel())


def _free_fermion_solution(
    L: int,
    t: float,
    U: float,
    n_up: int,
    n_down: int,
    basis: np.ndarray,
    dim: int,
    diag: np.ndarray,
    matvec: Any,
    csr: Optional[Any] = None,
) -> Tuple[_Candidate, float]:
    """``U = 0``: the spin sectors decouple -- build the exact product state."""
    cands: List[_Candidate] = []
    up_vec, up_e0, up_evals = _spin_sector_solution(L, n_up, t)
    dn_vec, dn_e0, dn_evals = _spin_sector_solution(L, n_down, t)

    up_masks = _site_masks(L, n_up)
    dn_masks = _site_masks(L, n_down)
    product_masks = np.bitwise_or(
        up_masks[:, None], np.left_shift(dn_masks[None, :], L)
    ).ravel()
    idx = np.searchsorted(basis, product_masks)
    if idx.size == dim and np.array_equal(basis[idx], product_masks):
        vec = np.zeros(dim, dtype=np.float64)
        vec[idx] = np.outer(up_vec, dn_vec).ravel()
        method = (
            f"free fermions U = 0 solved by exact spin-sector factorization: "
            f"H = H_up + H_dn with [H_up, H_dn] = 0, so the ground state is the "
            f"product of the two sector ground states, each obtained by full "
            f"dense diagonalization of the C(L,n_sigma)-dimensional spinless "
            f"sector (C({L},{n_up})={int(math.comb(L, n_up))}, "
            f"C({L},{n_down})={int(math.comb(L, n_down))}); "
            f"E0 = {up_e0!r} + {dn_e0!r}; "
            + _conventions(L, n_up, n_down, dim)
        )
        cand = _evaluate(matvec, vec, dim, evals=_product_levels(up_evals, dn_evals),
                         method=method)
        if cand is not None:
            cands.append(cand)

    if dim <= _CROSSCHECK_DIM:
        k = int(min(_LANCZOS_K, dim - 1))
        rng = np.random.default_rng(_V0_SEED)
        op = _as_operator(matvec, dim, csr)
        res = _safe_eigsh(
            op, dim, k, _normalized(rng.standard_normal(dim)),
            min(dim, max(24, 4 * k + 8)),
        )
        if res is not None:
            vals, vecs = res
            for j in range(vecs.shape[1]):
                c = _evaluate(
                    matvec, vecs[:, j], dim, evals=vals,
                    method="lanczos(arpack,SA) cross-check of the U=0 factorization; "
                           + _conventions(L, n_up, n_down, dim),
                )
                if c is not None:
                    cands.append(c)

    best = _best_candidate(cands)
    if best is None:  # pragma: no cover - the dense sector solve always works
        best = _fallback_candidate(dim, diag, matvec, L, n_up, n_down)
        return best, 0.0
    gap = _gap_from_levels(best.evals, best.rq)
    return best, gap


def _fallback_candidate(
    dim: int, diag: np.ndarray, matvec: Any, L: int, n_up: int, n_down: int
) -> _Candidate:
    """Last-resort estimate: uniform vector, honest residual, never raises."""
    vec = _normalized(np.ones(dim, dtype=np.float64))
    method = (
        "iterative solvers did not return a usable eigenpair; reporting the "
        "Rayleigh quotient of the uniform sector vector with its measured "
        "residual (result not converged); " + _conventions(L, n_up, n_down, dim)
    )
    cand = _evaluate(matvec, vec, dim, method=method)
    if cand is None:  # pragma: no cover - a finite matvec always yields a value
        cand = _Candidate(0.0, float("inf"), vec, np.asarray([0.0]), method)
    return cand


def _lanczos_solution(
    dim: int,
    diag: np.ndarray,
    matvec: Any,
    L: int,
    U: float,
    n_up: int,
    n_down: int,
    csr: Optional[Any] = None,
) -> Tuple[_Candidate, float]:
    """Matrix-free Lanczos with physics-informed fallbacks."""
    op = _as_operator(matvec, dim, csr)
    k = int(min(_LANCZOS_K, max(dim - 1, 1)))
    rng = np.random.default_rng(_V0_SEED)
    cands: List[_Candidate] = []

    def run(v0: np.ndarray, ncv: int, tag: str) -> None:
        res = _safe_eigsh(op, dim, k, v0, ncv)
        if res is None:
            return
        vals, vecs = res
        for j in range(np.asarray(vecs).shape[1]):
            cand = _evaluate(
                matvec, vecs[:, j], dim, evals=vals,
                method=f"{tag}; " + _conventions(L, n_up, n_down, dim),
            )
            if cand is not None:
                cands.append(cand)

    run(
        _normalized(rng.standard_normal(dim)),
        min(dim, max(24, 4 * k + 8)),
        "Lanczos exact diagonalization: scipy.sparse.linalg.eigsh (which='SA', "
        f"k={k}, tol=0) on a matrix-free LinearOperator, random deterministic start",
    )
    if _needs_second_opinion(cands, dim):
        run(
            _atomic_start_vector(diag, dim, rng),
            min(dim, max(60, 8 * k + 16)),
            "Lanczos exact diagonalization: scipy.sparse.linalg.eigsh (which='SA') "
            "restarted from a start vector uniform on the minimal-double-occupancy "
            "subspace plus noise (the atomic-limit ground space)",
        )

    best = _best_candidate(cands)
    if best is None or best.residual > _RESIDUAL_TOL:
        extra = _lobpcg_candidates(op, matvec, dim, k, diag)
        if extra:
            cands.extend(extra)
        best = _best_candidate(cands)

    if best is None:
        best = _fallback_candidate(dim, diag, matvec, L, n_up, n_down)
        return best, 0.0

    gap = _gap_from_levels(best.evals, best.rq)
    return best, gap


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #
def _solve(
    L: int, t: float, U: float, n_up: int, n_down: int
) -> Tuple[_Candidate, float, int]:
    """Return ``(candidate, gap, dim)`` for one sector."""
    dim = int(math.comb(L, n_up) * math.comb(L, n_down))
    if dim <= 0:  # pragma: no cover - unreachable for the validated ranges
        raise ValueError(f"empty sector: L={L}, n_up={n_up}, n_down={n_down}")

    basis = _sector_basis(L, n_up, n_down)
    if basis.shape[0] != dim:  # pragma: no cover - internal consistency
        raise RuntimeError(
            f"dimension mismatch: built {basis.shape[0]} states, expected {dim}"
        )
    diag = (U * _double_occupancy(basis, L)) if U != 0.0 else np.zeros(dim)

    # ---- atomic limit: the matrix is diagonal, so solve it exactly ----------
    if t == 0.0:
        matvec = _matvec_factory(diag, (), t)
        cand, gap = _atomic_solution(dim, diag, matvec, L, U, n_up, n_down)
        return cand, gap, dim

    blocks = _hopping_blocks(basis, L)
    matvec, csr = _make_matvec(dim, diag, blocks, t)

    # ---- free fermions: the two spin sectors decouple -----------------------
    if U == 0.0:
        cand, gap = _free_fermion_solution(
            L, t, U, n_up, n_down, basis, dim, diag, matvec, csr
        )
        return cand, gap, dim

    # ---- small sector: full dense spectrum ----------------------------------
    if dim <= _DENSE_MAX_DIM:
        cand, gap = _dense_solution(
            dim, diag, blocks, t, matvec, L, U, n_up, n_down
        )
        return cand, gap, dim

    # ---- otherwise: matrix-free Lanczos -------------------------------------
    cand, gap = _lanczos_solution(dim, diag, matvec, L, U, n_up, n_down, csr)
    return cand, gap, dim


def run(inputs: dict) -> Dict[str, Any]:
    """Validate ``inputs`` and return the Hubbard ground-state observables.

    Parameters
    ----------
    inputs : dict
        ``{'L': int, 't': float, 'U': float, 'n_up': int, 'n_down': int}``;
        ``t`` defaults to ``1.0`` and ``U`` to ``4.0``.  Validation is performed
        by the :class:`Inputs` model before any computation.

    Returns
    -------
    dict
        ``E0``, ``e0``, ``residual``, ``dim``, ``gap`` and ``method`` -- plain
        Python types, safe for ``json.dumps``.
    """
    if not isinstance(inputs, dict):
        raise TypeError("run() expects a dict of inputs")

    cfg = Inputs(**inputs)
    L = int(cfg.L)
    t = float(cfg.t)
    U = float(cfg.U)
    n_up = int(cfg.n_up)
    n_down = int(cfg.n_down)

    cand, gap, dim = _solve(L, t, U, n_up, n_down)

    return {
        "E0": float(cand.rq),
        "e0": float(cand.rq / L),
        "residual": float(cand.residual),
        "dim": int(dim),
        "gap": float(gap),
        "method": str(cand.method),
    }


# --------------------------------------------------------------------------- #
# self-tests (run with: python tool_module.py)
# --------------------------------------------------------------------------- #
def _reference_operator_matrix(L: int, t: float, U: float, n_up: int, n_down: int):
    """Dense Hamiltonian rebuilt from the raw ``c^dag``/``c`` algebra.

    Independent of the bit-trick representation used by the solver: the
    fermionic phases are applied explicitly, so this also verifies the "no
    Jordan-Wigner string on adjacent bonds" claim.
    """
    n_modes = 2 * L
    basis = [s for s in range(1 << n_modes)
             if sum((s >> m) & 1 for m in range(L)) == n_up
             and sum((s >> (L + m)) & 1 for m in range(L)) == n_down]
    basis.sort()
    index = {s: k for k, s in enumerate(basis)}
    dim = len(basis)
    H = np.zeros((dim, dim), dtype=np.float64)
    for k, state in enumerate(basis):
        occ = [(state >> m) & 1 for m in range(n_modes)]
        for i in range(L):
            if occ[i] and occ[L + i]:
                H[k, k] += U
        for i in range(L - 1):
            for spin in (0, 1):
                q, p = i + spin * L, (i + 1) + spin * L
                for src, dst in ((q, p), (p, q)):
                    if not occ[src]:
                        continue
                    sign = -1.0 if (sum(occ[:src]) % 2) else 1.0
                    mid = state & ~(1 << src)
                    occ_mid = list(occ)
                    occ_mid[src] = 0
                    if occ_mid[dst]:
                        continue
                    if sum(occ_mid[:dst]) % 2:
                        sign = -sign
                    H[index[mid | (1 << dst)], k] += -t * sign
    return 0.5 * (H + H.T), basis


def _self_test() -> int:
    """Analytic and cross-representation checks; prints a PASS/FAIL summary."""
    failures: List[str] = []

    def check(name: str, got: float, want: float, tol: float) -> None:
        ok = abs(got - want) <= tol
        print(f"  [{'OK ' if ok else 'FAIL'}] {name}: got {got!r}, want {want!r}")
        if not ok:
            failures.append(name)

    # 1. explicit-algebra operator equals the production matrix elementwise.
    for (L, t, U, nu, nd) in ((4, 1.0, 4.0, 2, 2), (3, 1.3, 2.0, 2, 1),
                              (5, 0.7, 0.0, 2, 3)):
        ref, basis = _reference_operator_matrix(L, t, U, nu, nd)
        mine_basis = _sector_basis(L, nu, nd)
        if not np.array_equal(np.asarray(basis, dtype=np.int64), mine_basis):
            failures.append(f"basis L={L}")
            continue
        diag = U * _double_occupancy(mine_basis, L)
        blocks = _hopping_blocks(mine_basis, L)
        H = _dense_hamiltonian(len(basis), diag, blocks, t)
        if not np.allclose(ref, H, atol=1e-12):
            failures.append(f"operator L={L}")
    print(f"  [{'OK ' if not failures else 'FAIL'}] operator algebra cross-check")

    # 2. two-site closed form, 3. free-fermion spectrum, 4. atomic limit.
    out = run({"L": 2, "t": 1.0, "U": 4.0, "n_up": 1, "n_down": 1})
    check("L=2 closed form", out["E0"], (4.0 - math.sqrt(16.0 + 16.0)) / 2.0, 1e-12)
    for L in (3, 5, 8):
        for n in (1, 2):
            eps = [-2.0 * math.cos(k * math.pi / (L + 1)) for k in range(1, L + 1)]
            want = 2.0 * sum(sorted(eps)[:n])
            out = run({"L": L, "t": 1.0, "U": 0.0, "n_up": n, "n_down": n})
            check(f"free fermions L={L} n={n}", out["E0"], want, 1e-9)
    for (L, U, nu, nd) in ((8, 4.0, 4, 4), (9, 4.0, 5, 4), (10, 4.0, 5, 5)):
        out = run({"L": L, "t": 0.0, "U": U, "n_up": nu, "n_down": nd})
        want = U * max(0, nu + nd - L)
        check(f"atomic L={L} n=({nu},{nd})", out["E0"], want, 1e-10)
        if out["residual"] > _RESIDUAL_TOL:
            failures.append(f"atomic residual L={L}")

    # 5. residual and dimension invariants over a small sweep.
    for (L, t, U, nu, nd) in ((4, 1.0, 4.0, 2, 2), (6, 1.0, 2.0, 3, 3),
                              (5, 1.0, 8.0, 3, 1), (8, 1.0, 4.0, 4, 4),
                              (8, 1.0, 100.0, 4, 4), (7, 0.0, 0.0, 3, 2)):
        out = run({"L": L, "t": t, "U": U, "n_up": nu, "n_down": nd})
        if out["dim"] != math.comb(L, nu) * math.comb(L, nd):
            failures.append(f"dim L={L}")
        if out["residual"] > _RESIDUAL_TOL:
            failures.append(f"residual L={L} t={t} U={U}")
        if abs(out["e0"] * L - out["E0"]) > 1e-12 * max(1.0, abs(out["E0"])):
            failures.append(f"e0 L={L}")

    print(f"self-test: {'PASS' if not failures else 'FAIL ' + str(failures)}")
    return 0 if not failures else 1


if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        raise SystemExit(_self_test())
    payload = (
        json.loads(sys.argv[1])
        if len(sys.argv) > 1
        else {"L": 4, "t": 1.0, "U": 4.0, "n_up": 2, "n_down": 2}
    )
    print(json.dumps(run(payload), indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# seed 注册（Tool Builder 晋升）
# 实现来自 research_data/tool_builds/hubbard_ed/attempt_2/tool_module.py 的
# DSH 编码站产物；经 golden + 三层验证 + 批评者审查 + 人工批准注册后原样晋升，
# 除本注册块外未改动交付代码（可用 diff 与原 tool_module.py 核对）。
# 少了这个块，工具不会进注册表——实验会以「未注册的工具」失败。
# ---------------------------------------------------------------------------
from .registry import ToolSpec, register  # noqa: E402

register(ToolSpec(
    name='hubbard_ed',
    version='1.0.0',
    type='exact_solver',
    description='一维单带最近邻 Hubbard 链（开放边界、固定粒子数扇区）基态能量的精确对角化',
    input_model=Inputs,
    run=run,
    applicable_domains=["quantum_many_body"],  # 与 build_tool 正式注册一致
    benchmark_refs=['hubbard_ed.yaml'],
    known_limitations=[
        'dim = C(L, L/2)^2 随 L 指数增长；L>12 时稠密路径不可用（ED 是有限希尔伯特空间 方法，不意味着免除浮点误差与迭代收敛检查）',
        '只支持开放边界（OBC）。周期边界（PBC）的边界键约定不同，需要另立 spec， 不得用本工具冒充',
        '只支持固定 (n_up, n_down) 扇区；不给出跨扇区的全局基态，也不处理 非零温或有限化学势',
        't<0 或 U<0 在数学上可算，但 fixtures 只用 t>0、U>=0 覆盖',
    ],
))
