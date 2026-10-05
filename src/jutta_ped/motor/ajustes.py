"""Ajustes por camera (05f): ROI, crop, execucao e perfis. Dados imutaveis + funcoes puras (sem relogio, sem I/O).

Convencoes (docs/etapas_motor/05f_configuracao_camera.md, secao 2):
* ROI: retangulo NORMALIZADO (fracoes do quadro, 0..1), convertido para pixels pela dimensao de cada Cena.
* Pertencimento (`selecionar_roi`) usa SEMPRE a parte VISIVEL da caixa (recortada ao quadro); bordas da ROI sao INCLUSIVAS.
* Precedencia (`efetivos`), sem somar margens: campo explicito da camera > perfil escolhido > receita (vinculo) > padrao (0).
  Dentro de um mesmo nivel, a margem do eixo (`margem_h`/`margem_v`) vence a margem unica (`margem`).
* A camera so APERTA a validade dos atributos (`receita_efetiva`): nunca amplia, nunca zera.
"""
from __future__ import annotations

import copy
import dataclasses
import math
from dataclasses import dataclass, field
from typing import Any

from src.jutta_ped.motor.cena import Cena
from src.jutta_ped.motor.receita import ORCAMENTO_PADRAO_MS, contrato_da_receita, hash_config

CRITERIOS = ("ponto_de_apoio", "centro", "intersecao")
INTERSECAO_MIN_PADRAO = 0.5
MINIMO_PX_MAX = 200
ORCAMENTO_MAX_MS = 60_000.0
VALIDADE_MAX_MS = 3_600_000.0
INTERVALO_MAX_S = 60.0
_EPS = 1e-9


@dataclass(frozen=True)
class Roi:
    x1: float
    y1: float
    x2: float
    y2: float
    criterio: str = "ponto_de_apoio"
    intersecao_min: float = INTERSECAO_MIN_PADRAO


@dataclass(frozen=True)
class MargemCrop:
    margem: float | None = None          # atalho: define h e v
    margem_h: float | None = None        # fracao da LARGURA da caixa, por lado
    margem_v: float | None = None        # fracao da ALTURA da caixa, por lado
    minimo_px: int | None = None         # folga minima em pixels por lado


@dataclass(frozen=True)
class Execucao:
    orcamento_ms: float | None = None
    validade_max_ms: float | None = None
    intervalo_min_s: float | None = None


@dataclass(frozen=True)
class AjustesCamera:
    perfil: str | None = None
    roi: Roi | None = None
    crop: MargemCrop = field(default_factory=MargemCrop)
    execucao: Execucao = field(default_factory=Execucao)


# ------------------------------------------------------------------------------------------------------------- perfis

PERFIS: dict[str, AjustesCamera] = {
    "generico": AjustesCamera(perfil="generico", crop=MargemCrop(margem=0.10)),
    # conferido em crop_frame do pipeline de recorte legado (modulo/*/crop_pipeline.py): pad_x = max(8, int(w*0.12)), pad_y = max(8, int(h*0.08))
    "pediatria_legado": AjustesCamera(perfil="pediatria_legado", crop=MargemCrop(margem_h=0.12, margem_v=0.08, minimo_px=8)),
}
DESCRICOES_PERFIS: dict[str, str] = {
    "generico": "Margem unica de 10% da caixa em cada lado.",
    "pediatria_legado": "Igual ao recorte do pipeline legado atual: 12% horizontal, 8% vertical, minimo de 8 px por lado.",
}


def perfis_para_painel() -> list[dict[str, Any]]:
    """`[{nome, descricao, ajustes}]` (contexto de cameras/analise.html)."""
    return [{"nome": n, "descricao": DESCRICOES_PERFIS.get(n, ""), "ajustes": ajustes_para_dict(a)} for n, a in PERFIS.items()]


# ------------------------------------------------------------------------------------------------------------- dict <-> dataclass

def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _chaves_desconhecidas(obj: dict, validas: set[str], rot: str, p: list[str]) -> None:
    extras = sorted(str(k) for k in obj if k not in validas)
    if extras:
        p.append(f"{rot}: chaves desconhecidas {extras} (validas: {sorted(validas)})")


def _fracao(obj: dict, chave: str, rot: str, p: list[str]) -> float | None:
    v = obj.get(chave)
    if v is None:
        return None
    if not _num(v) or not 0.0 <= v <= 1.0:
        p.append(f"{rot}.{chave} deve ser numero finito em [0, 1] (veio {v!r})")
        return None
    return float(v)


def _faixa(obj: dict, chave: str, rot: str, p: list[str], minimo: float, maximo: float, *, minimo_aberto: bool) -> float | None:
    v = obj.get(chave)
    if v is None:
        return None
    ok = _num(v) and ((v > minimo) if minimo_aberto else (v >= minimo)) and v <= maximo
    if not ok:
        lim = f"({minimo}, {maximo}]" if minimo_aberto else f"[{minimo}, {maximo}]"
        p.append(f"{rot}.{chave} deve ser numero finito em {lim} (veio {v!r})")
        return None
    return float(v)


def _roi_de_dict(d: Any, p: list[str]) -> Roi | None:
    if d is None:
        return None
    if not isinstance(d, dict):
        p.append("roi deve ser um objeto (ou nulo)")
        return None
    _chaves_desconhecidas(d, {"x1", "y1", "x2", "y2", "criterio", "intersecao_min"}, "roi", p)
    antes = len(p)
    faltam = [k for k in ("x1", "y1", "x2", "y2") if d.get(k) is None]
    if faltam:
        p.append(f"roi exige {faltam}")
    vals = {k: _fracao(d, k, "roi", p) for k in ("x1", "y1", "x2", "y2") if d.get(k) is not None}
    if len(p) == antes and vals["x2"] <= vals["x1"]:  # type: ignore[operator]
        p.append("roi: x2 deve ser maior que x1")
    if len(p) == antes and vals["y2"] <= vals["y1"]:  # type: ignore[operator]
        p.append("roi: y2 deve ser maior que y1")
    criterio = d.get("criterio", "ponto_de_apoio")
    if criterio is None:
        criterio = "ponto_de_apoio"
    if criterio not in CRITERIOS:
        p.append(f"roi.criterio deve ser um de {list(CRITERIOS)} (veio {criterio!r})")
    im = d.get("intersecao_min", INTERSECAO_MIN_PADRAO)
    if im is None:
        im = INTERSECAO_MIN_PADRAO
    if not _num(im) or not 0.0 < im <= 1.0:
        p.append(f"roi.intersecao_min deve estar em (0, 1] (veio {im!r})")
    if len(p) != antes:
        return None
    return Roi(vals["x1"], vals["y1"], vals["x2"], vals["y2"], criterio, float(im))  # type: ignore[arg-type]


def ajustes_de_dict(d: Any) -> AjustesCamera:
    """Valida e constroi. `ValueError` com TODOS os problemas (separados por '; '); `.args[1]` traz a lista."""
    p: list[str] = []
    if not isinstance(d, dict):
        raise ValueError("ajustes deve ser um objeto", ["ajustes deve ser um objeto"])
    _chaves_desconhecidas(d, {"perfil", "roi", "crop", "execucao"}, "ajustes", p)
    perfil = d.get("perfil")
    if perfil is not None and (not isinstance(perfil, str) or perfil not in PERFIS):
        p.append(f"perfil desconhecido: {perfil!r} (conhecidos: {sorted(PERFIS)})")
    roi = _roi_de_dict(d.get("roi"), p)
    crop_d, ex_d = d.get("crop"), d.get("execucao")
    crop, ex = MargemCrop(), Execucao()
    if crop_d is not None:
        if not isinstance(crop_d, dict):
            p.append("crop deve ser um objeto")
        else:
            _chaves_desconhecidas(crop_d, {"margem", "margem_h", "margem_v", "minimo_px"}, "crop", p)
            mp = crop_d.get("minimo_px")
            if mp is not None and (isinstance(mp, bool) or not isinstance(mp, int) or not 0 <= mp <= MINIMO_PX_MAX):
                p.append(f"crop.minimo_px deve ser inteiro em [0, {MINIMO_PX_MAX}] (veio {mp!r})")
                mp = None
            crop = MargemCrop(_fracao(crop_d, "margem", "crop", p), _fracao(crop_d, "margem_h", "crop", p),
                              _fracao(crop_d, "margem_v", "crop", p), mp)
    if ex_d is not None:
        if not isinstance(ex_d, dict):
            p.append("execucao deve ser um objeto")
        else:
            _chaves_desconhecidas(ex_d, {"orcamento_ms", "validade_max_ms", "intervalo_min_s"}, "execucao", p)
            ex = Execucao(_faixa(ex_d, "orcamento_ms", "execucao", p, 0.0, ORCAMENTO_MAX_MS, minimo_aberto=True),
                          _faixa(ex_d, "validade_max_ms", "execucao", p, 0.0, VALIDADE_MAX_MS, minimo_aberto=True),
                          _faixa(ex_d, "intervalo_min_s", "execucao", p, 0.0, INTERVALO_MAX_S, minimo_aberto=False))
    if p:
        raise ValueError("; ".join(p), p)
    return AjustesCamera(perfil, roi, crop, ex)


def problemas_de_ajustes(d: Any) -> list[str]:
    """Lista de problemas (vazia = valido), sem levantar."""
    try:
        ajustes_de_dict(d)
    except ValueError as exc:
        return list(exc.args[1]) if len(exc.args) > 1 else [str(exc)]
    return []


def ajustes_para_dict(a: AjustesCamera) -> dict[str, Any]:
    """Forma canonica (todas as chaves, `None` onde nao definido). Inverso de `ajustes_de_dict`."""
    roi = None if a.roi is None else {"x1": a.roi.x1, "y1": a.roi.y1, "x2": a.roi.x2, "y2": a.roi.y2,
                                      "criterio": a.roi.criterio, "intersecao_min": a.roi.intersecao_min}
    return {"perfil": a.perfil, "roi": roi,
            "crop": {"margem": a.crop.margem, "margem_h": a.crop.margem_h, "margem_v": a.crop.margem_v, "minimo_px": a.crop.minimo_px},
            "execucao": {"orcamento_ms": a.execucao.orcamento_ms, "validade_max_ms": a.execucao.validade_max_ms,
                         "intervalo_min_s": a.execucao.intervalo_min_s}}


def hash_ajustes(a: AjustesCamera) -> str:
    """Hash estavel da forma canonica (nao depende de ordem de chaves nem de int/float na entrada)."""
    return hash_config("ajustes", ajustes_para_dict(a))


# ------------------------------------------------------------------------------------------------------------- precedencia

def _primeiro(*valores: Any) -> Any:
    return next((v for v in valores if v is not None), None)


def efetivos(ajustes: AjustesCamera | None, receita_vinculo: dict[str, Any] | None = None) -> AjustesCamera:
    """Ajustes RESOLVIDOS: crop com `margem_h`/`margem_v`/`minimo_px` concretos (`margem` fica None), execucao com padroes.
    Camera explicita > perfil > vinculo da receita (`margem_recorte`, `orcamento_ms`) > padrao. Margens nunca se somam.
    `orcamento_ms` cai em 500 (padrao da receita) so quando ha vinculo; sem vinculo e sem valor explicito fica None.
    Idempotente: `efetivos(efetivos(a)) == efetivos(a)` (sem vinculo)."""
    a = ajustes or AjustesCamera()
    perfil = PERFIS.get(a.perfil) if a.perfil else None
    pc = perfil.crop if perfil else MargemCrop()
    v = receita_vinculo if isinstance(receita_vinculo, dict) else {}
    rm = v.get("margem_recorte") if _num(v.get("margem_recorte")) else None
    h = _primeiro(a.crop.margem_h, a.crop.margem, pc.margem_h, pc.margem, rm, 0.0)
    vv = _primeiro(a.crop.margem_v, a.crop.margem, pc.margem_v, pc.margem, rm, 0.0)
    minimo = _primeiro(a.crop.minimo_px, pc.minimo_px, 0)
    ro = v.get("orcamento_ms") if _num(v.get("orcamento_ms")) else None
    padrao_o = ORCAMENTO_PADRAO_MS if receita_vinculo is not None else None
    orc = _primeiro(a.execucao.orcamento_ms, ro, padrao_o)
    ex = Execucao(None if orc is None else float(orc), a.execucao.validade_max_ms, float(_primeiro(a.execucao.intervalo_min_s, 0.0)))
    return AjustesCamera(a.perfil, a.roi, MargemCrop(None, float(h), float(vv), int(minimo)), ex)


# ------------------------------------------------------------------------------------------------------------- ROI

def selecionar_roi(roi: Roi | None, bbox: Any, largura: int, altura: int) -> bool:
    """A deteccao pertence a ROI? `roi=None` = quadro inteiro (True).

    Regras (documentadas e testadas):
    * a ROI normalizada e convertida pela dimensao DA CENA (`largura`, `altura`); bordas da ROI sao inclusivas;
    * a caixa e recortada ao quadro ANTES de qualquer criterio (so a parte visivel conta): `ponto_de_apoio` = centro da borda
      inferior da caixa recortada; `centro` = centro da caixa recortada; `intersecao` = area(caixa recortada ∩ ROI) / area(caixa
      recortada) >= `intersecao_min` (com tolerancia de 1e-9);
    * caixa sem parte visivel, invalida ou nao finita = False (conservador: nao e tratada como dentro).
    """
    if roi is None:
        return True
    try:
        bx1, by1, bx2, by2 = (float(v) for v in bbox)
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(v) for v in (bx1, by1, bx2, by2)) or largura <= 0 or altura <= 0:
        return False
    x1, y1, x2, y2 = max(0.0, bx1), max(0.0, by1), min(float(largura), bx2), min(float(altura), by2)
    if x2 <= x1 or y2 <= y1:
        return False
    rx1, ry1, rx2, ry2 = roi.x1 * largura, roi.y1 * altura, roi.x2 * largura, roi.y2 * altura
    if roi.criterio == "intersecao":
        ix = min(x2, rx2) - max(x1, rx1)
        iy = min(y2, ry2) - max(y1, ry1)
        if ix <= 0 or iy <= 0:
            return False
        return (ix * iy) / ((x2 - x1) * (y2 - y1)) >= roi.intersecao_min - _EPS
    px, py = ((x1 + x2) / 2.0, (y1 + y2) / 2.0) if roi.criterio == "centro" else ((x1 + x2) / 2.0, y2)
    return rx1 <= px <= rx2 and ry1 <= py <= ry2


def marcar_roi(cena: Cena, roi: Roi | None) -> Cena:
    """Cena nova com `na_roi` de cada deteccao calculado (nenhuma e descartada). Sem ROI devolve a propria Cena."""
    if roi is None:
        return cena
    dets = tuple(dataclasses.replace(d, na_roi=selecionar_roi(roi, d.bbox_xyxy, cena.largura, cena.altura)) for d in cena.deteccoes)
    return dataclasses.replace(cena, deteccoes=dets)


# ------------------------------------------------------------------------------------------------------------- receita efetiva

def _num_limpo(x: float) -> float | int:
    return int(x) if float(x).is_integer() else float(x)


def _apertar_condicoes(conds: Any, validade_max: float, contratos: dict[str, dict[str, Any] | None]) -> None:
    if not isinstance(conds, list):
        return
    for cond in conds:
        if not isinstance(cond, dict):
            continue
        contrato = contratos.get(cond.get("especialista")) if isinstance(cond.get("especialista"), str) else None
        attr = None
        if isinstance(contrato, dict) and isinstance(cond.get("atributo"), str):
            a = (contrato.get("atributos") or {}).get(cond["atributo"])
            attr = a.get("validade_ms") if isinstance(a, dict) and _num(a.get("validade_ms")) else None
        declarada = cond.get("validade_ms") if _num(cond.get("validade_ms")) else None
        novo = min(x for x in (validade_max, declarada, attr) if x is not None)
        if declarada is None and attr is not None and novo >= attr:
            continue                                   # nada a apertar: sem campo novo
        if declarada is not None and novo >= declarada:
            continue
        cond["validade_ms"] = _num_limpo(novo)


def receita_efetiva(definicao: dict[str, Any], ajustes: AjustesCamera | None, contratos: dict[str, dict[str, Any] | None] | None = None) -> dict[str, Any]:
    """COPIA da receita (contrato 1 ou 2) com os ajustes da camera aplicados; a entrada nunca e mutada.
    * vinculos: `margem_recorte` = max(h, v) efetivos (so quando ha valor ou o campo ja existia; para h != v e o limite superior:
      o recorte real usa h/v/minimo, ver `MotorSombra.definir_ajustes`) e `orcamento_ms` da camera quando explicito (camera ou perfil);
    * condicoes: `validade_ms` apertada por `validade_max_ms` (min com a declarada e com a do atributo em `contratos`); nunca amplia."""
    saida = copy.deepcopy(definicao)
    if ajustes is None or not isinstance(saida, dict):
        return saida
    perfil = PERFIS.get(ajustes.perfil) if ajustes.perfil else None
    orc_cam = ajustes.execucao.orcamento_ms
    if orc_cam is None and perfil is not None:
        orc_cam = perfil.execucao.orcamento_ms
    vinculos = saida.get("vinculos")
    for v in vinculos if isinstance(vinculos, list) else []:
        if not isinstance(v, dict):
            continue
        ef = efetivos(ajustes, v)
        m = max(ef.crop.margem_h, ef.crop.margem_v)  # type: ignore[type-var]
        if "margem_recorte" in v or m > 0:
            v["margem_recorte"] = m
        if orc_cam is not None:
            v["orcamento_ms"] = _num_limpo(orc_cam)
    vmax = ajustes.execucao.validade_max_ms
    if vmax is not None:
        contratos = contratos or {}
        if contrato_da_receita(saida) == 2:
            for papel in (saida.get("papeis") or {}).values() if isinstance(saida.get("papeis"), dict) else []:
                if isinstance(papel, dict):
                    _apertar_condicoes(papel.get("condicoes"), vmax, contratos)
        elif isinstance(saida.get("regra"), dict):
            _apertar_condicoes(saida["regra"].get("condicoes"), vmax, contratos)
    return saida
