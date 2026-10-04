"""Estatistica minima e deterministica (sem numpy) para os relatorios da bateria."""
from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any


def percentil(valores: Iterable[float], q: float) -> float | None:
    """Percentil `q` (0-100) por interpolacao linear entre ranks vizinhos. Lista vazia -> None."""
    v = sorted(float(x) for x in valores)
    if not v:
        return None
    if len(v) == 1:
        return v[0]
    pos = (len(v) - 1) * (q / 100.0)
    lo, hi = math.floor(pos), math.ceil(pos)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def resumir(valores: Iterable[float], casas: int = 3) -> dict[str, Any]:
    v = [float(x) for x in valores]
    if not v:
        return {"n": 0, "media": None, "p50": None, "p95": None, "max": None}
    return {
        "n": len(v),
        "media": round(sum(v) / len(v), casas),
        "p50": round(percentil(v, 50), casas),
        "p95": round(percentil(v, 95), casas),
        "max": round(max(v), casas),
    }
