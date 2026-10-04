"""Perfil de execucao de UMA camera no caminho atual (evidencia para a hipotese do GIL; nao a confirma sozinha).

Roda `_process_frame` na thread principal sobre quadros ja decodificados (sem custo de decodificacao), primeiro SEM e depois
COM cProfile, e separa o tempo em:

* funcoes Python (executam bytecode: seguram o GIL enquanto rodam);
* chamadas nativas (builtins/C: inferencia OpenVINO/PyTorch, OpenCV, NumPy), que em geral liberam o GIL, mas nem todas.

Limites: o cProfile soma overhead por chamada Python e infla essa fatia (o overhead medido vai no resultado); "tempo em
bytecode" nao e "tempo esperando o GIL". Esperar o GIL so aparece com varias threads: veja `escala.py`.
"""
from __future__ import annotations

import cProfile
import pstats
import tempfile
import time
from pathlib import Path
from typing import Any


def _pacote(arquivo: str) -> str:
    a = arquivo.replace("\\", "/")
    for nome in ("ultralytics", "openvino", "torch", "numpy", "cv2", "modulo/pediatria", "src/jutta_ped", "psutil"):
        if f"/{nome}/" in a or a.endswith(f"/{nome}.py"):
            return nome
    return "stdlib/outros"


# Funcoes Python que apenas ENVOLVEM uma chamada nativa bloqueante (pybind11/C++ nao aparece como builtin no cProfile): o
# tempo proprio delas e inferencia nativa, nao bytecode. Sem esta separacao a fatia "Python" ficava inflada (~52 % so no `infer`).
ENVOLTORIOS_NATIVOS = {("openvino", "_ov_api.py"): None}   # (pacote, arquivo) -> qualquer funcao do arquivo


def _eh_envoltorio_nativo(arquivo: str) -> bool:
    a = arquivo.replace("\\", "/")
    return any(a.endswith(f"/{pac}/{arq}") for (pac, arq) in ENVOLTORIOS_NATIVOS)


def classificar_stats(stats: dict[tuple, tuple], topo: int = 15) -> dict[str, Any]:
    """`pstats.Stats.stats` -> fatias de tempo proprio (tottime): Python puro x envoltorio de chamada nativa x builtin/C."""
    total = sum(v[2] for v in stats.values()) or 1e-12
    py = env = nat = 0.0
    por_pacote: dict[str, float] = {}
    linhas = {"python": [], "envoltorio": [], "nativo": []}
    for (arquivo, linha, nome), (cc, nc, tt, ct, _) in stats.items():
        if arquivo == "~":                              # builtin/C visivel ao cProfile
            nat += tt
            linhas["nativo"].append((tt, nome))
        elif _eh_envoltorio_nativo(arquivo):
            env += tt
            linhas["envoltorio"].append((tt, f"{Path(arquivo).name}:{linha}({nome})"))
        else:
            py += tt
            linhas["python"].append((tt, f"{Path(arquivo).name}:{linha}({nome})"))
            por_pacote[_pacote(arquivo)] = por_pacote.get(_pacote(arquivo), 0.0) + tt
    rank = lambda l: [{"tempo_proprio_s": round(t, 4), "pct": round(100 * t / total, 2), "funcao": n} for t, n in sorted(l, reverse=True)[:topo]]  # noqa: E731
    return {
        "tempo_total_s": round(total, 4),
        "pct_python_puro": round(100 * py / total, 2),
        "pct_envoltorio_de_inferencia_nativa": round(100 * env / total, 2),
        "pct_nativo_builtin": round(100 * nat / total, 2),
        "pct_python_por_pacote": {k: round(100 * v / total, 2) for k, v in sorted(por_pacote.items(), key=lambda kv: -kv[1])},
        "top_python": rank(linhas["python"]),
        "top_envoltorio": rank(linhas["envoltorio"]),
        "top_nativo": rank(linhas["nativo"]),
    }


def perfilar(video: Path, *, quadros: int = 150, aquecimento: int = 20, dispositivo: str = "auto") -> dict[str, Any]:
    import cv2

    from src.jutta_ped.service import runtime

    cap = cv2.VideoCapture(str(video))
    todos = []
    while len(todos) < aquecimento + quadros:
        ok, q = cap.read()
        if not ok:
            break
        todos.append(q)
    cap.release()
    if len(todos) < aquecimento + quadros:
        quadros = len(todos) - aquecimento
    with tempfile.TemporaryDirectory(prefix="bateria_perfil_") as tmp:
        cfg = runtime.SessionStartConfig(source=str(video), camera_id="perfil", report_dir=Path(tmp), requested_device=dispositivo)
        s = runtime.PediatriaHeadlessSession(cfg)
        s.evidence_dir.mkdir(parents=True, exist_ok=True)
        s.service.start_session()
        for q in todos[:aquecimento]:
            s._process_frame(q)
        mesmos = todos[aquecimento:aquecimento + quadros]            # MESMOS quadros nas duas passadas
        # Passada de aquecimento sobre estes mesmos quadros (descartada): o primeiro encontro com cada forma de recorte faz o OpenVINO
        # compilar/reformatar o especialista; sem isto a 1a passada medida absorvia esse custo e ficava artificialmente lenta.
        for q in mesmos:
            s._process_frame(q)
        s.service.reset_session_state()
        t0 = time.perf_counter()
        for q in mesmos:
            s._process_frame(q)
        t_sem = time.perf_counter() - t0
        s.service.reset_session_state()                              # volta ao mesmo estado inicial (rastreador, caches)
        pr = cProfile.Profile()
        t1 = time.perf_counter()
        pr.enable()
        for q in mesmos:
            s._process_frame(q)
        pr.disable()
        t_com = time.perf_counter() - t1
        placement = {"dispositivo": s.device, "backend": s.placement.backend if s.placement else None}
    stats = pstats.Stats(pr).stats  # type: ignore[attr-defined]
    return {
        "video": video.name, "quadros_por_fase": quadros, "aquecimento": aquecimento, "placement": placement,
        "fps_sem_perfil": round(quadros / t_sem, 3), "fps_com_perfil": round(quadros / t_com, 3),
        "overhead_do_cprofile_pct": round(100 * (t_com / t_sem - 1), 1),
        "ms_por_quadro_sem_perfil": round(1000 * t_sem / quadros, 2),
        "perfil": classificar_stats(stats),
        "leitura": ("pct_python_puro = tempo proprio em funcoes Python (executam bytecode, seguram o GIL); pct_envoltorio_de_inferencia_nativa = "
                    "funcoes Python que so esperam uma chamada nativa de inferencia (OpenVINO); pct_nativo_builtin = builtins/C visiveis "
                    "(OpenCV, NumPy, imencode). O cProfile infla a fatia Python (ver overhead). Esperar o GIL so e medido com varias threads: "
                    "ver `escala`."),
    }
