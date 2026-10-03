"""Teste operacional de ponta a ponta do painel Jutta Ped (NAO e teste unitario).

Sobe o servico REAL (uvicorn + painel + modelos de IA), usa o go2rtc REAL ja em execucao e
exercita o sistema como um operador/administrador faria, medindo o que importa em producao:
tempo de subida da IA, fps por camera, memoria/VRAM por camera, isolamento de falhas, auto-cura
do go2rtc, alertas reais por SSE, reinicio, encerramento gracioso e limpeza.

Seguranca com o go2rtc do gateway: so cria/remove streams `cam_*` (os do painel), le os de demo
e, ao final, confere que o YAML do go2rtc ficou identico ao do inicio.

Uso:
    .venv\\Scripts\\python.exe tools\\teste_operacional_painel.py [--duracao 120] [--fontes a b]
Saida: relatorio Markdown em docs/testes_operacionais/AAAA-MM-DD_HHMM.md (e no console).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
SENHA = "operacional-123"
GO2RTC = "http://127.0.0.1:1984"


# ---------------------------------------------------------------------------- relatorio


@dataclass
class Passo:
    nome: str
    status: str = "OK"          # OK | FALHA | AVISO | PULADO
    detalhe: str = ""
    segundos: float = 0.0
    metricas: dict = field(default_factory=dict)


class Relatorio:
    def __init__(self) -> None:
        self.passos: list[Passo] = []
        self.inicio = datetime.now()

    def passo(self, nome: str):
        relatorio = self

        class _Ctx:
            def __enter__(self_inner):
                self_inner.p = Passo(nome)
                self_inner.t0 = time.monotonic()
                print(f"\n=== {nome}", flush=True)
                return self_inner.p

            def __exit__(self_inner, tipo, exc, tb):
                p = self_inner.p
                p.segundos = round(time.monotonic() - self_inner.t0, 1)
                if exc is not None:
                    p.status = "FALHA"
                    p.detalhe = (p.detalhe + " | " if p.detalhe else "") + f"{type(exc).__name__}: {exc}"
                    traceback.print_exception(tipo, exc, tb)
                relatorio.passos.append(p)
                print(f"    -> {p.status} ({p.segundos}s) {p.detalhe}", flush=True)
                return True      # nunca aborta a bateria: os passos seguintes decidem se dependem deste

        return _Ctx()

    def ok(self) -> bool:
        return all(p.status != "FALHA" for p in self.passos)

    def markdown(self, extra: dict) -> str:
        linhas = [
            f"# Teste operacional do painel — {self.inicio:%d/%m/%Y %H:%M}",
            "",
            f"Resultado geral: **{'APROVADO' if self.ok() else 'REPROVADO'}** "
            f"({sum(p.status == 'OK' for p in self.passos)} ok, {sum(p.status == 'AVISO' for p in self.passos)} avisos, "
            f"{sum(p.status == 'FALHA' for p in self.passos)} falhas, {sum(p.status == 'PULADO' for p in self.passos)} pulados).",
            "",
            "## Ambiente",
            "",
        ]
        linhas += [f"- {k}: {v}" for k, v in extra.items()]
        linhas += ["", "## Passos", "", "| # | Passo | Resultado | Tempo | Detalhe |", "|---|---|---|---|---|"]
        for i, p in enumerate(self.passos, 1):
            detalhe = p.detalhe.replace("|", "\\|").replace("\n", " ")
            linhas.append(f"| {i} | {p.nome} | {p.status} | {p.segundos}s | {detalhe} |")
        metricas = [(p.nome, p.metricas) for p in self.passos if p.metricas]
        if metricas:
            linhas += ["", "## Medidas", ""]
            for nome, m in metricas:
                linhas.append(f"- **{nome}**: " + ", ".join(f"{k}={v}" for k, v in m.items()))
        return "\n".join(linhas) + "\n"


# ---------------------------------------------------------------------------- utilitarios


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def esperar(condicao, timeout: float, intervalo: float = 1.0, descricao: str = "condicao"):
    limite = time.monotonic() + timeout
    ultimo = None
    while time.monotonic() < limite:
        try:
            ultimo = condicao()
        except Exception as exc:  # noqa: BLE001
            ultimo = exc
        if ultimo and not isinstance(ultimo, Exception):
            return ultimo
        time.sleep(intervalo)
    raise TimeoutError(f"{descricao} nao ocorreu em {timeout:.0f}s (ultimo: {ultimo!r})")


def vram_mb() -> int | None:
    try:
        saida = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
        return int(saida.stdout.strip().splitlines()[0])
    except Exception:  # noqa: BLE001
        return None


def go2rtc_streams() -> dict:
    return httpx.get(f"{GO2RTC}/api/streams", timeout=5).json()


def go2rtc_yaml() -> bytes | None:
    try:
        caminho = httpx.get(f"{GO2RTC}/api", timeout=5).json().get("config_path")
        return Path(caminho).read_bytes() if caminho else None
    except Exception:  # noqa: BLE001
        return None


class Painel:
    """Servico real em subprocesso + cliente HTTP logado."""

    def __init__(self, porta: int, banco: Path) -> None:
        self.porta, self.banco = porta, banco
        self.base = f"http://127.0.0.1:{porta}"
        self.proc: subprocess.Popen | None = None
        self.http = httpx.Client(base_url=self.base, timeout=30, follow_redirects=False)
        self.log = Path(tempfile.gettempdir()) / f"painel_operacional_{porta}.log"

    def iniciar(self, timeout: float = 90) -> float:
        env = dict(os.environ, JUTTA_ADMIN_PASSWORD=SENHA, JUTTA_PANEL_DB=f"sqlite:///{self.banco.as_posix()}", PYTHONUNBUFFERED="1")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        t0 = time.monotonic()
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "tools" / "run_pediatria_service_api.py"), "--port", str(self.porta)],
            cwd=str(ROOT), env=env, stdout=self.log.open("ab"), stderr=subprocess.STDOUT, creationflags=flags,
        )
        esperar(lambda: self.proc.poll() is None and httpx.get(f"{self.base}/health", timeout=2).status_code == 200, timeout, 0.5, "servico /health")
        return time.monotonic() - t0

    def encerrar_gracioso(self, timeout: float = 30) -> tuple[float, int | None]:
        t0 = time.monotonic()
        self.proc.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            return time.monotonic() - t0, None
        return time.monotonic() - t0, self.proc.returncode

    def matar(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                psutil.Process(self.proc.pid).kill()
            except psutil.Error:
                pass

    # -- cliente --
    def csrf(self, caminho: str = "/painel/cameras") -> str:
        html = self.http.get(caminho).text
        m = re.search(r'"X-CSRF-Token": "([^"]+)"', html) or re.search(r'name="csrf_token" value="([^"]+)"', html)
        if not m:
            raise RuntimeError(f"sem token CSRF em {caminho}")
        return m.group(1)

    def login(self) -> None:
        token = self.csrf("/login")
        r = self.http.post("/login", data={"login": "admin", "senha": SENHA, "csrf_token": token})
        if r.status_code != 303:
            raise RuntimeError(f"login falhou: {r.status_code}")

    def hx(self, metodo: str, caminho: str, **kw) -> httpx.Response:
        return self.http.request(metodo, caminho, headers={"X-CSRF-Token": self.csrf(), "HX-Request": "true"}, **kw)

    def camera(self, nome: str, rtsp: str, ia: bool = True) -> int:
        dados = {"nome": nome, "fabricante": "rtsp_generico", "rtsp_url": rtsp}
        if ia:
            dados["ia_pediatria"] = "on"
        r = self.hx("POST", "/painel/cameras", data=dados)
        if r.status_code != 200 or "HX-Retarget" in r.headers:
            raise RuntimeError(f"cadastro recusado: {re.sub('<[^>]+>', '', r.text)[:150]}")
        ids = {int(i) for i in re.findall(r'id="camera-(\d+)"', r.text)}
        return max(ids)

    def ia_estado(self, camera_id: int) -> str:
        html = self.http.get(f"/painel/cameras/{camera_id}/ia-estado").text
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()

    def api_cameras(self) -> dict[str, dict]:
        return {c["camera_id"]: c for c in httpx.get(f"{self.base}/cameras", timeout=10).json()["cameras"]}

    def processos(self) -> list[psutil.Process]:
        """Processo lancado + filhos: no Windows o python da venv e um launcher e o servico real e um filho."""
        raiz = psutil.Process(self.proc.pid)
        return [raiz, *raiz.children(recursive=True)]

    def cpu_pct(self, segundos: float) -> float:
        procs = self.processos()
        for p in procs:
            p.cpu_percent(None)
        time.sleep(segundos)
        return sum(p.cpu_percent(None) for p in procs) / psutil.cpu_count()

    def threads(self) -> int:
        return sum(p.num_threads() for p in self.processos())

    def rss_mb(self) -> float:
        try:
            filhos = psutil.Process(self.proc.pid).children(recursive=True)
            return round(sum(p.memory_info().rss for p in [psutil.Process(self.proc.pid), *filhos]) / 2**20, 0)
        except psutil.Error:
            return -1


class OuvinteSse(threading.Thread):
    """Mantem uma conexao em /painel/stream coletando os eventos recebidos."""

    def __init__(self, painel: Painel, nome: str) -> None:
        super().__init__(daemon=True, name=f"sse-{nome}")
        self.painel, self.eventos, self.parar, self.conectou = painel, [], threading.Event(), threading.Event()

    def run(self) -> None:
        cookies = dict(self.painel.http.cookies)
        try:
            with httpx.stream("GET", f"{self.painel.base}/painel/stream", cookies=cookies, timeout=httpx.Timeout(10, read=None)) as r:
                self.conectou.set()
                atual: dict = {}
                for linha in r.iter_lines():
                    if self.parar.is_set():
                        return
                    if linha == "":
                        if atual:
                            self.eventos.append(atual)
                            atual = {}
                    elif linha.startswith(":"):
                        continue
                    elif ":" in linha:
                        k, v = linha.split(":", 1)
                        atual[k] = (atual.get(k, "") + "\n" if k in atual else "") + v.lstrip()
        except Exception:  # noqa: BLE001 -- encerrar o servidor derruba a conexao: esperado
            pass

    def popups(self) -> list[dict]:
        return [e for e in self.eventos if e.get("event") == "popup"]

    def acks(self) -> list[dict]:
        return [e for e in self.eventos if e.get("event") == "ack"]


# ---------------------------------------------------------------------------- bateria


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--duracao", type=int, default=120, help="segundos esperando um alerta real da IA (padrao 120)")
    ap.add_argument("--fontes", nargs=2, default=["entrada_social_vis", "corredor_colab_vis"], metavar=("A", "B"), help="streams de demo do go2rtc")
    ap.add_argument("--saida", type=Path, default=ROOT / "docs" / "testes_operacionais")
    args = ap.parse_args()

    rel = Relatorio()
    ambiente: dict = {"python": sys.version.split()[0], "so": sys.platform}
    tmp = Path(tempfile.mkdtemp(prefix="painel_operacional_"))
    painel = Painel(porta_livre(), tmp / "painel.db")
    yaml_antes = go2rtc_yaml()
    cam_antes: set[str] = set()
    ouvintes: list[OuvinteSse] = []
    cams: dict[str, int] = {}
    fontes = {"A": f"rtsp://127.0.0.1:8554/{args.fontes[0]}", "B": f"rtsp://127.0.0.1:8554/{args.fontes[1]}"}

    try:
        with rel.passo("Pre-requisitos (go2rtc real, streams de demo, porta, GPU)") as p:
            streams = go2rtc_streams()
            faltam = [f for f in args.fontes if f not in streams]
            if faltam:
                raise RuntimeError(f"streams de demo ausentes no go2rtc: {faltam}")
            cam_antes = {n for n in streams if n.startswith("cam_")}
            versao = httpx.get(f"{GO2RTC}/api", timeout=5).json().get("version")
            ambiente.update({"go2rtc": versao, "streams_go2rtc": len(streams), "vram_inicial_mb": vram_mb(), "cpus": psutil.cpu_count()})
            p.detalhe = f"go2rtc {versao}, {len(streams)} streams, VRAM {ambiente['vram_inicial_mb']} MB"
            if cam_antes:
                p.status, p.detalhe = "AVISO", p.detalhe + f"; ja existiam streams cam_* ({sorted(cam_antes)}) -- serao preservados"

        with rel.passo("Subir o servico real (uvicorn + painel + admin inicial)") as p:
            seg = painel.iniciar()
            painel.login()
            p.detalhe = f"/health em {seg:.1f}s; login admin OK; RSS {painel.rss_mb():.0f} MB"
            p.metricas = {"boot_s": round(seg, 1), "rss_mb": painel.rss_mb()}

        with rel.passo("Abrir 2 conexoes SSE (dois operadores) e conferir o heartbeat"):
            for nome in ("op1", "op2"):
                o = OuvinteSse(painel, nome)
                o.start()
                ouvintes.append(o)
            esperar(lambda: all(o.conectou.is_set() for o in ouvintes), 15, 0.2, "SSE conectado")

        with rel.passo("Cadastrar camera A pelo painel -> stream aparece no go2rtc e sincroniza") as p:
            cams["A"] = painel.camera("Operacional A", fontes["A"])
            esperar(lambda: f"cam_{cams['A']}" in go2rtc_streams(), 20, 0.5, "stream cam_A no go2rtc")
            html = painel.http.get("/painel/cameras").text
            if "sincronizado" not in html:
                raise RuntimeError("lista nao mostra 'sincronizado'")
            p.detalhe = f"cam_{cams['A']} criado no go2rtc e marcado sincronizado"

        rss0, vram0 = painel.rss_mb(), vram_mb()
        with rel.passo("IA da camera A sobe sozinha em segundo plano (modelos reais)") as p:
            t0 = time.monotonic()
            pronto = esperar(lambda: (e := painel.ia_estado(cams["A"])) and not any(x in e for x in ("Iniciando", "Aguardando")) and e, 180, 2, "IA da camera A")
            tempo = time.monotonic() - t0
            if "Erro" in pronto:
                raise RuntimeError(f"IA em erro: {pronto}")
            rss1, vram1 = painel.rss_mb(), vram_mb()
            p.detalhe = f"estado '{pronto}' em {tempo:.0f}s; +{rss1 - rss0:.0f} MB RAM; VRAM {vram0}->{vram1} MB"
            p.metricas = {"subida_ia_s": round(tempo), "ram_por_camera_mb": rss1 - rss0, "vram_por_camera_mb": (vram1 - vram0) if vram0 is not None and vram1 is not None else None}

        with rel.passo("Throughput real: fps da camera A e overlay com bboxes") as p:
            id_a = f"cam_{cams['A']}"
            f0, t0 = painel.api_cameras()[id_a]["frames_processed"], time.monotonic()
            uso_cpu = painel.cpu_pct(10)
            f1 = painel.api_cameras()[id_a]["frames_processed"]
            fps = (f1 - f0) / (time.monotonic() - t0)
            objetos = None
            for _ in range(30):
                o = painel.http.get(f"/painel/cameras/{cams['A']}/overlay").json()
                if o.get("objects"):
                    objetos = o
                    break
                time.sleep(1)
            p.metricas = {"fps": round(fps, 1), "cpu_servico_pct": round(uso_cpu), "frame": f"{(objetos or {}).get('frame_w')}x{(objetos or {}).get('frame_h')}"}
            p.detalhe = f"{fps:.1f} fps, CPU do servico ~{uso_cpu:.0f}% do total ({psutil.cpu_count()} nucleos); overlay {'com ' + str(len(objetos['objects'])) + ' objetos' if objetos else 'SEM objetos em 30s'}"
            if fps < 1:
                raise RuntimeError(f"fps muito baixo ({fps:.2f})")
            if not objetos:
                p.status = "AVISO"

        with rel.passo("Cadastrar camera B: duas cameras em paralelo, threads separadas") as p:
            rss0, vram0 = painel.rss_mb(), vram_mb()
            cams["B"] = painel.camera("Operacional B", fontes["B"])
            esperar(lambda: (e := painel.ia_estado(cams["B"])) and not any(x in e for x in ("Iniciando", "Aguardando")) and e, 180, 2, "IA da camera B")
            time.sleep(10)
            api = painel.api_cameras()
            fa0, fb0, t0 = api[f"cam_{cams['A']}"]["frames_processed"], api[f"cam_{cams['B']}"]["frames_processed"], time.monotonic()
            time.sleep(10)
            api = painel.api_cameras()
            dt = time.monotonic() - t0
            fps_a = (api[f"cam_{cams['A']}"]["frames_processed"] - fa0) / dt
            fps_b = (api[f"cam_{cams['B']}"]["frames_processed"] - fb0) / dt
            rss1, vram1 = painel.rss_mb(), vram_mb()
            threads = painel.threads()
            p.metricas = {"fps_A": round(fps_a, 1), "fps_B": round(fps_b, 1), "ram_camera_extra_mb": rss1 - rss0, "vram_camera_extra_mb": (vram1 - vram0) if vram0 is not None and vram1 is not None else None, "threads_processo": threads}
            p.detalhe = f"A {fps_a:.1f} fps, B {fps_b:.1f} fps; +{rss1 - rss0:.0f} MB RAM; VRAM {vram0}->{vram1} MB (total {vram1}/4096)"
            if min(fps_a, fps_b) < 0.5:
                raise RuntimeError("uma das cameras parou de processar")

        with rel.passo("Isolamento de falha: camera fantasma (IP inalcancavel) nao afeta A e B") as p:
            r = painel.hx("POST", "/painel/cameras", data={"nome": "Fantasma", "fabricante": "intelbras", "ip": "10.255.255.1", "porta": "554", "canal": "1", "stream_tipo": "principal", "usuario": "x", "senha": "y", "ia_pediatria": "on"})
            cams["F"] = max(int(i) for i in re.findall(r'id="camera-(\d+)"', r.text))
            fa0 = painel.api_cameras()[f"cam_{cams['A']}"]["frames_processed"]
            estado_f = esperar(lambda: (e := painel.ia_estado(cams["F"])) and ("Erro" in e or "Aguardando" in e) and e, 120, 3, "estado de erro/espera da fantasma")
            time.sleep(8)
            fa1 = painel.api_cameras()[f"cam_{cams['A']}"]["frames_processed"]
            if fa1 <= fa0:
                raise RuntimeError("camera A parou enquanto a fantasma falhava")
            p.detalhe = f"fantasma em '{estado_f}'; A seguiu processando (+{fa1 - fa0} frames)"

        with rel.passo(f"Alerta REAL da IA: popup por SSE nos 2 operadores (ate {args.duracao}s)") as p:
            try:
                esperar(lambda: all(o.popups() for o in ouvintes), args.duracao, 2, "popup real")
            except TimeoutError:
                p.status, p.detalhe = "AVISO", f"a IA nao gerou alerta de crianca desacompanhada em {args.duracao}s com os videos de demo (sem falha do sistema); veja --duracao"
            else:
                html = ouvintes[0].popups()[0].get("data", "")
                m = re.search(r'id="popup-([^"]+)"', html)
                event_id = m.group(1)
                imagem = painel.http.get(f"/painel/evidence/{event_id}/crop")
                if imagem.status_code != 200 or not imagem.content.startswith(b"\xff\xd8"):
                    raise RuntimeError(f"evidencia nao serviu um JPEG ({imagem.status_code})")
                r = painel.hx("POST", f"/painel/popups/{event_id}/ack")
                esperar(lambda: any(event_id in (a.get("data") or "") for a in ouvintes[1].acks()), 10, 0.5, "ack no outro operador")
                recentes = painel.http.get("/painel/eventos/recentes").text
                if "admin" not in recentes:
                    raise RuntimeError("tabela de recentes nao mostra a ciencia")
                p.detalhe = f"popup {event_id}; recorte JPEG {len(imagem.content) // 1024} KB; ack propagado ao 2o operador; ciencia na tabela"

        with rel.passo("Volume de evidencias: suprimidos nao inundam o disco (limite de 1 evento a cada 10 s)") as p:
            total_arquivos = total_mb = excesso = 0
            for sessao in httpx.get(f"{painel.base}/sessions", timeout=10).json()["sessions"]:
                pasta = Path(sessao["evidence_dir"])
                if not pasta.exists() or sessao["camera_id"] not in {f"cam_{cams['A']}", f"cam_{cams['B']}"}:
                    continue
                arquivos = [f for f in pasta.rglob("*") if f.is_file()]
                total_arquivos += len(arquivos)
                total_mb += sum(f.stat().st_size for f in arquivos) / 2**20
                suprimidos = len(list((pasta / "metadata").glob("*popup_suppressed*.json"))) if (pasta / "metadata").exists() else 0
                inicio = datetime.fromisoformat(sessao["started_at"])
                maximo = (datetime.now() - inicio).total_seconds() / 10 + 3
                excesso = max(excesso, suprimidos - maximo)
                print(f"    sessao {sessao['session_id'][-6:]} {sessao['camera_id']}: {len(arquivos)} arquivos, {suprimidos} suprimidos (max esperado {maximo:.0f})")
            p.metricas = {"arquivos": total_arquivos, "mb": round(total_mb, 1)}
            p.detalhe = f"{total_arquivos} arquivos / {total_mb:.1f} MB nas sessoes A e B"
            if excesso > 0:
                raise RuntimeError(f"suprimidos acima do limite esperado (+{excesso:.0f})")

        with rel.passo("Auto-cura: stream da camera A removido direto no go2rtc volta sozinho") as p:
            nome = f"cam_{cams['A']}"
            httpx.delete(f"{GO2RTC}/api/streams", params={"src": nome}, timeout=5)
            t0 = time.monotonic()
            if nome in go2rtc_streams():
                raise RuntimeError("nao consegui remover o stream para o teste")
            esperar(lambda: nome in go2rtc_streams(), 60, 1, "stream recriado pelo painel")
            p.detalhe = f"{nome} recriado pelo painel em {time.monotonic() - t0:.0f}s"

        with rel.passo("Editar camera A (renomear): a sessao de IA e reiniciada de verdade e volta a analisar") as p:
            sessao_antes = painel.api_cameras()[f"cam_{cams['A']}"]["session_id"]
            r = painel.hx("PUT", f"/painel/cameras/{cams['A']}", data={"nome": "Operacional A2", "fabricante": "rtsp_generico", "rtsp_url": "rtsp://***:***@127.0.0.1:8554/" + args.fontes[0], "ia_pediatria": "on"})
            if "HX-Retarget" in r.headers:
                raise RuntimeError(re.sub("<[^>]+>", "", r.text))
            t0 = time.monotonic()
            esperar(lambda: painel.api_cameras()[f"cam_{cams['A']}"].get("session_id") not in (None, sessao_antes), 90, 1, "nova sessao de IA para A")
            esperar(lambda: (e := painel.ia_estado(cams["A"])) and not any(x in e for x in ("Iniciando", "Aguardando", "Erro")) and e, 120, 2, "IA de A apos edicao")
            p.detalhe = f"sessao {sessao_antes[-6:]} -> {painel.api_cameras()[f'cam_{cams[chr(65)]}']['session_id'][-6:]}; IA de A voltou em {time.monotonic() - t0:.0f}s"

        with rel.passo("Encerramento gracioso (Ctrl+Break) com 2 SSE conectados") as p:
            seg, codigo = painel.encerrar_gracioso()
            p.metricas = {"encerrar_s": round(seg, 1), "codigo_saida": codigo}
            p.detalhe = f"encerrou em {seg:.1f}s (codigo {codigo})"
            if codigo is None:
                raise RuntimeError("o servico nao encerrou sozinho (precisou ser morto)")
            texto_log = painel.log.read_text(encoding="utf-8", errors="replace")
            ultimo = texto_log[texto_log.rfind("Shutting down"):] if "Shutting down" in texto_log else ""
            limpo = "Application shutdown complete" in ultimo and "Finished server process" in ultimo and "Cancel" not in ultimo
            if not limpo:
                raise RuntimeError("o log nao mostra desligamento limpo (ou houve cancelamento forcado de conexoes)")
            # Ctrl+Break: depois do desligamento limpo o uvicorn re-dispara o sinal e o padrao do Windows para
            # SIGBREAK termina o processo com codigo 3 (artefato do metodo de parada; Ctrl+C/NSSM saem com 0).
            if codigo == 3 and sys.platform == "win32":
                p.detalhe += " -- codigo 3 = SIGBREAK re-disparado pelo uvicorn no Windows; desligamento limpo comprovado no log"
            elif codigo != 0:
                raise RuntimeError(f"codigo de saida {codigo} com desligamento limpo no log")
            if seg > 15:
                p.status = "AVISO"

        for o in ouvintes:
            o.parar.set()

        with rel.passo("Reinicio: cameras persistem, sessao antiga continua valida, IA volta sozinha") as p:
            streams_antes = {n for n in go2rtc_streams() if n.startswith("cam_")}
            painel.http = httpx.Client(base_url=painel.base, timeout=30, follow_redirects=False, cookies=painel.http.cookies)   # mesmo cookie de antes
            seg = painel.iniciar()
            if painel.http.get("/painel/cameras").status_code != 200:
                raise RuntimeError("cookie de sessao anterior deixou de valer apos o reinicio")
            html = painel.http.get("/painel/cameras").text
            for nome in ("Operacional A2", "Operacional B", "Fantasma"):
                if nome not in html:
                    raise RuntimeError(f"camera '{nome}' sumiu apos reiniciar")
            t0 = time.monotonic()
            for chave in ("A", "B"):
                esperar(lambda c=chave: (e := painel.ia_estado(cams[c])) and not any(x in e for x in ("Iniciando", "Aguardando", "Erro")) and e, 180, 2, f"IA {chave} apos reinicio")
            streams_depois = {n for n in go2rtc_streams() if n.startswith("cam_")}
            p.detalhe = f"servico de volta em {seg:.0f}s, sessao preservada, 3 cameras no banco, IA A e B em {time.monotonic() - t0:.0f}s; streams cam_* {len(streams_antes)}->{len(streams_depois)}"

        with rel.passo("Limpeza: remover cameras pelo painel limpa o go2rtc (YAML do gateway identico)") as p:
            for chave, cid in cams.items():
                painel.hx("DELETE", f"/painel/cameras/{cid}")
            esperar(lambda: {n for n in go2rtc_streams() if n.startswith("cam_")} <= cam_antes, 45, 1, "cam_* removidos do go2rtc")
            time.sleep(1)
            yaml_depois = go2rtc_yaml()
            if yaml_antes is not None and yaml_depois != yaml_antes:
                raise RuntimeError("o go2rtc.yaml do gateway ficou DIFERENTE do original")
            p.detalhe = "cam_* removidos; go2rtc.yaml identico ao original"

    finally:
        for o in ouvintes:
            o.parar.set()
        painel.matar()
        # rede de seguranca: nunca deixar stream do teste no go2rtc do gateway
        try:
            for nome in [n for n in go2rtc_streams() if n.startswith("cam_") and n not in cam_antes]:
                httpx.delete(f"{GO2RTC}/api/streams", params={"src": nome}, timeout=5)
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    ambiente["log_do_servico"] = str(painel.log)
    texto = rel.markdown(ambiente)
    args.saida.mkdir(parents=True, exist_ok=True)
    destino = args.saida / f"{rel.inicio:%Y-%m-%d_%H%M}.md"
    destino.write_text(texto, encoding="utf-8")
    print("\n" + texto)
    print(f"Relatorio salvo em {destino}")
    return 0 if rel.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
