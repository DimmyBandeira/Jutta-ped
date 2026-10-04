"""Qualidade dos alertas por EPISODIOS REVISADOS: perdidos, falsos e repetidos. Avaliador VERSAO 2.

Divisao de trabalho (nada aqui inventa rotulo nem confirma revisao):
* `analisar_video` roda o caminho atual sobre o video INTEIRO e registra a COBERTURA (terminou por EOF? todos os quadros?).
* `preparar_revisao` empacota o que uma pessoa precisa para rotular.
* `avaliar` calcula metricas SOMENTE para anotacao OFICIAL: estruturalmente valida, de origem humana, confirmada, revisao completa,
  procedencia do video confirmada (com tipo e responsavel), criterio de inicio em cada episodio, mesmo SHA-256 e analise com cobertura
  integral CONSISTENTE. Qualquer outra coisa -> `metricas=None` + motivos (pendencias visiveis).
* `leitura_provisoria` aplica o mesmo calculo a uma PROPOSTA (ex.: de IA) so para mostrar o que sairia se ela fosse confirmada; o
  resultado e rotulado `oficial=False` dentro do proprio `leitura` e usa nomes que NAO sao os das metricas oficiais.

LIMITES DE CONFIANCA (por desenho): `revisor`, `data`, `origem_revisao` e `procedencia` sao DECLARACOES num arquivo editavel. O avaliador nao
verifica identidade, nao prova que a revisao ocorreu e nao assina arquivos (um JSON de analise ou de anotacao editado a mao nao e detectado,
alem de inconsistencias internas). Ele recusa o que e inconsistente ou incompleto; a confianca vem do processo humano, nao do codigo.

Contrato da anotacao (esquema 2), um arquivo por video:
  {"esquema": 2, "video": "x.dav", "sha256": "<64 hex>",
   "origem_revisao": "humana" | "ia", "status": "confirmada" | "proposta_nao_confirmada",
   "revisado": true|false,                      # coerente com status: confirmada <=> revisado
   "revisor": "Nome", "data": "AAAA-MM-DD", "revisao_completa_do_video": true|false,
   "revisao_cega": true|false,                  # opcional; false/ausente = o revisor viu os alertas do sistema (gera aviso)
   "procedencia": {"confirmada": true, "fonte": "de onde veio o arquivo", "tipo": "real"|"sintetico", "confirmado_por": "Nome"},
   "episodios": [ {"id": "e1", "sujeito_local": "opcional", "inicio_s": 3.0, "fim_s": 9.5,
                   "criterio_inicio": "o que marca o inicio (ex.: adulto sai do campo)",      # obrigatorio para metricas oficiais
                   "estado_esperado": "CHILD_ALONE|CHILD_SEPARATED|qualquer", "observacao": "..."} ],   # lista OBRIGATORIA
   "negativo_confirmado": true,                 # obrigatorio quando "episodios" e [] (negativo DELIBERADO)
   "associacoes": [ {"t_s": 6.333, "trilha": 1, "episodio": "e1" | null} ]}   # opcional: decisao humana; null = nao pertence a nenhum
  Campos livres e ignorados pelo calculo: `metodo`, `observacao`, `instrucoes`, `derivada_de_proposta`.

Semantica (documentada tambem em docs/referencia/README.md, secao 6):
* CANDIDATOS de um alerta: episodios com estado compativel. Se o alerta cai DENTRO do intervalo [inicio, fim] de algum episodio, so esses
  sao candidatos (a tolerancia nao compete com um intervalo que ja o contem). So um alerta fora de todos os intervalos usa a janela de
  tolerancia [inicio - tol, fim + tol].
* ASSOCIACAO EXCLUSIVA: cada alerta conta para no maximo um episodio. Deteccao = casamento maximo entre alertas e episodios (caminho
  aumentante, alertas em ordem de tempo); preferencia entre candidatos = (distancia ao intervalo, inicio_s, id). Os demais alertas vao ao
  candidato preferido (repetidos) ou, sem candidato, sao falsos. Resultado independe da ordem de entrada dos alertas.
* AMBIGUIDADE: alerta com 2+ candidatos nao tem pessoa registrada; e listado em `alertas_ambiguos`. `detectados` e um LIMITE SUPERIOR sob
  ambiguidade; `detectados_sem_ambiguidade` conta so episodios com alerta de candidato unico ou associacao manual.
* ASSOCIACOES MANUAIS sao decisoes humanas aplicadas antes do calculo; as que ficam fora da janela do episodio sao listadas.
* TRILHAS: `trilhas_adicionais` = trilhas distintas - 1 por episodio (pode ser fragmentacao OU outra pessoa); `transicoes_de_trilha` =
  mudancas de trilha na ordem do tempo (1,2,1 = 2). Nenhuma das duas prova fragmentacao.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

VERSAO_AVALIADOR = 2
ESTADOS_ESPERADOS = ("qualquer", "CHILD_ALONE", "CHILD_SEPARATED")
TIPOS_PROCEDENCIA = ("real", "sintetico")
# Heuristica contra autor IA em anotacao "humana": nao prova nada, so recusa o obvio. Nomes comuns nao devem disparar (ex.: "Maria").
PADRAO_IA = re.compile(r"\b(ia|ai|codex|claude|gpt|chatgpt|llm|bot|gemini|copilot|anthropic|openai|sonnet|opus|haiku|fable|assistente|assistant)\b", re.IGNORECASE)
TOL_ASSOCIACAO_MANUAL_S = 0.05
NEGATIVO_CURTO_S = 600.0


# --------------------------------------------------------------------------------------------- analise e cobertura

def aguardar_e_encerrar(sessao: Any, limite_s: float, *, relogio: Callable[[], float] = time.perf_counter,
                        dormir: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Espera o fim da sessao (EOF) ou o limite; registra o MOTIVO antes de pedir a parada (um `stop()` apagaria a diferenca
    entre terminar sozinho e ser interrompido por timeout)."""
    t0 = relogio()
    while sessao.thread is not None and sessao.thread.is_alive() and relogio() - t0 < limite_s:
        dormir(0.2)
    timeout = sessao.thread is not None and sessao.thread.is_alive()
    st = sessao.state
    instantaneo = {"timeout": bool(timeout), "status": st.status, "stop_reason": st.stop_reason, "last_error": st.last_error,
                   "quadros_analisados": st.frames_processed, "duracao_s": round(relogio() - t0, 2)}
    sessao.stop()
    return instantaneo


def calcular_cobertura(instantaneo: dict[str, Any], quadros_do_arquivo: int) -> dict[str, Any]:
    """`integral` so quando terminou SOZINHA por fim de arquivo, sem erro, e analisou exatamente os quadros que o arquivo declara."""
    analisados = int(instantaneo.get("quadros_analisados") or 0)
    eof = (not instantaneo["timeout"] and instantaneo.get("status") == "finished" and instantaneo.get("stop_reason") == "eof"
           and not instantaneo.get("last_error"))
    motivos = []
    if instantaneo["timeout"]:
        motivos.append("timeout: a analise nao terminou no limite de tempo")
    elif not eof:
        motivos.append(f"nao terminou por fim de arquivo (status={instantaneo.get('status')}, motivo={instantaneo.get('stop_reason')}, erro={instantaneo.get('last_error')})")
    if quadros_do_arquivo <= 0:
        motivos.append("o arquivo nao declara numero de quadros: cobertura nao comprovavel")
    elif analisados != quadros_do_arquivo:
        motivos.append(f"quadros analisados ({analisados}) diferem dos declarados pelo arquivo ({quadros_do_arquivo})")
    return {"integral": not motivos, "terminou_por_eof": bool(eof), "timeout": bool(instantaneo["timeout"]),
            "quadros_analisados": analisados, "quadros_do_arquivo": quadros_do_arquivo,
            "razao": round(analisados / quadros_do_arquivo, 4) if quadros_do_arquivo > 0 else None,
            "motivo_parada": instantaneo.get("stop_reason"), "status_final": instantaneo.get("status"),
            "erro": instantaneo.get("last_error"), "motivos_nao_integral": motivos}


def cobertura_consistente(cob: Any) -> tuple[bool, str]:
    """A cobertura e recalculada a partir dos campos-base: um `integral: true` que os campos nao sustentam e recusado
    (nao e assinatura: um arquivo reeditado com campos coerentes passaria)."""
    if not isinstance(cob, dict):
        return False, "cobertura ausente ou malformada"
    try:
        eof, timeout = bool(cob["terminou_por_eof"]), bool(cob["timeout"])
        analisados, total, erro = int(cob["quadros_analisados"]), int(cob["quadros_do_arquivo"]), cob.get("erro")
        declarada = bool(cob["integral"])
    except (KeyError, TypeError, ValueError):
        return False, "cobertura sem os campos-base (terminou_por_eof, timeout, quadros_analisados, quadros_do_arquivo, integral)"
    recalculada = eof and not timeout and not erro and total > 0 and analisados == total
    if declarada != recalculada:
        return False, f"cobertura inconsistente: integral={declarada} mas os campos-base indicam integral={recalculada}"
    return True, ""


def analisar_video(video: Path, *, dispositivo: str = "auto", limite_s: float = 900.0) -> dict[str, Any]:
    """Caminho atual, arquivo inteiro, sem ritmo (todos os quadros). Devolve linha do tempo + cobertura."""
    import cv2

    from src.jutta_ped.service import runtime
    from tools.bateria.ambiente import sha256_arquivo

    cap = cv2.VideoCapture(str(video))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    eventos: list[dict[str, Any]] = []
    manager = runtime.PediatriaSessionManager()
    manager.add_event_listener(lambda p: eventos.append(p) if p.get("event_type") in ("popup_alert", "popup_suppressed") else None)
    evid = Path(tempfile.mkdtemp(prefix="bateria_episodios_"))
    cfg = runtime.SessionStartConfig(source=str(video), camera_id="episodio", report_dir=evid, requested_device=dispositivo,
                                     video_end_policy="stop", reconnect_enabled=False)
    estado = manager.start_session(cfg)
    sessao = manager._get(estado["session_id"])
    instantaneo = aguardar_e_encerrar(sessao, limite_s)
    linha = []
    for p in eventos:
        crianca = p.get("selected_child") or {}
        quadro = int(p.get("frame_index") or 0)
        linha.append({"tipo": p["event_type"], "quadro": quadro, "t_s": round(quadro / fps, 3), "estado": p.get("stable_state"),
                      "trilha": crianca.get("track_id"), "confianca": p.get("child_confidence"), "motivo": p.get("reason"),
                      "evidencias": {k: v for k, v in (p.get("evidence_paths") or {}).items() if isinstance(v, str)}})
    return {"versao_avaliador": VERSAO_AVALIADOR, "video": video.name, "sha256": sha256_arquivo(video), "fps": round(fps, 3),
            "quadros_do_arquivo": total, "quadros_analisados": instantaneo["quadros_analisados"], "tempo_de_analise_s": instantaneo["duracao_s"],
            "cobertura": calcular_cobertura(instantaneo, total),
            "alertas": [e for e in linha if e["tipo"] == "popup_alert"], "suprimidos": [e for e in linha if e["tipo"] == "popup_suppressed"],
            "trilhas_distintas_nos_alertas": sorted({e["trilha"] for e in linha if e["trilha"] is not None}),
            "evidencias_tmp": str(evid)}


def preparar_revisao(analise: dict[str, Any], destino: Path, video: Path, *, passo_s: float = 2.0, largura: int = 320) -> dict[str, Any]:
    """Pacote para a pessoa rotular: quadro de cada alerta, folha de contato do video e rascunho de anotacao (esquema 2)."""
    import cv2
    import numpy as np

    destino.mkdir(parents=True, exist_ok=True)
    copiados = []
    for i, a in enumerate(analise["alertas"] + analise["suprimidos"], 1):
        for chave, caminho in a["evidencias"].items():
            if str(caminho).lower().endswith((".jpg", ".jpeg", ".png")) and Path(caminho).exists():
                nome = f"{a['tipo']}_{i:02d}_t{a['t_s']:.1f}s_{chave}{Path(caminho).suffix}"
                shutil.copy2(caminho, destino / nome)
                copiados.append(nome)
    cap = cv2.VideoCapture(str(video))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    miniaturas = []
    t = 0.0
    while t * fps < n:
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, q = cap.read()
        if ok:
            h = round(q.shape[0] * largura / q.shape[1])
            m = cv2.resize(q, (largura, h))
            cv2.putText(m, f"t={t:.0f}s", (6, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            miniaturas.append(m)
        t += passo_s
    cap.release()
    colunas = 4
    if miniaturas:
        h = miniaturas[0].shape[0]
        linhas = (len(miniaturas) + colunas - 1) // colunas
        folha = np.zeros((linhas * h, colunas * largura, 3), np.uint8)
        for k, m in enumerate(miniaturas):
            folha[(k // colunas) * h:(k // colunas + 1) * h, (k % colunas) * largura:(k % colunas + 1) * largura] = m
        cv2.imwrite(str(destino / "linha_do_tempo.jpg"), folha)
    rascunho = {"esquema": 2, "video": analise["video"], "sha256": analise["sha256"], "origem_revisao": "humana",
                "status": "proposta_nao_confirmada", "revisado": False, "revisor": "", "data": "", "revisao_completa_do_video": False,
                "revisao_cega": False, "procedencia": {"confirmada": False, "fonte": "", "tipo": "", "confirmado_por": ""},
                "episodios": [], "negativo_confirmado": False,
                "instrucoes": ("RASCUNHO GERADO PELO SISTEMA (nao e revisao). Veja linha_do_tempo.jpg e o video INTEIRO. Para cada trecho em que um alerta "
                               "DEVERIA ocorrer, acrescente um episodio {id, inicio_s, fim_s, criterio_inicio, estado_esperado, observacao}. Se o video e um "
                               "NEGATIVO, deixe episodios=[] e ponha negativo_confirmado=true. Informe a procedencia (fonte, tipo real|sintetico, confirmado_por). "
                               "Para confirmar use `bateria_referencia.py confirmar`; editar a mao tambem vale, mas e uma DECLARACAO sua. Os alertas do sistema abaixo "
                               "sao apoio, NAO rotulos; se voce os consultou, a revisao nao e cega (revisao_cega=false)."),
                "alertas_do_sistema_para_apoio": [{"t_s": a["t_s"], "estado": a["estado"], "trilha": a["trilha"]} for a in analise["alertas"]]}
    (destino / "episodios.rascunho.json").write_text(json.dumps(rascunho, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"pasta": str(destino), "arquivos_de_evidencia": copiados, "linha_do_tempo": bool(miniaturas)}


# --------------------------------------------------------------------------------------------- contrato da anotacao

def _numero(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _texto(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def validar_anotacao(rev: Any, *, duracao_s: float | None = None, exigir_negativo_deliberado: bool = True) -> list[str]:
    """Erros ESTRUTURAIS da anotacao (lista vazia = estruturalmente valida). Nao decide se e oficial."""
    if not isinstance(rev, dict):
        return ["a anotacao deve ser um objeto JSON"]
    erros: list[str] = []
    if rev.get("esquema", 2) != 2:
        erros.append(f"esquema {rev.get('esquema')!r} nao suportado (esperado 2)")
    if not _texto(rev.get("video")):
        erros.append("'video' obrigatorio (texto)")
    if not isinstance(rev.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", rev["sha256"]):
        erros.append("'sha256' obrigatorio (64 hex minusculos)")
    if rev.get("origem_revisao") not in ("humana", "ia"):
        erros.append("'origem_revisao' obrigatorio: 'humana' ou 'ia'")
    if rev.get("status") not in ("confirmada", "proposta_nao_confirmada"):
        erros.append("'status' obrigatorio: 'confirmada' ou 'proposta_nao_confirmada'")
    for campo in ("revisado", "revisao_completa_do_video"):
        if not isinstance(rev.get(campo), bool):
            erros.append(f"'{campo}' obrigatorio (booleano)")
    if "revisao_cega" in rev and not isinstance(rev["revisao_cega"], bool):
        erros.append("'revisao_cega' deve ser booleano")
    if rev.get("origem_revisao") == "ia" and rev.get("status") == "confirmada":
        erros.append("anotacao de origem 'ia' nao pode ter status 'confirmada' (confirmacao exige revisao humana)")
    if isinstance(rev.get("revisado"), bool) and rev.get("status") in ("confirmada", "proposta_nao_confirmada") \
            and rev["revisado"] != (rev["status"] == "confirmada"):
        erros.append("'revisado' e 'status' incoerentes (confirmada <=> revisado=true)")
    if not _texto(rev.get("revisor")):
        erros.append("'revisor' obrigatorio (nome de quem revisou/propos)")
    try:
        date.fromisoformat(str(rev.get("data", "")))
    except ValueError:
        erros.append("'data' obrigatoria no formato AAAA-MM-DD")
    pro = rev.get("procedencia")
    if pro is not None:
        if not isinstance(pro, dict) or not isinstance(pro.get("confirmada"), bool):
            erros.append("'procedencia' deve ser {confirmada: bool, fonte, tipo, confirmado_por}")
        elif pro["confirmada"]:
            if not _texto(pro.get("fonte")):
                erros.append("procedencia.confirmada=true exige 'fonte' (de onde veio o arquivo)")
            if pro.get("tipo") not in TIPOS_PROCEDENCIA:
                erros.append(f"procedencia.confirmada=true exige 'tipo' em {list(TIPOS_PROCEDENCIA)}")
            if not _texto(pro.get("confirmado_por")):
                erros.append("procedencia.confirmada=true exige 'confirmado_por' (quem confirmou a origem)")
    eps = rev.get("episodios")
    if not isinstance(eps, list):
        erros.append("'episodios' e OBRIGATORIA (lista); use [] com negativo_confirmado=true para um negativo deliberado")
        eps = []
    ids: set[str] = set()
    for i, ep in enumerate(eps):
        rot = f"episodios[{i}]"
        if not isinstance(ep, dict):
            erros.append(f"{rot}: deve ser objeto")
            continue
        if not _texto(ep.get("id")):
            erros.append(f"{rot}: 'id' obrigatorio")
        elif ep["id"] in ids:
            erros.append(f"{rot}: id duplicado '{ep['id']}'")
        else:
            ids.add(ep["id"])
        ini, fim = ep.get("inicio_s"), ep.get("fim_s")
        if not _numero(ini) or not _numero(fim):
            erros.append(f"{rot}: 'inicio_s' e 'fim_s' devem ser numeros finitos")
        else:
            if ini < 0:
                erros.append(f"{rot}: inicio_s negativo")
            if fim <= ini:
                erros.append(f"{rot}: exige inicio_s < fim_s")
            if duracao_s is not None and fim > duracao_s + 0.5:
                erros.append(f"{rot}: fim_s ({fim}) alem da duracao do video ({duracao_s:.2f} s)")
        if ep.get("estado_esperado") not in ESTADOS_ESPERADOS:
            erros.append(f"{rot}: 'estado_esperado' deve ser um de {list(ESTADOS_ESPERADOS)}")
        for campo in ("criterio_inicio", "sujeito_local"):
            if campo in ep and not isinstance(ep[campo], str):
                erros.append(f"{rot}: '{campo}' deve ser texto")
    neg = rev.get("negativo_confirmado", False)
    if not isinstance(neg, bool):
        erros.append("'negativo_confirmado' deve ser booleano")
    elif isinstance(rev.get("episodios"), list):
        if not eps and not neg and exigir_negativo_deliberado:
            erros.append("'episodios' vazio exige negativo_confirmado=true (um negativo precisa ser declarado de forma deliberada)")
        if eps and neg:
            erros.append("negativo_confirmado=true e incompativel com episodios nao vazios")
    assoc = rev.get("associacoes", [])
    if not isinstance(assoc, list):
        erros.append("'associacoes' deve ser lista")
    else:
        for i, a in enumerate(assoc):
            ok = isinstance(a, dict) and _numero(a.get("t_s")) and "episodio" in a and (a["episodio"] is None or a["episodio"] in ids)
            ok = ok and (a.get("trilha") is None or (isinstance(a.get("trilha"), int) and not isinstance(a.get("trilha"), bool)))
            if not ok:
                erros.append(f"associacoes[{i}]: exige t_s numerico, 'episodio' (id existente ou null) e 'trilha' inteira ou ausente/null")
    return erros


def _sobrepostos(eps: list[dict[str, Any]]) -> list[tuple[str, str]]:
    pares = []
    for i, a in enumerate(eps):
        for b in eps[i + 1:]:
            if a["inicio_s"] < b["fim_s"] and b["inicio_s"] < a["fim_s"]:
                pares.append((a["id"], b["id"]))
    return pares


def classificar_anotacao(rev: Any, *, duracao_s: float | None = None, exigir_negativo_deliberado: bool = True) -> dict[str, Any]:
    """Estrutura + elegibilidade para metricas OFICIAIS. `motivos` = pendencias que impedem; `avisos` = ressalvas que nao impedem."""
    if rev is None:
        return {"valida": False, "oficial": False, "estado": "sem_anotacao", "erros": [], "motivos": ["nenhuma anotacao para este video"], "avisos": []}
    erros = validar_anotacao(rev, duracao_s=duracao_s, exigir_negativo_deliberado=exigir_negativo_deliberado)
    if erros:
        return {"valida": False, "oficial": False, "estado": "invalida", "erros": erros, "motivos": ["anotacao estruturalmente invalida"], "avisos": []}
    motivos, avisos = [], []
    if rev["origem_revisao"] != "humana":
        motivos.append("origem da anotacao e IA (proposta): so revisao humana gera metricas oficiais")
    if rev["status"] != "confirmada" or not rev["revisado"]:
        motivos.append("anotacao nao confirmada (status=proposta_nao_confirmada, revisado=false)")
    if not rev["revisao_completa_do_video"]:
        motivos.append("sem revisao_completa_do_video: alertas fora dos episodios nao podem ser chamados de falsos")
    if not (rev.get("procedencia") or {}).get("confirmada"):
        motivos.append("procedencia do arquivo de video nao confirmada (fonte, tipo real|sintetico e confirmado_por)")
    if rev["origem_revisao"] == "humana" and PADRAO_IA.search(rev["revisor"]):
        motivos.append(f"revisor '{rev['revisor']}' parece identificar uma IA em anotacao declarada humana")
    for ep in rev["episodios"]:
        if not _texto(ep.get("criterio_inicio")):
            motivos.append(f"episodio '{ep['id']}' sem criterio_inicio (define o significado do atraso e do inicio do episodio)")
    pares = _sobrepostos(rev["episodios"])
    por_id = {e["id"]: e for e in rev["episodios"]}
    for a, b in pares:
        sa, sb = por_id[a].get("sujeito_local"), por_id[b].get("sujeito_local")
        if not (_texto(sa) and _texto(sb) and sa != sb):
            avisos.append(f"episodios '{a}' e '{b}' se sobrepõem sem sujeitos_locais distintos declarados: alertas na sobreposicao ficam ambiguos")
    if not rev.get("revisao_cega", False):
        avisos.append("revisao nao cega: o revisor pode ter consultado os alertas do sistema (vies de confirmacao)")
    estado = "proposta_ia" if rev["origem_revisao"] == "ia" else ("confirmada_humana" if not motivos else "humana_incompleta")
    return {"valida": True, "oficial": not motivos, "estado": estado, "erros": [], "motivos": motivos, "avisos": avisos}


def confirmar_anotacao(proposta: dict[str, Any], *, revisor: str, data: str, revisao_completa: bool, episodios_conferidos: bool,
                       procedencia_confirmada: bool, procedencia_fonte: str, procedencia_tipo: str,
                       criterios_inicio: dict[str, str] | None = None, revisao_cega: bool = False) -> dict[str, Any]:
    """Registra a DECLARACAO de uma pessoa de que revisou. Nao revisa nada. Cada declaracao e explicita (nenhum padrao confirma por voce):
    revisao completa, episodios conferidos, procedencia (confirmada, fonte, tipo), criterio de inicio de cada episodio e se a revisao foi cega.
    A proposta original e preservada em `derivada_de_proposta` (inclui o `metodo` dela, que NAO vale para a anotacao confirmada).
    Levanta ValueError com a lista de problemas."""
    problemas = []
    if not _texto(revisor) or PADRAO_IA.search(revisor):
        problemas.append("revisor deve ser o nome de uma pessoa (nao vazio e sem identificar uma IA)")
    if not revisao_completa:
        problemas.append("a confirmacao exige declarar que o video foi revisado por inteiro")
    if not episodios_conferidos:
        problemas.append("a confirmacao exige declarar que cada episodio e intervalo da proposta foi conferido (ou corrigido) pela pessoa")
    if not procedencia_confirmada:
        problemas.append("a procedencia do arquivo precisa ser confirmada explicitamente (--procedencia-confirmada)")
    if not _texto(procedencia_fonte):
        problemas.append("informe a fonte/procedencia do arquivo de video")
    novo = json.loads(json.dumps(proposta))
    metodo = novo.pop("metodo", None)
    for ep in novo.get("episodios", []):
        if isinstance(ep, dict) and criterios_inicio and ep.get("id") in criterios_inicio:
            ep["criterio_inicio"] = criterios_inicio[ep["id"]]
    novo.update({"esquema": 2, "origem_revisao": "humana", "status": "confirmada", "revisado": True, "revisor": str(revisor).strip(), "data": data,
                 "revisao_completa_do_video": True, "revisao_cega": bool(revisao_cega),
                 "procedencia": {"confirmada": bool(procedencia_confirmada), "fonte": str(procedencia_fonte).strip(), "tipo": procedencia_tipo,
                                 "confirmado_por": str(revisor).strip()}})
    if not novo.get("episodios"):
        novo["negativo_confirmado"] = True
    novo["derivada_de_proposta"] = {"origem_revisao": proposta.get("origem_revisao"), "revisor": proposta.get("revisor"), "data": proposta.get("data"),
                                    "status": proposta.get("status"), "metodo_original": metodo}
    problemas += validar_anotacao(novo)
    for ep in novo.get("episodios", []):
        if isinstance(ep, dict) and not _texto(ep.get("criterio_inicio")):
            problemas.append(f"episodio '{ep.get('id')}' sem criterio_inicio (use --criterio-inicio ID=texto)")
    if problemas:
        raise ValueError("; ".join(problemas))
    return novo


def carregar_anotacoes(pasta: Path) -> dict[str, tuple[Path, Any]]:
    """sha256 -> (arquivo, conteudo). Dois arquivos para o mesmo video levantam ValueError (evita escolher um em silencio)."""
    achados: dict[str, tuple[Path, Any]] = {}
    for arq in sorted(pasta.glob("*.json")) if pasta.exists() else []:
        try:
            dados = json.loads(arq.read_text(encoding="utf-8"))
        except ValueError:
            dados = None
        chave = dados.get("sha256") if isinstance(dados, dict) and isinstance(dados.get("sha256"), str) else f"invalido:{arq.name}"
        if chave in achados:
            raise ValueError(f"mais de uma anotacao para o mesmo video em {pasta}: {achados[chave][0].name} e {arq.name}")
        achados[chave] = (arq, dados)
    return achados


# --------------------------------------------------------------------------------------------- associacao e metricas

def _distancia(t: float, ep: dict[str, Any]) -> float:
    return 0.0 if ep["inicio_s"] <= t <= ep["fim_s"] else min(abs(t - ep["inicio_s"]), abs(t - ep["fim_s"]))


def _candidatos(alerta: dict[str, Any], eps: list[dict[str, Any]], tol: float) -> list[str]:
    """Dentro de algum intervalo -> so esses; a tolerancia so vale para alerta fora de todos os intervalos."""
    compat = [e for e in eps if e["estado_esperado"] in ("qualquer", alerta["estado"])]
    dentro = [e for e in compat if e["inicio_s"] <= alerta["t_s"] <= e["fim_s"]]
    pool = dentro or [e for e in compat if e["inicio_s"] - tol <= alerta["t_s"] <= e["fim_s"] + tol]
    return [e["id"] for e in sorted(pool, key=lambda e: (_distancia(alerta["t_s"], e), e["inicio_s"], e["id"]))]


def _chave_alerta(a: dict[str, Any]) -> tuple:
    trilha = a.get("trilha")
    return (a["t_s"], -1 if trilha is None else int(trilha), str(a.get("estado")), a.get("quadro", 0))


def associar(alertas: list[dict[str, Any]], eps: list[dict[str, Any]], tol: float, manuais: list[dict[str, Any]]) -> dict[str, Any]:
    """Associacao EXCLUSIVA e deterministica (independe da ordem de entrada). Ver docstring do modulo."""
    ordem = sorted(range(len(alertas)), key=lambda i: _chave_alerta(alertas[i]))
    atribuicao: dict[int, str | None] = {}
    fixos: set[int] = set()
    erros: list[str] = []
    fora_da_janela: list[dict[str, Any]] = []
    por_id = {e["id"]: e for e in eps}
    for m in manuais:
        achados = [i for i in ordem if abs(alertas[i]["t_s"] - m["t_s"]) <= TOL_ASSOCIACAO_MANUAL_S
                   and (m.get("trilha") is None or alertas[i].get("trilha") == m.get("trilha")) and i not in fixos]
        if not achados:
            erros.append(f"associacao manual sem alerta correspondente (t_s={m['t_s']}, trilha={m.get('trilha')})")
            continue
        i = achados[0]
        atribuicao[i] = m.get("episodio")
        fixos.add(i)
        alvo = por_id.get(m.get("episodio"))
        if alvo is not None and (_distancia(alertas[i]["t_s"], alvo) > tol or alvo["estado_esperado"] not in ("qualquer", alertas[i]["estado"])):
            fora_da_janela.append({"t_s": alertas[i]["t_s"], "trilha": alertas[i].get("trilha"), "episodio": alvo["id"]})
    cand = {i: _candidatos(alertas[i], eps, tol) for i in ordem}
    ancorados = {e for e in atribuicao.values() if e is not None}
    casado: dict[str, int] = {}

    def tentar(i: int, vistos: set[str]) -> bool:
        for e in cand[i]:
            if e in vistos or e in ancorados:
                continue
            vistos.add(e)
            if e not in casado or tentar(casado[e], vistos):
                casado[e] = i
                return True
        return False

    livres = [i for i in ordem if i not in fixos]
    for i in livres:
        tentar(i, set())
    ancoras = {i: e for e, i in casado.items()}
    for i in livres:
        atribuicao[i] = ancoras[i] if i in ancoras else (cand[i][0] if cand[i] else None)
    ambiguos = [i for i in ordem if i not in fixos and len(cand[i]) >= 2]
    return {"atribuicao": atribuicao, "ambiguos": ambiguos, "candidatos": cand, "erros": erros, "fixos": fixos, "fora_da_janela": fora_da_janela}


def _duracao(analise: dict[str, Any]) -> float | None:
    return analise["quadros_do_arquivo"] / analise["fps"] if analise.get("fps") and analise.get("quadros_do_arquivo") else None


def _metricas(analise: dict[str, Any], rev: dict[str, Any], tol: float, *, rotulos: dict[str, str] | None = None) -> dict[str, Any]:
    alertas, eps = analise["alertas"], rev["episodios"]
    r = associar(alertas, eps, tol, rev.get("associacoes", []))
    if r["erros"]:
        raise ValueError("; ".join(r["erros"]))
    por_ep = []
    for ep in eps:
        idx = sorted((i for i, e in r["atribuicao"].items() if e == ep["id"]), key=lambda i: _chave_alerta(alertas[i]))
        trilhas = [alertas[i]["trilha"] for i in idx if alertas[i].get("trilha") is not None]
        transicoes = sum(1 for a, b in zip(trilhas, trilhas[1:]) if a != b)
        sem_amb = any(i in r["fixos"] or len(r["candidatos"][i]) == 1 for i in idx)
        por_ep.append({"id": ep["id"], "criterio_inicio": ep.get("criterio_inicio"), "detectado": bool(idx), "detectado_sem_ambiguidade": sem_amb,
                       "alertas": len(idx), "atraso_s": round(alertas[idx[0]]["t_s"] - ep["inicio_s"], 3) if idx else None,
                       "repetidos": max(0, len(idx) - 1), "trilhas": sorted(set(trilhas)), "trilhas_adicionais": max(0, len(set(trilhas)) - 1),
                       "transicoes_de_trilha": transicoes})
    falsos = [{"t_s": alertas[i]["t_s"], "estado": alertas[i]["estado"], "trilha": alertas[i].get("trilha")}
              for i in sorted(r["atribuicao"], key=lambda k: _chave_alerta(alertas[k])) if r["atribuicao"][i] is None]
    perdidos = [e["id"] for e in por_ep if not e["detectado"]]
    dur = _duracao(analise)
    nome_falsos, nome_n_falsos = (rotulos or {}).get("alertas_falsos", "alertas_falsos"), (rotulos or {}).get("n_alertas_falsos", "n_alertas_falsos")
    saida = {"episodios_positivos": len(eps), "detectados": len(eps) - len(perdidos),
             "detectados_sem_ambiguidade": sum(e["detectado_sem_ambiguidade"] for e in por_ep), "perdidos": perdidos,
             nome_falsos: falsos, nome_n_falsos: len(falsos),
             "alertas_repetidos": sum(e["repetidos"] for e in por_ep), "trilhas_adicionais": sum(e["trilhas_adicionais"] for e in por_ep),
             "transicoes_de_trilha": sum(e["transicoes_de_trilha"] for e in por_ep),
             "alertas_ambiguos": [{"t_s": alertas[i]["t_s"], "trilha": alertas[i].get("trilha"), "candidatos": r["candidatos"][i]} for i in r["ambiguos"]],
             "associacoes_manuais_fora_da_janela": r["fora_da_janela"], "episodios_sobrepostos": _sobrepostos(eps),
             "duracao_do_video_s": round(dur, 2) if dur else None, "por_episodio": por_ep}
    if not eps and dur is not None and dur < NEGATIVO_CURTO_S:
        saida["aviso_negativo_curto"] = (f"negativo de {dur:.0f} s: teste de sanidade, NAO estima falsos alertas por hora "
                                         f"(menos de {NEGATIVO_CURTO_S:.0f} s de video)")
    return saida


def _base(analise: dict[str, Any], rev: Any, estado: dict[str, Any]) -> dict[str, Any]:
    r = rev if isinstance(rev, dict) else {}
    pro = r.get("procedencia") if isinstance(r.get("procedencia"), dict) else {}
    return {"versao_avaliador": VERSAO_AVALIADOR, "video": analise["video"], "alertas_emitidos": len(analise["alertas"]),
            "alertas_suprimidos": len(analise["suprimidos"]), "trilhas_distintas_nos_alertas": analise["trilhas_distintas_nos_alertas"],
            "cobertura": analise.get("cobertura"),
            "anotacao": {"estado": estado["estado"], "oficial": estado["oficial"], "pendencias": estado["motivos"], "avisos": estado["avisos"],
                         "erros": estado["erros"], "origem_revisao": r.get("origem_revisao"), "revisor_declarado": r.get("revisor"),
                         "data_declarada": r.get("data"), "revisao_cega": r.get("revisao_cega"),
                         "procedencia": {k: pro.get(k) for k in ("confirmada", "fonte", "tipo", "confirmado_por")} if pro else None}}


def _impedimento(analise: dict[str, Any], rev: Any, estado: dict[str, Any]) -> str | None:
    if rev is None:
        return "nenhum arquivo de anotacao para este video"
    if not isinstance(rev, dict):
        return "anotacao invalida: " + "; ".join(estado["erros"])
    if rev.get("sha256") != analise["sha256"]:
        return "o SHA-256 da anotacao nao confere com o do video analisado"
    if not estado["valida"]:
        return "anotacao invalida: " + "; ".join(estado["erros"])
    cob = analise.get("cobertura")
    if cob is None:
        return "analise sem registro de cobertura (gerada por avaliador anterior a versao 2): cobertura integral nao comprovada"
    ok, porque = cobertura_consistente(cob)
    if not ok:
        return "cobertura da analise nao confiavel: " + porque
    if not cob["integral"]:
        return "cobertura integral nao comprovada: " + "; ".join(cob["motivos_nao_integral"])
    return None


def avaliar(analise: dict[str, Any], revisao: Any, *, tolerancia_s: float = 2.0) -> dict[str, Any]:
    """Metricas OFICIAIS ou `metricas=None` + motivo. Nunca estima."""
    estado = classificar_anotacao(revisao, duracao_s=_duracao(analise))
    saida = _base(analise, revisao, estado)
    motivo = _impedimento(analise, revisao, estado)
    if motivo is None and not estado["oficial"]:
        motivo = "anotacao nao oficial: " + "; ".join(estado["motivos"])
    if motivo is not None:
        return {**saida, "revisado": False, "motivo": motivo, "metricas": None}
    try:
        metricas = _metricas(analise, revisao, tolerancia_s)
    except (ValueError, KeyError, TypeError) as exc:
        return {**saida, "revisado": False, "motivo": f"anotacao invalida: {type(exc).__name__}: {exc}", "metricas": None}
    return {**saida, "revisado": True, "tolerancia_s": tolerancia_s, "metricas": metricas}


ROTULOS_PROVISORIOS = {"alertas_falsos": "alertas_fora_dos_episodios_propostos", "n_alertas_falsos": "n_alertas_fora_dos_episodios_propostos"}


def leitura_provisoria(analise: dict[str, Any], proposta: Any, *, tolerancia_s: float = 2.0) -> dict[str, Any]:
    """O que sairia SE a proposta fosse confirmada. Rotulada `oficial=False` (no envelope E dentro de `leitura`), com nomes que nao
    coincidem com os das metricas oficiais ("falso" exige revisao completa); nunca substitui `avaliar`."""
    estado = classificar_anotacao(proposta, duracao_s=_duracao(analise), exigir_negativo_deliberado=False)
    saida = _base(analise, proposta, estado)
    aviso = ("LEITURA PROVISORIA a partir de uma anotacao NAO confirmada: nao sao metricas de qualidade e nao devem ser citadas como tal. "
             "Depende de confirmacao humana, procedencia do video e criterio de inicio dos episodios.")
    if estado["oficial"]:
        return {**saida, "oficial": False, "aviso": "a anotacao e oficial: use avaliar()", "leitura": None}
    motivo = None
    if not isinstance(proposta, dict):
        motivo = "proposta invalida: " + "; ".join(estado["erros"])
    elif proposta.get("sha256") != analise["sha256"]:
        motivo = "o SHA-256 da proposta nao confere com o do video analisado"
    elif not estado["valida"]:
        motivo = "proposta invalida: " + "; ".join(estado["erros"])
    leitura = None
    if motivo is None:
        try:
            leitura = _metricas(analise, proposta, tolerancia_s, rotulos=ROTULOS_PROVISORIOS)
            leitura = {"_oficial": False, "_aviso": aviso, "_negativo_declarado": bool(proposta.get("negativo_confirmado", False)), **leitura}
        except (ValueError, KeyError, TypeError) as exc:
            motivo = f"proposta invalida: {type(exc).__name__}: {exc}"
    cob = analise.get("cobertura")
    ok, _ = cobertura_consistente(cob)
    ressalva = None if ok and cob["integral"] else "cobertura integral da analise nao comprovada"
    return {**saida, "oficial": False, "aviso": aviso, "tolerancia_s": tolerancia_s, "motivo": motivo, "ressalva": ressalva, "leitura": leitura}
