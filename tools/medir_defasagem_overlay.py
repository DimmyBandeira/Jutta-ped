"""Mede quanto as caixas da IA ficam ATRAS do video que o operador ve (Edge/Chrome real, WebRTC/MSE do go2rtc).

Diagnostico, nao e teste automatico. Requer: go2rtc com um stream cuja fonte seja um ARQUIVO local de video
(ex.: os streams de demo `entrada_social_vis`), Playwright e Edge/Chrome. Sobe so a API da IA (sem painel, entao nao
mexe nos streams `cam_*` do go2rtc). Uso:  python tools/medir_defasagem_overlay.py [cameras_extra_de_carga]

Achado que originou esta ferramenta (docs/painel_web.md §19): com varias cameras a IA analisava imagens ~12 s antigas
porque lia o stream em sequencia; o runtime agora usa LatestFrameCapture.

Para cada frame que a IA salva, descobre a posicao dele no arquivo de video; faz o mesmo com o frame que o
<video> do navegador esta exibindo (captura via canvas) e com snapshots do go2rtc (referencia comum).
net = defasagem_IA - defasagem_navegador  => positivo: a caixa mostra um momento ANTERIOR ao que se ve na tela.
"""
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import cv2
import httpx
import numpy as np
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
GO2RTC = "http://127.0.0.1:1984"
STREAM = os.environ.get("JUTTA_STREAM_DEMO", "entrada_social_vis")
EXTRAS = int(sys.argv[1]) if len(sys.argv) > 1 else 0
AQUECER, JANELA, PORTA = 40.0, 20.0, 8792
TAM = (96, 72)
FPS = 25.0


def mini(img):
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return cv2.resize(g, TAM, interpolation=cv2.INTER_AREA).astype(np.float32)


fonte = httpx.get(f"{GO2RTC}/api/streams", timeout=5).json()[STREAM]["producers"][0]["url"]
arquivo = re.match(r"ffmpeg:(.+?)#", fonte).group(1)
cap = cv2.VideoCapture(arquivo)
quadros = []
while True:
    ok, f = cap.read()
    if not ok:
        break
    quadros.append(mini(f))
cap.release()
video = np.stack(quadros)
N = len(video)


def pos(m):
    e = ((video - m) ** 2).mean(axis=(1, 2))
    i = int(e.argmin())
    return i, float(e[i])


JS_FRAME = """() => {
  const el = document.querySelector('video-stream');
  const v = (el && el.video) || document.querySelector('video');
  if (!v || v.videoWidth === 0 || v.readyState < 2) return null;
  const c = document.createElement('canvas'); c.width = 96; c.height = 72;
  const x = c.getContext('2d'); x.drawImage(v, 0, 0, 96, 72);
  return Array.from(x.getImageData(0, 0, 96, 72).data);
}"""


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ovv_"))
    proc = subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-c",
         f"import uvicorn; uvicorn.run('src.jutta_ped.api.app:app', host='127.0.0.1', port={PORTA}, log_level='warning')"],
        cwd=str(ROOT), stdout=(tmp / "s.log").open("wb"), stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{PORTA}"
    try:
        for _ in range(60):
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        r = httpx.post(f"{base}/cameras/enable", timeout=120, json={"source": f"rtsp://127.0.0.1:8554/{STREAM}", "camera_id": "ovv", "report_dir": str(tmp / "rel"), "diagnostic_log_interval_frames": 5})
        sessao = r.json()["session_id"]
        for k in range(EXTRAS):
            httpx.post(f"{base}/cameras/enable", timeout=120, json={"source": "rtsp://127.0.0.1:8554/corredor_colab_vis", "camera_id": f"carga{k}", "report_dir": str(tmp / "rel_c")})
        print(f"IA ligada com {EXTRAS} camera(s) de carga; aquecendo...", flush=True)

        with sync_playwright() as pw:
            browser = None
            for canal in ("msedge", "chrome"):
                try:
                    browser = pw.chromium.launch(channel=canal, headless=True, args=["--autoplay-policy=no-user-gesture-required"])
                    break
                except Exception:
                    continue
            page = browser.new_page(viewport={"width": 960, "height": 720})
            page.goto(f"{GO2RTC}/stream.html?src={STREAM}")
            time.sleep(AQUECER)

            t0 = time.time()
            snaps, brow = [], []
            while time.time() - t0 < JANELA:
                t = time.time()
                jpg = httpx.get(f"{GO2RTC}/api/frame.jpeg", params={"src": STREAM}, timeout=15).content
                img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                i, e = pos(mini(img))
                snaps.append(((t + time.time()) / 2, i, e))
                t = time.time()
                dados = page.evaluate(JS_FRAME)
                if dados:
                    rgba = np.array(dados, np.uint8).reshape(72, 96, 4)
                    g = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2GRAY).astype(np.float32)
                    i, e = pos(g)
                    brow.append(((t + time.time()) / 2, i, e))
                time.sleep(0.15)
            browser.close()

        pasta = tmp / "rel" / "evidence" / "sessions" / sessao / "frames"
        ia = []
        for f in sorted(pasta.glob("*frame_diagnostic*_frame.jpg")):
            m = re.match(r"(\d{8}_\d{6}_\d{3})", f.name)
            q = datetime.strptime(m.group(1), "%Y%m%d_%H%M%S_%f").timestamp()
            if t0 <= q <= t0 + JANELA:
                img = cv2.imread(str(f))
                i, e = pos(mini(img))
                if e < 400:
                    ia.append((q, i))

        def interp(serie, t):
            ts = np.array([s[0] for s in serie])
            k = int(np.clip(np.searchsorted(ts, t), 1, len(serie) - 1))
            (ta, ia_, _), (tb, ib, _) = serie[k - 1], serie[k]
            return (ia_ + ((ib - ia_) % N) * (t - ta) / (tb - ta)) % N if tb != ta else float(ia_)

        brow_ok = [b for b in brow if b[2] < 600]
        print(f"amostras: snapshots={len(snaps)} navegador={len(brow_ok)}/{len(brow)} frames_IA={len(ia)}")
        lag_ia, lag_nav, net = [], [], []
        for q, i in ia:
            ref = interp(snaps, q)
            li = ((ref - i) % N) / FPS
            ln = ((ref - interp(brow_ok, q)) % N) / FPS if len(brow_ok) > 2 else float("nan")
            lag_ia.append(li); lag_nav.append(ln); net.append(li - ln)
        print(f"defasagem da IA   vs referencia (snapshot): mediana {np.median(lag_ia):.2f} s")
        print(f"defasagem do VIDEO NO NAVEGADOR vs referencia: mediana {np.nanmedian(lag_nav):.2f} s")
        print(f"==> CAIXA ATRAS DA IMAGEM EXIBIDA: mediana {np.nanmedian(net):.2f} s (min {np.nanmin(net):.2f}, max {np.nanmax(net):.2f})")
    finally:
        try:
            httpx.post(f"{base}/cameras/ovv/disable", timeout=30)
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()


main()
