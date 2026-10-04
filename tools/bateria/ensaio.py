"""Um ensaio da bateria: N cameras simuladas no CAMINHO ATUAL (PediatriaSessionManager + sessoes + modelos por sessao).

Fases medidas separadamente: carga inicial (carregar modelos e abrir a fonte), primeiro quadro, aquecimento (descartado) e
janela estavel (onde ficam todas as metricas). Instrumentacao: so envolve `_process_frame` para cronometrar e relacionar o
quadro ao instante de entrega pela fonte; nao altera o comportamento da analise.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any

from tools.bateria.amostrador import AmostradorRecursos
from tools.bateria.estatistica import resumir
from tools.bateria.fonte_ritmada import CapturaRitmada


def executar_ensaio(*, n_cameras: int, analysis_fps: float, dispositivo: str, videos: list[Path], aquecimento_s: float,
                    estavel_s: float, inicio_janela_epoch: float | None = None, deslocamento_video: int = 0) -> dict[str, Any]:
    t_proc0 = time.perf_counter()
    from src.jutta_ped.service import runtime

    importacao_s = time.perf_counter() - t_proc0
    fontes: dict[str, CapturaRitmada] = {}
    observacoes: dict[str, list] = {f"bat{i}": [] for i in range(n_cameras)}
    eventos: list[dict[str, Any]] = []

    original_open = runtime.open_capture_source
    original_process = runtime.PediatriaHeadlessSession._process_frame

    def open_falso(origem):
        indice = int(str(origem).rsplit("/", 1)[-1])
        video = videos[(indice + deslocamento_video) % len(videos)]
        cap = CapturaRitmada(video)
        fontes[origem] = cap
        return runtime.LatestFrameCapture(cap)

    def process_instrumentado(self, frame):
        t_in = time.perf_counter()
        original_process(self, frame)
        t_out = time.perf_counter()
        cap = fontes.get(self.config.source)
        observacoes[self.camera_id].append((cap.prod.get(id(frame)) if cap else None, t_in, t_out))

    runtime.open_capture_source = open_falso
    runtime.PediatriaHeadlessSession._process_frame = process_instrumentado
    amostrador = AmostradorRecursos(observacoes)
    manager = runtime.PediatriaSessionManager()
    manager.add_event_listener(lambda p: eventos.append({"t": time.perf_counter(), "tipo": p.get("event_type"), "camera": p.get("camera_id"),
                                                         "estado": p.get("stable_state")}))
    sessoes: dict[str, Any] = {}
    cargas: dict[str, float] = {}
    primeiro_quadro: dict[str, float] = {}
    try:
        vram_base = amostrador.vram_usada_mb()
        amostrador.iniciar()
        with tempfile.TemporaryDirectory(prefix="bateria_") as tmp:
            for i in range(n_cameras):
                cfg = runtime.SessionStartConfig(source=f"rtsp://bateria/{i}", camera_id=f"bat{i}", analysis_fps=analysis_fps,
                                                 requested_device=dispositivo, report_dir=Path(tmp), reconnect_enabled=False)
                t0 = time.perf_counter()
                estado = manager.start_session(cfg)
                cargas[cfg.camera_id] = round(time.perf_counter() - t0, 3)
                sessoes[cfg.camera_id] = manager._get(estado["session_id"])
                t_start = t0
                while not observacoes[cfg.camera_id] and time.perf_counter() - t_start < 180:
                    time.sleep(0.02)
                primeiro_quadro[cfg.camera_id] = round(time.perf_counter() - t_start, 3)
            t_ref = time.perf_counter()
            if inicio_janela_epoch is not None:
                win_ini = t_ref + (inicio_janela_epoch - time.time())
                if win_ini < t_ref:
                    raise RuntimeError("a janela estavel combinada ja passou (aumente a margem de inicio)")
            else:
                win_ini = t_ref + aquecimento_s
            win_fim = win_ini + estavel_s
            while time.perf_counter() < win_ini:
                time.sleep(0.05)
            base = {c: (s.state.frames_processed, getattr(s.capture, "dropped", 0), getattr(s.capture, "lidos", 0)) for c, s in sessoes.items()}
            time.sleep(max(0.0, win_fim - time.perf_counter()))
            fim = {c: (s.state.frames_processed, getattr(s.capture, "dropped", 0), getattr(s.capture, "lidos", 0)) for c, s in sessoes.items()}
            t_fim_real = time.perf_counter()
            estado_final = {c: {"status": s.state.status, "stop_reason": s.state.stop_reason, "last_error": s.state.last_error,
                                "reconexoes": s.state.reconnect_attempts} for c, s in sessoes.items()}
            colocacao = {c: {"dispositivo": s.device, "backend": s.placement.backend if s.placement else None, "motivo": s.device_reason,
                             "analysis_fps_alvo": s._analysis_fps or None} for c, s in sessoes.items()}
            t_stop = time.perf_counter()
            for s in sessoes.values():
                s.stop()
            parada_s = round(time.perf_counter() - t_stop, 3)
        amostrador.parar()
    finally:
        runtime.open_capture_source = original_open
        runtime.PediatriaHeadlessSession._process_frame = original_process

    janela = t_fim_real - win_ini
    validade_problemas = []
    por_camera: dict[str, Any] = {}
    for c in observacoes:
        no_periodo = [o for o in observacoes[c] if win_ini <= o[2] <= win_fim]
        idades_uso = [a for t, a in amostrador.idades[c] if win_ini <= t <= win_fim]
        por_camera[c] = {
            "fonte": videos[(int(c[3:]) + deslocamento_video) % len(videos)].name,
            "fps_fonte": round(fontes[f"rtsp://bateria/{c[3:]}"].fps, 3),
            "carga_inicial_s": cargas[c], "primeiro_quadro_s": primeiro_quadro[c],
            "quadros_analisados": fim[c][0] - base[c][0], "fps_efetivo": round((fim[c][0] - base[c][0]) / janela, 3),
            "quadros_lidos_da_fonte": fim[c][2] - base[c][2], "quadros_descartados": fim[c][1] - base[c][1],
            "latencia_processamento_ms": resumir(((o[2] - o[1]) * 1000 for o in no_periodo), 2),
            "idade_na_publicacao_ms": resumir(((o[2] - o[0]) * 1000 for o in no_periodo if o[0] is not None), 2),
            "idade_em_uso_ms": resumir((a * 1000 for a in idades_uso), 2),
        }
    for c, v in por_camera.items():
        if v["quadros_analisados"] <= 0:
            validade_problemas.append(f"{c}: 0 quadros analisados na janela estavel (fonte={v['fonte']}, estado={estado_final[c]['status']}, "
                                      f"motivo={estado_final[c]['stop_reason']}, erro={estado_final[c]['last_error']})")
        elif estado_final[c]["status"] != "running":
            validade_problemas.append(f"{c}: sessao nao estava em execucao no fim da janela ({estado_final[c]['status']})")
    rec = [r for r in amostrador.recursos if win_ini <= r["t"] <= win_fim]
    todos = amostrador.recursos
    vram = [r["vram_usada_mb"] for r in rec if r["vram_usada_mb"] is not None]

    def campo(nome: str, lista=rec):
        return resumir((r[nome] for r in lista if r[nome] is not None), 2)

    return {
        "config": {"cameras": n_cameras, "analysis_fps": analysis_fps, "dispositivo_pedido": dispositivo, "aquecimento_s": aquecimento_s,
                   "estavel_s": estavel_s, "janela_real_s": round(janela, 2), "deslocamento_video": deslocamento_video,
                   "fontes": [v.name for v in videos]},
        "colocacao": colocacao,
        "estado_final_das_sessoes": estado_final,
        "validade": {"valido": not validade_problemas, "problemas": validade_problemas},
        "fases": {"importacao_s": round(importacao_s, 3), "carga_inicial_s": cargas, "primeiro_quadro_s": primeiro_quadro, "parada_s": parada_s},
        "agregado": {
            "fps_efetivo_total": round(sum(v["fps_efetivo"] for v in por_camera.values()), 3),
            "quadros_descartados_total": sum(v["quadros_descartados"] for v in por_camera.values()),
            "cpu_processo_pct_do_total": campo("cpu_processo_pct_do_total"),
            "cpu_processo_nucleos_equivalentes": resumir((r["cpu_processo_pct_de_um_nucleo"] / 100.0 for r in rec), 2),
            "cpu_sistema_pct": campo("cpu_sistema_pct"),
            "rss_mb": campo("rss_mb"), "rss_pico_mb": round(max((r["rss_mb"] for r in todos), default=0.0), 1),
            "gpu_util_pct": campo("gpu_util_pct"),
            "vram_usada_mb": campo("vram_usada_mb"), "vram_base_mb": vram_base,
            "vram_delta_medio_mb": round(sum(vram) / len(vram) - vram_base, 1) if vram and vram_base is not None else None,
            "vram_pico_mb": round(max((r["vram_usada_mb"] for r in todos if r["vram_usada_mb"] is not None), default=0.0), 1),
            "nucleos_logicos": amostrador.nucleos,
            "amostras_de_recursos_na_janela": len(rec),
        },
        "cameras": por_camera,
        "eventos": {"popup_alert": sum(1 for e in eventos if e["tipo"] == "popup_alert"),
                    "popup_suppressed": sum(1 for e in eventos if e["tipo"] == "popup_suppressed")},
    }
