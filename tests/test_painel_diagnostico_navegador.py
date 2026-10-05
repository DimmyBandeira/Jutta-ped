"""Regressão curta da primeira cena e URLs de imagens no Edge.

JUTTA_DIAG_RTSP habilita uma amostra RTSP com mestre real; sem ela usa cena sintética.
Servidor HTTP e imagens são reais; gerente de sessões/go2rtc são os da fixture isolada.
"""
import os
import time
from pathlib import Path

import numpy as np
import pytest

from painel_helpers import SENHA_ADMIN
from test_painel_especialistas_navegador import navegador
from src.jutta_ped.motor.cena import Deteccao, criar_cena
from src.jutta_ped.motor.diagnostico import DiagnosticoMestre
from src.jutta_ped.painel.models import Camera


def test_primeira_cena_e_imagens_sem_404(navegador, painel_servidor, monkeypatch):
    servidor = painel_servidor
    diag = DiagnosticoMestre()
    gerente = servidor.app.state.painel.sombra
    monkeypatch.setattr(gerente, "diagnostico", lambda camera_id: diag)
    monkeypatch.setattr(gerente, "ia_rodando", lambda camera_id: True)
    with servidor.database.session() as db:
        camera = db.get(Camera, 1)
        camera.diagnostico_ativo = True
    contexto = navegador.new_context(viewport={"width": 390, "height": 844})
    try:
        pg = contexto.new_page()
        pg.goto(servidor.url + "/login")
        pg.fill("[name=login]", "admin")
        pg.fill("[name=senha]", SENHA_ADMIN)
        pg.click("button[type=submit]")
        pg.wait_for_url("**/painel/monitoramento")
        erros, pedidos = [], []
        pg.on("pageerror", lambda erro: erros.append(str(erro)))
        pg.on("response", lambda r: erros.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        pg.on("request", lambda r: pedidos.append(r.url))
        pg.goto(servidor.url + "/painel/cameras/1/diagnostico")
        # Dois ciclos de atualização sem cena: nenhum pedido de JPEG.
        pg.wait_for_timeout(2200)
        assert not any(".jpg" in url for url in pedidos)
        assert pg.locator("#diag-sem-quadro").is_visible()

        quadro = np.full((480, 640, 3), 90, np.uint8)
        deteccoes = [Deteccao(1, "person", (50., 50., 200., 400.), .9)]
        url = os.environ.get("JUTTA_DIAG_RTSP")
        if url:
            import cv2
            from ultralytics import YOLO
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG,
                                   [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
            try:
                ok, quadro = cap.read()
                assert ok, "RTSP não entregou quadro"
            finally:
                cap.release()
            modelo = YOLO(str(Path("src/models/yolo11n_openvino_model")), task="detect")
            resultado = modelo.track(quadro, device="cpu", classes=[0], persist=True, verbose=False)[0]
            deteccoes = []
            for box in resultado.boxes:
                if box.id is not None:
                    deteccoes.append(Deteccao(int(box.id.item()), "person", tuple(map(float, box.xyxy[0])), float(box.conf.item())))
            print(f"RTSP real: {quadro.shape[1]}x{quadro.shape[0]}, pessoas={len(deteccoes)}")
        h, w = quadro.shape[:2]
        diag.processar_cena(criar_cena("cam_1", 1, 42, time.monotonic(), w, h, deteccoes, quadro))
        pg.wait_for_function("document.querySelector('#diag-quadro').naturalWidth > 0", timeout=15000)
        pg.wait_for_function("document.querySelector('#diag-tem-quadro').dataset.q === '42'")
        pg.wait_for_timeout(1000)
        for img in pg.locator("#diag-resumo img").all():
            assert img.evaluate("e => e.complete && e.naturalWidth > 0")
        assert not erros, erros
        assert pg.evaluate("document.documentElement.scrollWidth <= innerWidth")
    finally:
        contexto.close()
