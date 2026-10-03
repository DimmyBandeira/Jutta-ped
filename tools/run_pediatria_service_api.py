from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description="Sobe a API local do Jutta Ped.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "uvicorn/FastAPI nao instalados. Rode: pip install -r requirements-mvp.txt"
        ) from exc
    opcoes = {"host": args.host, "port": args.port, "timeout_graceful_shutdown": 5}   # rede de seguranca p/ conexoes abertas
    if args.reload:
        uvicorn.run("src.jutta_ped.api.server:app", reload=True, **opcoes)
        return
    # Sem reload usamos o Server diretamente para que as conexoes SSE do painel saibam que o
    # servico esta encerrando e terminem sozinhas (senao saem por cancelamento forcado, codigo 3).
    from src.jutta_ped.painel import encerramento

    servidor = uvicorn.Server(uvicorn.Config("src.jutta_ped.api.server:app", **opcoes))
    encerramento.servidor = servidor
    servidor.run()
    if not servidor.started:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
