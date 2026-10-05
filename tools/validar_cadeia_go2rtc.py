"""Ensaio curto isolado: cenário SimulaterDSA -> go2rtc -> runtime e Edge reais.

Uso: python tools/validar_cadeia_go2rtc.py --cenario caminho/perinatal.yaml
Requer go2rtc oficial em runtime/go2rtc/go2rtc.exe e fontes já publicadas.
Não muda o banco operacional nem para o simulador. Relatório/imagens em runtime.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def esperar(fn, segundos=40):
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        valor = fn()
        if valor:
            return valor
        time.sleep(.2)
    raise TimeoutError("Condição não atendida dentro do prazo")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--cenario", type=Path, required=True)
    args = p.parse_args()
    import httpx
    import uvicorn
    from fastapi import FastAPI
    from playwright.sync_api import sync_playwright
    from sqlalchemy import select
    from src.jutta_ped.painel.panel import mount_panel
    from src.jutta_ped.painel.config_service import salvar_config
    from src.jutta_ped.painel.models import Camera
    from src.jutta_ped.painel.simulater_dsa import ler_cenario
    from src.jutta_ped.service.runtime import PediatriaSessionManager, SessionStartConfig

    total_esperado = len(ler_cenario(args.cenario.read_bytes(), '127.0.0.1'))
    pasta = ROOT / "runtime" / ("validacao_go2rtc_" + time.strftime("%Y%m%d_%H%M%S"))
    pasta.mkdir(parents=True)
    senha = secrets.token_urlsafe(20)
    os.environ["JUTTA_ADMIN_PASSWORD"] = senha
    for porta in (1984, 8556):
        with socket.socket() as s:
            assert s.connect_ex(("127.0.0.1", porta)) != 0, f"Porta {porta} ocupada: ensaio não reutiliza nem interrompe outro serviço"
    app, manager = FastAPI(), PediatriaSessionManager()
    db = mount_panel(app, runtime_dir=pasta, db_url=f"sqlite:///{(pasta/'painel.db').as_posix()}",
                     manager=manager, config_factory=lambda **kw: SessionStartConfig(**kw, force_cpu=True, report_dir=pasta/'evidencias'),
                     evidence_roots=[pasta/'evidencias'], background=False)
    sv = app.state.painel
    with db.session() as session:
        salvar_config(session, {"go2rtc_exe": str(ROOT/'runtime/go2rtc/go2rtc.exe'), "go2rtc_rtsp_url": "rtsp://127.0.0.1:8556"})
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta, log_level="warning", timeout_graceful_shutdown=3))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    rel = {"pasta": str(pasta), "status": "falhou", "popup": "sintético; somente teste de interface", "erros": []}
    try:
        esperar(lambda: server.started)
        assert sv.go2rtc.garantir() == "rodando", sv.go2rtc.detalhe
        esperar(sv.go2rtc._api_responde)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True, args=["--autoplay-policy=no-user-gesture-required"])
            pg = browser.new_page(viewport={"width": 1280, "height": 900})
            pg.on("pageerror", lambda e: rel["erros"].append(str(e)))
            base = f"http://127.0.0.1:{porta}"
            pg.goto(base+'/login')
            pg.fill('[name=login]', 'admin')
            pg.fill('[name=senha]', senha)
            pg.click('button[type=submit]')
            pg.wait_for_url('**/painel/monitoramento')
            pg.goto(base+'/painel/cameras')
            pg.get_by_role('button', name='SimulaterDSA — importar cenário').click()
            pg.set_input_files('[name=arquivo_yaml]', str(args.cenario))
            pg.get_by_role('button', name='Cadastrar câmeras do cenário').click()
            pg.wait_for_selector(f'text={total_esperado} câmeras cadastradas')
            with db.session() as session:
                cameras = list(session.scalars(select(Camera).order_by(Camera.id)))
                rel['cameras_importadas'] = len(cameras)
                assert all(c.go2rtc_sync == 'ok' and not c.ia_pediatria for c in cameras)
                cam = cameras[0]
                cid, nome = cam.id, cam.nome
            # Habilita pelo formulário normal, preservando a origem importada.
            pg.locator(f'[hx-get="/painel/cameras/{cid}/editar"]').click()
            pg.locator('[name=ia_pediatria]').check()
            with pg.expect_response(lambda r: r.request.method == 'PUT' and r.url.endswith(f'/painel/cameras/{cid}')) as salvo:
                pg.locator('#form-area button[type=submit]').click()
            assert salvo.value.status == 200
            sv.ia.reconcile()
            print('Partida IA:', sv.ia.estado(cid), flush=True)
            esperar(lambda: manager.camera_status(f'cam_{cid}').get('frames_processed', 0) > 5, 70)
            status = manager.camera_status(f'cam_{cid}')
            rel['runtime'] = {k: status.get(k) for k in ('frames_processed', 'operational_status', 'session_id')}
            rel['origem_runtime'] = f'rtsp://127.0.0.1:8556/cam_{cid}'
            sv.sombra.reconcile()
            pg.goto(base+f'/painel/cameras/{cid}/diagnostico')
            pg.get_by_role('button', name='Ligar diagnóstico').click()
            pg.wait_for_function("document.querySelector('#diag-quadro')?.naturalWidth > 0", timeout=30000)
            rel['diagnostico_cenas'] = sv.sombra.diagnostico(cid).resumo()['contadores']
            pg.goto(base+'/painel/monitoramento')
            pg.get_by_text('Tocar todas', exact=True).click()
            frame = pg.locator(f'#player-{cid}').element_handle().content_frame()
            video = "document.querySelector('video-stream')?.video || document.querySelector('video')"
            frame.wait_for_function(f"() => {{const v={video};return v && v.videoWidth>0 && v.currentTime>0}}", timeout=40000)
            medir = f"() => {{const v={video};return {{t:v.currentTime,w:v.videoWidth,h:v.videoHeight}}}}"
            antes = frame.evaluate(medir)
            pg.wait_for_timeout(4000)
            depois = frame.evaluate(medir)
            assert depois['t'] > antes['t']
            rel['video'] = {'antes': antes, 'depois': depois}
            pg.wait_for_function(f"() => {{const c=document.querySelector('#overlay-{cid}');if(!c?.width)return false;const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;return d.some((v,i)=>i%4===3&&v>0)}}", timeout=35000)
            rel['overlay_real'] = True
            pg.set_viewport_size({'width':390,'height':844})
            # Popups sintéticos, separados da inferência: força a condição de várias notificações.
            sys.path.insert(0, str(ROOT/'tests'))
            from painel_helpers import payload_alerta
            esperar(lambda: sv.eventos.total_inscritos > 0)
            for n in range(3):
                sv.eventos.on_runtime_event(payload_alerta(pasta/'evidencias', 900+n, camera_id=f'cam_{cid}', sessao='validacao_interface'))
            pg.wait_for_selector('#popup-validacao_interface_902', timeout=10000)
            pg.wait_for_timeout(300)  # deixa terminar a animação de entrada antes da evidência visual
            assert pg.evaluate('document.documentElement.scrollWidth <= innerWidth')
            pg.screenshot(path=str(pasta/'mobile.png'))
            pg.locator('#popup-fechar-todos').click()
            pg.wait_for_function("document.querySelectorAll('#popup-stack .popup').length === 0")
            rel['popups_mobile'] = '3 recebidos via SSE; fechar todos funcionou; sem overflow'
            assert not rel['erros'], rel['erros']
            with httpx.Client() as c:
                streams = c.get('http://127.0.0.1:1984/api/streams').json()
                rel['go2rtc_consumidores'] = len(streams[f'cam_{cid}'].get('consumers') or [])
                rel['simulador_pronto'] = sum(x['ready'] for x in c.get('http://127.0.0.1:9997/v3/paths/list').json()['items'])
            rel['status'] = 'aprovado'
            browser.close()
    except Exception as exc:
        rel['falha'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        for cam in manager.cameras():
            if cam.get('active'):
                manager.disable_camera(cam['camera_id'])
        sv.parar()
        server.should_exit = True
        thread.join(10)
        db.dispose()
        (pasta/'resultado.json').write_text(json.dumps(rel, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(rel, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
