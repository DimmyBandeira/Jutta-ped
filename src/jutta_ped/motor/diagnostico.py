"""Diagnostico do MESTRE (05f): ultimo estado da camera so em MEMORIA (nunca em disco) para a tela de configuracao.

* `processar_cena` apenas guarda a REFERENCIA da ultima Cena (o quadro ja e a copia limpa entregue pelo runtime; sem copia extra) e
  conta; todo o custo (recortes, JPEG) so acontece quando a interface PEDE (`resumo`, `jpeg_quadro`, `jpeg_recorte`).
* Sem dependencia de especialista, receita ou motor: o recorte exibido e o calculado com os ajustes EFETIVOS (`definir_ajustes`).
* Nada e gravado: os JPEG sao bytes em memoria.
"""
from __future__ import annotations

import threading
from collections import deque
import time
from collections.abc import Callable
from typing import Any

from src.jutta_ped.motor.ajustes import AjustesCamera, ajustes_para_dict, efetivos
from src.jutta_ped.motor.cena import Cena
from src.jutta_ped.motor.observacao import extrair_recorte, geometria_recorte

LARGURA_MAX_JPEG = 960
QUALIDADE_JPEG = 75
_COR_DENTRO = (80, 200, 60)        # BGR
_COR_FORA = (60, 140, 255)
_COR_ROI = (255, 200, 0)


CENAS_RETIDAS = 6


class DiagnosticoMestre:
    def __init__(self, relogio: Callable[[], float] = time.monotonic, *, largura_max_jpeg: int = LARGURA_MAX_JPEG) -> None:
        self._relogio = relogio
        self._largura_max = largura_max_jpeg
        self._lock = threading.Lock()
        self._cena: Cena | None = None
        self._recentes: deque[Cena] = deque(maxlen=CENAS_RETIDAS)     # so referencias: o resumo e as imagens do MESMO quadro_id nao divergem
        self._recebida_em_local: float | None = None
        self._geracao: int | None = None
        self._ajustes: AjustesCamera = efetivos(None)
        self._contadores = {"cenas": 0, "vazias": 0, "recusadas": 0}

    # ------------------------------------------------------------------ entrada

    def processar_cena(self, cena: Cena) -> list:
        """Guarda a ULTIMA cena da geracao vigente. Cena de geracao menor que a vigente e recusada (contada). Devolve [] (sem eventos)."""
        agora = self._relogio()
        with self._lock:
            if self._geracao is not None and cena.geracao < self._geracao:
                self._contadores["recusadas"] += 1
                return []
            self._geracao = cena.geracao
            self._cena, self._recebida_em_local = cena, agora
            self._recentes.append(cena)
            self._contadores["cenas"] += 1
            if not cena.deteccoes:
                self._contadores["vazias"] += 1
        return []

    def definir_ajustes(self, ajustes: AjustesCamera | None) -> None:
        """Ajustes que o recorte exibido usa (resolvidos por `efetivos`; ja efetivos = idempotente)."""
        with self._lock:
            self._ajustes = efetivos(ajustes)

    def limpar(self) -> None:
        """Esquece a cena, a geracao e os contadores (os ajustes ficam)."""
        with self._lock:
            self._cena = self._recebida_em_local = self._geracao = None
            self._recentes.clear()
            self._contadores = {"cenas": 0, "vazias": 0, "recusadas": 0}

    # ------------------------------------------------------------------ leitura

    def _estado(self) -> tuple[Cena | None, float | None, AjustesCamera, dict[str, int]]:
        with self._lock:
            return self._cena, self._recebida_em_local, self._ajustes, dict(self._contadores)

    def _cena_e_ajustes(self, quadro_id: int | None) -> tuple[Cena | None, AjustesCamera]:
        """A cena pedida pelo `quadro_id` (entre as retidas) ou, sem id, a ultima. Id nao retido = None (a imagem nao pode divergir do resumo)."""
        with self._lock:
            if quadro_id is None:
                return self._cena, self._ajustes
            for c in reversed(self._recentes):
                if c.quadro_id == quadro_id:
                    return c, self._ajustes
            return None, self._ajustes

    @staticmethod
    def _geometria(cena: Cena, det: Any, aj: AjustesCamera) -> tuple | None:
        return geometria_recorte(cena.quadro, det.bbox_xyxy, 0.0, margem_h=aj.crop.margem_h, margem_v=aj.crop.margem_v,
                                 minimo_px=aj.crop.minimo_px or 0)

    def resumo(self) -> dict[str, Any]:
        cena, local, aj, cont = self._estado()
        roi = None
        if aj.roi is not None:
            r = aj.roi
            roi = {"x1": r.x1, "y1": r.y1, "x2": r.x2, "y2": r.y2, "criterio": r.criterio, "intersecao_min": r.intersecao_min}
            if cena is not None:
                roi["px"] = [round(r.x1 * cena.largura, 1), round(r.y1 * cena.altura, 1), round(r.x2 * cena.largura, 1), round(r.y2 * cena.altura, 1)]
        base = {"ativo": cena is not None, "recebido_ha_s": None, "quadro_id": None, "geracao": None, "largura": None, "altura": None,
                "roi": roi, "ajustes": ajustes_para_dict(aj), "sujeitos": [], "contadores": cont}
        if cena is None:
            return base
        sujeitos = []
        for d in cena.deteccoes:
            rec, motivo = None, None
            if not d.na_roi:
                motivo = "fora_da_roi"
            elif cena.quadro is None or not hasattr(cena.quadro, "shape"):
                motivo = "sem_quadro"
            else:
                g = self._geometria(cena, d, aj)
                if g is None:
                    motivo = "recorte_vazio_ou_invalido"
                else:
                    (bx1, by1, bx2, by2), (cx1, cy1, cx2, cy2) = g
                    rec = {"origem_xy": [cx1, cy1], "tamanho": [cx2 - cx1, cy2 - cy1], "bbox_no_recorte": [bx1 - cx1, by1 - cy1, bx2 - cx1, by2 - cy1],
                           "folgas_px": {"esq": bx1 - cx1, "dir": cx2 - bx2, "topo": by1 - cy1, "base": cy2 - by2}}
            sujeitos.append({"trilha_id": d.trilha_id, "classe": d.classe, "bbox_xyxy": [float(v) for v in d.bbox_xyxy],
                             "confianca": float(d.confianca), "na_roi": bool(d.na_roi), "recorte": rec, "motivo_sem_recorte": motivo})
        base.update({"recebido_ha_s": max(0.0, round(self._relogio() - local, 3)) if local is not None else None, "quadro_id": cena.quadro_id,
                     "geracao": cena.geracao, "largura": cena.largura, "altura": cena.altura, "sujeitos": sujeitos})
        return base

    def _codificar(self, imagem: Any) -> bytes | None:
        import cv2

        ok, buf = cv2.imencode(".jpg", imagem, [int(cv2.IMWRITE_JPEG_QUALITY), QUALIDADE_JPEG])
        return bytes(buf) if ok else None

    def jpeg_quadro(self, quadro_id: int | None = None) -> bytes | None:
        """Quadro atual com caixas (verde = na ROI, laranja = fora), retangulo da ROI e rotulos `#trilha conf%`. None sem dado/quadro."""
        import cv2
        import numpy as np

        cena, aj = self._cena_e_ajustes(quadro_id)
        q = None if cena is None else cena.quadro
        if q is None or not hasattr(q, "shape") or len(q.shape) < 2 or q.shape[0] <= 0 or q.shape[1] <= 0:
            return None
        altura, largura = q.shape[:2]
        escala = min(1.0, self._largura_max / float(largura))
        img = cv2.resize(q, (max(1, int(round(largura * escala))), max(1, int(round(altura * escala)))), interpolation=cv2.INTER_AREA) if escala < 1.0 else q.copy()
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        img = np.ascontiguousarray(img)

        def pt(x: float, y: float) -> tuple[int, int]:
            return int(round(x * escala)), int(round(y * escala))

        if aj.roi is not None:
            r = aj.roi
            cv2.rectangle(img, pt(r.x1 * largura, r.y1 * altura), pt(r.x2 * largura, r.y2 * altura), _COR_ROI, 2)
        for d in cena.deteccoes:
            cor = _COR_DENTRO if d.na_roi else _COR_FORA
            x1, y1, x2, y2 = d.bbox_xyxy
            cv2.rectangle(img, pt(x1, y1), pt(x2, y2), cor, 2)
            rotulo = f"#{d.trilha_id} {int(round(d.confianca * 100))}%" + ("" if d.na_roi else " fora")
            ox, oy = pt(x1, y1)
            cv2.putText(img, rotulo, (max(0, ox), max(12, oy - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, cor, 1, cv2.LINE_AA)
        return self._codificar(img)

    def jpeg_recorte(self, trilha_id: int, quadro_id: int | None = None) -> bytes | None:
        """JPEG do recorte ATUAL (com os ajustes efetivos) do sujeito `trilha_id`; None sem dado, sujeito inexistente, fora da ROI ou recorte vazio."""
        cena, aj = self._cena_e_ajustes(quadro_id)
        if cena is None or cena.quadro is None:
            return None
        for d in cena.deteccoes:
            if d.trilha_id == trilha_id and d.na_roi:
                rec = extrair_recorte(cena.quadro, d.bbox_xyxy, 0.0, margem_h=aj.crop.margem_h, margem_v=aj.crop.margem_v, minimo_px=aj.crop.minimo_px or 0)
                return None if rec is None else self._codificar(rec.imagem)
        return None
