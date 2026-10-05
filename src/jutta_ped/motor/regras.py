"""Regras espaciais/temporais da mae (receita CONTRATO 2): validacao + motor de regras PURO e deterministico.

Sem relogio global, sem threads, sem dependencia de dominio: o tempo vem SO de `cena.recebido_em` (segundos, monotonico local).
Semantica em docs/etapas_motor/03b_cena_regras.md ("Semantica temporal"). Em resumo, por (sujeito, evento):
* `sim` (evidencia positiva) soma o intervalo desde o `sim` anterior se ele for <= lacuna_max_s; intervalo maior recomeca a corrida
  (o tempo da lacuna nao conta). `nao` zera a corrida e, se o evento esta ativo, acumula tempo negativo sustentado.
* `indeterminado` e sujeito ausente NAO somam, NAO zeram e NAO contam como negativo.
* O evento so inicia com `positivo_s >= duracao_s` por evidencias `sim`; so encerra como RESOLVIDO por evidencia negativa sustentada.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from src.jutta_ped.motor.cena import Cena
from src.jutta_ped.motor.receita import (
    COMBINACOES,
    INDETERMINADO,
    NAO,
    SIM,
    problema_igual,
    problema_validade,
    validar_vinculos,
)
from src.jutta_ped.motor.receita import _num as _numero

LACUNA_PADRAO_S = 2.0
PERDA_PADRAO_S = 10.0
ENCERRAR_NEGATIVO_PADRAO_S = 5.0
UNIDADES = ("pixels", "altura_sujeito", "diagonal_quadro")
TIPOS_RELACAO = ("existe_proximo", "nenhum_proximo")
NAO_SE_APLICA = "nao_se_aplica"
EPS = 1e-9

M_RESOLVIDO = "resolvido_evidencia_negativa"
M_PERDA = "perda_de_observacao"
M_AUSENTE = "sujeito_ausente_prolongado"
M_RECONFIGURADO = "reconfigurado"
M_GERACAO = "geracao_encerrada"
M_SESSAO = "sessao_encerrada"

_CHAVES_RECEITA = {"id", "versao", "contrato", "vinculos", "papeis", "relacoes", "eventos"}
_CHAVES_VINCULO = {"especialista", "versoes_aceitas", "parametros", "margem_recorte", "orcamento_ms"}
_CHAVES_PAPEL = {"classe", "combinacao", "condicoes"}
OPERADORES = ("igual", "minimo", "maximo", "maior_que")
_CHAVES_CONDICAO = {"especialista", "atributo", "igual", "minimo", "maximo", "maior_que", "validade_ms"}
_CHAVES_RELACAO = {"tipo", "sujeito", "outro", "distancia"}
METRICAS = ("intervalo_bbox", "centro", "ponto_de_apoio")
_CHAVES_DISTANCIA = {"unidade", "maximo", "metrica"}
_CHAVES_EVENTO = {"id", "sujeito", "quando_todas", "duracao_s", "lacuna_max_s", "perda_max_s", "encerrar_negativo_s"}


def _positivo(x: Any) -> bool:
    return _numero(x) and x > 0


def _desconhecidas(obj: dict[str, Any], validas: set[str], rotulo: str) -> list[str]:
    extras = sorted(str(k) for k in obj if k not in validas)
    return [f"{rotulo}: chaves desconhecidas {extras} (validas: {sorted(validas)})"] if extras else []


# ====================================================================================================== validacao (contrato 2)

def validar_contrato2(receita: Any, contratos: dict[str, dict[str, Any] | None], fontes: tuple[str, ...] = ("visivel",)) -> list[str]:
    """Problemas de uma receita de contrato 2 (estrutura malformada vira problema, nunca excecao). Lista vazia = valida."""
    try:
        return _validar2(receita, contratos, fontes)
    except (TypeError, AttributeError, KeyError, ValueError) as exc:
        return [f"estrutura invalida ({type(exc).__name__}: {exc})"]


def _validar2(r: Any, contratos: dict[str, dict[str, Any] | None], fontes: tuple[str, ...]) -> list[str]:
    if not isinstance(r, dict):
        return ["receita deve ser um objeto"]
    p: list[str] = _desconhecidas(r, _CHAVES_RECEITA, "receita (contrato 2)")
    if not isinstance(r.get("id"), str) or not r.get("id") or not isinstance(r.get("versao"), str) or not r.get("versao"):
        p.append("receita exige 'id' e 'versao' (texto)")
    vinculos, papeis = r.get("vinculos"), r.get("papeis")
    if not isinstance(vinculos, list) or not vinculos or not all(isinstance(v, dict) for v in vinculos):
        return p + ["receita exige 'vinculos' (lista nao vazia de objetos)"]
    pv, vinculados = validar_vinculos(vinculos, contratos, fontes)
    p += pv
    for v in vinculos:
        p += _desconhecidas(v, _CHAVES_VINCULO, f"vinculo {v.get('especialista')!r}")
    if not isinstance(papeis, dict) or not papeis:
        return p + ["contrato 2 exige 'papeis' (objeto nao vazio: id -> papel)"]
    for pid_papel, papel in papeis.items():
        p += _problemas_papel(pid_papel, papel, vinculados, contratos)
    relacoes = r.get("relacoes", {})
    if not isinstance(relacoes, dict):
        p.append("'relacoes' deve ser um objeto (id -> relacao)")
        relacoes = {}
    for rid, rel in relacoes.items():
        p += _problemas_relacao(rid, rel, papeis)
        if rid in papeis:
            p.append(f"relacao '{rid}': id coincide com um papel (ids de papeis e relacoes devem ser distintos)")
    eventos = r.get("eventos")
    if not isinstance(eventos, list) or not eventos:
        return p + ["contrato 2 exige 'eventos' (lista nao vazia)"]
    vistos: set[str] = set()
    for i, ev in enumerate(eventos, start=1):
        p += _problemas_evento(i, ev, papeis, relacoes, vistos)
    return p


def _problemas_papel(pid_papel: Any, papel: Any, vinculados: set[str], contratos: dict[str, dict[str, Any] | None]) -> list[str]:
    if not isinstance(pid_papel, str) or not pid_papel:
        return [f"papel com id invalido: {pid_papel!r}"]
    rot = f"papel '{pid_papel}'"
    if not isinstance(papel, dict):
        return [f"{rot}: deve ser um objeto"]
    p = _desconhecidas(papel, _CHAVES_PAPEL, rot)
    classe = papel.get("classe")
    if not isinstance(classe, str) or not classe:
        p.append(f"{rot}: 'classe' deve ser texto nao vazio")
    if papel.get("combinacao") not in COMBINACOES:
        p.append(f"{rot}: combinacao deve estar em {COMBINACOES}")
    conds = papel.get("condicoes")
    if not isinstance(conds, list):
        return p + [f"{rot}: 'condicoes' deve ser uma lista"]
    if not conds and papel.get("combinacao") != "todas":
        p.append(f"{rot}: 'condicoes' vazias so valem com combinacao 'todas' (o papel e so a classe)")
    for i, c in enumerate(conds, start=1):
        p += problemas_condicao_papel(f"{rot}, condicao {i}", c, classe if isinstance(classe, str) else None, vinculados, contratos)
    return p


def problemas_condicao_papel(rot: str, cond: Any, classe: str | None, vinculados: set[str], contratos: dict[str, dict[str, Any] | None]) -> list[str]:
    if not isinstance(cond, dict):
        return [f"{rot}: condicao deve ser um objeto"]
    p = _desconhecidas(cond, _CHAVES_CONDICAO, rot)
    pid, nome = cond.get("especialista"), cond.get("atributo")
    operadores = [k for k in OPERADORES if k in cond]
    if len(operadores) != 1:
        p.append(f"{rot}: use exatamente um operador entre {list(OPERADORES)} (veio {operadores})")
    op = operadores[0] if len(operadores) == 1 else None
    if not isinstance(pid, str) or not isinstance(nome, str) or pid not in vinculados:
        return p + [f"{rot}: usa especialista {pid!r} sem vinculo (ou atributo invalido)"]
    c = contratos.get(pid)
    if c is None:
        if op == "maior_que":                       # o atributo COMPARADO pode ser de outro especialista, cujo contrato e o que mudou
            p += _problemas_maior_que(pid, nome, None, cond["maior_que"], vinculados, contratos, classe)
        return p
    classe_mestre = c["entrada"]["alvo"].get("classe_mestre")
    if classe is not None and classe_mestre is not None and classe_mestre != classe:
        p.append(f"{rot}: {pid} avalia a classe mestre '{classe_mestre}', mas o papel e da classe '{classe}'")
    a = c["atributos"].get(nome)
    if a is None:
        return p + [f"{pid}: atributo '{nome}' inexistente (tem: {sorted(c['atributos'])})"]
    if op == "igual":
        msg = problema_igual(pid, nome, a, cond["igual"])
        if msg:
            p.append(msg)
    elif op in ("minimo", "maximo"):
        if a["tipo"] != "numero":
            p.append(f"{pid}.{nome}: '{op}' so vale em atributo numero (este e '{a['tipo']}')")
        elif not _numero(cond[op]):
            p.append(f"{pid}.{nome}: '{op}' deve ser numero finito")
    elif op == "maior_que":
        p += _problemas_maior_que(pid, nome, a, cond["maior_que"], vinculados, contratos, classe)
    msg = problema_validade(pid, nome, a, cond)
    if msg:
        p.append(msg)
    return p


def _problemas_maior_que(pid: str, nome: str, a: dict[str, Any] | None, alvo: Any, vinculados: set[str], contratos: dict[str, dict[str, Any] | None],
                         classe: str | None) -> list[str]:
    rot = f"{pid}.{nome} maior_que"
    if not isinstance(alvo, dict):
        return [f"{rot}: deve ser {{especialista, atributo}}"]
    p = _desconhecidas(alvo, {"especialista", "atributo"}, rot)
    if a is not None and a["tipo"] != "numero":
        p.append(f"{pid}.{nome}: 'maior_que' exige atributo numero (este e '{a['tipo']}')")
    pid2, nome2 = alvo.get("especialista"), alvo.get("atributo")
    if not isinstance(pid2, str) or not isinstance(nome2, str) or pid2 not in vinculados:
        return p + [f"{rot}: especialista {pid2!r} sem vinculo (ou atributo invalido)"]
    c2 = contratos.get(pid2)
    if c2 is None:
        return p
    cm2 = c2["entrada"]["alvo"].get("classe_mestre")
    if classe is not None and cm2 is not None and cm2 != classe:
        p.append(f"{rot}: {pid2} avalia a classe mestre '{cm2}', mas o papel e da classe '{classe}'")
    a2 = c2["atributos"].get(nome2)
    if a2 is None:
        return p + [f"{pid2}: atributo '{nome2}' inexistente (tem: {sorted(c2['atributos'])})"]
    if a2["tipo"] != "numero":
        p.append(f"{pid2}.{nome2}: 'maior_que' exige atributo numero (este e '{a2['tipo']}')")
    return p


def _problemas_relacao(rid: Any, rel: Any, papeis: dict[str, Any]) -> list[str]:
    if not isinstance(rid, str) or not rid:
        return [f"relacao com id invalido: {rid!r}"]
    rot = f"relacao '{rid}'"
    if not isinstance(rel, dict):
        return [f"{rot}: deve ser um objeto"]
    p = _desconhecidas(rel, _CHAVES_RELACAO, rot)
    if rel.get("tipo") not in TIPOS_RELACAO:
        p.append(f"{rot}: tipo deve estar em {TIPOS_RELACAO}")
    for campo in ("sujeito", "outro"):
        v = rel.get(campo)
        if not isinstance(v, str) or v not in papeis:
            p.append(f"{rot}: '{campo}' deve apontar para um papel existente (veio {v!r}; papeis: {sorted(papeis)})")
    d = rel.get("distancia")
    if not isinstance(d, dict):
        return p + [f"{rot}: 'distancia' deve ser {{unidade, maximo}}"]
    p += _desconhecidas(d, _CHAVES_DISTANCIA, f"{rot}, distancia")
    if d.get("unidade") not in UNIDADES:
        p.append(f"{rot}: distancia.unidade deve estar em {UNIDADES}")
    if d.get("metrica", "intervalo_bbox") not in METRICAS:
        p.append(f"{rot}: distancia.metrica deve estar em {METRICAS}")
    if not _positivo(d.get("maximo")):
        p.append(f"{rot}: distancia.maximo deve ser numero finito > 0")
    return p


def _problemas_evento(i: int, ev: Any, papeis: dict[str, Any], relacoes: dict[str, Any], vistos: set[str]) -> list[str]:
    if not isinstance(ev, dict):
        return [f"evento {i}: deve ser um objeto"]
    eid = ev.get("id")
    rot = f"evento '{eid}'" if isinstance(eid, str) and eid else f"evento {i}"
    p = _desconhecidas(ev, _CHAVES_EVENTO, rot)
    if not isinstance(eid, str) or not eid:
        p.append(f"{rot}: 'id' deve ser texto nao vazio")
    elif eid in vistos:
        p.append(f"{rot}: id repetido")
    else:
        vistos.add(eid)
    suj = ev.get("sujeito")
    suj_ok = isinstance(suj, str) and suj in papeis
    if not suj_ok:
        p.append(f"{rot}: 'sujeito' deve ser um papel existente (veio {suj!r}; papeis: {sorted(papeis)})")
    q = ev.get("quando_todas")
    if not isinstance(q, list) or not q:
        p.append(f"{rot}: 'quando_todas' deve ser lista nao vazia")
    else:
        for item in q:
            if not isinstance(item, str):
                p.append(f"{rot}: item invalido em quando_todas: {item!r}")
            elif item in relacoes:
                rel = relacoes[item]
                if suj_ok and isinstance(rel, dict) and rel.get("sujeito") != suj:
                    p.append(f"{rot}: relacao '{item}' tem outro sujeito ('{rel.get('sujeito')}'), nao '{suj}'")
            elif item in papeis:
                if suj_ok and item != suj:
                    p.append(f"{rot}: papel '{item}' em quando_todas deve ser o proprio sujeito '{suj}'")
            else:
                p.append(f"{rot}: '{item}' em quando_todas nao e relacao nem papel")
    if not _positivo(ev.get("duracao_s")):
        p.append(f"{rot}: 'duracao_s' obrigatorio, numero finito > 0")
    for campo in ("lacuna_max_s", "perda_max_s", "encerrar_negativo_s"):
        if campo in ev and not _positivo(ev[campo]):
            p.append(f"{rot}: '{campo}' deve ser numero finito > 0")
    return p


def validar_vinculo_contrato2(receita: dict[str, Any], pid: str, contrato: dict[str, Any], fontes: tuple[str, ...] = ("visivel",)) -> list[str]:
    """Revalida SO a parte da receita contrato 2 que usa `pid` (vinculo + condicoes dentro de papeis) contra o contrato novo dele."""
    vinc = [v for v in receita["vinculos"] if isinstance(v, dict) and v.get("especialista") == pid]
    if not vinc:
        return []
    parcial = {k: v for k, v in vinc[0].items() if k != "versoes_aceitas"}
    p, vinculados = validar_vinculos([parcial], {pid: contrato}, fontes)
    for papel_id, papel in (receita.get("papeis") or {}).items():
        if not isinstance(papel, dict):
            continue
        for i, cond in enumerate(papel.get("condicoes") or [], start=1):
            alvo = cond.get("maior_que") if isinstance(cond, dict) else None
            if isinstance(cond, dict) and (cond.get("especialista") == pid or (isinstance(alvo, dict) and alvo.get("especialista") == pid)):
                todos = {v["especialista"] for v in receita["vinculos"] if isinstance(v, dict) and isinstance(v.get("especialista"), str)}
                p += problemas_condicao_papel(f"papel '{papel_id}', condicao {i}", cond, papel.get("classe"), todos, {pid: contrato})
    return p


# ====================================================================================================== geometria

class CaixaInvalida(ValueError):
    pass


def validar_caixa(b: Any) -> tuple[float, float, float, float]:
    if not isinstance(b, (tuple, list)) or len(b) != 4 or not all(_numero(v) for v in b):
        raise CaixaInvalida(f"caixa invalida: {b!r}")
    x1, y1, x2, y2 = (float(v) for v in b)
    if x2 <= x1 or y2 <= y1:
        raise CaixaInvalida(f"caixa vazia/invertida: {b!r}")
    return x1, y1, x2, y2


def intervalo_pixels(a: Any, b: Any) -> float:
    """Menor distancia (pixels) entre duas caixas xyxy: 0 se sobrepostas ou encostadas. NAO e distancia fisica."""
    ax1, ay1, ax2, ay2 = validar_caixa(a)
    bx1, by1, bx2, by2 = validar_caixa(b)
    dx = max(ax1 - bx2, bx1 - ax2, 0.0)
    dy = max(ay1 - by2, by1 - ay2, 0.0)
    return math.hypot(dx, dy)


def _pixels(a: Any, b: Any, metrica: str) -> float:
    if metrica == "intervalo_bbox":
        return intervalo_pixels(a, b)
    ax1, ay1, ax2, ay2 = validar_caixa(a)
    bx1, by1, bx2, by2 = validar_caixa(b)
    if metrica == "centro":
        return math.hypot((ax1 + ax2) / 2 - (bx1 + bx2) / 2, (ay1 + ay2) / 2 - (by1 + by2) / 2)
    if metrica == "ponto_de_apoio":                  # centro da borda inferior de cada caixa
        return math.hypot((ax1 + ax2) / 2 - (bx1 + bx2) / 2, ay2 - by2)
    raise ValueError(f"metrica desconhecida: {metrica!r}")


def distancia_em(a: Any, b: Any, unidade: str, largura: int, altura: int, metrica: str = "intervalo_bbox") -> float:
    """Distancia entre as caixas `a` (sujeito) e `b` pela `metrica`, na `unidade`: pixels, altura_sujeito (/ altura da caixa de `a`) ou
    diagonal_quadro. NENHUMA metrica e distancia fisica (perspectiva, profundidade e lente nao sao modeladas)."""
    px = _pixels(a, b, metrica)
    if unidade == "pixels":
        return px
    if unidade == "altura_sujeito":
        x1, y1, x2, y2 = validar_caixa(a)
        return px / (y2 - y1)
    if unidade == "diagonal_quadro":
        if not (_numero(largura) and _numero(altura) and largura > 0 and altura > 0):
            raise CaixaInvalida(f"dimensoes do quadro invalidas: {largura!r}x{altura!r}")
        return px / math.hypot(largura, altura)
    raise ValueError(f"unidade desconhecida: {unidade!r}")


def negar(estado: str) -> str:
    return NAO if estado == SIM else SIM if estado == NAO else INDETERMINADO


def relacao_3v(tipo: str, ha_sim_perto: bool, ha_indeterminado_perto: bool) -> str:
    existe = SIM if ha_sim_perto else INDETERMINADO if ha_indeterminado_perto else NAO
    return existe if tipo == "existe_proximo" else negar(existe)


# ====================================================================================================== estado temporal

@dataclass
class _Estado:
    positivo_s: float = 0.0
    run_last: float | None = None        # instante do ultimo `sim` da corrida em curso (None = sem corrida)
    ultimo_sim: float | None = None
    neg_s: float = 0.0
    neg_last: float | None = None
    ativo: bool = False
    ultima_evid: float | None = None     # ultimo `sim` ou `nao`
    ultimo_visto: float | None = None    # ultima cena em que o sujeito apareceu
    # Uma amostra EXPLICITAMENTE indeterminada/ausente interrompeu a corrida: o tempo ja confirmado e preservado (se a pausa for
    # curta), mas a PONTE ate a proxima evidencia valida nao e creditada; a primeira evidencia da retomada so fixa a nova referencia.
    # (Quadros simplesmente nao amostrados nao pausam: continuam sujeitos so a `lacuna_max_s`.)
    run_pausada: bool = False
    neg_pausada: bool = False

    def nome(self) -> str:
        return "ativo" if self.ativo else "candidato" if self.run_last is not None else "inativo"


@dataclass(frozen=True)
class _Evento:
    id: str
    papel: str
    itens: tuple[str, ...]
    duracao_s: float
    lacuna_s: float
    perda_s: float
    negativo_s: float


@dataclass
class ResultadoRegras:
    sujeitos: list[dict[str, Any]] = field(default_factory=list)
    eventos: list[dict[str, Any]] = field(default_factory=list)
    rejeitada: str | None = None

    @property
    def transicoes(self) -> list[dict[str, Any]]:
        return [e for e in self.eventos if e["transicao"]]


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), 6)


class AvaliadorRegras:
    """Estado das regras de UMA camera/geracao/configuracao. `definicao` = receita contrato 2 JA VALIDADA. Nao e thread-safe (o motor serializa)."""

    def __init__(self, definicao: dict[str, Any]) -> None:
        self.papeis: dict[str, str] = {pid: p["classe"] for pid, p in definicao["papeis"].items()}
        self.relacoes: dict[str, dict[str, Any]] = dict(definicao.get("relacoes") or {})
        self.eventos: tuple[_Evento, ...] = tuple(
            _Evento(e["id"], e["sujeito"], tuple(e["quando_todas"]), float(e["duracao_s"]), float(e.get("lacuna_max_s", LACUNA_PADRAO_S)),
                    float(e.get("perda_max_s", PERDA_PADRAO_S)), float(e.get("encerrar_negativo_s", ENCERRAR_NEGATIVO_PADRAO_S)))
            for e in definicao["eventos"])
        self._estados: dict[tuple[str, int], _Estado] = {}
        self.ultimo_t: float | None = None
        self._ligacao: tuple[str, int] | None = None

    # ------------------------------------------------------------------ entrada

    def motivo_recusa(self, cena: Any) -> str | None:
        """Razao pela qual a cena NAO pode avancar o estado (sem alterar nada), ou None."""
        if not isinstance(cena, Cena):
            return "cena_invalida"
        if not _numero(cena.recebido_em):
            return "tempo_invalido"
        if self._ligacao is not None and self._ligacao != (cena.camera_ref, cena.geracao):
            return "camera_ou_geracao_diferente"
        if self.ultimo_t is not None and cena.recebido_em <= self.ultimo_t:
            return "cena_fora_de_ordem"
        try:
            for d in cena.deteccoes:
                validar_caixa(d.bbox_xyxy)
        except (CaixaInvalida, AttributeError, TypeError):
            return "caixa_invalida"
        if not (_numero(cena.largura) and _numero(cena.altura) and cena.largura > 0 and cena.altura > 0):
            return "dimensoes_invalidas"
        return None

    def descartar(self, motivo: str) -> list[dict[str, Any]]:
        """Descarta TODO o estado (reconfigurado | geracao_encerrada | sessao_encerrada). Devolve as linhas dos eventos que estavam ativos
        (transicao 'encerrado' com o motivo): nao e resolucao."""
        saida = []
        for (eid, trilha), st in sorted(self._estados.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            if st.ativo:
                saida.append({"id": eid, "trilha_id": trilha, "estado": "inativo", "positivo_s": _r(st.positivo_s), "idade_ultima_evidencia_s": None,
                              "sustentacao_perdida": True, "transicao": "encerrado", "motivo": motivo})
        self._estados.clear()
        self.ultimo_t = None
        self._ligacao = None
        return saida

    # ------------------------------------------------------------------ avanco

    def avancar(self, cena: Cena, estados_papeis: dict[str, dict[int, str]]) -> ResultadoRegras:
        """`estados_papeis[papel][trilha_id]` in sim/nao/indeterminado para cada deteccao da classe do papel (faltando = indeterminado)."""
        motivo = self.motivo_recusa(cena)
        if motivo:
            return ResultadoRegras(rejeitada=motivo)
        t = float(cena.recebido_em)
        self._ligacao = (cena.camera_ref, cena.geracao)
        self.ultimo_t = t
        por_papel = self._estados_por_papel(cena, estados_papeis)
        sujeitos, rel_por_sujeito = self._relacoes(cena, por_papel)
        linhas = self._eventos(cena, t, por_papel, rel_por_sujeito)
        return ResultadoRegras(sujeitos=sujeitos, eventos=linhas)

    def _estados_por_papel(self, cena: Cena, entrada: dict[str, dict[int, str]]) -> dict[str, dict[int, str]]:
        saida: dict[str, dict[int, str]] = {}
        for papel, classe in self.papeis.items():
            dado = entrada.get(papel) or {}
            saida[papel] = {}
            for d in cena.deteccoes:
                if d.classe == classe:
                    v = dado.get(d.trilha_id, INDETERMINADO)
                    saida[papel][d.trilha_id] = v if v in (SIM, NAO, INDETERMINADO) else INDETERMINADO
        return saida

    def _relacoes(self, cena: Cena, por_papel: dict[str, dict[int, str]]) -> tuple[list[dict[str, Any]], dict[tuple[str, int], str]]:
        det = {(d.classe, d.trilha_id): d for d in cena.deteccoes}
        rels: dict[tuple[str, int], str] = {}
        saida: list[dict[str, Any]] = []
        for d in cena.deteccoes:
            papeis_d = {p: por_papel[p][d.trilha_id] for p, c in self.papeis.items() if c == d.classe}
            if not papeis_d:
                continue
            relacoes: dict[str, Any] = {}
            for rid, rel in self.relacoes.items():
                if self.papeis[rel["sujeito"]] != d.classe:
                    continue
                estado_s = por_papel[rel["sujeito"]][d.trilha_id]
                if estado_s == NAO:
                    relacoes[rid] = {"estado": NAO_SE_APLICA, "proximos": [], "proximos_indeterminados": []}
                    continue
                sim_perto, ind_perto = [], []
                maximo = float(rel["distancia"]["maximo"])
                classe_o = self.papeis[rel["outro"]]
                for trilha_o, est_o in por_papel[rel["outro"]].items():
                    if classe_o == d.classe and trilha_o == d.trilha_id:
                        continue
                    if est_o == NAO:
                        continue
                    outro = det[(classe_o, trilha_o)]
                    if distancia_em(d.bbox_xyxy, outro.bbox_xyxy, rel["distancia"]["unidade"], cena.largura, cena.altura,
                                     rel["distancia"].get("metrica", "intervalo_bbox")) <= maximo + EPS:
                        (sim_perto if est_o == SIM else ind_perto).append(trilha_o)
                estado = INDETERMINADO if estado_s == INDETERMINADO else relacao_3v(rel["tipo"], bool(sim_perto), bool(ind_perto))
                rels[(rid, d.trilha_id)] = estado
                relacoes[rid] = {"estado": estado, "proximos": sorted(sim_perto), "proximos_indeterminados": sorted(ind_perto)}
            saida.append({"trilha_id": d.trilha_id, "classe": d.classe, "bbox_xyxy": list(d.bbox_xyxy), "papeis": papeis_d, "relacoes": relacoes})
        return saida, rels

    def _amostra_evento(self, ev: _Evento, trilha: int, por_papel: dict[str, dict[int, str]], rels: dict[tuple[str, int], str]) -> str:
        papel = por_papel[ev.papel][trilha]
        if papel != SIM:
            return papel                                  # nao -> evidencia negativa; indeterminado -> indeterminado
        valores = [papel if item == ev.papel else rels[(item, trilha)] for item in ev.itens]
        return NAO if NAO in valores else SIM if all(v == SIM for v in valores) else INDETERMINADO

    def _eventos(self, cena: Cena, t: float, por_papel: dict[str, dict[int, str]], rels: dict[tuple[str, int], str]) -> list[dict[str, Any]]:
        linhas: list[dict[str, Any]] = []
        for ev in self.eventos:
            presentes = por_papel[ev.papel]
            chaves = sorted(set(presentes) | {trilha for (eid, trilha) in self._estados if eid == ev.id})
            for trilha in chaves:
                esta = trilha in presentes
                valor = self._amostra_evento(ev, trilha, por_papel, rels) if esta else None
                st = self._estados.setdefault((ev.id, trilha), _Estado())
                transicao, motivo = self._passo(ev, st, t, valor, esta)
                nome = st.nome()
                idade = None if st.ultima_evid is None else t - st.ultima_evid
                perdida = nome != "inativo" and (st.ultimo_sim is None or t - st.ultimo_sim > ev.lacuna_s + EPS)
                linhas.append({"id": ev.id, "trilha_id": trilha, "estado": nome, "positivo_s": _r(st.positivo_s),
                               "idade_ultima_evidencia_s": _r(idade), "sustentacao_perdida": bool(perdida),
                               "transicao": transicao, "motivo": motivo})
                if nome == "inativo":
                    del self._estados[(ev.id, trilha)]
        return linhas

    @staticmethod
    def _encerrar(st: _Estado) -> None:
        st.ativo, st.run_last, st.positivo_s, st.neg_s, st.neg_last = False, None, 0.0, 0.0, None
        st.run_pausada = st.neg_pausada = False

    def _passo(self, ev: _Evento, st: _Estado, t: float, valor: str | None, presente: bool) -> tuple[str | None, str | None]:
        """Uma amostra (sim/nao/indeterminado; None = sujeito ausente) no instante `t`. Devolve (transicao, motivo)."""
        if presente:
            st.ultimo_visto = t
        if valor == SIM:
            if st.run_last is not None and t - st.run_last <= ev.lacuna_s + EPS:
                if not st.run_pausada:
                    st.positivo_s += t - st.run_last
                # pausada por amostra indeterminada/ausente: preserva o confirmado, NAO credita a ponte
            else:
                st.positivo_s = 0.0                       # sem corrida ou lacuna longa: recomeca (o tempo da lacuna nao conta)
            st.run_last = st.ultimo_sim = st.ultima_evid = t
            st.run_pausada = False
            st.neg_s, st.neg_last, st.neg_pausada = 0.0, None, False
            if not st.ativo and st.positivo_s + EPS >= ev.duracao_s:
                st.ativo = True
                return "iniciado", None
            return None, None
        if valor == NAO:
            st.run_last, st.positivo_s, st.run_pausada = None, 0.0, False
            st.ultima_evid = t
            if not st.ativo:
                st.neg_s, st.neg_last, st.neg_pausada = 0.0, None, False
                return None, None
            if st.neg_last is not None and t - st.neg_last <= ev.lacuna_s + EPS:
                if not st.neg_pausada:
                    st.neg_s += t - st.neg_last
            else:
                st.neg_s = 0.0                            # negativo nao sustentado (houve lacuna): recomeca
            st.neg_last, st.neg_pausada = t, False
            if st.neg_s + EPS >= ev.negativo_s:
                self._encerrar(st)
                return "encerrado", M_RESOLVIDO
            return None, None
        # indeterminado ou ausente: nao soma, nao zera, nao e negativo -- e PAUSA a corrida (positiva e negativa) em curso
        if st.run_last is not None:
            st.run_pausada = True
        if st.neg_last is not None:
            st.neg_pausada = True
        idade = None if st.ultima_evid is None else t - st.ultima_evid
        if st.ativo:
            if not presente and st.ultimo_visto is not None and t - st.ultimo_visto > ev.perda_s + EPS:
                self._encerrar(st)
                return "encerrado", M_AUSENTE
            if idade is not None and idade > ev.perda_s + EPS:
                self._encerrar(st)
                return "encerrado", M_PERDA
        elif st.run_last is not None and idade is not None and idade > ev.perda_s + EPS:
            st.run_last, st.positivo_s, st.run_pausada = None, 0.0, False        # candidato esquecido: volta a inativo, sem transicao
        return None, None


def avaliar_sequencia(definicao: dict[str, Any], cenas: Sequence[Cena], estados: Sequence[dict[str, dict[int, str]]]) -> list[ResultadoRegras]:
    """Utilitario de teste/diagnostico: roda um `AvaliadorRegras` novo sobre uma sequencia de (cena, estados)."""
    av = AvaliadorRegras(definicao)
    return [av.avancar(c, e) for c, e in zip(cenas, estados, strict=True)]
