"""Threads x processos: a hipotese do GIL como gargalo, testada separada da medicao de capacidade.

Mesma carga (N cameras, `analysis_fps` sem limite) de duas formas:
  A) N sessoes como THREADS de um unico processo (e assim que o caminho atual roda);
  B) N PROCESSOS independentes com 1 camera cada, janelas estaveis alinhadas por relogio de parede.

REGRA PRE-REGISTRADA (definida antes de olhar os resultados):
  * "consistente com o GIL como gargalo"  se  soma_fps(B) / soma_fps(A) >= 1.30  E  CPU media do sistema em (A) < 80 %;
  * "limitado por CPU (nao pelo GIL)"      se  CPU media do sistema em (A) >= 80 %  (os processos tambem disputam as mesmas CPUs);
  * "sem diferenca relevante"              se  razao < 1.10;
  * "inconclusivo"                         nos demais casos.
Confundidores a declarar no relatorio: threads internas do OpenVINO/NumPy competem por nucleos nos dois cenarios; carga de
modelos simultanea nos N processos; GPU compartilhada se `dispositivo=cuda`.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from tools.bateria.estatistica import resumir

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "tools" / "bateria_referencia.py"


def veredito(soma_fps_threads: float, soma_fps_processos: float, cpu_sistema_media_threads: float | None) -> dict[str, Any]:
    razao = soma_fps_processos / soma_fps_threads if soma_fps_threads else None
    cpu = cpu_sistema_media_threads
    if razao is None or cpu is None:
        texto = "inconclusivo (dados insuficientes)"
    elif razao >= 1.30 and cpu < 80:
        texto = "consistente com o GIL como gargalo"
    elif cpu >= 80:
        texto = "limitado por CPU (nao pelo GIL)"
    elif razao < 1.10:
        texto = "sem diferenca relevante"
    else:
        texto = "inconclusivo"
    return {"razao_processos_sobre_threads": round(razao, 3) if razao is not None else None,
            "cpu_sistema_media_threads_pct": cpu, "conclusao": texto,
            "regra": "razao>=1.30 e CPU<80% => GIL; CPU>=80% => limitado por CPU; razao<1.10 => sem diferenca; senao inconclusivo"}


def _rodar(args: list[str], timeout: float) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(SCRIPT), "ensaio", *args], cwd=RAIZ, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ensaio(args: list[str], arquivo: Path, timeout: float = 900) -> dict[str, Any]:
    p = _rodar([*args, "--arquivo", str(arquivo)], timeout)
    p.wait(timeout=timeout)
    if p.returncode != 0 or not arquivo.exists():
        raise RuntimeError(f"ensaio falhou (codigo {p.returncode}): {args}")
    return json.loads(arquivo.read_text(encoding="utf-8"))


def comparar(n: int, *, analysis_fps: float, videos: list[str], aquecimento: float, estavel: float, margem_inicio: float = 120.0,
             dispositivo: str = "auto") -> dict[str, Any]:
    base = ["--analysis-fps", str(analysis_fps), "--dispositivo", dispositivo, "--aquecimento", str(aquecimento), "--estavel", str(estavel)]
    if videos:
        base += ["--videos", *videos]
    with tempfile.TemporaryDirectory(prefix="bateria_escala_") as tmp:
        tmp = Path(tmp)
        threads = _ensaio(["--cameras", str(n), *base], tmp / "threads.json")
        margem = margem_inicio
        for tentativa in range(2):
            inicio = time.time() + margem
            procs = [_rodar(["--cameras", "1", "--deslocamento-video", str(i), "--inicio-janela-epoch", str(inicio),
                             "--arquivo", str(tmp / f"proc{i}.json"), *base], 900) for i in range(n)]
            amostras: list[float] = []
            gpu: list[float] = []

            def amostrar() -> None:
                psutil.cpu_percent(None)
                while time.time() < inicio:
                    time.sleep(0.5)
                psutil.cpu_percent(None)
                while time.time() < inicio + estavel:
                    time.sleep(1.0)
                    amostras.append(psutil.cpu_percent(None))

            th = threading.Thread(target=amostrar, daemon=True)
            th.start()
            codigos = [p.wait(timeout=margem + estavel + 600) for p in procs]
            th.join(timeout=5)
            if all(c == 0 for c in codigos):
                break
            margem *= 2                      # a carga simultanea demorou mais que a margem: repete uma vez com margem maior
        else:
            raise RuntimeError("os ensaios em processos falharam duas vezes")
        resultados = [json.loads((tmp / f"proc{i}.json").read_text(encoding="utf-8")) for i in range(n)]
    soma_threads = threads["agregado"]["fps_efetivo_total"]
    soma_proc = round(sum(r["agregado"]["fps_efetivo_total"] for r in resultados), 3)
    cpu_sist_threads = threads["agregado"]["cpu_sistema_pct"]["media"]
    return {
        "n": n, "analysis_fps": analysis_fps, "dispositivo": dispositivo, "margem_inicio_s": margem,
        "threads": {"fps_total": soma_threads, "fps_por_camera": {c: v["fps_efetivo"] for c, v in threads["cameras"].items()},
                    "cpu_sistema_pct": threads["agregado"]["cpu_sistema_pct"], "cpu_processo_pct_do_total": threads["agregado"]["cpu_processo_pct_do_total"],
                    "rss_mb": threads["agregado"]["rss_mb"], "descartados": threads["agregado"]["quadros_descartados_total"],
                    "ensaio_completo": threads},
        "processos": {"fps_total": soma_proc, "fps_por_processo": [r["agregado"]["fps_efetivo_total"] for r in resultados],
                      "cpu_sistema_pct": resumir(amostras, 2),
                      "cpu_processos_pct_do_total_soma_das_medias": round(sum(r["agregado"]["cpu_processo_pct_do_total"]["media"] or 0 for r in resultados), 2),
                      "rss_mb_soma": round(sum(r["agregado"]["rss_mb"]["media"] or 0 for r in resultados), 1),
                      "descartados": sum(r["agregado"]["quadros_descartados_total"] for r in resultados),
                      "ensaios_completos": resultados},
        "veredito": veredito(soma_threads, soma_proc, cpu_sist_threads),
    }
