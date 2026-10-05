"""Observacao (o que o detector mestre/rastreador viu num quadro) e recorte com a geometria preservada."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Observacao:
    """Uma pessoa/objeto rastreado num quadro. `capturado_em` esta na MESMA base de tempo do relogio do motor (monotonico, s)."""

    camera_ref: str
    geracao: int                       # geracao da sessao da camera; muda a cada (re)inicio
    quadro_id: int
    trilha_id: int
    classe: str                        # classe do detector mestre
    bbox_xyxy: tuple[float, float, float, float]
    capturado_em: float
    confianca: float = 1.0


@dataclass(frozen=True)
class Recorte:
    """Imagem recortada + transformacao entre coordenadas do crop e do quadro (so translacao: o crop nao e redimensionado aqui)."""

    imagem: Any
    origem_xy: tuple[int, int]                       # canto superior esquerdo do crop NO quadro
    bbox_quadro: tuple[int, int, int, int]           # bbox da observacao, ja ajustada ao quadro
    bbox_no_recorte: tuple[int, int, int, int]       # a mesma bbox em coordenadas do crop
    quadro_wh: tuple[int, int]
    folgas_px: tuple[int, int, int, int] = (0, 0, 0, 0)   # (esq, dir, topo, base) realmente aplicadas apos o ajuste ao quadro

    @property
    def altura_alvo_px(self) -> int:
        return self.bbox_quadro[3] - self.bbox_quadro[1]

    def para_quadro(self, x: float, y: float) -> tuple[float, float]:
        return x + self.origem_xy[0], y + self.origem_xy[1]

    def para_recorte(self, x: float, y: float) -> tuple[float, float]:
        return x - self.origem_xy[0], y - self.origem_xy[1]


def ajustar_bbox(quadro: Any, bbox: Any) -> tuple[int, int, int, int] | None:
    """Recorta a bbox aos limites do quadro (x2/y2 exclusivos). None se invalida, nao finita ou vazia depois do ajuste."""
    if quadro is None or not hasattr(quadro, "shape") or len(quadro.shape) < 2:
        return None
    altura, largura = quadro.shape[:2]
    if isinstance(bbox, (str, bytes)) or not hasattr(bbox, "__len__") or len(bbox) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return None
    if any(v != v or v in (float("inf"), float("-inf")) for v in (x1, y1, x2, y2)):
        return None
    ix1, iy1 = max(0, int(x1 // 1)), max(0, int(y1 // 1))
    ix2, iy2 = min(largura, int(-(-x2 // 1))), min(altura, int(-(-y2 // 1)))
    if ix2 <= ix1 or iy2 <= iy1:
        return None
    return ix1, iy1, ix2, iy2


def _folga(dimensao: int, fracao: float, minimo_px: int) -> int:
    return max(minimo_px, int(fracao * dimensao))     # truncamento, como o legado (crop_frame)


def geometria_recorte(quadro: Any, bbox: Any, margem: float = 0.0, *, margem_h: float | None = None, margem_v: float | None = None,
                      minimo_px: int = 0) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]] | None:
    """(bbox ajustada ao quadro, caixa do recorte (cx1, cy1, cx2, cy2)) ou None, SEM copiar pixels.

    * So `margem` (sem margem_h/margem_v/minimo_px): caminho ANTIGO, folga = round(dimensao_da_bbox_ajustada * margem).
    * Com `margem_h`/`margem_v`/`minimo_px`: folga por lado = max(minimo_px, int(fracao * dimensao)); a dimensao e a da caixa
      EXTERNA original (arredondada para fora, antes de recortar ao quadro, como o legado); eixo sem fracao explicita usa `margem`.
    """
    base = ajustar_bbox(quadro, bbox)
    if base is None:
        return None
    x1, y1, x2, y2 = base
    largura, altura = quadro.shape[1], quadro.shape[0]
    if margem_h is None and margem_v is None and not minimo_px:
        if not (0.0 <= margem <= 1.0):
            return None
        mx, my = int(round((x2 - x1) * margem)), int(round((y2 - y1) * margem))
    else:
        fh = margem if margem_h is None else margem_h
        fv = margem if margem_v is None else margem_v
        if not (0.0 <= fh <= 1.0 and 0.0 <= fv <= 1.0) or isinstance(minimo_px, bool) or not isinstance(minimo_px, int) or minimo_px < 0:
            return None
        rx1, ry1, rx2, ry2 = (float(v) for v in bbox)
        wx, wy = int(-(-rx2 // 1)) - int(rx1 // 1), int(-(-ry2 // 1)) - int(ry1 // 1)
        mx, my = _folga(wx, fh, minimo_px), _folga(wy, fv, minimo_px)
    cx1, cy1, cx2, cy2 = max(0, x1 - mx), max(0, y1 - my), min(largura, x2 + mx), min(altura, y2 + my)
    if cx2 <= cx1 or cy2 <= cy1:
        return None
    return base, (cx1, cy1, cx2, cy2)


def extrair_recorte(quadro: Any, bbox: Any, margem: float = 0.0, *, margem_h: float | None = None, margem_v: float | None = None,
                    minimo_px: int = 0) -> Recorte | None:
    """Recorta `bbox` (+ folga por lado, limitada ao quadro; ver `geometria_recorte`). None descarta o recorte (vazio/invalido).
    `Recorte.folgas_px` = (esq, dir, topo, base) realmente aplicadas depois do ajuste ao quadro."""
    g = geometria_recorte(quadro, bbox, margem, margem_h=margem_h, margem_v=margem_v, minimo_px=minimo_px)
    if g is None:
        return None
    (x1, y1, x2, y2), (cx1, cy1, cx2, cy2) = g
    imagem = quadro[cy1:cy2, cx1:cx2]
    if imagem.size == 0:
        return None
    return Recorte(imagem.copy(), (cx1, cy1), (x1, y1, x2, y2), (x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1), (quadro.shape[1], quadro.shape[0]),
                   (x1 - cx1, cx2 - x2, y1 - cy1, cy2 - y2))
