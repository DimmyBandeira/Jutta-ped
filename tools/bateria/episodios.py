"""Qualidade dos alertas por EPISODIOS REVISADOS: perdidos, falsos e repetidos por troca de trilha.

Divisao de trabalho (nada aqui inventa rotulo):
* `analisar_video` roda o caminho atual sobre o video INTEIRO (arquivo: todos os quadros) e devolve a linha do tempo de alertas.
* `preparar_revisao` empacota o que uma pessoa precisa para rotular: quadro de cada alerta e uma linha do tempo do video.
* `avaliar` so calcula metricas quando existe um arquivo de episodios com `revisado: true` para aquele video (mesmo SHA-256).
  Sem revisao, o resultado e DESCRITIVO e as metricas de qualidade ficam `None` (indisponiveis), nunca estimadas.

Arquivo de episodios (um por video):
  {"video": "x.dav", "sha256": "...", "revisado": true, "revisor": "nome", "data": "2026-10-04",
   "revisao_completa_do_video": true,   # a pessoa viu o video todo: tudo fora dos episodios e 'sem alerta esperado'
   "episodios": [{"id": "e1", "inicio_s": 3.0, "fim_s": 9.5, "estado_esperado": "CHILD_ALONE|CHILD_SEPARATED|qualquer",
                  "observacao": "..."}]}
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

ESTADOS_ALERTA = ("CHILD_ALONE", "CHILD_SEPARATED")


def analisar_video(video: Path, *, dispositivo: str = "auto", limite_s: float = 900.0) -> dict[str, Any]:
    """Caminho atual, arquivo inteiro, sem ritmo (todos os quadros). Devolve linha do tempo; guarda evidencias em `evidencias_tmp`."""
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
    t0 = time.perf_counter()
    estado = manager.start_session(cfg)
    sessao = manager._get(estado["session_id"])
    while sessao.thread is not None and sessao.thread.is_alive() and time.perf_counter() - t0 < limite_s:
        time.sleep(0.2)
    duracao = time.perf_counter() - t0
    resumo = sessao.status()
    sessao.stop()
    linha = []
    for p in eventos:
        crianca = p.get("selected_child") or {}
        quadro = int(p.get("frame_index") or 0)
        linha.append({"tipo": p["event_type"], "quadro": quadro, "t_s": round(quadro / fps, 3), "estado": p.get("stable_state"),
                      "trilha": crianca.get("track_id"), "confianca": p.get("child_confidence"), "motivo": p.get("reason"),
                      "evidencias": {k: v for k, v in (p.get("evidence_paths") or {}).items() if isinstance(v, str)}})
    return {"video": video.name, "sha256": sha256_arquivo(video), "fps": round(fps, 3), "quadros_do_arquivo": total,
            "quadros_analisados": resumo.get("frames_processed"), "tempo_de_analise_s": round(duracao, 2),
            "terminou": not (sessao.thread is not None and sessao.thread.is_alive()),
            "alertas": [e for e in linha if e["tipo"] == "popup_alert"], "suprimidos": [e for e in linha if e["tipo"] == "popup_suppressed"],
            "trilhas_distintas_nos_alertas": sorted({e["trilha"] for e in linha if e["trilha"] is not None}),
            "evidencias_tmp": str(evid)}


def preparar_revisao(analise: dict[str, Any], destino: Path, video: Path, *, passo_s: float = 2.0, largura: int = 320) -> dict[str, Any]:
    """Pacote para a pessoa rotular: quadro de cada alerta e folha de contato do video (um quadro a cada `passo_s`)."""
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
    rascunho = {"video": analise["video"], "sha256": analise["sha256"], "revisado": False, "revisor": "", "data": "",
                "revisao_completa_do_video": False, "episodios": [],
                "instrucoes": ("Veja linha_do_tempo.jpg e o video. Para cada trecho em que um alerta DEVERIA ocorrer, acrescente um episodio "
                               "{id, inicio_s, fim_s, estado_esperado, observacao}. Marque revisao_completa_do_video=true so se viu o video todo. "
                               "Depois ponha revisado=true. Os alertas do sistema abaixo sao so apoio; NAO sao rotulos."),
                "alertas_do_sistema_para_apoio": [{"t_s": a["t_s"], "estado": a["estado"], "trilha": a["trilha"]} for a in analise["alertas"]]}
    (destino / "episodios.rascunho.json").write_text(json.dumps(rascunho, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"pasta": str(destino), "arquivos_de_evidencia": copiados, "linha_do_tempo": bool(miniaturas)}


def avaliar(analise: dict[str, Any], revisao: dict[str, Any] | None, *, tolerancia_s: float = 2.0) -> dict[str, Any]:
    """Metricas SOMENTE com revisao humana valida. Retorna descritivo + `metricas=None` caso contrario."""
    alertas = analise["alertas"]
    base = {"video": analise["video"], "alertas_emitidos": len(alertas), "alertas_suprimidos": len(analise["suprimidos"]),
            "trilhas_distintas_nos_alertas": analise["trilhas_distintas_nos_alertas"]}
    if not revisao:
        return {**base, "revisado": False, "motivo": "nenhum arquivo de episodios para este video", "metricas": None}
    if revisao.get("sha256") != analise["sha256"]:
        return {**base, "revisado": False, "motivo": "o SHA-256 do arquivo de episodios nao confere com o do video analisado", "metricas": None}
    if not revisao.get("revisado"):
        return {**base, "revisado": False, "motivo": "episodios ainda nao revisados (revisado=false)", "metricas": None}
    if not revisao.get("revisao_completa_do_video"):
        return {**base, "revisado": True, "motivo": "revisao parcial: sem 'revisao_completa_do_video', alertas fora dos episodios nao podem ser chamados de falsos",
                "metricas": None}
    eps = revisao.get("episodios", [])

    def dentro(a, ep) -> bool:
        ok_estado = ep.get("estado_esperado", "qualquer") in ("qualquer", a["estado"])
        return ok_estado and ep["inicio_s"] - tolerancia_s <= a["t_s"] <= ep["fim_s"] + tolerancia_s

    por_ep, usados = [], set()
    for ep in eps:
        casados = [(i, a) for i, a in enumerate(alertas) if dentro(a, ep)]
        usados.update(i for i, _ in casados)
        trilhas = {a["trilha"] for _, a in casados if a["trilha"] is not None}
        por_ep.append({"id": ep["id"], "detectado": bool(casados), "alertas": len(casados),
                       "atraso_s": round(casados[0][1]["t_s"] - ep["inicio_s"], 3) if casados else None,
                       "repetidos": max(0, len(casados) - 1), "trocas_de_trilha": max(0, len(trilhas) - 1), "trilhas": sorted(trilhas)})
    falsos = [{"t_s": a["t_s"], "estado": a["estado"], "trilha": a["trilha"]} for i, a in enumerate(alertas) if i not in usados]
    perdidos = [e["id"] for e in por_ep if not e["detectado"]]
    return {**base, "revisado": True, "revisor": revisao.get("revisor"), "data_da_revisao": revisao.get("data"), "tolerancia_s": tolerancia_s,
            "metricas": {"episodios_positivos": len(eps), "detectados": len(eps) - len(perdidos), "perdidos": perdidos,
                         "alertas_falsos": falsos, "n_alertas_falsos": len(falsos),
                         "alertas_repetidos": sum(e["repetidos"] for e in por_ep), "trocas_de_trilha": sum(e["trocas_de_trilha"] for e in por_ep),
                         "por_episodio": por_ep}}
