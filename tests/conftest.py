"""Configuracao global dos testes.

Testes de navegador (Qt WebEngine) precisam que, ANTES de qualquer QApplication ser criada
na sessao: (1) QtWebEngineWidgets esteja importado, (2) AA_ShareOpenGLContexts esteja ligado
e (3) as flags do Chromium estejam no ambiente. Por isso isso vive aqui, e nao no modulo de teste.
Sao flags so de teste: sem GPU, sem sandbox, autoplay livre e sem a politica de mesma origem,
para que o teste consiga inspecionar o iframe do go2rtc (outra porta = outra origem).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--disable-gpu --no-sandbox --autoplay-policy=no-user-gesture-required --disable-web-security --disable-site-isolation-trials",
)

try:  # Qt e WebEngine sao opcionais para quem so roda os testes de backend
    from PyQt6 import QtWebEngineWidgets  # noqa: F401
    from PyQt6.QtCore import QCoreApplication, Qt

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
except ImportError:
    pass
