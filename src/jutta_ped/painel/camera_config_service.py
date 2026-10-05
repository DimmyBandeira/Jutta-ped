"""Configuracao de analise por camera (05f): modo simples, ajustes (ROI, crop, execucao) e versionamento imutavel.

Regras do servidor (docs/etapas_motor/05f_configuracao_camera.md, secoes 3 e 4):
* Modo simples: UM especialista (fonte `visivel`, classe mestre `person`, ativo) e, opcionalmente, UMA versao de receita que use
  SOMENTE esse especialista. Nada selecionado = diagnostico do mestre (valido).
* Atomico: qualquer problema levanta `ErroConfig` ANTES de gravar; a configuracao anterior fica intacta.
* Cada mudanca real cria uma `CameraConfigVersao` imutavel (igual = nenhuma versao nova) e atualiza `cameras.receita_versao_id`
  na MESMA transacao. Receita removida desliga a sombra explicitamente.
* Receita legada (mais de um especialista) ja associada continua como esta; so sai dela por transicao explicita.
* Nada aqui executa especialista nem reinicia a analise: a validacao usa o contrato leve (manifesto).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.jutta_ped.motor.ajustes import (
    AjustesCamera,
    ajustes_de_dict,
    ajustes_para_dict,
    efetivos,
    perfis_para_painel,
    problemas_de_ajustes,
)
from src.jutta_ped.motor.receita import hash_config
from src.jutta_ped.painel import receitas_service as rs
from src.jutta_ped.painel.models import Camera, CameraConfigVersao, Receita, ReceitaVersao
from src.jutta_ped.painel.sombra_service import estado_para_camera

CLASSE_MESTRE = "person"
FONTE = "visivel"
VERDADEIRO = {"1", "on", "true", "sim", "yes", "x"}

CAMPOS_CFG = ("especialista", "receita_versao_id", "perfil", "roi_ativa", "roi_x1", "roi_y1", "roi_x2", "roi_y2", "roi_criterio",
              "roi_intersecao_min", "crop_margem", "crop_margem_h", "crop_margem_v", "crop_minimo_px",
              "exec_orcamento_ms", "exec_validade_max_ms", "exec_intervalo_min_s")


class ErroConfig(Exception):
    def __init__(self, problemas: list[str] | str) -> None:
        self.problemas = [problemas] if isinstance(problemas, str) else list(problemas)
        super().__init__("; ".join(self.problemas))

    @property
    def mensagem(self) -> str:
        return self.problemas[0] if len(self.problemas) == 1 else f"{len(self.problemas)} problemas: " + " · ".join(self.problemas)


@dataclass
class ResultadoConfig:
    mudou: bool
    versao: int | None
    texto: str
    sombra_desligada: bool = False
    config_hash: str | None = None


# ---------------------------------------------------------------------------------------------- formulario <-> ajustes

def _texto(form: Any, chave: str) -> str:
    v = form.get(chave) if hasattr(form, "get") else None
    return "" if v is None else str(v).strip()


def _fmt(n: float | int | None) -> str:
    if n is None:
        return ""
    r = round(float(n), 6)
    return str(int(r)) if r.is_integer() else f"{r:g}"


def _num(texto: str, rotulo: str, problemas: list[str], minimo: float, maximo: float, *, minimo_aberto: bool = False,
         inteiro: bool = False, unidade: str = "") -> float | None:
    """Converte um campo digitado (vazio = None). Mensagens legiveis NA UNIDADE DO FORMULARIO (percentual, ms, s, px)."""
    if not texto:
        return None
    try:
        n = float(texto.replace(",", "."))
    except ValueError:
        problemas.append(f"{rotulo}: '{texto}' não é um número.")
        return None
    if not math.isfinite(n):
        problemas.append(f"{rotulo}: '{texto}' não é um número válido.")
        return None
    if inteiro and not n.is_integer():
        problemas.append(f"{rotulo}: deve ser um número inteiro (veio '{texto}').")
        return None
    dentro = (n > minimo if minimo_aberto else n >= minimo) and n <= maximo
    if not dentro:
        faixa = f"maior que {_fmt(minimo)} e até {_fmt(maximo)}" if minimo_aberto else f"entre {_fmt(minimo)} e {_fmt(maximo)}"
        problemas.append(f"{rotulo}: deve ser {faixa}{unidade} (veio '{texto}').")
        return None
    return n


def _fracao(pct: float | None) -> float | None:
    return None if pct is None else round(pct / 100.0, 6)


def ler_form(form: Any) -> dict[str, str]:
    """Campos digitados (todos como texto, sem mudar o que o usuario escreveu, so aparado). `roi_ativa` vira "1"/""."""
    cfg = {c: _texto(form, c) for c in CAMPOS_CFG}
    cfg["roi_ativa"] = "1" if cfg["roi_ativa"].lower() in VERDADEIRO else ""
    cfg["remover_receita"] = "1" if _texto(form, "remover_receita").lower() in VERDADEIRO else ""
    return cfg


def form_para_ajustes(cfg: dict[str, str]) -> tuple[AjustesCamera | None, list[str]]:
    """Formulario (percentuais) -> `AjustesCamera` (fracoes), com TODOS os problemas legiveis. `(None, problemas)` se invalido."""
    p: list[str] = []
    roi: dict[str, Any] | None = None
    if cfg.get("roi_ativa"):
        coords = {}
        for k, rot in (("roi_x1", "ROI x1 (esquerda)"), ("roi_y1", "ROI y1 (topo)"), ("roi_x2", "ROI x2 (direita)"), ("roi_y2", "ROI y2 (base)")):
            if not cfg.get(k):
                p.append(f"{rot}: informe um valor entre 0 e 100 (%).")
            coords[k[4:]] = _num(cfg.get(k, ""), rot, p, 0, 100, unidade="%")
        inter = _num(cfg.get("roi_intersecao_min", ""), "ROI interseção mínima", p, 0, 100, minimo_aberto=True, unidade="%")
        roi = {**{k: _fracao(v) for k, v in coords.items()}, "criterio": cfg.get("roi_criterio") or "ponto_de_apoio"}
        if inter is not None:
            roi["intersecao_min"] = _fracao(inter)
    crop = {
        "margem": _fracao(_num(cfg.get("crop_margem", ""), "Margem do recorte", p, 0, 100, unidade="%")),
        "margem_h": _fracao(_num(cfg.get("crop_margem_h", ""), "Margem horizontal do recorte", p, 0, 100, unidade="%")),
        "margem_v": _fracao(_num(cfg.get("crop_margem_v", ""), "Margem vertical do recorte", p, 0, 100, unidade="%")),
        "minimo_px": _num(cfg.get("crop_minimo_px", ""), "Folga mínima do recorte (px)", p, 0, 200, inteiro=True),
    }
    if crop["minimo_px"] is not None:
        crop["minimo_px"] = int(crop["minimo_px"])
    execucao = {
        "orcamento_ms": _num(cfg.get("exec_orcamento_ms", ""), "Orçamento (ms)", p, 0, 60_000, minimo_aberto=True),
        "validade_max_ms": _num(cfg.get("exec_validade_max_ms", ""), "Validade máxima (ms)", p, 0, 3_600_000, minimo_aberto=True),
        "intervalo_min_s": _num(cfg.get("exec_intervalo_min_s", ""), "Intervalo mínimo entre quadros (s)", p, 0, 60),
    }
    dados = {"perfil": cfg.get("perfil") or None, "roi": roi, "crop": crop, "execucao": execucao}
    if not p:
        p += problemas_de_ajustes(dados)
    if p:
        return None, p
    return ajustes_de_dict(dados), []


def ajustes_para_form(a: AjustesCamera | None, especialista: str = "", receita_versao_id: int | None = None) -> dict[str, str]:
    """Inverso de `form_para_ajustes`: valores do formulario, JA em percentual onde aplicavel."""
    a = a or AjustesCamera()
    r = a.roi
    pct = lambda x: "" if x is None else _fmt(x * 100.0)  # noqa: E731
    return {
        "especialista": especialista or "", "receita_versao_id": "" if receita_versao_id is None else str(receita_versao_id),
        "perfil": a.perfil or "", "roi_ativa": "1" if r is not None else "",
        "roi_x1": pct(r.x1) if r else "", "roi_y1": pct(r.y1) if r else "", "roi_x2": pct(r.x2) if r else "", "roi_y2": pct(r.y2) if r else "",
        "roi_criterio": r.criterio if r else "ponto_de_apoio", "roi_intersecao_min": pct(r.intersecao_min) if r else "50",
        "crop_margem": pct(a.crop.margem), "crop_margem_h": pct(a.crop.margem_h), "crop_margem_v": pct(a.crop.margem_v),
        "crop_minimo_px": _fmt(a.crop.minimo_px), "exec_orcamento_ms": _fmt(a.execucao.orcamento_ms),
        "exec_validade_max_ms": _fmt(a.execucao.validade_max_ms), "exec_intervalo_min_s": _fmt(a.execucao.intervalo_min_s),
    }


# ---------------------------------------------------------------------------------------------- receitas e especialistas

def _problemas_contrato(pid: str, contrato: dict[str, Any] | None) -> list[str]:
    """O especialista serve ao modo simples? (ativo, alvo `recorte`, fonte visivel, classe mestre `person`)."""
    if contrato is None:
        return [f"O especialista '{pid}' não está ativo (ou o pacote ativo é ilegível)."]
    p: list[str] = []
    entrada = contrato["entrada"]
    if entrada["fonte"] != FONTE:
        p.append(f"O especialista '{pid}' usa a fonte '{entrada['fonte']}'; o modo simples só aceita '{FONTE}'.")
    alvo = entrada["alvo"]
    if alvo.get("tipo") != "recorte":
        p.append(f"O especialista '{pid}' tem alvo '{alvo.get('tipo')}'; o modo simples só aceita 'recorte'.")
    if alvo.get("classe_mestre") != CLASSE_MESTRE:
        p.append(f"O especialista '{pid}' avalia a classe '{alvo.get('classe_mestre')}'; o modo simples só aceita '{CLASSE_MESTRE}'.")
    return p


def _pids(definicao: Any) -> list[str]:
    vinculos = definicao.get("vinculos") if isinstance(definicao, dict) else None
    out: list[str] = []
    for v in vinculos if isinstance(vinculos, list) else []:
        pid = v.get("especialista") if isinstance(v, dict) else None
        if isinstance(pid, str) and pid not in out:
            out.append(pid)
    return out


def _carregar_versao(versao: ReceitaVersao) -> dict[str, Any] | None:
    try:
        d = json.loads(versao.definicao_json)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _todas_versoes(db: Session) -> list[tuple[ReceitaVersao, str]]:
    return [(v, nome) for v, nome in db.execute(select(ReceitaVersao, Receita.nome).join(Receita, Receita.id == ReceitaVersao.receita_id)
                                                .order_by(Receita.nome, ReceitaVersao.versao.desc())).all()]


def _problemas_receita(versao: ReceitaVersao, nome: str, esp: str, contratos: dict[str, dict[str, Any]]) -> list[str]:
    """Por que esta versao de receita NAO serve a uma camera cujo especialista e `esp` (vazio = sem especialista escolhido)."""
    rotulo = f"{nome} v{versao.versao}"
    definicao = _carregar_versao(versao)
    if definicao is None:
        return [f"A receita {rotulo} está ilegível."]
    pids = _pids(definicao)
    if len(pids) != 1:
        return [f"A receita {rotulo} usa {len(pids)} especialistas ({', '.join(pids) or 'nenhum'}): fora do modo simples (um especialista por câmera)."]
    p: list[str] = []
    if not esp:
        p.append(f"Escolha o especialista '{pids[0]}' antes de associar a receita {rotulo} (ela usa só ele).")
    elif pids[0] != esp:
        p.append(f"A receita {rotulo} usa o especialista '{pids[0]}', diferente do selecionado '{esp}'.")
    p += rs.revalidar(definicao, contratos)
    return p


def receita_e_legada(db: Session, camera: Camera) -> tuple[bool, str | None]:
    """A receita associada a camera usa mais de um especialista (fora do modo simples)? `(legada, rotulo)`."""
    if camera.receita_versao_id is None:
        return False, None
    versao = db.get(ReceitaVersao, camera.receita_versao_id)
    if versao is None:
        return False, None
    definicao = _carregar_versao(versao)
    rotulo = rs.rotulo_versao(db, versao.id)
    return (definicao is not None and len(_pids(definicao)) > 1), rotulo


def texto_legado(db: Session, camera: Camera) -> str | None:
    legada, rotulo = receita_e_legada(db, camera)
    if not legada:
        return None
    versao = db.get(ReceitaVersao, camera.receita_versao_id)
    pids = _pids(_carregar_versao(versao) or {})
    return (f"Esta câmera usa a receita {rotulo}, que combina {len(pids)} especialistas ({', '.join(pids)}) e está fora do modo simples "
            f"(um especialista por câmera). Ela continua funcionando como está. Para salvar ajustes, escolha uma receita simples ou "
            f"remova a receita (transição explícita).")


# ---------------------------------------------------------------------------------------------- leitura da configuracao

def vigente(db: Session, camera_id: int) -> CameraConfigVersao | None:
    return db.scalar(select(CameraConfigVersao).where(CameraConfigVersao.camera_id == camera_id).order_by(CameraConfigVersao.versao.desc()).limit(1))


def ajustes_da_versao(cfg: CameraConfigVersao | None) -> AjustesCamera:
    if cfg is None:
        return AjustesCamera()
    try:
        return ajustes_de_dict(json.loads(cfg.ajustes_json))
    except (ValueError, TypeError):
        return AjustesCamera()


def historico(db: Session, camera_id: int, limite: int = 20) -> list[dict[str, Any]]:
    linhas = db.scalars(select(CameraConfigVersao).where(CameraConfigVersao.camera_id == camera_id)
                        .order_by(CameraConfigVersao.versao.desc()).limit(limite)).all()
    rotulos = rs.rotulos_versoes(db, {c.receita_versao_id for c in linhas if c.receita_versao_id})
    return [{"versao": c.versao, "config_hash": c.config_hash, "especialista": c.especialista_id, "receita_label": rotulos.get(c.receita_versao_id),
             "criado_por": c.criado_por, "criado_em": c.criado_em} for c in linhas]


def definicao_da_camera(db: Session, camera: Camera) -> dict[str, Any] | None:
    """Definicao (dict) da versao de receita associada AGORA a camera, ou None (sem receita ou ilegivel)."""
    if camera.receita_versao_id is None:
        return None
    versao = db.get(ReceitaVersao, camera.receita_versao_id)
    return _carregar_versao(versao) if versao is not None else None


def vinculo_de(definicao: dict[str, Any] | None, esp: str | None) -> dict[str, Any] | None:
    for v in (definicao or {}).get("vinculos", []) if isinstance((definicao or {}).get("vinculos"), list) else []:
        if isinstance(v, dict) and v.get("especialista") == esp:
            return v
    return None


def _hash_config(esp: str, contratos: dict[str, dict[str, Any]], versao_receita: ReceitaVersao | None, ajustes: AjustesCamera) -> str:
    c = contratos.get(esp) if esp else None
    ident = [c["id"], c["versao"], c["identidade"]] if c else None
    return hash_config("camera-config", ident, versao_receita.config_hash if versao_receita else None, ajustes_para_dict(ajustes))


def _config_atual_efetiva(db: Session, camera: Camera) -> tuple[CameraConfigVersao | None, str, int | None, AjustesCamera]:
    """(linha vigente, especialista, receita_versao_id, ajustes) da camera AGORA. Sem linha: deriva da associacao viva (pre-0004)."""
    cfg = vigente(db, camera.id)
    if cfg is not None:
        return cfg, cfg.especialista_id or "", camera.receita_versao_id, ajustes_da_versao(cfg)
    esp = ""
    if camera.receita_versao_id is not None:
        versao = db.get(ReceitaVersao, camera.receita_versao_id)
        pids = _pids(_carregar_versao(versao) or {}) if versao else []
        if len(pids) == 1:
            esp = pids[0]
    return None, esp, camera.receita_versao_id, AjustesCamera()


# ---------------------------------------------------------------------------------------------- escrita

def _validar_estado(db: Session, esp: str, rvid: int | None, contratos: dict[str, dict[str, Any]]) -> tuple[list[str], ReceitaVersao | None]:
    problemas: list[str] = []
    versao: ReceitaVersao | None = None
    if esp:
        problemas += _problemas_contrato(esp, contratos.get(esp))
    if rvid is not None:
        versao = db.get(ReceitaVersao, rvid)
        if versao is None:
            problemas.append("Versão de receita inexistente.")
        else:
            nome = db.scalar(select(Receita.nome).where(Receita.id == versao.receita_id)) or "?"
            problemas += _problemas_receita(versao, nome, esp, contratos)
    return list(dict.fromkeys(problemas)), versao


def _gravar(db: Session, camera: Camera, esp: str, rvid: int | None, versao_receita: ReceitaVersao | None, ajustes: AjustesCamera,
            contratos: dict[str, dict[str, Any]], login: str | None) -> ResultadoConfig:
    """Cria a nova versao SE algo mudou, mantem `cameras.receita_versao_id` em sincronia e desliga a sombra se ficou sem receita."""
    atual, esp_atual, rvid_atual, ajustes_atual = _config_atual_efetiva(db, camera)
    novo_hash = _hash_config(esp, contratos, versao_receita, ajustes)
    padrao = not esp and rvid is None and ajustes == AjustesCamera()
    mudou = (atual is None and not (padrao and camera.receita_versao_id is None)) or (
        atual is not None and (atual.especialista_id or "", atual.receita_versao_id, atual.config_hash) != (esp, rvid, novo_hash))
    sombra_desligada = rvid is None and bool(camera.sombra_ativa)     # sem receita nao ha sombra: nunca fica "ligada" sem alvo
    camera.receita_versao_id = rvid
    if sombra_desligada:
        camera.sombra_ativa = False
    if not mudou:
        return ResultadoConfig(False, atual.versao if atual else None, "Nenhuma alteração: a configuração vigente já é esta.", sombra_desligada,
                               atual.config_hash if atual else None)
    ultima = db.scalar(select(CameraConfigVersao.versao).where(CameraConfigVersao.camera_id == camera.id).order_by(CameraConfigVersao.versao.desc()).limit(1)) or 0
    try:
        db.add(CameraConfigVersao(camera_id=camera.id, versao=ultima + 1, especialista_id=esp or None, receita_versao_id=rvid,
                                  ajustes_json=json.dumps(ajustes_para_dict(ajustes), sort_keys=True, ensure_ascii=False), config_hash=novo_hash, criado_por=login))
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ErroConfig("Outra alteração foi gravada ao mesmo tempo; recarregue a página e tente de novo.") from None
    texto = f"Configuração da câmera '{camera.nome}' salva (versão {ultima + 1}). A análise operacional não foi reiniciada."
    if sombra_desligada:
        texto += " A câmera ficou sem receita: a sombra foi DESLIGADA (ligue-a de novo depois de associar uma receita)."
    return ResultadoConfig(True, ultima + 1, texto, sombra_desligada, novo_hash)


def salvar_config(db: Session, instalador: Any, camera_id: int, dados_form: Any, login: str | None) -> ResultadoConfig:
    """Salva a configuracao do formulario de `analise` (atomico). `ErroConfig` = nada foi gravado."""
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise ErroConfig("Câmera não encontrada.")
    cfg = dados_form if isinstance(dados_form, dict) and set(CAMPOS_CFG) <= set(dados_form) else ler_form(dados_form)
    problemas: list[str] = []
    ajustes, p = form_para_ajustes(cfg)
    problemas += p
    esp, bruto = cfg["especialista"], cfg["receita_versao_id"]
    rvid: int | None = None
    if bruto:
        if not bruto.isdigit():
            problemas.append("Versão de receita inválida.")
        else:
            rvid = int(bruto)
    contratos = rs.contratos_ativos(instalador)
    erros_estado, versao = _validar_estado(db, esp, rvid, contratos)
    problemas += erros_estado
    legada, rotulo_legado = receita_e_legada(db, camera)
    if legada and rvid is None and not cfg.get("remover_receita"):
        problemas.append(f"A câmera usa a receita {rotulo_legado}, fora do modo simples: para salvar, escolha uma receita simples "
                         f"ou marque a remoção da receita (transição explícita). Nada foi alterado.")
    problemas = list(dict.fromkeys(problemas))
    if problemas or ajustes is None:
        raise ErroConfig(problemas or ["Ajustes inválidos."])
    return _gravar(db, camera, esp, rvid, versao, ajustes, contratos, login)


def associar(db: Session, instalador: Any, camera_id: int, versao_id: int | None, login: str | None = None) -> str:
    """Associacao pela rota `POST /cameras/{id}/receita` (e importacao): mesma regra do modo simples. Uma receita simples define o
    especialista da camera; remover (None) mantem especialista e ajustes e desliga a sombra. Devolve o texto para o usuario."""
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise ErroConfig("Câmera não encontrada.")
    _atual, esp_atual, _rvid, ajustes = _config_atual_efetiva(db, camera)
    contratos = rs.contratos_ativos(instalador)
    if versao_id is None:
        esp = esp_atual
        if esp and esp not in contratos:
            esp = ""                                              # especialista desativado: nao carrega um estado invalido adiante
        tinha = camera.receita_versao_id is not None
        _gravar(db, camera, esp, None, None, ajustes, contratos, login)
        return f"Receita removida da câmera '{camera.nome}' (sombra desligada)." if tinha else f"A câmera '{camera.nome}' já estava sem receita."
    versao = db.get(ReceitaVersao, versao_id)
    if versao is None:
        raise ErroConfig("Versão de receita inexistente.")
    definicao = _carregar_versao(versao)
    pids = _pids(definicao or {})
    nome = db.scalar(select(Receita.nome).where(Receita.id == versao.receita_id)) or "?"
    if definicao is None or len(pids) != 1:
        raise ErroConfig(_problemas_receita(versao, nome, pids[0] if len(pids) == 1 else "", contratos))
    problemas, versao = _validar_estado(db, pids[0], versao_id, contratos)
    if problemas:
        raise ErroConfig(problemas)
    _gravar(db, camera, pids[0], versao_id, versao, ajustes, contratos, login)
    return f"Receita {rs.rotulo_versao(db, versao_id)} associada à câmera '{camera.nome}' (especialista: {pids[0]})."


# ---------------------------------------------------------------------------------------------- contexto de cameras/analise.html

def especialistas_compat(instalador: Any, contratos: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    contratos = rs.contratos_ativos(instalador) if contratos is None else contratos
    compat = {pid: c for pid, c in contratos.items() if not _problemas_contrato(pid, c)}
    return rs.especialistas_ativos(instalador, compat)


def receitas_do_modo(db: Session, contratos: dict[str, dict[str, Any]], esp: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """(receitas_compat, receitas_fora_modo). Compat: versoes VALIDAS que usam so o especialista `esp` (vazio = todas as simples)."""
    compat: list[dict[str, Any]] = []
    fora: list[dict[str, str]] = []
    for versao, nome in _todas_versoes(db):
        rotulo = f"{nome} v{versao.versao}"
        definicao = _carregar_versao(versao)
        pids = _pids(definicao) if definicao is not None else []
        if definicao is None:
            fora.append({"label": rotulo, "motivo": "definição ilegível"})
            continue
        if len(pids) != 1:
            fora.append({"label": rotulo, "motivo": f"usa {len(pids)} especialistas ({', '.join(pids) or 'nenhum'}): um especialista por câmera"})
            continue
        if esp and pids[0] != esp:
            continue
        if _problemas_contrato(pids[0], contratos.get(pids[0])) or rs.revalidar(definicao, contratos):
            continue
        compat.append({"versao_id": versao.id, "label": rotulo, "especialista": pids[0], "valida": True, "motivos": []})
    return compat, fora


def contexto_pagina(db: Session, instalador: Any, sombra: Any, camera_id: int, cfg_digitado: dict[str, str] | None = None) -> dict[str, Any] | None:
    """Contexto de `cameras/analise.html` (chaves exatas do contrato). None se a camera nao existe.
    `cfg_digitado`: valores do formulario recusado (re-renderizacao 422); `flash`/`erro`/`problemas` ficam para a rota."""
    camera = db.get(Camera, camera_id)
    if camera is None:
        return None
    contratos = rs.contratos_ativos(instalador)
    atual, esp, rvid, ajustes = _config_atual_efetiva(db, camera)
    legado = texto_legado(db, camera)
    cfg = dict(cfg_digitado) if cfg_digitado is not None else ajustes_para_form(ajustes, esp, None if legado else rvid)
    esp_selecionado = cfg.get("especialista", "")
    receitas_compat, receitas_fora = receitas_do_modo(db, contratos, esp_selecionado)
    efetivo = None
    if atual is not None:
        versao = db.get(ReceitaVersao, rvid) if rvid is not None else None
        definicao = _carregar_versao(versao) if versao else None
        ef = efetivos(ajustes, vinculo_de(definicao, atual.especialista_id))
        efetivo = {"versao": atual.versao, "config_hash": atual.config_hash, "ajustes": ajustes_para_dict(ef), "especialista": atual.especialista_id,
                   "receita_label": rs.rotulo_versao(db, rvid) if rvid is not None else None}
    estado, _erro = estado_para_camera(sombra, camera)
    return {
        "cam": {"id": camera.id, "nome": camera.nome}, "especialistas_compat": especialistas_compat(instalador, contratos),
        "receitas_compat": receitas_compat, "receitas_fora_modo": receitas_fora, "cfg": cfg, "efetivo": efetivo,
        "perfis": perfis_para_painel(), "legado": legado, "diagnostico_ativo": bool(camera.diagnostico_ativo), "sombra_estado": estado,
        "flash": None, "erro": None, "problemas": [],
    }
