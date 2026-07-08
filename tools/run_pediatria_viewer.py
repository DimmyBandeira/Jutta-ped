"""Runner do Demo Viewer da IA Pediatria.

Este e um apresentacao/cliente OPCIONAL: abre a janela Qt para demonstrar a
IA visualmente (start/stop, popup de alerta, video anotado). Nao e o
runtime da IA -- isso e responsabilidade do servico, que funciona sozinho
sem UI via `tools/run_pediatria_service_api.py` ou headless em
`src/jutta_ped/service/runtime.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.jutta_ped.ui.demo_viewer import main

if __name__ == "__main__":
    main()
