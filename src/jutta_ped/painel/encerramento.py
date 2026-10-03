"""Sinal de "o servico esta encerrando", visivel para conexoes de longa duracao (SSE).

O uvicorn espera as conexoes abertas terminarem antes de desligar; sem isto, as conexoes SSE so
caem por cancelamento forcado apos `timeout_graceful_shutdown` e o processo sai com codigo 3 (que
um gerenciador de servico trata como falha). O script de execucao registra aqui o `uvicorn.Server`.
"""
from __future__ import annotations

servidor = None   # uvicorn.Server do processo; definido por tools/run_pediatria_service_api.py


def deve_encerrar() -> bool:
    return bool(servidor is not None and getattr(servidor, "should_exit", False))
