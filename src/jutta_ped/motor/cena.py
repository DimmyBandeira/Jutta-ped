"""Cena: o que o MESTRE (detector + rastreador) viu num quadro, em forma genérica e imutável.

Contrato (documentado em docs/etapas_motor/03b_cena_regras.md):
* Unidades: coordenadas em PIXELS do quadro original (origem no canto superior esquerdo, x2/y2 exclusivos); tempo em
  segundos de um relógio MONOTÔNICO local (`recebido_em` = instante em que o quadro foi recebido localmente, não horário
  físico da câmera); confiança em [0, 1].
* Identidade: `trilha_id` é o identificador do RASTREADOR dentro de (camera_ref, geracao). Não há afirmação de identidade
  entre IDs diferentes nem entre gerações.
* Propriedade/cópia: a Cena e as Deteccao são imutáveis (tuplas); o `quadro` é uma REFERÊNCIA que o produtor promete não
  mutar depois de publicar (o runtime já entrega uma cópia limpa do quadro). A Cena é segura para passar a outra thread.
* Cena SEM detecções é válida e importante: avança o tempo (expiração e estado temporal). Ausência de trilha NÃO é
  resultado negativo de especialista nem de regra: vira "ausente"/indeterminado nas camadas acima.
* Validade: `idade_s(agora)` informa quanto o quadro envelheceu; quem processa decide o limite (orçamento do pedido).
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from src.jutta_ped.motor.observacao import Observacao


class CenaInvalida(ValueError):
    pass


@dataclass(frozen=True)
class Deteccao:
    trilha_id: int
    classe: str
    bbox_xyxy: tuple[float, float, float, float]
    confianca: float = 1.0
    na_roi: bool = True              # dentro da ROI da camera (05f); fora da ROI a deteccao continua na Cena (contexto das relacoes)

    @property
    def largura(self) -> float:
        return self.bbox_xyxy[2] - self.bbox_xyxy[0]

    @property
    def altura(self) -> float:
        return self.bbox_xyxy[3] - self.bbox_xyxy[1]


@dataclass(frozen=True)
class Cena:
    camera_ref: str
    geracao: int
    quadro_id: int
    recebido_em: float
    largura: int
    altura: int
    deteccoes: tuple[Deteccao, ...] = ()
    quadro: Any = None                   # referência (não mutar); None só em testes de regras puras

    def idade_s(self, agora: float) -> float:
        return agora - self.recebido_em

    @property
    def vazia(self) -> bool:
        return not self.deteccoes

    def por_trilha(self) -> dict[int, Deteccao]:
        return {d.trilha_id: d for d in self.deteccoes}


def _finito(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def criar_cena(camera_ref: str, geracao: int, quadro_id: int, recebido_em: float, largura: int, altura: int,
               deteccoes: Iterable[Deteccao] = (), quadro: Any = None) -> Cena:
    """Valida e constrói. Levanta `CenaInvalida` com a razão (nada de sucesso silencioso com dado ausente)."""
    if not isinstance(camera_ref, str) or not camera_ref:
        raise CenaInvalida("camera_ref deve ser texto não vazio")
    if isinstance(geracao, bool) or not isinstance(geracao, int) or isinstance(quadro_id, bool) or not isinstance(quadro_id, int):
        raise CenaInvalida("geracao e quadro_id devem ser inteiros")
    if not _finito(recebido_em):
        raise CenaInvalida("recebido_em deve ser um número finito (segundos, relógio monotônico)")
    if isinstance(largura, bool) or isinstance(altura, bool) or not isinstance(largura, int) or not isinstance(altura, int) or largura <= 0 or altura <= 0:
        raise CenaInvalida("largura e altura devem ser inteiros positivos")
    vistas: set[tuple[int, str]] = set()
    saida: list[Deteccao] = []
    for d in deteccoes:
        if not isinstance(d, Deteccao):
            raise CenaInvalida("deteccoes deve conter objetos Deteccao")
        if isinstance(d.trilha_id, bool) or not isinstance(d.trilha_id, int):
            raise CenaInvalida(f"trilha_id inválido: {d.trilha_id!r}")
        if not isinstance(d.classe, str) or not d.classe:
            raise CenaInvalida("classe deve ser texto não vazio")
        if not isinstance(d.bbox_xyxy, tuple) or len(d.bbox_xyxy) != 4 or not all(_finito(v) for v in d.bbox_xyxy):
            raise CenaInvalida(f"bbox inválida na trilha {d.trilha_id}: {d.bbox_xyxy!r}")
        x1, y1, x2, y2 = d.bbox_xyxy
        if x2 <= x1 or y2 <= y1:
            raise CenaInvalida(f"bbox vazia/invertida na trilha {d.trilha_id}: {d.bbox_xyxy!r}")
        if not _finito(d.confianca) or not 0.0 <= d.confianca <= 1.0:
            raise CenaInvalida(f"confiança fora de [0, 1] na trilha {d.trilha_id}: {d.confianca!r}")
        chave = (d.trilha_id, d.classe)
        if chave in vistas:
            raise CenaInvalida(f"trilha {d.trilha_id} repetida na classe {d.classe!r} no mesmo quadro")
        vistas.add(chave)
        saida.append(d)
    return Cena(camera_ref, geracao, quadro_id, float(recebido_em), largura, altura, tuple(saida), quadro)


def deteccoes_de_objetos(itens: Iterable[Any], classe: str) -> list[Deteccao]:
    """Adapta objetos com `track_id`/`bbox_xyxy`/`confidence` (as detecções do pipeline atual) a `Deteccao` da classe mestre dada.
    Descarta (não inventa) itens sem trilha inteira ≥ 0 ou com bbox ilegível."""
    saida: list[Deteccao] = []
    for it in itens:
        trilha, bbox = getattr(it, "track_id", None), getattr(it, "bbox_xyxy", None)
        if isinstance(trilha, bool) or not isinstance(trilha, int) or trilha < 0 or bbox is None:
            continue
        try:
            caixa = tuple(float(v) for v in bbox)
            conf = float(getattr(it, "confidence", 1.0))
        except (TypeError, ValueError):
            continue
        if len(caixa) != 4:
            continue
        saida.append(Deteccao(trilha, classe, caixa, conf))  # type: ignore[arg-type]
    return saida


def observacoes_de_cena(cena: Cena) -> list[Observacao]:
    """Uma `Observacao` por detecção da Cena (mesmo instante de captura, mesma geração/câmera)."""
    return [Observacao(cena.camera_ref, cena.geracao, cena.quadro_id, d.trilha_id, d.classe, d.bbox_xyxy, cena.recebido_em, d.confianca)
            for d in cena.deteccoes]
