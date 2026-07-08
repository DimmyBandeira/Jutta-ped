from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from src.jutta_ped.service.runtime import PediatriaSessionManager, SessionStartConfig

app = FastAPI(
    title="Jutta Ped Local Service",
    version="0.1.0",
    description="Servico local image-only para analise pediatrica por camera.",
)
manager = PediatriaSessionManager()

ROOT = Path(__file__).resolve().parents[3]
_MANIFEST_PATH = ROOT / "plugin" / "manifest.json"

_STREAM_REF_DESCRIPTION = (
    "Referencia de stream/fonte fornecida pelo mainframe (Core), no modelo "
    "'plugin recebe stream_ref, nao cadastra camera propria' (ver "
    "plugin/README.md). Preferida sobre 'source'. Aceita o mesmo formato de "
    "'source' hoje (arquivo, URL RTSP/HTTP ou indice USB) enquanto o Core "
    "nao resolve stream_ref para um transporte proprio."
)


class _SourceRefMixin(BaseModel):
    source: str | int | None = Field(
        default=None,
        description="Compatibilidade: arquivo, URL de camera ou indice USB. Preterido em favor de stream_ref.",
    )
    stream_ref: str | int | None = Field(default=None, description=_STREAM_REF_DESCRIPTION)

    @model_validator(mode="after")
    def _require_source_or_stream_ref(self) -> "_SourceRefMixin":
        if self.source is None and self.stream_ref is None:
            raise ValueError("Informe 'stream_ref' (preferido) ou 'source'.")
        return self

    @property
    def source_ref(self) -> str | int:
        """Normaliza source/stream_ref num unico valor: stream_ref manda quando presente."""
        return self.stream_ref if self.stream_ref is not None else self.source  # type: ignore[return-value]


_VIDEO_END_POLICIES = {"stop", "loop"}


class _ReconnectPolicyMixin(BaseModel):
    """Politica de recuperacao de falha de leitura da fonte (ver
    `classify_source_kind`/`_handle_read_failure` em runtime.py). Defaults
    preservam o comportamento anterior a esta rodada: reconexao automatica
    ligada para RTSP/USB, arquivo local para (sem loop) no EOF.
    """

    reconnect_enabled: bool = Field(default=True, description="Reconectar automaticamente fontes de rede/dispositivo (RTSP/HTTP/USB) quando a leitura falhar.")
    max_reconnect_attempts: int = Field(default=5, ge=-1, le=100, description="-1 = tentativas ilimitadas.")
    reconnect_backoff_seconds: float = Field(default=2.0, ge=0.1, le=120.0, description="Backoff inicial entre tentativas; dobra a cada tentativa ate o teto abaixo.")
    reconnect_backoff_max_seconds: float = Field(default=30.0, ge=0.1, le=600.0)
    video_end_policy: str = Field(default="stop", description="So se aplica a arquivo local: 'stop' (padrao, encerra no EOF) ou 'loop' (reinicia do frame 0).")

    @model_validator(mode="after")
    def _validate_video_end_policy(self) -> "_ReconnectPolicyMixin":
        if self.video_end_policy not in _VIDEO_END_POLICIES:
            raise ValueError("video_end_policy deve ser 'stop' ou 'loop'.")
        return self


class StartSessionRequest(_SourceRefMixin, _ReconnectPolicyMixin):
    camera_id: str | None = Field(default=None, description="Nome operacional da camera.")
    force_cpu: bool = False
    modo_coleta: bool = False
    requested_device: str = "auto"
    diagnostic_log_interval_frames: int = Field(default=60, ge=1, le=3600)
    cooldown_seconds: float = Field(default=120.0, ge=0.0, le=3600.0)
    report_dir: str | None = Field(default=None, description="Diretorio de saida operacional.")


class CameraEnableRequest(_SourceRefMixin, _ReconnectPolicyMixin):
    camera_id: str | None = Field(default=None, description="Nome operacional da camera.")
    force_cpu: bool = False
    modo_coleta: bool = False
    requested_device: str = "auto"
    diagnostic_log_interval_frames: int = Field(default=60, ge=1, le=3600)
    cooldown_seconds: float = Field(default=120.0, ge=0.0, le=3600.0)
    report_dir: str | None = Field(default=None, description="Diretorio de saida operacional.")


_DATASET_COLLECTION_MODES = {"coleta", "coleta_dataset", "dataset_collection", "collect"}


class InstanceConfig(_ReconnectPolicyMixin):
    """Bloco `config` do contrato de instancia (POST /instances).

    Espelha os campos operacionais ja existentes em StartSessionRequest/
    CameraEnableRequest, sem tocar thresholds/modelos/regras de acuracia.
    """

    force_cpu: bool = False
    mode: str = Field(default="normal", description="Rotulo operacional livre; 'coleta'/'dataset_collection' liga modo_coleta quando modo_coleta nao e informado explicitamente.")
    modo_coleta: bool | None = Field(default=None, description="Override explicito; se omitido, e derivado de 'mode'.")
    requested_device: str = "auto"
    diagnostic_log_interval_frames: int = Field(default=60, ge=1, le=3600)
    cooldown_seconds: float = Field(default=120.0, ge=0.0, le=3600.0)
    report_dir: str | None = None


class InstanceStartRequest(BaseModel):
    """Payload de POST /instances -- contrato de runtime de plugin.

    stream_ref e o campo principal (obrigatorio); 'source' legado continua
    valido apenas nos endpoints antigos (/sessions/start, /cameras/enable).
    """

    camera_id: str = Field(..., min_length=1, description="Nome operacional da camera. Obrigatorio neste contrato.")
    stream_id: str | None = Field(default=None, description="Identidade do stream dentro da camera. Opcional nesta fase.")
    stream_ref: str | int = Field(..., description="Referencia de stream/fonte fornecida pelo Core. Campo principal deste contrato.")
    lease_id: str | None = Field(default=None, description="Lease do WG Runtime Contract. Opcional nesta fase, ja previsto no contrato.")
    config: InstanceConfig = Field(default_factory=InstanceConfig)


def _config_from_instance_request(payload: InstanceStartRequest) -> SessionStartConfig:
    modo_coleta = payload.config.modo_coleta
    if modo_coleta is None:
        modo_coleta = str(payload.config.mode or "normal").strip().lower() in _DATASET_COLLECTION_MODES
    return SessionStartConfig(
        source=payload.stream_ref,
        camera_id=payload.camera_id,
        stream_id=payload.stream_id,
        lease_id=payload.lease_id,
        force_cpu=payload.config.force_cpu,
        modo_coleta=modo_coleta,
        requested_device=payload.config.requested_device,
        report_dir=Path(payload.config.report_dir) if payload.config.report_dir else SessionStartConfig.report_dir,
        diagnostic_log_interval_frames=payload.config.diagnostic_log_interval_frames,
        cooldown_seconds=payload.config.cooldown_seconds,
        reconnect_enabled=payload.config.reconnect_enabled,
        max_reconnect_attempts=payload.config.max_reconnect_attempts,
        reconnect_backoff_seconds=payload.config.reconnect_backoff_seconds,
        reconnect_backoff_max_seconds=payload.config.reconnect_backoff_max_seconds,
        video_end_policy=payload.config.video_end_policy,
    )


def _config_from_camera_request(payload: CameraEnableRequest) -> SessionStartConfig:
    return SessionStartConfig(
        source=payload.source_ref,
        camera_id=payload.camera_id,
        force_cpu=payload.force_cpu,
        modo_coleta=payload.modo_coleta,
        requested_device=payload.requested_device,
        report_dir=Path(payload.report_dir) if payload.report_dir else SessionStartConfig.report_dir,
        diagnostic_log_interval_frames=payload.diagnostic_log_interval_frames,
        cooldown_seconds=payload.cooldown_seconds,
        reconnect_enabled=payload.reconnect_enabled,
        max_reconnect_attempts=payload.max_reconnect_attempts,
        reconnect_backoff_seconds=payload.reconnect_backoff_seconds,
        reconnect_backoff_max_seconds=payload.reconnect_backoff_max_seconds,
        video_end_policy=payload.video_end_policy,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sessions/start")
def start_session(payload: StartSessionRequest) -> dict[str, Any]:
    try:
        config = SessionStartConfig(
            source=payload.source_ref,
            camera_id=payload.camera_id,
            force_cpu=payload.force_cpu,
            modo_coleta=payload.modo_coleta,
            requested_device=payload.requested_device,
            report_dir=Path(payload.report_dir) if payload.report_dir else SessionStartConfig.report_dir,
            diagnostic_log_interval_frames=payload.diagnostic_log_interval_frames,
            cooldown_seconds=payload.cooldown_seconds,
            reconnect_enabled=payload.reconnect_enabled,
            max_reconnect_attempts=payload.max_reconnect_attempts,
            reconnect_backoff_seconds=payload.reconnect_backoff_seconds,
            reconnect_backoff_max_seconds=payload.reconnect_backoff_max_seconds,
            video_end_policy=payload.video_end_policy,
        )
        return manager.start_session(config)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/sessions/{session_id}/stop")
def stop_session(session_id: str) -> dict[str, Any]:
    try:
        return manager.stop_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Sessao nao encontrada") from exc


@app.get("/sessions/{session_id}/status")
def session_status(session_id: str) -> dict[str, Any]:
    try:
        return manager.status(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Sessao nao encontrada") from exc


@app.get("/sessions/{session_id}/summary")
def session_summary(session_id: str) -> dict[str, Any]:
    try:
        return manager.summary(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Sessao nao encontrada") from exc


@app.get("/sessions")
def list_sessions() -> dict[str, Any]:
    return {"sessions": manager.sessions()}


@app.post("/cameras/enable")
def enable_camera(payload: CameraEnableRequest) -> dict[str, Any]:
    try:
        return manager.enable_camera(_config_from_camera_request(payload))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/cameras/{camera_id}/disable")
def disable_camera(camera_id: str) -> dict[str, Any]:
    return manager.disable_camera(camera_id)


@app.get("/cameras/{camera_id}/status")
def camera_status(camera_id: str) -> dict[str, Any]:
    return manager.camera_status(camera_id)


@app.get("/cameras")
def list_cameras() -> dict[str, Any]:
    return {"cameras": manager.cameras()}


# ---------------------------------------------------------------------------
# Contrato de runtime de plugin (ver plugin/manifest.json, plugin/README.md).
# Reaproveita PediatriaSessionManager por baixo: instance_id == session_id.
# Endpoints acima (/sessions/*, /cameras/*) continuam funcionando sem
# alteracao de comportamento -- esta e uma camada nova e compativel.
# ---------------------------------------------------------------------------


@app.get("/plugin/manifest")
def plugin_manifest() -> dict[str, Any]:
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


@app.post("/instances")
def start_instance(payload: InstanceStartRequest) -> dict[str, Any]:
    try:
        config = _config_from_instance_request(payload)
        enabled = manager.enable_camera(config)
        return manager.instance_status(enabled["session_id"])
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/instances")
def list_instances() -> dict[str, Any]:
    return {"instances": manager.instances()}


@app.get("/instances/{instance_id}")
def instance_status(instance_id: str) -> dict[str, Any]:
    try:
        return manager.instance_status(instance_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Instancia nao encontrada") from exc


@app.post("/instances/{instance_id}/stop")
def stop_instance(instance_id: str) -> dict[str, Any]:
    try:
        manager.stop_session(instance_id)
        return manager.instance_status(instance_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Instancia nao encontrada") from exc


@app.get("/instances/{instance_id}/summary")
def instance_summary(instance_id: str) -> dict[str, Any]:
    try:
        summary = manager.summary(instance_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Instancia nao encontrada") from exc
    return {"plugin_id": "ia.pediatria", "instance_id": instance_id, **summary}


@app.get("/instances/{instance_id}/overlay/latest")
def instance_overlay_latest(instance_id: str) -> dict[str, Any]:
    try:
        return manager.overlay_latest(instance_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Instancia nao encontrada") from exc


@app.get("/instances/{instance_id}/events")
def instance_events(instance_id: str, limit: int = 50, event_type: str | None = None) -> dict[str, Any]:
    try:
        events = manager.events(instance_id, limit=limit, event_type=event_type)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Instancia nao encontrada") from exc
    return {"instance_id": instance_id, "events": events}
