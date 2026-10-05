"""Fecha os criterios restantes do 05f PELO PAINEL, sobre a integracao go2rtc ja validada: configuracao no navegador, orquestracao de IA do
proprio painel (laco de producao, sem `reconcile()` manual) e visualizacao dos crops com a configuracao vigente.

    python tools/validar_painel_05f.py [--cenario <perinatal.yaml>] [--espera 90]

Servidor HTTP real, go2rtc real da mae (RTSP 8556; o simulador em 8554 nao e tocado), `IaOrquestrador` com `background=True`, Edge headless.
Banco/pasta isolados em runtime/ (ignorado pelo Git; as capturas de tela contem quadros de video e NAO devem ser versionadas).
O que prova: operar o modo simples pelo navegador, a IA subir pelo orquestrador a partir do restream, a sombra em tres cameras de
configuracao propria, o isolamento ao salvar uma delas, os crops exibidos coerentes com a configuracao vigente (conferidos contra a formula
do contrato a partir dos numeros que a propria pagina mostra) e CPU explicita honrada. Nao e medicao de capacidade nem de qualidade.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.validar_motor_especialistas import (  # noqa: E402
    BackendRoteador, CENARIO, FONTES, ModeloDeterministico, PACOTE_DET, PACOTE_REAL, definicoes_receitas, montar_pacote_conformidade,
)
from tools.medicao_janela import dispositivo_efetivo  # noqa: E402


def esperar(fn, segundos=60, passo=0.5, msg="condicao"):
    fim = time.monotonic() + segundos
    while time.monotonic() < fim:
        v = fn()
        if v:
            return v
        time.sleep(passo)
    raise TimeoutError(f"{msg} nao atendida em {segundos}s")


def esperado_folgas(cfg: dict, bbox, largura, altura):
    """Formula do contrato (docs 05f): folga/lado = min(max(minimo, int(fracao*dim da caixa externa)), distancia ate a borda do quadro)."""
    x1, y1, x2, y2 = bbox
    wx, wy = int(-(-x2 // 1)) - int(x1 // 1), int(-(-y2 // 1)) - int(y1 // 1)
    bx1, by1, bx2, by2 = max(0, int(x1 // 1)), max(0, int(y1 // 1)), min(largura, int(-(-x2 // 1))), min(altura, int(-(-y2 // 1)))
    mh, mv, mn = cfg["h"] / 100.0, cfg["v"] / 100.0, cfg["min"]
    px, py = max(mn, int(mh * wx)), max(mn, int(mv * wy))
    return {"esq": min(px, bx1), "dir": min(px, largura - bx2), "topo": min(py, by1), "base": min(py, altura - by2)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cenario", type=Path, default=CENARIO)
    ap.add_argument("--espera", type=float, default=90.0)
    args = ap.parse_args()
    import httpx
    import uvicorn
    from fastapi import FastAPI
    from playwright.sync_api import sync_playwright
    from sqlalchemy import select
    from src.jutta_ped.especialistas.instalador import Instalador
    from src.jutta_ped.painel.config_service import salvar_config
    from src.jutta_ped.painel.models import Camera, CameraConfigVersao, EventoPediatria
    from src.jutta_ped.painel.panel import mount_panel
    from src.jutta_ped.service.runtime import PediatriaSessionManager, SessionStartConfig
    from tools.pacote_papel import montar

    for porta in (1984, 8556):
        with socket.socket() as s:
            assert s.connect_ex(("127.0.0.1", porta)) != 0, f"porta {porta} ocupada: o roteiro nao reutiliza nem interrompe outro servico"
    pasta = ROOT / "runtime" / f"validacao_painel_05f_{time.strftime('%Y%m%d_%H%M%S')}"
    pasta.mkdir(parents=True)
    senha = secrets.token_urlsafe(20)
    os.environ["JUTTA_ADMIN_PASSWORD"] = senha
    # pacotes pelo ciclo normal (instalar + ativar), antes de o painel subir
    inst = Instalador(pasta / "especialistas")
    for z in (montar(ROOT / "src/models/pediatria_child_detector_v6_jutta_openvino_model", pasta / "pacotes" / "papel.zip"),
              montar_pacote_conformidade(pasta / "pacotes" / "conformidade.zip")):
        r = inst.instalar(z)
        inst.ativar(r.id, r.versao)
    app, manager = FastAPI(), PediatriaSessionManager()
    backend = BackendRoteador()
    db = mount_panel(app, runtime_dir=pasta, db_url=f"sqlite:///{(pasta / 'painel.db').as_posix()}", manager=manager,
                     config_factory=lambda **kw: SessionStartConfig(**kw, force_cpu=True, report_dir=pasta / "evidencias"),
                     evidence_roots=[pasta / "evidencias"], background=True, especialistas_backends={"pt": backend, "openvino": backend})
    sv = app.state.painel
    sv.executor.dispositivos_permitidos = ("cpu",)                      # CPU explicita tambem para os especialistas
    with db.session() as session:
        salvar_config(session, {"go2rtc_exe": str(ROOT / "runtime/go2rtc/go2rtc.exe"), "go2rtc_rtsp_url": "rtsp://127.0.0.1:8556"})
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        porta_http = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=porta_http, log_level="warning", timeout_graceful_shutdown=3))
    threading.Thread(target=server.run, daemon=True).start()
    rel: dict = {"pasta": str(pasta), "status": "falhou", "erros_js": [], "http_com_erro": [], "passos": []}
    nomes = {"A": FONTES["A"], "B": FONTES["B"], "C": FONTES["C"]}
    ids: dict[str, int] = {}
    try:
        esperar(lambda: server.started, 30, msg="servidor")
        esperar(lambda: sv.go2rtc._api_responde(), 40, msg="go2rtc da mae")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="msedge", headless=True)
            pg = browser.new_page(viewport={"width": 1280, "height": 900})
            pg.on("pageerror", lambda e: rel["erros_js"].append(str(e)))
            pg.on("response", lambda r: rel["http_com_erro"].append(f"{r.status} {r.url}") if r.status >= 400 and "favicon" not in r.url else None)
            base = f"http://127.0.0.1:{porta_http}"
            pg.goto(base + "/login")
            pg.fill("[name=login]", "admin")
            pg.fill("[name=senha]", senha)
            pg.click("button[type=submit]")
            pg.wait_for_url("**/painel/monitoramento")

            # 1) cadastro das cameras pelo cenario SimulaterDSA (rota da interface)
            pg.goto(base + "/painel/cameras")
            pg.get_by_role("button", name="SimulaterDSA — importar cenário").click()
            pg.set_input_files("[name=arquivo_yaml]", str(args.cenario))
            pg.get_by_role("button", name="Cadastrar câmeras do cenário").click()
            pg.wait_for_selector("text=câmeras cadastradas")
            with db.session() as session:
                por_nome = {c.nome: c.id for c in session.scalars(select(Camera))}
            ids = {k: por_nome[v] for k, v in nomes.items()}
            rel["cameras"] = {k: {"id": ids[k], "fonte_publicada": nomes[k]} for k in ids}
            rel["passos"].append("cenario importado pela interface")

            # 2) receitas importadas pela interface (JSON do contrato publico)
            pg.goto(base + "/painel/receitas")
            for nome, definicao in definicoes_receitas().items():
                pg.fill("#imp-nome", nome)
                pg.fill("#imp-desc", "ensaio 05f pelo painel")
                pg.fill("#imp-json", json.dumps(definicao))
                pg.get_by_role("button", name="Importar como nova versão").click()
                pg.wait_for_selector(f"text=Definição importada como {nome}")
            rel["passos"].append("receitas importadas pela interface")

            # 3) IA ligada pelo formulario normal; configuracao de analise pelo formulario de analise; sombra pela tela de receitas
            cfgs = {"A": dict(esp=PACOTE_REAL, rec="ensaio-papel-contrato2", h=12, v=8, min=8, intervalo=0.4),
                    "B": dict(esp=PACOTE_REAL, rec="ensaio-papel-contrato2", h=25, v=15, min=16, intervalo=1.0),
                    "C": dict(esp=PACOTE_DET, rec="ensaio-porte-contrato1", h=5, v=5, min=8, intervalo=0.4)}
            for k, cid in ids.items():
                pg.goto(base + "/painel/cameras")
                pg.locator(f'[hx-get="/painel/cameras/{cid}/editar"]').click()
                pg.locator("[name=ia_pediatria]").check()
                with pg.expect_response(lambda r, cid=cid: r.request.method == "PUT" and r.url.endswith(f"/painel/cameras/{cid}")) as salvo:
                    pg.locator("#form-area button[type=submit]").click()
                assert salvo.value.status == 200, salvo.value.status
                pg.goto(base + f"/painel/cameras/{cid}/analise")
                c = cfgs[k]
                pg.select_option("#a-esp", c["esp"])
                valor_rec = pg.evaluate("(n) => [...document.querySelectorAll('#a-rec option')].find(o => o.textContent.includes(n))?.value", c["rec"])
                assert valor_rec, f"receita {c['rec']} nao listada como compativel"
                pg.select_option("#a-rec", valor_rec)
                pg.fill("#c-h", str(c["h"]))
                pg.fill("#c-v", str(c["v"]))
                pg.fill("#c-p", str(c["min"]))
                pg.fill("#x-i", str(c["intervalo"]))
                pg.get_by_role("button", name="Salvar configuração").click()
                pg.wait_for_selector("#tit-efetivo")
                assert pg.locator(".mensagem.erro").count() == 0, pg.locator(".mensagem.erro").first.inner_text()
            pg.goto(base + "/painel/receitas")
            for k, cid in ids.items():
                pg.locator(f'form[action="/painel/cameras/{cid}/sombra"] button:has-text("Ligar sombra")').click()
                pg.wait_for_selector(f'form[action="/painel/cameras/{cid}/sombra"] button:has-text("Desligar sombra")')
            rel["passos"].append("IA ligada, analise configurada e sombra ligada nas 3 cameras, tudo pelo navegador")

            # 4) orquestracao: o LACO do painel (sem reconcile manual) sobe a IA a partir do restream do go2rtc e monta a sombra
            def pronto():
                return all(sv.ia.estado(cid).get("estado") == "rodando" and sv.sombra.estado(cid)["estado"] == "ativa" for cid in ids.values())
            esperar(pronto, args.espera, 1.0, "IA rodando + sombra ativa nas 3 cameras (orquestrador)")
            esperar(lambda: all(manager.camera_status(f"cam_{cid}").get("frames_processed", 0) > 5 for cid in ids.values()), 60, 1.0, "quadros processados")
            rel["orquestracao"] = {k: {"ia": sv.ia.estado(cid).get("estado"), "sombra": sv.sombra.estado(cid)["estado"],
                                       "origem_runtime": f"rtsp://127.0.0.1:8556/cam_{cid}",
                                       "frames_processed": manager.camera_status(f"cam_{cid}").get("frames_processed")} for k, cid in ids.items()}
            rel["passos"].append("orquestrador do painel subiu a IA e a sombra (laco de producao, sem reconcile manual)")

            # 5) crops no navegador: diagnostico ligado pela interface; numeros da propria pagina conferidos contra a formula do contrato
            def conferir_crops(k: str, cfg: dict) -> dict:
                cid = ids[k]
                pg.goto(base + f"/painel/cameras/{cid}/diagnostico")
                if pg.get_by_role("button", name="Ligar diagnóstico", exact=True).count():
                    pg.get_by_role("button", name="Ligar diagnóstico", exact=True).click()
                try:
                    pg.wait_for_function("document.querySelector('#diag-quadro')?.naturalWidth > 0", timeout=40000)
                except Exception:
                    d = sv.sombra.diagnostico(cid)
                    rel["debug_sem_quadro"] = {"camera": k, "pagina": pg.inner_text("body")[:600], "diag": d.resumo()["contadores"] if d else None,
                                               "sombra": sv.sombra.estado(cid), "ia": sv.ia.estado(cid), "porta": [sv.sombra.porta(cid).recebidos, sv.sombra.porta(cid).ativa],
                                               "frames": manager.camera_status(f"cam_{cid}").get("frames_processed")}
                    raise
                # recortes: so linhas com pessoa dentro da regiao; espera pelo menos uma com imagem carregada
                pg.wait_for_selector('#diag-resumo img[alt^="Recorte"]', state='attached', timeout=40000)
                pg.evaluate("() => document.querySelectorAll('#diag-resumo img[alt^=\"Recorte\"]').forEach(i => { i.loading = 'eager'; i.scrollIntoView(); })")   # `loading=lazy` fora da tela nao carrega em headless
                pg.wait_for_function("document.querySelectorAll('#diag-resumo img[alt^=\"Recorte\"]').length > 0 && [...document.querySelectorAll('#diag-resumo img[alt^=\"Recorte\"]')].every(i => i.complete)", timeout=40000)
                pg.wait_for_timeout(300)
                dados = pg.evaluate("""() => {
                  const cab = document.querySelector('#diag-resumo')?.innerText || '';
                  const linhas = [...document.querySelectorAll('#diag-resumo tbody tr')].map(tr => {
                    const t = [...tr.children].map(td => td.innerText.trim());
                    const img = tr.querySelector('img[alt^="Recorte"]');
                    return {trilha: t[0], caixa: t[1], recorte: t[4], folgas: t[5], img: img ? {w: img.naturalWidth, h: img.naturalHeight, ok: img.complete && img.naturalWidth > 0} : null};
                  });
                  return {cab, linhas};
                }""")
                m = re.search(r"(\d+)×(\d+) px", dados["cab"])
                largura, altura = int(m.group(1)), int(m.group(2))
                conferidas, falhas = 0, []
                for ln in dados["linhas"]:
                    if not ln["img"]:
                        continue
                    caixa = [float(x) for x in ln["caixa"].replace("\n", " ").split(",")]
                    tam = re.search(r"(\d+)×(\d+)", ln["recorte"])
                    f = {n: int(v) for n, v in re.findall(r"(esq|dir|topo|base) (\d+)", ln["folgas"])}
                    esp = esperado_folgas(cfg, caixa, largura, altura)
                    # a pagina arredonda a caixa para inteiro ("%.0f"): tolera 1 px de diferenca vinda do arredondamento exibido
                    if any(abs(f.get(n, -99) - esp[n]) > 1 for n in esp):
                        falhas.append({"linha": ln["trilha"], "exibido": f, "esperado": esp})
                    if not ln["img"]["ok"] or (tam and (ln["img"]["w"], ln["img"]["h"]) != (int(tam.group(1)), int(tam.group(2)))):
                        falhas.append({"linha": ln["trilha"], "imagem": ln["img"], "texto": ln["recorte"]})
                    conferidas += 1
                efetivo = pg.locator("span.aviso code").first.inner_text() if pg.locator("span.aviso code").count() else None
                return {"quadro_largura_altura": [largura, altura], "recortes_conferidos": conferidas, "falhas": falhas, "config_hash_exibido": efetivo}

            crops1 = {k: conferir_crops(k, cfgs[k]) for k in ids}
            rel["crops_na_interface"] = crops1
            assert all(v["recortes_conferidos"] > 0 and not v["falhas"] for v in crops1.values()), crops1
            pg.screenshot(path=str(pasta / "diagnostico_ultima_camera.png"))      # contem quadro de video: NAO versionar
            rel["passos"].append("crops exibidos e conferidos nas 3 cameras com a configuracao vigente")

            # 6) isolamento ao SALVAR uma camera (A) pelo formulario: so ela muda; B e C seguem com a mesma montagem
            def montagem(cid):
                s = sv.sombra._slots[cid]
                return (s.montagem, s.assinatura, id(s.motor), s.porta.recebidos)
            antes = {k: montagem(ids[k]) for k in "BC"}
            with db.session() as session:
                hash_antes = {k: session.scalars(select(CameraConfigVersao).where(CameraConfigVersao.camera_id == ids[k]).order_by(CameraConfigVersao.versao.desc())).first().config_hash for k in ids}
            sessao_a = manager.camera_status(f"cam_{ids['A']}").get("session_id")
            pg.goto(base + f"/painel/cameras/{ids['A']}/analise")
            pg.fill("#c-h", "30")
            pg.get_by_role("button", name="Salvar configuração").click()
            pg.wait_for_selector("#tit-efetivo")
            cfgs["A"]["h"] = 30
            esperar(lambda: sv.sombra._slots[ids["A"]].montagem != antes.get("A", (None,))[0] and sv.sombra.estado(ids["A"])["estado"] == "ativa", 40, 0.5, "A remontada")
            with db.session() as session:
                hash_depois = {k: session.scalars(select(CameraConfigVersao).where(CameraConfigVersao.camera_id == ids[k]).order_by(CameraConfigVersao.versao.desc())).first().config_hash for k in ids}
            depois = {k: montagem(ids[k]) for k in "BC"}
            assert hash_depois["A"] != hash_antes["A"] and hash_depois["B"] == hash_antes["B"] and hash_depois["C"] == hash_antes["C"], (hash_antes, hash_depois)
            assert all(depois[k][:3] == antes[k][:3] for k in "BC"), (antes, depois)            # mesma montagem, mesmo motor, sem reinicio
            assert manager.camera_status(f"cam_{ids['A']}").get("session_id") == sessao_a      # a analise operacional de A NAO reiniciou
            esperar(lambda: any(e["config_hash"] for e in sv.sombra.eventos(ids["A"])), 40, 0.5, "evento novo de A")
            hashes_receita_a = {e["config_hash"] for e in sv.sombra.eventos(ids["A"], 200)}
            assert len(hashes_receita_a) == 1, hashes_receita_a                                  # so a configuracao vigente no feed de A
            crops2 = {k: conferir_crops(k, cfgs[k]) for k in ("A", "B")}
            assert all(v["recortes_conferidos"] > 0 and not v["falhas"] for v in crops2.values()), crops2
            rel["isolamento_ao_salvar_A"] = {"hash_config": {"antes": hash_antes, "depois": hash_depois}, "B_e_C_mesma_montagem_e_motor": True,
                                             "sessao_operacional_de_A_nao_reiniciou": True, "feed_de_A_so_com_config_vigente": True, "crops_apos_salvar": crops2}
            rel["passos"].append("salvar A pelo formulario: so A remontou; sessao de A nao reiniciou; B e C intactas; crops de A refletem 30 %")

            # 7) sombra x operacional e dispositivo efetivo
            pg.goto(base + "/painel/monitoramento")
            with db.session() as session:
                eventos_banco = session.query(EventoPediatria).count()
            todos = [e for cid in ids.values() for e in sv.sombra.eventos(cid, 500)]
            rel["sombra"] = {"eventos_no_feed": len(todos), "eventos_pediatria_no_banco(operacional, nao da sombra)": eventos_banco,
                             "alerta_real_true_no_feed": sum(1 for e in todos if e.get("alerta_real"))}
            sessoes = {k: next(x for x in manager._sessions.values() if x.camera_id == f"cam_{ids[k]}") for k in ids}      # noqa: SLF001
            rel["dispositivos_efetivos"] = {
                "sessoes": {k: {"pedido": s.device, "detector": dispositivo_efetivo(s.service.crop_pipeline.person_model, s.device),
                                "classificador": dispositivo_efetivo(s.service.runner.model, s.device)} for k, s in sessoes.items()},
                "especialistas": {f"{pid}/{mid}": (dispositivo_efetivo(getattr(m, "_m", None), m.dispositivo) if not isinstance(m, ModeloDeterministico) else "n/a deterministico")
                                  for pid, car in sv.executor._carregados.items() for mid, m in car.modelos.items()},
                "fallbacks": {pid: car.fallbacks for pid, car in sv.executor._carregados.items()}}
            efetivos = [d for s in rel["dispositivos_efetivos"]["sessoes"].values() for d in (s["detector"], s["classificador"])] + \
                       [d for d in rel["dispositivos_efetivos"]["especialistas"].values() if isinstance(d, dict)]
            assert all(d["execution_devices"] == ["CPU"] and not d["divergente_do_solicitado"] for d in efetivos), rel["dispositivos_efetivos"]
            assert not rel["erros_js"], rel["erros_js"]
            assert not rel["http_com_erro"], rel["http_com_erro"]
            with httpx.Client() as c:
                streams = c.get("http://127.0.0.1:1984/api/streams").json()
                rel["go2rtc_consumidores"] = {k: len(streams[f"cam_{cid}"].get("consumers") or []) for k, cid in ids.items()}
                rel["simulador_fontes_prontas"] = sum(x["ready"] for x in c.get("http://127.0.0.1:9997/v3/paths/list").json()["items"])
            rel["status"] = "aprovado"
            browser.close()
    except Exception as exc:
        rel["falha"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        for cam in manager.cameras():
            if cam.get("active"):
                manager.disable_camera(cam["camera_id"])
        sv.parar()
        server.should_exit = True
        time.sleep(1)
        db.dispose()
        (pasta / "resultado.json").write_text(json.dumps(rel, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(json.dumps(rel, ensure_ascii=False, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
