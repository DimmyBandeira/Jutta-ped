from __future__ import annotations

"""Entrypoint do servico completo: API JSON (`app.py`) + painel web.

Separado de `app.py` para que importar a API (ex.: testes) nao crie banco nem
usuario administrador como efeito colateral.
"""

from src.jutta_ped.api.app import app
from src.jutta_ped.painel.panel import mount_panel

mount_panel(app)
