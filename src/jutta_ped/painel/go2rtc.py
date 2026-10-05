from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from src.jutta_ped.painel.crypto import Cifra
from src.jutta_ped.painel.db import Database
from src.jutta_ped.painel.models import Camera, Fabricante, StreamTipo

logger = logging.getLogger("jutta_ped.painel.go2rtc")

PREFIXO_STREAM = "cam_"

BASE_YAML_PADRAO = """\
# Gerado pelo painel Jutta Ped na primeira execucao. Pode editar a vontade:
# o painel nunca sobrescreve este arquivo. Os streams das cameras ficam em
# streams.yaml (gerado automaticamente a cada alteracao de camera).
api:
  listen: "127.0.0.1:1984"
rtsp:
  listen: "127.0.0.1:8554"
webrtc:
  listen: ":8555"
"""


def _host_porta(url: str, padrao_porta: int) -> tuple[str, int]:
    """(host, porta) de uma URL http/rtsp. Host vazio/invalido vira 127.0.0.1; porta ausente usa o padrao."""
    try:
        partes = urlsplit(url if "//" in url else f"//{url}")
        return partes.hostname or "127.0.0.1", partes.port or padrao_porta
    except ValueError:
        return "127.0.0.1", padrao_porta


def render_base_yaml(api_url: str = "http://127.0.0.1:1984", rtsp_url: str = "rtsp://127.0.0.1:8554") -> str:
    """YAML base do go2rtc com as portas das URLs configuradas (api e RTSP de retransmissao).

    Serve para o go2rtc conviver com outro servidor RTSP na mesma maquina (ex.: o MediaMTX do SimulaterDSA em 8554): configure a
    "Base RTSP" para outra porta (ex.: rtsp://127.0.0.1:8556) e o go2rtc passa a escutar nela. Os padroes reproduzem BASE_YAML_PADRAO."""
    api_host, api_porta = _host_porta(api_url, 1984)
    rtsp_host, rtsp_porta = _host_porta(rtsp_url, 8554)
    return (BASE_YAML_PADRAO.split("api:")[0] + f'api:\n  listen: "{api_host}:{api_porta}"\nrtsp:\n  listen: "{rtsp_host}:{rtsp_porta}"\nwebrtc:\n  listen: ":8555"\n')


def porta_em_uso(host: str, porta: int, timeout: float = 0.3) -> bool:
    """True se algo ja ATENDE em host:porta (tentativa de conexao curta). Nao liga nem desliga nada."""
    import socket

    try:
        with socket.create_connection((host if host not in ("", "0.0.0.0") else "127.0.0.1", porta), timeout=timeout):
            return True
    except OSError:
        return False


def nome_stream(camera: Camera) -> str | None:
    """Nome do stream no go2rtc: estavel (`cam_<id>`) para cameras gerenciadas."""
    if camera.fabricante == Fabricante.go2rtc_teste:
        return camera.go2rtc_stream_name
    return f"{PREFIXO_STREAM}{camera.id}"


def montar_url_rtsp(camera: Camera, cifra: Cifra) -> str | None:
    """URL de origem do stream (com credenciais). `None` para stream de teste (ja existe no go2rtc)."""
    if camera.fabricante == Fabricante.go2rtc_teste:
        return None
    if camera.fabricante == Fabricante.rtsp_generico:
        if not camera.rtsp_url_enc:
            raise ValueError("URL RTSP nao cadastrada.")
        return cifra.decifrar(camera.rtsp_url_enc)
    if not camera.senha_enc or not camera.usuario or not camera.ip:
        raise ValueError("Dados de conexao incompletos.")
    usuario = quote(camera.usuario, safe="")
    senha = quote(cifra.decifrar(camera.senha_enc), safe="")
    principal = camera.stream_tipo == StreamTipo.principal
    base = f"rtsp://{usuario}:{senha}@{camera.ip}:{camera.porta}"
    if camera.fabricante == Fabricante.intelbras:
        return f"{base}/cam/realmonitor?channel={camera.canal}&subtype={0 if principal else 1}"
    return f"{base}/Streaming/Channels/{camera.canal}0{1 if principal else 2}"  # hikvision


_CREDENCIAIS_RE = re.compile(r"(rtsps?://)[^@/\s]+@")


def mascarar_url(url: str) -> str:
    return _CREDENCIAIS_RE.sub(r"\1***:***@", url)


def render_streams_yaml(streams: dict[str, str]) -> str:
    """YAML so de streams. Valores em aspas duplas JSON (subconjunto valido de YAML) para nao
    depender de escapes de `?`, `&`, `%` e `#` das URLs."""
    linhas = ["# GERADO AUTOMATICAMENTE pelo painel Jutta Ped -- nao edite (sera sobrescrito).", "streams:"]
    if not streams:
        return "\n".join(linhas[:1] + ["streams: {}"]) + "\n"
    for nome in sorted(streams):
        linhas.append(f"  {nome}: {json.dumps(streams[nome])}")
    return "\n".join(linhas) + "\n"


def escrever_atomico(path: Path, conteudo: str, *, tentativas: int = 5, espera: float = 0.1) -> None:
    """Grava via arquivo temporario + rename. No Windows o destino pode ficar travado por
    instantes (antivirus, sincronizador de nuvem, o proprio go2rtc lendo): tenta de novo e,
    por ultimo, grava direto no destino."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(conteudo, encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    for tentativa in range(tentativas):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(espera * (tentativa + 1))
    try:
        path.write_text(conteudo, encoding="utf-8")
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


class Go2rtcIndisponivel(Exception):
    pass


class Go2rtcClient:
    """Cliente minimo da API HTTP do go2rtc (`/api/streams`)."""

    def __init__(self, base_url: str, *, timeout: float = 2.0, transport: httpx.BaseTransport | None = None) -> None:
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _chamar(self, metodo: str, **params: str) -> httpx.Response:
        try:
            resposta = self._http.request(metodo, "/api/streams", params=params or None)
        except httpx.HTTPError as exc:  # inclui timeout/conexao recusada; nunca repassa a URL (tem credencial)
            raise Go2rtcIndisponivel(f"go2rtc inacessivel ({type(exc).__name__})") from None
        if resposta.status_code >= 400:
            raise Go2rtcIndisponivel(f"go2rtc respondeu HTTP {resposta.status_code}")
        return resposta

    def listar(self) -> dict[str, Any]:
        dados = self._chamar("GET").json()
        return dados if isinstance(dados, dict) else {}

    def criar(self, nome: str, src: str) -> None:
        self._chamar("PUT", name=nome, src=src)

    def remover(self, nome: str) -> None:
        self._chamar("DELETE", src=nome)


@dataclass
class ResumoSync:
    go2rtc_online: bool = False
    ok: int = 0
    pendente: int = 0
    erro: int = 0
    removidos: int = 0
    detalhe: str = ""

    def texto(self) -> str:
        estado = "go2rtc online" if self.go2rtc_online else "go2rtc offline"
        return f"{estado} — {self.ok} ok, {self.pendente} pendente(s), {self.erro} com erro"


class Go2rtcSync:
    """Reconcilia o go2rtc a partir do banco (fonte da verdade).

    Idempotente e com um unico escritor (lock). Sempre reescreve `streams.yaml`
    (para o proximo boot do go2rtc) e tenta aplicar a quente pela API.
    Falha do go2rtc nunca impede salvar a camera: ela fica `pendente`.
    """

    def __init__(
        self,
        database: Database,
        cifra: Cifra,
        streams_yaml: Path,
        *,
        client_factory: Callable[[], Go2rtcClient],
    ) -> None:
        self._database = database
        self._cifra = cifra
        self._streams_yaml = streams_yaml
        self._client_factory = client_factory
        self._lock = threading.Lock()
        self.ultimo_resumo = ResumoSync()

    def reconcile(self) -> ResumoSync:
        with self._lock:
            self.ultimo_resumo = self._reconcile()
            return self.ultimo_resumo

    def _reconcile(self) -> ResumoSync:
        resumo = ResumoSync()
        with self._database.session() as db:
            todas = db.query(Camera).order_by(Camera.id).all()
            for camera in todas:
                if not camera.gerenciada_pelo_sync:  # stream de teste ja existe no go2rtc: nada a sincronizar
                    camera.go2rtc_sync, camera.go2rtc_sync_erro = "ok", None
            cameras = [c for c in todas if c.gerenciada_pelo_sync]
            desejados: dict[str, tuple[Camera, str]] = {}
            for camera in cameras:
                try:
                    desejados[nome_stream(camera)] = (camera, montar_url_rtsp(camera, self._cifra))  # type: ignore[index]
                except ValueError as exc:
                    camera.go2rtc_sync, camera.go2rtc_sync_erro = "erro", str(exc)[:200]
                    resumo.erro += 1

            try:
                escrever_atomico(self._streams_yaml, render_streams_yaml({n: url for n, (_c, url) in desejados.items()}))
            except OSError as exc:  # o arquivo so serve ao proximo boot do go2rtc; a API a quente segue valendo
                logger.warning("Nao consegui gravar %s (%s)", self._streams_yaml.name, type(exc).__name__)

            client = self._client_factory()
            try:
                try:
                    vivos = set(client.listar())
                    resumo.go2rtc_online = True
                except Go2rtcIndisponivel as exc:
                    resumo.detalhe = str(exc)
                    for nome, (camera, url) in desejados.items():
                        hash_atual = hashlib.sha256(url.encode()).hexdigest()
                        if camera.go2rtc_sync_hash == hash_atual:
                            camera.go2rtc_sync, camera.go2rtc_sync_erro = "ok", None
                            resumo.ok += 1
                        else:
                            camera.go2rtc_sync, camera.go2rtc_sync_erro = "pendente", resumo.detalhe
                            resumo.pendente += 1
                    return resumo

                for nome, (camera, url) in desejados.items():
                    hash_atual = hashlib.sha256(url.encode()).hexdigest()
                    try:
                        if nome not in vivos or camera.go2rtc_sync_hash != hash_atual:
                            if nome in vivos:
                                client.remover(nome)
                            client.criar(nome, url)
                        camera.go2rtc_sync, camera.go2rtc_sync_erro, camera.go2rtc_sync_hash = "ok", None, hash_atual
                        resumo.ok += 1
                    except Go2rtcIndisponivel as exc:
                        camera.go2rtc_sync, camera.go2rtc_sync_erro = "erro", str(exc)[:200]
                        resumo.erro += 1

                for orfao in sorted(n for n in vivos if n.startswith(PREFIXO_STREAM) and n not in desejados):
                    try:
                        client.remover(orfao)
                        resumo.removidos += 1
                    except Go2rtcIndisponivel:
                        logger.warning("Nao consegui remover stream orfao %s", orfao)
            finally:
                client.close()
        return resumo

    def sondar(self) -> tuple[bool, int]:
        """(API respondeu, total de streams no go2rtc). Somente leitura; nunca levanta."""
        client = self._client_factory()
        try:
            return True, len(client.listar())
        except Go2rtcIndisponivel:
            return False, 0
        finally:
            client.close()

    def nomes_vivos(self) -> list[str]:
        """Streams existentes no go2rtc que o painel NAO gerencia (candidatos a camera de teste)."""
        client = self._client_factory()
        try:
            return sorted(n for n in client.listar() if not n.startswith(PREFIXO_STREAM))
        except Go2rtcIndisponivel:
            return []
        finally:
            client.close()


def resolver_executavel(configurado: str, runtime_dir: Path) -> Path | None:
    candidatos = []
    if configurado.strip():
        candidatos.append(Path(configurado.strip()))
    candidatos.append(runtime_dir / "go2rtc" / ("go2rtc.exe" if sys.platform == "win32" else "go2rtc"))
    for candidato in candidatos:
        if candidato.is_file():
            return candidato
    achado = shutil.which("go2rtc")
    return Path(achado) if achado else None


@dataclass
class Go2rtcProcesso:
    """Mantem o go2rtc vivo como subprocesso (Windows agora; no Linux o systemd pode assumir).

    Estados: `externo` (ja respondia, nao e nosso), `rodando`, `sem_executavel`, `erro`, `parado`.
    """

    runtime_dir: Path
    configurado_exe: Callable[[], str]
    client_factory: Callable[[], Go2rtcClient]
    popen: Callable[..., Any] = subprocess.Popen
    configurado_rtsp: Callable[[], str] = lambda: "rtsp://127.0.0.1:8554"      # noqa: E731 -- "Base RTSP" das Configuracoes
    configurado_api: Callable[[], str] = lambda: "http://127.0.0.1:1984"       # noqa: E731
    porta_ocupada: Callable[[str, int], bool] = porta_em_uso
    intervalo_minimo_reinicio: float = 10.0
    relogio: Callable[[], float] = time.monotonic
    estado: str = "parado"
    detalhe: str = ""
    _proc: Any = field(default=None, repr=False)
    _ultimo_spawn: float = field(default=-1e9, repr=False)

    @property
    def dir(self) -> Path:
        return self.runtime_dir / "go2rtc"

    @property
    def base_yaml(self) -> Path:
        return self.dir / "go2rtc.yaml"

    @property
    def streams_yaml(self) -> Path:
        return self.dir / "streams.yaml"

    def _api_responde(self) -> bool:
        client = self.client_factory()
        try:
            client.listar()
            return True
        except Go2rtcIndisponivel:
            return False
        finally:
            client.close()

    def garantir(self) -> str:
        if self._proc is not None and self._proc.poll() is None:
            self.estado = "rodando"
            return self.estado
        if self._proc is not None:
            self.detalhe = f"go2rtc encerrou (codigo {self._proc.returncode})"
            self._proc = None
        if self._api_responde():
            self.estado, self.detalhe = "externo", "go2rtc ja estava em execucao fora do painel"
            return self.estado
        exe = resolver_executavel(self.configurado_exe(), self.runtime_dir)
        if exe is None:
            self.estado = "sem_executavel"
            self.detalhe = f"Coloque o go2rtc em {self.dir} ou informe o caminho em Configuracoes."
            return self.estado
        if self.relogio() - self._ultimo_spawn < self.intervalo_minimo_reinicio:
            return self.estado
        self._ultimo_spawn = self.relogio()
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.base_yaml.exists():
            self.base_yaml.write_text(render_base_yaml(self.configurado_api(), self.configurado_rtsp()), encoding="utf-8")
        host, porta = _host_porta(self.configurado_rtsp(), 8554)
        if self.porta_ocupada(host, porta):
            self.estado = "erro"
            self.detalhe = (f"A porta RTSP {porta} ja esta em uso por outro programa (ex.: o MediaMTX do SimulaterDSA). Em Configuracoes, mude a "
                            f"'Base RTSP' para outra porta (ex.: rtsp://127.0.0.1:{porta + 2}) e, se ja existir, ajuste 'rtsp: listen' em {self.base_yaml}.")
            return self.estado
        if not self.streams_yaml.exists():
            escrever_atomico(self.streams_yaml, render_streams_yaml({}))
        try:
            log = (self.dir / "go2rtc.log").open("ab")
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self._proc = self.popen(
                [str(exe), "-config", str(self.base_yaml), "-config", str(self.streams_yaml)],
                cwd=str(self.dir),
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
            self.estado, self.detalhe = "rodando", f"pid {self._proc.pid}"
        except OSError as exc:
            self.estado, self.detalhe = "erro", f"Nao consegui iniciar o go2rtc ({type(exc).__name__})"
        return self.estado

    def parar(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            proc.kill()
        self.estado = "parado"
