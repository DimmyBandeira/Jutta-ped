from __future__ import annotations

"""Entrypoint do servico completo: API JSON (`app.py`) + painel web.

Separado de `app.py` para que importar a API (ex.: testes) nao crie banco nem
usuario administrador como efeito colateral.
"""

from src.jutta_ped.api.app import app, manager
from src.jutta_ped.painel.panel import mount_panel
from src.jutta_ped.service.runtime import DEFAULT_REPORT_DIR, SessionStartConfig

mount_panel(app, manager=manager, config_factory=SessionStartConfig, evidence_roots=[DEFAULT_REPORT_DIR])
