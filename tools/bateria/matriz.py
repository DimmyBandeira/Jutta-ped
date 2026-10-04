"""Matriz de ensaios: cada ensaio roda em PROCESSO LIMPO (RAM/VRAM/CPU de partida comparaveis, sem modelos de ensaios anteriores)."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "tools" / "bateria_referencia.py"


def plano(cameras: list[int], analysis_fps: list[float], extra_cuda: list[int], repeticoes: int, repeticao_inicial: int = 1) -> list[dict[str, Any]]:
    itens = [{"cameras": c, "analysis_fps": f, "dispositivo": "auto"} for f in analysis_fps for c in cameras]
    itens += [{"cameras": c, "analysis_fps": 5.0, "dispositivo": "cuda"} for c in extra_cuda]
    saida = []
    for rep in range(repeticao_inicial, repeticao_inicial + repeticoes):
        for it in itens:
            saida.append({**it, "repeticao": rep, "id": f"c{it['cameras']}_fps{int(it['analysis_fps'])}_{it['dispositivo']}_r{rep}"})
    return saida


def executar_plano(itens: list[dict[str, Any]], pasta: Path, *, aquecimento: float, estavel: float, videos: list[str] | None,
                   timeout_s: float = 1200.0) -> list[dict[str, Any]]:
    pasta.mkdir(parents=True, exist_ok=True)
    arq_indice = pasta / "indice.json"
    indice = json.loads(arq_indice.read_text(encoding="utf-8")) if arq_indice.exists() else []     # acumula repeticoes anteriores
    ids_novos = {it["id"] for it in itens}
    indice = [i for i in indice if i["id"] not in ids_novos]
    for it in itens:
        arq = pasta / f"{it['id']}.json"
        cmd = [sys.executable, str(SCRIPT), "ensaio", "--cameras", str(it["cameras"]), "--analysis-fps", str(it["analysis_fps"]),
               "--dispositivo", it["dispositivo"], "--aquecimento", str(aquecimento), "--estavel", str(estavel), "--arquivo", str(arq)]
        if videos:
            cmd += ["--videos", *videos]
        t0 = time.time()
        print(f"[{time.strftime('%H:%M:%S')}] {it['id']} ...", flush=True)
        try:
            p = subprocess.run(cmd, cwd=RAIZ, capture_output=True, text=True, timeout=timeout_s)
            status = "ok" if p.returncode == 0 and arq.exists() else f"falhou({p.returncode})"
            erro = (p.stderr or "")[-400:] if status != "ok" else ""
        except subprocess.TimeoutExpired:
            status, erro = "timeout", ""
        indice.append({**it, "arquivo": arq.name if arq.exists() else None, "status": status, "erro": erro, "duracao_s": round(time.time() - t0, 1)})
        print(f"    -> {status} em {indice[-1]['duracao_s']} s", flush=True)
        arq_indice.write_text(json.dumps(indice, ensure_ascii=False, indent=2), encoding="utf-8")
    return [i for i in indice if i["id"] in ids_novos]
