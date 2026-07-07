from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.jutta_ped.service.runtime import PediatriaSessionManager, SessionStartConfig

app = FastAPI(
    title="Jutta Ped Local Service",
    version="0.1.0",
    description="Servico local image-only para analise pediatrica por camera.",
)
manager = PediatriaSessionManager()


class StartSessionRequest(BaseModel):
    source: str | int = Field(..., description="Arquivo, URL de camera ou indice USB.")
    camera_id: str | None = Field(default=None, description="Nome operacional da camera.")
    force_cpu: bool = False
    modo_coleta: bool = False
    requested_device: str = "auto"
    diagnostic_log_interval_frames: int = Field(default=60, ge=1, le=3600)
    cooldown_seconds: float = Field(default=120.0, ge=0.0, le=3600.0)
    report_dir: str | None = Field(default=None, description="Diretorio de saida operacional.")


class CameraEnableRequest(BaseModel):
    source: str | int = Field(..., description="Arquivo, URL de camera ou indice USB.")
    camera_id: str | None = Field(default=None, description="Nome operacional da camera.")
    force_cpu: bool = False
    modo_coleta: bool = False
    requested_device: str = "auto"
    diagnostic_log_interval_frames: int = Field(default=60, ge=1, le=3600)
    cooldown_seconds: float = Field(default=120.0, ge=0.0, le=3600.0)
    report_dir: str | None = Field(default=None, description="Diretorio de saida operacional.")


def _config_from_camera_request(payload: CameraEnableRequest) -> SessionStartConfig:
    return SessionStartConfig(
        source=payload.source,
        camera_id=payload.camera_id,
        force_cpu=payload.force_cpu,
        modo_coleta=payload.modo_coleta,
        requested_device=payload.requested_device,
        report_dir=Path(payload.report_dir) if payload.report_dir else SessionStartConfig.report_dir,
        diagnostic_log_interval_frames=payload.diagnostic_log_interval_frames,
        cooldown_seconds=payload.cooldown_seconds,
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/sessions/start")
def start_session(payload: StartSessionRequest) -> dict[str, Any]:
    try:
        config = SessionStartConfig(
            source=payload.source,
            camera_id=payload.camera_id,
            force_cpu=payload.force_cpu,
            modo_coleta=payload.modo_coleta,
            requested_device=payload.requested_device,
            report_dir=Path(payload.report_dir) if payload.report_dir else SessionStartConfig.report_dir,
            diagnostic_log_interval_frames=payload.diagnostic_log_interval_frames,
            cooldown_seconds=payload.cooldown_seconds,
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
