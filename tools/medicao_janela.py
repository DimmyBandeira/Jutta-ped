"""Funcoes PURAS de medicao do ensaio multicamera (testadas em `tests/test_medicao_janela.py`).

Definicoes (todas no relogio monotonico do processo):
* Janela = [t_ini, t_fim). Populacao por **CONCLUSAO**: um evento pertence a janela se `avaliado_em` cai nela; uma chamada ao executor,
  se `t + ms/1000` (instante em que terminou) cai nela. Itens concluidos antes = `antes` (aquecimento); depois de `t_fim` = `drenagem`
  (trabalho que terminou enquanto as sessoes eram paradas): contados a parte e NUNCA misturados as estatisticas da janela.
* Contadores acumulados (quadros, descartes, despacho) so valem pela DIFERENCA entre dois snapshots (inicio e fim da janela).
* Cena != evento: contrato 2 emite um evento `tipo="cena"` por Cena; contrato 1 emite um evento por pessoa avaliada.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any


def classificar(t: float, t_ini: float, t_fim: float) -> str:
    if t < t_ini:
        return "antes"
    return "dentro" if t < t_fim else "depois"


def dividir_por_conclusao(itens: Iterable[dict[str, Any]], t_ini: float, t_fim: float, instante) -> dict[str, list[dict[str, Any]]]:
    """Separa itens em antes/dentro/depois pelo instante de CONCLUSAO devolvido por `instante(item)` (None = sem tempo: `antes`)."""
    saida: dict[str, list[dict[str, Any]]] = {"antes": [], "dentro": [], "depois": []}
    for it in itens:
        t = instante(it)
        saida["antes" if t is None else classificar(t, t_ini, t_fim)].append(it)
    return saida


def conclusao_evento(e: dict[str, Any]) -> float | None:
    return e.get("avaliado_em")


def conclusao_chamada(c: dict[str, Any]) -> float:
    return c["t"] + c["ms"] / 1000.0


def delta(ini: dict[str, Any], fim: dict[str, Any]) -> dict[str, Any]:
    """Diferenca campo a campo de dois snapshots numericos (aninhados). Campo nao numerico ou ausente no inicio conta desde zero."""
    saida: dict[str, Any] = {}
    for k, v in fim.items():
        a = ini.get(k, 0 if isinstance(v, (int, float)) else None)
        if isinstance(v, dict):
            saida[k] = delta(a if isinstance(a, dict) else {}, v)
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and isinstance(a, (int, float)):
            saida[k] = v - a
        else:
            saida[k] = v
    return saida


def cenas_e_avaliacoes(eventos: Iterable[dict[str, Any]]) -> dict[str, int]:
    """`cenas` = eventos tipo=cena (contrato 2); `avaliacoes_por_sujeito` = eventos sem tipo (contrato 1, um por pessoa)."""
    c = Counter("cena" if e.get("tipo") == "cena" else "sujeito" for e in eventos)
    return {"cenas": c["cena"], "avaliacoes_por_sujeito": c["sujeito"]}


def taxa(n: float, duracao_s: float) -> float | None:
    return round(n / duracao_s, 2) if duracao_s > 0 else None


def prontidao(estados: dict[str, dict[str, Any]], minimo_quadros: int, minimo_avaliacoes: int) -> dict[str, list[str]]:
    """Quais cameras ainda NAO estao prontas. `estados[cam] = {"quadros": n, "avaliacoes": n, "exige_avaliacao": bool}`."""
    faltando: dict[str, list[str]] = {}
    for cam, s in estados.items():
        f = []
        if s.get("quadros", 0) < minimo_quadros:
            f.append(f"quadros {s.get('quadros', 0)}/{minimo_quadros}")
        if s.get("exige_avaliacao") and s.get("avaliacoes", 0) < minimo_avaliacoes:
            f.append(f"avaliacoes {s.get('avaliacoes', 0)}/{minimo_avaliacoes}")
        if f:
            faltando[cam] = f
    return faltando


def dispositivo_efetivo(modelo_ultralytics: Any, solicitado: str | None = None) -> dict[str, Any]:
    """Hardware que o modelo REALMENTE usa, lido do OpenVINO compilado (so existe depois de uma inferencia). Nunca assume o pedido."""
    r: dict[str, Any] = {"solicitado": solicitado}
    try:
        ab = modelo_ultralytics.predictor.model
        r["device_do_backend"] = str(getattr(ab, "device", None))
        compilado = getattr(ab, "ov_compiled_model", None)
        if compilado is not None:
            r["execution_devices"] = [str(d) for d in compilado.get_property("EXECUTION_DEVICES")]
        else:
            r["execution_devices"] = None
            r["observacao"] = "modelo nao e OpenVINO compilado (use device_do_backend)"
    except Exception as exc:  # noqa: BLE001 -- sem inferencia previa ou backend diferente: dizer que nao sabe, nao inferir
        r["execution_devices"] = None
        r["indisponivel"] = f"{type(exc).__name__}: {exc}"
    r["divergente_do_solicitado"] = bool(solicitado and r.get("execution_devices") and not any(solicitado.split(":")[-1].lower().replace("intel_", "") in d.lower() for d in r["execution_devices"]))
    return r
