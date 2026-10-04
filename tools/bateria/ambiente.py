"""Coleta do ambiente, modelos, fontes e configuracao de uma execucao da bateria (procedimento reproduzivel)."""
from __future__ import annotations

import dataclasses
import hashlib
import os
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def sha256_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def hash_modelo(caminho: Path) -> dict[str, Any]:
    """Arquivo -> SHA-256; pasta (OpenVINO) -> hash canonico `jutta-dir-v1` (o mesmo dos pacotes de especialista)."""
    from src.jutta_ped.especialistas.fontes import Limites, PastaFonte
    from src.jutta_ped.especialistas.hashing import ALGORITMO_PASTA, hash_pasta

    if caminho.is_dir():
        fonte = PastaFonte(caminho)
        lst = fonte.listar(Limites())
        h = hash_pasta((e.caminho, e.tamanho, (lambda c=e.caminho: fonte.blocos(c))) for e in lst.entradas)
        return {"tipo": "pasta", "algoritmo": ALGORITMO_PASTA, "sha256": h, "bytes": sum(e.tamanho for e in lst.entradas)}
    return {"tipo": "arquivo", "algoritmo": "sha256", "sha256": sha256_arquivo(caminho), "bytes": caminho.stat().st_size}


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _cpu_nome() -> str:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def _versao(modulo: str) -> str | None:
    try:
        import importlib

        m = importlib.import_module(modulo)
        return str(getattr(m, "__version__", "?"))
    except Exception:  # noqa: BLE001
        return None


def _gpus() -> list[dict[str, Any]]:
    saida: list[dict[str, Any]] = []
    try:
        import pynvml

        pynvml.nvmlInit()
        driver = pynvml.nvmlSystemGetDriverVersion()
        driver = driver.decode() if isinstance(driver, bytes) else driver
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            nome = pynvml.nvmlDeviceGetName(h)
            saida.append({"api": "nvml", "indice": i, "nome": nome.decode() if isinstance(nome, bytes) else nome,
                          "vram_total_mb": pynvml.nvmlDeviceGetMemoryInfo(h).total // (1024 * 1024), "driver": driver})
    except Exception:  # noqa: BLE001
        pass
    try:
        import openvino as ov

        core = ov.Core()
        for d in core.available_devices:
            saida.append({"api": "openvino", "dispositivo": d, "nome": str(core.get_property(d, "FULL_DEVICE_NAME"))})
    except Exception:  # noqa: BLE001
        pass
    return saida


def descrever_video(caminho: Path) -> dict[str, Any]:
    import cv2

    cap = cv2.VideoCapture(str(caminho))
    try:
        quadros = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        info = {"arquivo": caminho.name, "caminho": str(caminho), "sha256": sha256_arquivo(caminho), "bytes": caminho.stat().st_size,
                "legivel": bool(cap.isOpened()), "largura": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), "altura": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                "fps": round(fps, 3), "quadros": quadros, "duracao_s": round(quadros / fps, 2) if fps else None}
    finally:
        cap.release()
    return info


def coletar(videos: list[Path]) -> dict[str, Any]:
    """Tudo que a bateria precisa para ser repetida e auditada. Nao inclui segredos nem caminhos pessoais alem dos de arquivo."""
    import psutil

    from src.jutta_ped.service import runtime

    modelos = {nome: {"caminho": str(p), **hash_modelo(p)} for nome, p in
               (("especialista", runtime.DEFAULT_MODEL_PATH), ("pessoa", runtime.DEFAULT_PERSON_MODEL_PATH))}
    cfg = dataclasses.asdict(runtime.SessionStartConfig(source="rtsp://exemplo"))
    cfg["report_dir"] = str(cfg["report_dir"])
    sujo = [l for l in _git("status", "--porcelain").splitlines() if l and not l.startswith("??")]
    return {
        "coletado_em": datetime.now().isoformat(timespec="seconds"),
        "sistema": {"so": platform.platform(), "python": sys.version.split()[0], "cpu": _cpu_nome(),
                    "threads_logicos": psutil.cpu_count(logical=True), "nucleos_fisicos": psutil.cpu_count(logical=False),
                    "ram_total_mb": psutil.virtual_memory().total // (1024 * 1024), "gpus": _gpus()},
        "bibliotecas": {m: _versao(m) for m in ("ultralytics", "torch", "openvino", "cv2", "numpy", "psutil", "pynvml")},
        "codigo": {"commit": _git("rev-parse", "HEAD"), "ramo": _git("rev-parse", "--abbrev-ref", "HEAD"),
                   "arquivos_rastreados_modificados": sujo},
        "modelos": modelos,
        "fontes": [descrever_video(v) for v in videos],
        "configuracao_padrao_da_sessao": cfg,
    }
