"""Wrapper de compatibilidade -- NAO adicione logica nova aqui.

O Demo Viewer da IA Pediatria mudou de lugar: agora vive em
`src/jutta_ped/ui/demo_viewer.py` e o runner atual e
`tools/run_pediatria_viewer.py`. Este arquivo so existe para nao quebrar
atalhos/.bat antigos que ainda apontam para
`tools/run_pediatria_popup_mvp.py` (ex.: `iniciar_mvp.bat`,
`tools/rodar_pediatria_local.bat`).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.jutta_ped.ui.demo_viewer import *  # noqa: F401,F403
from src.jutta_ped.ui.demo_viewer import main

if __name__ == "__main__":
    main()
