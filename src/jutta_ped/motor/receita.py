"""Receita: regra declarativa sobre atributos de especialistas (dados, sem codigo) + sua avaliacao com indeterminado explicito.

Formato (JSON):
  {"id", "versao",
   "vinculos": [{"especialista", "versoes_aceitas"?: [..], "parametros"?: {..}, "margem_recorte"?: 0..1, "orcamento_ms"?: n}],
   "regra": {"combinacao": "todas"|"alguma", "condicoes": [{"especialista", "atributo", "igual", "validade_ms"?: n}]}}
A receita so decide se a combinacao e sim/nao/indeterminado; contexto espacial/temporal e persistencia ficam no host.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

SIM, NAO, INDETERMINADO = "sim", "nao", "indeterminado"
ORCAMENTO_PADRAO_MS = 500.0
COMBINACOES = ("todas", "alguma")


def hash_config(*partes: Any) -> str:
    canonico = json.dumps(partes, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validar_receita(r: Any, contratos: dict[str, dict[str, Any] | None], fontes: tuple[str, ...] = ("visivel",)) -> list[str]:
    """Problemas de uma receita (estrutura malformada vira problema, nunca excecao). Lista vazia = valida."""
    try:
        return _validar(r, contratos, fontes)
    except (TypeError, AttributeError, KeyError, ValueError) as exc:
        return [f"estrutura invalida ({type(exc).__name__}: {exc})"]


def _validar(r: Any, contratos: dict[str, dict[str, Any] | None], fontes: tuple[str, ...] = ("visivel",)) -> list[str]:
    """Problemas de uma receita frente aos contratos dos especialistas (None = sem ativo). Lista vazia = valida."""
    if not isinstance(r, dict):
        return ["receita deve ser um objeto"]
    versao_contrato = contrato_da_receita(r)
    if versao_contrato == 2:
        from src.jutta_ped.motor.regras import validar_contrato2     # import tardio: regras.py importa este modulo
        return validar_contrato2(r, contratos, fontes)
    if versao_contrato != 1:
        return [f"contrato desconhecido: {r.get('contrato')!r} (suportados: 1, 2)"]
    p: list[str] = []
    if not isinstance(r.get("id"), str) or not r.get("id") or not isinstance(r.get("versao"), str) or not r.get("versao"):
        p.append("receita exige 'id' e 'versao' (texto)")
    vinculos, regra = r.get("vinculos"), r.get("regra")
    if not isinstance(vinculos, list) or not vinculos or not all(isinstance(v, dict) for v in vinculos):
        return p + ["receita exige 'vinculos' (lista nao vazia de objetos)"]
    if not isinstance(regra, dict) or regra.get("combinacao") not in COMBINACOES or not isinstance(regra.get("condicoes"), list) or not regra["condicoes"]:
        return p + [f"'regra' exige combinacao em {COMBINACOES} e 'condicoes' nao vazias"]
    pv, vinculados = validar_vinculos(vinculos, contratos, fontes)
    p += pv
    for cond in regra["condicoes"]:
        if not isinstance(cond, dict) or "igual" not in cond:
            p.append("condicao invalida (exige especialista, atributo e igual)")
            continue
        pid, nome = cond.get("especialista"), cond.get("atributo")
        if not isinstance(pid, str) or not isinstance(nome, str) or pid not in vinculados:
            p.append(f"condicao usa '{pid}' sem vinculo")
            continue
        c = contratos.get(pid)
        if c is None:
            continue
        a = c["atributos"].get(nome)
        if a is None:
            p.append(f"{pid}: atributo '{nome}' inexistente (tem: {sorted(c['atributos'])})")
            continue
        for msg in (problema_igual(pid, nome, a, cond["igual"]), problema_validade(pid, nome, a, cond)):
            if msg:
                p.append(msg)
    return p


def contrato_da_receita(r: dict[str, Any]) -> int | None:
    """Versao do contrato da receita: ausente = 1 (receita simples ja persistida); None se o valor nao for 1 nem 2."""
    if "contrato" not in r:
        return 1
    c = r["contrato"]
    return c if isinstance(c, int) and not isinstance(c, bool) and c in (1, 2) else None


def problema_igual(pid: str, nome: str, a: dict[str, Any], alvo: Any) -> str | None:
    if a["tipo"] == "tri" and alvo not in ("sim", "nao"):
        return f"{pid}.{nome}: 'igual' de atributo tri deve ser 'sim' ou 'nao' (veio {alvo!r})"
    if a["tipo"] == "categoria" and alvo not in a["categorias"]:
        return f"{pid}.{nome}: 'igual' {alvo!r} nao esta nas categorias {a['categorias']}"
    if a["tipo"] == "numero" and not _num(alvo):
        return f"{pid}.{nome}: 'igual' de atributo numero deve ser numerico"
    return None


def problema_validade(pid: str, nome: str, a: dict[str, Any], cond: dict[str, Any]) -> str | None:
    if "validade_ms" in cond and (not _num(cond["validade_ms"]) or not 0 < cond["validade_ms"] <= a["validade_ms"]):
        return f"{pid}.{nome}: validade_ms da receita deve estar em (0, {a['validade_ms']}] (validade do atributo)"
    return None


def validar_vinculos(vinculos: list[dict[str, Any]], contratos: dict[str, dict[str, Any] | None], fontes: tuple[str, ...]) -> tuple[list[str], set[str]]:
    """Validacao dos vinculos (comum aos contratos 1 e 2). Devolve (problemas, ids vinculados)."""
    p: list[str] = []
    vinculados: set[str] = set()
    for v in vinculos:
        pid = v.get("especialista")
        if not isinstance(pid, str) or not pid or pid in vinculados:
            p.append(f"vinculo invalido ou repetido: {pid!r}")
            continue
        vinculados.add(pid)
        c = contratos.get(pid)
        if c is None:
            p.append(f"{pid}: sem versao ativa instalada")
            continue
        if v.get("versoes_aceitas") is not None and (not isinstance(v["versoes_aceitas"], list) or c["versao"] not in v["versoes_aceitas"]):
            p.append(f"{pid}: versao ativa {c['versao']} fora de versoes_aceitas {v.get('versoes_aceitas')}")
        if c["entrada"]["alvo"]["tipo"] != "recorte":
            p.append(f"{pid}: alvo '{c['entrada']['alvo']['tipo']}' ainda nao e despachavel por este motor (so 'recorte')")
        if c["entrada"]["fonte"] not in fontes:
            p.append(f"{pid}: fonte '{c['entrada']['fonte']}' indisponivel (disponiveis: {list(fontes)})")
        m = v.get("margem_recorte", 0.0)
        if not _num(m) or not 0 <= m <= 1:
            p.append(f"{pid}: margem_recorte deve estar em [0, 1]")
        o = v.get("orcamento_ms", ORCAMENTO_PADRAO_MS)
        if not _num(o) or o <= 0:
            p.append(f"{pid}: orcamento_ms deve ser > 0")
        if not isinstance(v.get("parametros") or {}, dict):
            p.append(f"{pid}: parametros deve ser um objeto")
            continue
        for nome, valor in (v.get("parametros") or {}).items():
            d = c["parametros"].get(nome)
            if d is None:
                p.append(f"{pid}: parametro desconhecido '{nome}'")
            elif not _num(valor) or not d["min"] <= valor <= d["max"]:
                p.append(f"{pid}: parametro '{nome}'={valor!r} fora de [{d['min']}, {d['max']}]")
    return p, vinculados


def validar_vinculo(receita: dict[str, Any], pid: str, contrato: dict[str, Any], fontes: tuple[str, ...] = ("visivel",)) -> list[str]:
    """Revalida SO a parte da receita que usa `pid` contra o contrato (novo) dele: tipo/categorias de `igual`, atributo, parametros,
    alvo e fonte. Usado quando a versao/identidade ativa muda depois de `configurar` (ex.: ativacao pelo painel)."""
    if contrato_da_receita(receita) == 2:
        from src.jutta_ped.motor.regras import validar_vinculo_contrato2
        return validar_vinculo_contrato2(receita, pid, contrato, fontes)
    vinc = [v for v in receita["vinculos"] if isinstance(v, dict) and v.get("especialista") == pid]
    conds = [c for c in receita["regra"]["condicoes"] if isinstance(c, dict) and c.get("especialista") == pid]
    if not vinc:
        return []
    parcial = {"id": receita["id"], "versao": receita["versao"], "vinculos": [{k: v for k, v in vinc[0].items() if k != "versoes_aceitas"}],
               "regra": {"combinacao": "todas", "condicoes": conds or [{"especialista": pid, "atributo": "", "igual": "sim"}]}}
    problemas = validar_receita(parcial, {pid: contrato}, fontes)
    return [x for x in problemas if not (not conds and "inexistente" in x)]


def combinar(combinacao: str, resultados: list[str]) -> str:
    """`resultados`: sim/nao/indeterminado por condicao. Indeterminado so decide se nenhuma outra condicao decidir."""
    if combinacao == "todas":
        return NAO if NAO in resultados else SIM if all(x == SIM for x in resultados) else INDETERMINADO
    return SIM if SIM in resultados else NAO if all(x == NAO for x in resultados) else INDETERMINADO
