import numpy as np
from pydantic import BaseModel, Field

class Inputs(BaseModel):
    N: int = Field(ge=2, le=20)
    J: float = 1.0
    h: float = Field(default=1.0, ge=0)
    want_gap: bool = True

def run(inputs: dict) -> dict:
    p = Inputs(**inputs)
    N, J, h = p.N, p.J, p.h
    dim = 2 ** N
    H = np.zeros((dim, dim))
    for state in range(dim):
        bits = [(state >> i) & 1 for i in range(N)]
        H[state, state] += -h * sum(1 - 2 * b for b in bits)
        for i in range(N):
            j = (i + 1) % N
            H[state ^ (1 << i) ^ (1 << j), state] += -J
    evals, evecs = np.linalg.eigh(H)
    psi0 = evecs[:, 0]
    out = {
        "E0": float(evals[0]), "e0": float(evals[0] / N),
        "residual": float(np.linalg.norm(H @ psi0 - evals[0] * psi0)),
        "dim": dim, "method": "dense_eigh",
    }
    if p.want_gap:
        out["gap"] = float(evals[1] - evals[0])
    return out
