"""Receitas versionadas do painel: criar, nova versao imutavel, validar contra os especialistas ATIVOS e associar a camera.

Regras:
* A validacao usa `contrato_leve` (le so o manifesto): nunca instancia Ultralytics nem carrega modelo na requisicao.
* Atomico: qualquer problema levanta `ErroReceita` ANTES de gravar; a associacao anterior da camera fica intacta.
* A validade e recalculada a cada leitura: um especialista pode ter mudado de versao depois da gravacao.
* Nao ha logica de execucao aqui: a execucao e do motor (`src/jutta_ped/motor`).
"""
from __future__ import annotations

import json
import math
import re
from itertools import zip_longest
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.jutta_ped.especialistas.executor import contrato_leve
from src.jutta_ped.especialistas.instalador import ErroInstalacao
from src.jutta_ped.motor.receita import COMBINACOES, hash_config, validar_receita
from src.jutta_ped.painel.models import Camera, CameraConfigVersao, Receita, ReceitaVersao

NOME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,47}$")
FONTES = ("visivel",)
LINHAS_CONDICAO = 4
DESCRICAO_MAX = 300


class ErroReceita(Exception):
    def __init__(self, problemas: list[str] | str) -> None:
        self.problemas = [problemas] if isinstance(problemas, str) else list(problemas)
        super().__init__("; ".join(self.problemas))

    @property
    def mensagem(self) -> str:
        return self.problemas[0] if len(self.problemas) == 1 else f"{len(self.problemas)} problemas: " + " · ".join(self.problemas)


# ---------------------------------------------------------------------------------------------- especialistas ativos

def contratos_ativos(instalador: Any) -> dict[str, dict[str, Any]]:
    try:
        itens = instalador.listar()
    except ErroInstalacao:
        return {}
    saida: dict[str, dict[str, Any]] = {}
    for pid, esp in itens.items():
        if esp.get("ativa"):
            c = contrato_leve(instalador, pid)
            if c is not None:
                saida[pid] = c
    return saida


def especialistas_ativos(instalador: Any, contratos: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    contratos = contratos_ativos(instalador) if contratos is None else contratos
    try:
        registro = instalador.listar()
    except ErroInstalacao:
        registro = {}
    saida = []
    for pid, c in sorted(contratos.items()):
        ver = c["versao"]
        nome = (registro.get(pid, {}).get("versoes", {}).get(ver, {}) or {}).get("nome") or pid
        saida.append({"id": pid, "nome": nome, "versao": ver,
                      "atributos": [{"nome": n, "tipo": a["tipo"], "categorias": list(a["categorias"])} for n, a in c["atributos"].items()]})
    return saida


def revalidar(definicao: Any, contratos: dict[str, dict[str, Any]]) -> list[str]:
    """Problemas da receita frente aos especialistas ativos AGORA (lista vazia = valida)."""
    vinculos = definicao.get("vinculos") if isinstance(definicao, dict) else None
    pids = [v["especialista"] for v in vinculos if isinstance(v, dict) and isinstance(v.get("especialista"), str)] if isinstance(vinculos, list) else []
    return validar_receita(definicao, {pid: contratos.get(pid) for pid in pids}, FONTES)


# ---------------------------------------------------------------------------------------------- formulario

def _numero(texto: str) -> int | float | None:
    try:
        n = float(texto.strip().replace(",", "."))
    except ValueError:
        return None
    if not math.isfinite(n):
        return None
    return int(n) if n.is_integer() and abs(n) < 1e12 else n


def form_vazio(pids: list[str]) -> dict[str, Any]:
    return {"rid": None, "nome": "", "descricao": "", "combinacao": "todas",
            "vinculos": {pid: {"usar": False, "orcamento_ms": "", "margem_recorte": ""} for pid in pids},
            "condicoes": [{"especialista": "", "atributo": "", "igual": "", "validade_ms": ""} for _ in range(LINHAS_CONDICAO)]}


def ler_form(form: Any, pids: list[str], rid: int | None = None) -> dict[str, Any]:
    """Campos do formulario (multi-dict com `get`/`getlist`) -> estrutura de `form` do contrato, com os valores DIGITADOS."""
    usar = {str(x) for x in form.getlist("vinc_usar")}
    todos = list(dict.fromkeys([*pids, *sorted(usar)]))
    vinculos = {pid: {"usar": pid in usar, "orcamento_ms": str(form.get(f"vinc_orcamento__{pid}") or "").strip(),
                      "margem_recorte": str(form.get(f"vinc_margem__{pid}") or "").strip()} for pid in todos}
    colunas = [[str(x) for x in form.getlist(c)] for c in ("cond_esp", "cond_attr", "cond_igual", "cond_validade")]
    condicoes = [{"especialista": e.strip(), "atributo": a.strip(), "igual": i.strip(), "validade_ms": v.strip()}
                 for e, a, i, v in zip_longest(*colunas, fillvalue="")]
    while len(condicoes) < LINHAS_CONDICAO:
        condicoes.append({"especialista": "", "atributo": "", "igual": "", "validade_ms": ""})
    return {"rid": rid, "nome": str(form.get("nome") or "").strip(), "descricao": str(form.get("descricao") or "").strip(),
            "combinacao": str(form.get("combinacao") or "todas").strip(), "vinculos": vinculos, "condicoes": condicoes}


def _converter_igual(contratos: dict[str, dict[str, Any]], esp: str, attr: str, texto: str, rotulo: str, problemas: list[str]) -> Any:
    a = (contratos.get(esp) or {}).get("atributos", {}).get(attr)
    tipo = a["tipo"] if a else None
    if tipo == "numero":
        n = _numero(texto)
        if n is None:
            problemas.append(f"{rotulo}: '{texto}' não é um número válido para o atributo '{attr}'.")
        return n
    if tipo == "tri":
        t = texto.strip().lower().replace("ã", "a")
        return t if t in ("sim", "nao") else texto
    return texto


def montar_definicao(form: dict[str, Any], nome: str, versao: str, contratos: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
    """Dicionario da receita (formato do motor) a partir do formulario + problemas de CONVERSAO e de VALIDACAO."""
    problemas: list[str] = []
    vinculos: list[dict[str, Any]] = []
    for pid, d in form["vinculos"].items():
        if not d["usar"]:
            continue
        v: dict[str, Any] = {"especialista": pid}
        for campo, rotulo in (("orcamento_ms", "orçamento (ms)"), ("margem_recorte", "margem de recorte")):
            texto = str(d.get(campo) or "").strip()
            if texto:
                n = _numero(texto)
                if n is None:
                    problemas.append(f"{pid}: {rotulo} '{texto}' não é um número válido.")
                else:
                    v[campo] = n
        vinculos.append(v)
    condicoes: list[dict[str, Any]] = []
    for i, c in enumerate(form["condicoes"], start=1):
        if not any(c[k].strip() for k in ("especialista", "atributo", "igual", "validade_ms")):
            continue                                                   # linha vazia: ignorada
        rotulo = f"condição {i}"
        if not c["especialista"].strip() or not c["atributo"].strip() or not c["igual"].strip():
            problemas.append(f"{rotulo}: informe especialista, atributo e valor esperado.")
            continue
        cond: dict[str, Any] = {"especialista": c["especialista"].strip(), "atributo": c["atributo"].strip()}
        cond["igual"] = _converter_igual(contratos, cond["especialista"], cond["atributo"], c["igual"].strip(), rotulo, problemas)
        if c["validade_ms"].strip():
            n = _numero(c["validade_ms"])
            if n is None:
                problemas.append(f"{rotulo}: validade '{c['validade_ms']}' não é um número válido.")
            else:
                cond["validade_ms"] = n
        condicoes.append(cond)
    definicao = {"id": nome, "versao": versao, "vinculos": vinculos, "regra": {"combinacao": form["combinacao"], "condicoes": condicoes}}
    if not vinculos:
        problemas.append("Selecione ao menos um especialista.")
    if not condicoes and not any(p.startswith("condição") for p in problemas):
        problemas.append("Informe ao menos uma condição.")
    if form["combinacao"] not in COMBINACOES:
        problemas.append(f"Combinação deve ser uma de {list(COMBINACOES)}.")
    if not problemas:
        problemas += revalidar(definicao, contratos)
    return definicao, problemas


def definicao_para_form(rid: int, descricao: str, definicao: dict[str, Any], pids_ativos: list[str]) -> dict[str, Any]:
    form = form_vazio(list(dict.fromkeys([*pids_ativos, *[v["especialista"] for v in definicao.get("vinculos", [])]])))
    form.update(rid=rid, nome=definicao.get("id", ""), descricao=descricao, combinacao=definicao.get("regra", {}).get("combinacao", "todas"))
    for v in definicao.get("vinculos", []):
        form["vinculos"][v["especialista"]] = {"usar": True, "orcamento_ms": "" if "orcamento_ms" not in v else str(v["orcamento_ms"]),
                                               "margem_recorte": "" if "margem_recorte" not in v else str(v["margem_recorte"])}
    linhas = [{"especialista": c.get("especialista", ""), "atributo": c.get("atributo", ""), "igual": str(c.get("igual", "")),
               "validade_ms": "" if "validade_ms" not in c else str(c["validade_ms"])} for c in definicao.get("regra", {}).get("condicoes", [])]
    while len(linhas) < LINHAS_CONDICAO:
        linhas.append({"especialista": "", "atributo": "", "igual": "", "validade_ms": ""})
    form["condicoes"] = linhas
    return form


# O formulario simples edita SO o contrato 1 e SO estes campos. Qualquer outra coisa numa receita (contrato 2, parametros e
# versoes_aceitas de um vinculo, mais condicoes que as linhas do formulario...) nao e preservada por ele: recusa-se EDITAR, nunca se apaga.
_TOPO_FORM = {"id", "versao", "vinculos", "regra"}
_VINCULO_FORM = {"especialista", "orcamento_ms", "margem_recorte"}
_REGRA_FORM = {"combinacao", "condicoes"}
_COND_FORM = {"especialista", "atributo", "igual", "validade_ms"}
TAMANHO_MAX_IMPORTACAO = 64 * 1024


def contrato_da(definicao: Any) -> int:
    return definicao.get("contrato", 1) if isinstance(definicao, dict) and isinstance(definicao.get("contrato", 1), int) else 0


def campos_nao_editaveis(definicao: Any) -> list[str]:
    """Razoes pelas quais o FORMULARIO nao consegue editar (e portanto reescrever) esta definicao sem perder dados. Vazio = editavel."""
    if not isinstance(definicao, dict):
        return ["definição ilegível"]
    if contrato_da(definicao) != 1:
        return [f"usa o contrato {definicao.get('contrato')!r} (papéis, relações e eventos), que o formulário simples não edita"]
    motivos: list[str] = []
    extras = sorted(set(definicao) - _TOPO_FORM)
    if extras:
        motivos.append("campos fora do formulário: " + ", ".join(extras))
    for v in definicao.get("vinculos", []):
        if isinstance(v, dict) and set(v) - _VINCULO_FORM:
            motivos.append(f"vínculo {v.get('especialista')}: " + ", ".join(sorted(set(v) - _VINCULO_FORM)) + " não é editável")
    regra = definicao.get("regra", {})
    if isinstance(regra, dict):
        if set(regra) - _REGRA_FORM:
            motivos.append("regra: " + ", ".join(sorted(set(regra) - _REGRA_FORM)) + " não é editável")
        conds = regra.get("condicoes", [])
        if len(conds) > LINHAS_CONDICAO:
            motivos.append(f"{len(conds)} condições (o formulário tem {LINHAS_CONDICAO} linhas)")
        for c in conds:
            if isinstance(c, dict) and set(c) - _COND_FORM:
                motivos.append("condição: " + ", ".join(sorted(set(c) - _COND_FORM)) + " não é editável")
    return motivos


def resumo_definicao(definicao: dict[str, Any]) -> str:
    if contrato_da(definicao) == 2:
        return (f"contrato 2 · {len(definicao.get('vinculos', []))} especialista(s) · {len(definicao.get('papeis', {}))} papel(éis) · "
                f"{len(definicao.get('relacoes', {}))} relação(ões) · {len(definicao.get('eventos', []))} evento(s)")
    n_v, n_c = len(definicao.get("vinculos", [])), len(definicao.get("regra", {}).get("condicoes", []))
    return f"{n_v} especialista(s) · {n_c} condição(ões) · combinação '{definicao.get('regra', {}).get('combinacao')}'"


# ---------------------------------------------------------------------------------------------- escrita

def _config_hash(definicao: dict[str, Any]) -> str:
    return hash_config(definicao, (definicao["id"], definicao["versao"]))      # o mesmo calculo do evento de sombra do motor


def _validar_cabecalho(nome: str, descricao: str) -> list[str]:
    p: list[str] = []
    if not NOME_RE.match(nome):
        p.append("Nome inválido: use 3 a 48 caracteres minúsculos (letras, números, ponto, hífen ou sublinhado), começando por letra ou número.")
    if len(descricao) > DESCRICAO_MAX:
        p.append(f"Descrição longa demais (máximo {DESCRICAO_MAX} caracteres).")
    return p


def criar_receita(db: Session, instalador: Any, form: dict[str, Any], login: str | None) -> ReceitaVersao:
    nome, descricao = form["nome"], form["descricao"]
    problemas = _validar_cabecalho(nome, descricao)
    if not problemas and db.scalar(select(Receita.id).where(Receita.nome == nome)) is not None:
        problemas.append(f"Já existe uma receita chamada '{nome}'.")
    definicao, achados = montar_definicao(form, nome, "1", contratos_ativos(instalador))
    problemas += achados
    if problemas:
        raise ErroReceita(problemas)
    try:
        receita = Receita(nome=nome, descricao=descricao)
        db.add(receita)
        db.flush()
        versao = ReceitaVersao(receita_id=receita.id, versao=1, definicao_json=json.dumps(definicao, ensure_ascii=False),
                               config_hash=_config_hash(definicao), criado_por=login)
        db.add(versao)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ErroReceita(f"Já existe uma receita chamada '{nome}'.") from None
    return versao


def nova_versao(db: Session, instalador: Any, rid: int, form: dict[str, Any], login: str | None) -> ReceitaVersao:
    receita = db.get(Receita, rid)
    if receita is None:
        raise ErroReceita("Receita não encontrada.")
    anterior = db.scalar(select(ReceitaVersao).where(ReceitaVersao.receita_id == rid).order_by(ReceitaVersao.versao.desc()).limit(1))
    if anterior is not None:
        motivos = campos_nao_editaveis(json.loads(anterior.definicao_json))
        if motivos:                                      # o formulario reescreveria a definicao e APAGARIA estes campos em silencio
            raise ErroReceita(["O formulário não preserva esta receita (" + "; ".join(motivos) + "). Importe uma nova versão pelo JSON."])
    descricao = form["descricao"]
    problemas = _validar_cabecalho(receita.nome, descricao)
    ultima = db.scalar(select(ReceitaVersao.versao).where(ReceitaVersao.receita_id == rid).order_by(ReceitaVersao.versao.desc()).limit(1)) or 0
    definicao, achados = montar_definicao(form, receita.nome, str(ultima + 1), contratos_ativos(instalador))
    problemas += achados
    if problemas:
        raise ErroReceita(problemas)
    try:
        versao = ReceitaVersao(receita_id=rid, versao=ultima + 1, definicao_json=json.dumps(definicao, ensure_ascii=False),
                               config_hash=_config_hash(definicao), criado_por=login)
        db.add(versao)
        if descricao != receita.descricao:
            receita.descricao = descricao
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ErroReceita("Outra versão foi criada ao mesmo tempo; tente de novo.") from None
    return versao


def importar_definicao(db: Session, instalador: Any, nome: str, descricao: str, texto: str, login: str | None) -> ReceitaVersao:
    """Cria a receita (ou uma NOVA VERSAO imutavel, se o nome ja existe) a partir de uma definicao JSON (qualquer contrato suportado).
    Atomico e sem reescrita silenciosa: `id`/`versao` no JSON, se vierem, tem que coincidir com o que sera gravado."""
    problemas = _validar_cabecalho(nome, descricao)
    if len(texto.encode("utf-8")) > TAMANHO_MAX_IMPORTACAO:
        problemas.append(f"JSON grande demais (máximo {TAMANHO_MAX_IMPORTACAO // 1024} KiB).")
    definicao: Any = None
    if not problemas:
        try:
            definicao = json.loads(texto)
        except ValueError as exc:
            problemas.append(f"JSON inválido: {exc}")
        else:
            if not isinstance(definicao, dict):
                problemas.append("O JSON deve ser um objeto (a definição da receita).")
    if problemas:
        raise ErroReceita(problemas)
    receita = db.scalar(select(Receita).where(Receita.nome == nome))
    ultima = (db.scalar(select(ReceitaVersao.versao).where(ReceitaVersao.receita_id == receita.id).order_by(ReceitaVersao.versao.desc()).limit(1)) or 0) if receita else 0
    versao_txt = str(ultima + 1)
    if "id" in definicao and definicao["id"] != nome:
        problemas.append(f"O 'id' do JSON ({definicao['id']!r}) difere do nome informado ({nome!r}).")
    if "versao" in definicao and str(definicao["versao"]) != versao_txt:
        problemas.append(f"A 'versao' do JSON ({definicao['versao']!r}) difere da que seria gravada ({versao_txt!r}); remova o campo.")
    if problemas:
        raise ErroReceita(problemas)
    definicao = {**definicao, "id": nome, "versao": versao_txt}
    problemas = revalidar(definicao, contratos_ativos(instalador))
    if problemas:
        raise ErroReceita(problemas)
    try:
        if receita is None:
            receita = Receita(nome=nome, descricao=descricao)
            db.add(receita)
            db.flush()
        elif descricao and descricao != receita.descricao:
            receita.descricao = descricao
        versao = ReceitaVersao(receita_id=receita.id, versao=ultima + 1, definicao_json=json.dumps(definicao, ensure_ascii=False),
                               config_hash=_config_hash(definicao), criado_por=login)
        db.add(versao)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ErroReceita("Conflito ao gravar (outra alteração simultânea); tente de novo.") from None
    return versao


def associar_camera(db: Session, instalador: Any, camera_id: int, versao_id: int | None, login: str | None = None) -> str:
    """Associa (ou remove, com None) a receita da camera pelo MODO SIMPLES do servidor (05f): receita com mais de um especialista e
    RECUSADA; uma receita simples define/atualiza o especialista da camera e cria nova versao de configuracao. Remover desliga a
    sombra. Qualquer recusa levanta `ErroReceita` e nao altera nada (a associacao anterior fica intacta)."""
    from src.jutta_ped.painel import camera_config_service as ccs      # import tardio: o servico de configuracao importa este modulo

    try:
        return ccs.associar(db, instalador, camera_id, versao_id, login)
    except ccs.ErroConfig as exc:
        raise ErroReceita(exc.problemas) from None


def definir_sombra(db: Session, instalador: Any, camera_id: int, ligar: bool) -> str:
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise ErroReceita("Câmera não encontrada.")
    if not ligar:
        camera.sombra_ativa = False
        return f"Sombra desligada na câmera '{camera.nome}'."
    if camera.receita_versao_id is None:
        raise ErroReceita("Associe uma receita à câmera antes de ligar a sombra.")
    versao = db.get(ReceitaVersao, camera.receita_versao_id)
    problemas = revalidar(json.loads(versao.definicao_json), contratos_ativos(instalador)) if versao else ["Versão de receita inexistente."]
    if problemas:
        raise ErroReceita(problemas)
    camera.sombra_ativa = True
    return f"Sombra ligada na câmera '{camera.nome}' (diagnóstico: não gera alertas)."


# ---------------------------------------------------------------------------------------------- leitura

def rotulos_versoes(db: Session, ids: set[int] | None = None) -> dict[int, str]:
    consulta = select(ReceitaVersao.id, Receita.nome, ReceitaVersao.versao).join(Receita, Receita.id == ReceitaVersao.receita_id)
    if ids is not None:
        if not ids:
            return {}
        consulta = consulta.where(ReceitaVersao.id.in_(ids))
    return {i: f"{nome} v{v}" for i, nome, v in db.execute(consulta)}


def resumos_config(db: Session) -> dict[int, tuple[int, str]]:
    """camera_id -> (versao, config_hash) da configuracao de analise VIGENTE (maior versao) de cada camera."""
    saida: dict[int, tuple[int, str]] = {}
    for cid, v, h in db.execute(select(CameraConfigVersao.camera_id, CameraConfigVersao.versao, CameraConfigVersao.config_hash)
                                .order_by(CameraConfigVersao.camera_id, CameraConfigVersao.versao)):
        saida[cid] = (v, h)
    return saida


def hash_curto(config_hash: str | None) -> str | None:
    return None if not config_hash else config_hash.removeprefix("sha256:")[:10]


def rotulo_versao(db: Session, versao_id: int | None) -> str | None:
    return None if versao_id is None else rotulos_versoes(db, {versao_id}).get(versao_id)


def opcoes_versao(db: Session) -> list[dict[str, Any]]:
    linhas = db.execute(select(ReceitaVersao.id, Receita.nome, ReceitaVersao.versao).join(Receita, Receita.id == ReceitaVersao.receita_id)
                        .order_by(Receita.nome, ReceitaVersao.versao.desc())).all()
    return [{"id": i, "label": f"{nome} v{v}"} for i, nome, v in linhas]


def visao_receitas(db: Session, instalador: Any, contratos: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    contratos = contratos_ativos(instalador) if contratos is None else contratos
    receitas = list(db.scalars(select(Receita).order_by(Receita.nome)).all())
    versoes: dict[int, list[ReceitaVersao]] = {}
    for v in db.scalars(select(ReceitaVersao).order_by(ReceitaVersao.receita_id, ReceitaVersao.versao.desc())).all():
        versoes.setdefault(v.receita_id, []).append(v)
    camera_por_versao: dict[int, list[str]] = {}
    for nome, vid in db.execute(select(Camera.nome, Camera.receita_versao_id).where(Camera.receita_versao_id.is_not(None)).order_by(Camera.nome)):
        camera_por_versao.setdefault(vid, []).append(nome)
    saida = []
    for r in receitas:
        vs = versoes.get(r.id, [])
        if not vs:
            continue
        ultima = vs[0]
        definicao = json.loads(ultima.definicao_json)
        motivos = revalidar(definicao, contratos)
        cameras = sorted({n for v in vs for n in camera_por_versao.get(v.id, [])})
        nao_editavel = campos_nao_editaveis(definicao)
        saida.append({
            "contrato": contrato_da(definicao), "editavel": not nao_editavel, "motivos_nao_editavel": nao_editavel,
            "id": r.id, "nome": r.nome, "descricao": r.descricao, "versao": ultima.versao, "versao_id": ultima.id,
            "especialistas": [{"id": v["especialista"], "versao_ativa": (contratos.get(v["especialista"]) or {}).get("versao")} for v in definicao.get("vinculos", [])],
            "cameras": cameras, "valida": not motivos, "motivos": motivos,
            "versoes": [{"id": v.id, "versao": v.versao, "config_hash": v.config_hash, "criado_em": v.criado_em, "criado_por": v.criado_por,
                         "resumo": resumo_definicao(json.loads(v.definicao_json))} for v in vs],
        })
    return saida


def visao_cameras_receita(db: Session, instalador: Any, estado_sombra, contratos: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Uma linha por camera: receita associada (revalidada AGORA) e estado da sombra. `estado_sombra(camera) -> (estado, erro)`."""
    contratos = contratos_ativos(instalador) if contratos is None else contratos
    cameras = list(db.scalars(select(Camera).order_by(Camera.nome)).unique().all())
    ids = {c.receita_versao_id for c in cameras if c.receita_versao_id}
    rotulos = rotulos_versoes(db, ids)
    configs = resumos_config(db)
    definicoes = {v.id: v.definicao_json for v in db.scalars(select(ReceitaVersao).where(ReceitaVersao.id.in_(ids))).all()} if ids else {}
    saida = []
    for c in cameras:
        valida, motivos = True, []
        if c.receita_versao_id is not None:
            try:
                motivos = revalidar(json.loads(definicoes[c.receita_versao_id]), contratos)
            except (KeyError, ValueError):
                motivos = ["Versão da receita ilegível."]
            valida = not motivos
        estado, erro = estado_sombra(c)
        saida.append({"id": c.id, "nome": c.nome, "receita_versao_id": c.receita_versao_id, "receita_label": rotulos.get(c.receita_versao_id) if c.receita_versao_id else None,
                      "valida": valida, "motivos": motivos, "sombra_ativa": bool(c.sombra_ativa), "sombra_estado": estado, "sombra_erro": erro,
                      "config_versao": configs.get(c.id, (None, None))[0], "config_hash": hash_curto(configs.get(c.id, (None, None))[1])})
    return saida


def form_da_ultima_versao(db: Session, instalador: Any, rid: int) -> dict[str, Any] | None:
    r = db.get(Receita, rid)
    if r is None:
        return None
    v = db.scalar(select(ReceitaVersao).where(ReceitaVersao.receita_id == rid).order_by(ReceitaVersao.versao.desc()).limit(1))
    if v is None:
        return None
    definicao = json.loads(v.definicao_json)
    motivos = campos_nao_editaveis(definicao)
    if motivos:
        raise ErroReceita(["O formulário não preserva esta receita (" + "; ".join(motivos) + "). Importe uma nova versão pelo JSON."])
    return definicao_para_form(r.id, r.descricao, definicao, sorted(contratos_ativos(instalador)))
