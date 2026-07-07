from __future__ import annotations

import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SERVICE_SCRIPT = ROOT / "tools" / "run_pediatria_service_api.py"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def health_url(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    return f"http://{host}:{port}/health"


def is_service_running(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 0.5) -> bool:
    try:
        with urllib.request.urlopen(health_url(host, port), timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def build_start_command(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> list[str]:
    return [sys.executable, str(SERVICE_SCRIPT), "--host", host, "--port", str(port)]


def start_service_process(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> subprocess.Popen:
    """Sobe o servico local (uvicorn) em background, sem janela de console.

    Nao mata o processo quando quem chamou fecha: o servico deve continuar
    disponivel para outros clientes (ver Rodada 3).
    """
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.Popen(
        build_start_command(host, port),
        cwd=str(ROOT),
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
