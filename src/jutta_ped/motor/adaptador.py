"""Adaptador entre o laco de captura existente e o motor em sombra (so converte tipos; nao decide nada)."""
from __future__ import annotations

import itertools
import threading
from collections.abc import Iterable
from typing import Any

from src.jutta_ped.motor.observacao import Observacao

_geracoes = itertools.count(1)
_trava = threading.Lock()


def proxima_geracao() -> int:
    """Identificador de sessao de captura, unico e crescente no processo (a cada (re)inicio de uma camera)."""
    with _trava:
        return next(_geracoes)


def observacoes_de_deteccoes(camera_ref: str, geracao: int, quadro_id: int, capturado_em: float, deteccoes: Iterable[Any], classe: str = "person") -> list[Observacao]:
    """Deteccoes do mestre (objetos com `bbox_xyxy`, `track_id`, `confidence`) -> observacoes. Descarta as sem trilha ou malformadas."""
    saida: list[Observacao] = []
    for d in deteccoes:
        trilha = getattr(d, "track_id", None)
        bbox = getattr(d, "bbox_xyxy", None)
        if isinstance(trilha, bool) or not isinstance(trilha, int) or trilha < 0 or bbox is None:
            continue
        try:
            saida.append(Observacao(camera_ref, geracao, quadro_id, trilha, classe, tuple(float(v) for v in bbox), capturado_em, float(getattr(d, "confidence", 1.0))))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
    return saida
