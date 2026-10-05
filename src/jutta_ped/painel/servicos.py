from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from src.jutta_ped.painel.config_service import ler_config
from src.jutta_ped.painel.db import Database
from src.jutta_ped.painel.go2rtc import PREFIXO_STREAM, Go2rtcProcesso, Go2rtcSync, nome_stream
from src.jutta_ped.painel.models import Camera

logger = logging.getLogger("jutta_ped.painel.servicos")


class LacoPeriodico:
    """Thread daemon que roda `fn` a cada `intervalo` s, ou logo que `acordar()` for chamado."""

    def __init__(self, nome: str, fn: Callable[[], None], intervalo: float) -> None:
        self.nome, self._fn, self._intervalo = nome, fn, intervalo
        self._parar = threading.Event()
        self._acordar = threading.Event()
        self._thread: threading.Thread | None = None

    def iniciar(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._rodar, name=self.nome, daemon=True)
        self._thread.start()

    def acordar(self) -> None:
        self._acordar.set()

    def parar(self, timeout: float = 10.0) -> None:
        self._parar.set()
        self._acordar.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _rodar(self) -> None:
        while not self._parar.is_set():
            try:
                self._fn()
            except Exception:  # noqa: BLE001 -- o laco nunca pode morrer
                logger.exception("Falha no laco %s", self.nome)
            self._acordar.wait(self._intervalo)
            self._acordar.clear()


class GerenteIa(Protocol):
    """Parte de `PediatriaSessionManager` usada pelo orquestrador (facilita os testes)."""

    def enable_camera(self, config: Any) -> dict[str, Any]: ...
    def disable_camera(self, camera_id: str) -> dict[str, Any]: ...
    def camera_status(self, camera_id: str) -> dict[str, Any]: ...
    def cameras(self) -> list[dict[str, Any]]: ...
    def overlay_latest(self, instance_id: str) -> dict[str, Any]: ...


@dataclass
class _Tentativa:
    falhas: int = 0
    proxima_em: float = 0.0


@dataclass
class IaOrquestrador:
    """Mantem as sessoes de IA alinhadas com o banco (`camera.ia_pediatria`).

    - Roda fora das requisicoes: carregar modelo leva segundos e nao pode travar a UI.
    - So inicia a IA quando o stream da camera ja esta sincronizado no go2rtc; ela le o
      restream do go2rtc (uma unica conexao com a camera).
    - Partidas escalonadas (`intervalo_partida`) e limite de cameras (`max_cameras_ia`).
    - Falha de partida: novo intento com backoff exponencial (ate 5 min), isolado por camera.
    """

    database: Database
    gerente: GerenteIa
    config_loader: Callable[[], dict[str, Any]]
    config_factory: Callable[..., Any]
    intervalo_partida: float = 3.0
    # Entrega a PORTA de sombra da camera (sempre o mesmo objeto) no INICIO da sessao: ligar/desligar a sombra depois
    # troca o alvo da porta e nao reinicia a analise.
    sombra_provider: Callable[[int], Any] | None = None
    relogio: Callable[[], float] = time.monotonic
    dormir: Callable[[float], None] = time.sleep
    _estado: dict[int, dict[str, str | None]] = field(default_factory=dict)
    _tentativas: dict[int, _Tentativa] = field(default_factory=dict)
    _reiniciar: set[int] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def reiniciar(self, camera_id: int) -> None:
        """Pede parar+subir a IA da camera (ex.: apos editar). Executado no proximo ciclo."""
        with self._lock:
            self._reiniciar.add(camera_id)

    def estado(self, camera_id: int) -> dict[str, str | None]:
        with self._lock:
            return dict(self._estado.get(camera_id) or {"estado": "desligada", "erro": None})

    def _definir(self, camera_id: int, estado: str, erro: str | None = None) -> None:
        with self._lock:
            self._estado[camera_id] = {"estado": estado, "erro": erro}

    def reconcile(self) -> None:
        cfg = self.config_loader()
        with self.database.session() as db:
            todas = db.query(Camera).order_by(Camera.id).all()
            candidatas = [(c.id, nome_stream(c), c.go2rtc_sync, c.ia_pediatria) for c in todas]
        existentes = {cid for cid, *_ in candidatas}

        with self._lock:
            reiniciar, self._reiniciar = set(self._reiniciar), set()
        for camera_id in reiniciar:
            self._parar(camera_id)
            self._tentativas.pop(camera_id, None)

        # Sessoes de cameras que sairam do banco ou desligaram a IA
        querem = [cid for cid, _n, _s, ia in candidatas if ia]
        limite = int(cfg["max_cameras_ia"])
        desejadas = set(querem[:limite])
        for cid in querem[limite:]:
            self._definir(cid, "limite", f"Limite de {limite} câmeras com IA atingido.")
        for item in self.gerente.cameras():
            cam = str(item.get("camera_id") or "")
            if not cam.startswith(PREFIXO_STREAM):
                continue
            try:
                cid = int(cam[len(PREFIXO_STREAM):])
            except ValueError:
                continue
            if (cid not in desejadas or cid not in existentes) and item.get("active"):
                self._parar(cid)
        with self._lock:
            for cid in list(self._estado):
                if cid not in desejadas and self._estado[cid]["estado"] != "limite":
                    del self._estado[cid]

        primeira = True
        for cid, stream, sync, _ia in candidatas:
            if cid not in desejadas:
                continue
            if self._status_ativo(cid):
                self._definir(cid, "rodando")
                self._tentativas.pop(cid, None)
                continue
            if sync != "ok":
                self._definir(cid, "aguardando_stream")
                continue
            tentativa = self._tentativas.setdefault(cid, _Tentativa())
            if self.relogio() < tentativa.proxima_em:
                continue
            if not primeira:
                self.dormir(self.intervalo_partida)  # escalona partidas para nao saturar CPU/GPU
            primeira = False
            self._iniciar(cid, stream, cfg, tentativa)

    def parar_todas(self) -> None:
        """Encerramento do servico: para TODA sessao de IA (com join das threads) antes de o
        interpretador finalizar, em vez de deixar threads daemon dentro de codigo nativo de inferencia
        (OpenVINO/ffmpeg) sendo derrubadas durante a finalizacao do Python."""
        try:
            itens = list(self.gerente.cameras())
        except Exception:  # noqa: BLE001
            return
        for item in itens:
            if not item.get("active"):
                continue
            try:
                self.gerente.disable_camera(str(item["camera_id"]))
            except Exception:  # noqa: BLE001
                logger.exception("Falha ao parar a sessao %s no encerramento", item.get("camera_id"))
        with self._lock:
            self._estado.clear()

    def _status_ativo(self, camera_id: int) -> bool:
        return bool(self.gerente.camera_status(f"{PREFIXO_STREAM}{camera_id}").get("active"))

    def _parar(self, camera_id: int) -> None:
        try:
            self.gerente.disable_camera(f"{PREFIXO_STREAM}{camera_id}")
        except Exception:  # noqa: BLE001
            logger.exception("Falha ao parar IA da camera %s", camera_id)
        self._estado.pop(camera_id, None)

    def _iniciar(self, camera_id: int, stream: str | None, cfg: dict[str, Any], tentativa: _Tentativa) -> None:
        self._definir(camera_id, "iniciando")
        origem = f"{str(cfg['go2rtc_rtsp_url']).rstrip('/')}/{stream}"
        try:
            extras: dict[str, Any] = {}
            if self.sombra_provider is not None:
                extras["motor_sombra"] = self.sombra_provider(camera_id)
            self.gerente.enable_camera(
                self.config_factory(
                    source=origem,
                    camera_id=f"{PREFIXO_STREAM}{camera_id}",
                    stream_id=stream,
                    cooldown_seconds=float(cfg["cooldown_seconds"]),
                    **extras,
                )
            )
        except Exception as exc:  # noqa: BLE001 -- isolado por camera
            tentativa.falhas += 1
            tentativa.proxima_em = self.relogio() + min(300.0, 5.0 * 2 ** (tentativa.falhas - 1))
            self._definir(camera_id, "erro", f"{type(exc).__name__}: {exc}"[:200])
            logger.warning("Partida da IA falhou (camera %s, tentativa %s): %s", camera_id, tentativa.falhas, type(exc).__name__)
            return
        tentativa.falhas = 0
        self._definir(camera_id, "rodando")


@dataclass
class PainelServicos:
    """Agrupa os servicos de fundo do painel (go2rtc + IA) para o ciclo de vida do app."""

    database: Database
    sync: Go2rtcSync
    go2rtc: Go2rtcProcesso
    ia: IaOrquestrador | None
    runtime_dir: Path
    intervalo_go2rtc: float = 15.0
    intervalo_ia: float = 10.0
    intervalo_sombra: float = 10.0
    cifra: Any = None
    eventos: Any = None
    sombra: Any = None                  # GerenteSombra
    executor: Any = None                # Executor compartilhado (sombra)
    iniciado_em: datetime = field(default_factory=datetime.now)
    _lacos: list[LacoPeriodico] = field(default_factory=list)
    # Ultima sondagem da API do go2rtc, feita no laco de fundo (nunca na renderizacao): {"api_ok", "streams", "em" (monotonic)}.
    _go2rtc_saude: dict[str, Any] | None = None
    relogio: Callable[[], float] = time.monotonic

    def _ciclo_go2rtc(self) -> None:
        self.go2rtc.garantir()
        self.sync.reconcile()
        try:
            api_ok, streams = self.sync.sondar()
        except Exception:  # noqa: BLE001 -- sondagem nunca derruba o laco
            api_ok, streams = False, 0
        self._go2rtc_saude = {"api_ok": bool(api_ok), "streams": int(streams), "em": self.relogio()}

    def go2rtc_estado(self) -> tuple[str, float | None]:
        """(estado, idade da sondagem em s): `ok` | `inacessivel` | `desatualizado` | `desconhecido`. Le o cache; nao sonda.
        `ok` so diz que a API do go2rtc respondeu: nao comprova produtor conectado nem quadros chegando."""
        saude = self._go2rtc_saude
        if saude is None:
            return "desconhecido", None
        idade = max(0.0, self.relogio() - saude["em"])
        if idade > 3 * max(self.intervalo_go2rtc, 1.0):
            return "desatualizado", idade
        return ("ok" if saude["api_ok"] else "inacessivel"), idade

    def iniciar(self) -> None:
        self._lacos = [LacoPeriodico("painel-go2rtc", self._ciclo_go2rtc, self.intervalo_go2rtc)]
        if self.ia is not None:
            self._lacos.append(LacoPeriodico("painel-ia", self.ia.reconcile, self.intervalo_ia))
        if self.sombra is not None:
            self._lacos.append(LacoPeriodico("painel-sombra", self.sombra.reconcile, self.intervalo_sombra))
        for laco in self._lacos:
            laco.iniciar()

    def acordar(self) -> None:
        """Chamado DEPOIS de qualquer mutacao commitada: invalida snapshots antigos da sombra e acorda os lacos."""
        if self.sombra is not None:
            self.sombra.invalidar()
        for laco in self._lacos:
            laco.acordar()

    def parar(self) -> None:
        for laco in self._lacos:          # primeiro os lacos: o orquestrador nao pode religar a IA
            laco.parar()
        if self.sombra is not None:       # nenhum despacho de sombra sobrevive ao servico
            self.sombra.parar_tudo()
        if self.ia is not None:
            self.ia.parar_todas()
        self.go2rtc.parar()


def config_loader_para(database: Database) -> Callable[[], dict[str, Any]]:
    def _ler() -> dict[str, Any]:
        with database.session() as db:
            return ler_config(db)

    return _ler
