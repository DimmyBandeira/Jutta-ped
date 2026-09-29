from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from src.jutta_ped.painel.models import Configuracao

# chave -> (padrao, tipo)
CAMPOS: dict[str, tuple[Any, type]] = {
    "go2rtc_api_url": ("http://127.0.0.1:1984", str),
    "go2rtc_public_url": ("http://127.0.0.1:1984", str),
    "go2rtc_rtsp_url": ("rtsp://127.0.0.1:8554", str),
    "go2rtc_exe": ("", str),
    "cooldown_seconds": (120, int),
    "evidence_retention_days": (30, int),
    "public_base_url": ("", str),
    "max_cameras_ia": (4, int),
}

_URLS_HTTP = {"go2rtc_api_url", "go2rtc_public_url"}
_LIMITES = {"cooldown_seconds": (0, 3600), "evidence_retention_days": (1, 3650), "max_cameras_ia": (1, 64)}


def ler_config(db: Session) -> dict[str, Any]:
    gravados = {c.chave: c.valor for c in db.query(Configuracao).all()}
    resultado: dict[str, Any] = {}
    for chave, (padrao, tipo) in CAMPOS.items():
        bruto = gravados.get(chave)
        if bruto is None:
            resultado[chave] = padrao
            continue
        try:
            resultado[chave] = tipo(bruto)
        except ValueError:
            resultado[chave] = padrao
    return resultado


def validar_config(dados: dict[str, str]) -> tuple[dict[str, Any], str | None]:
    """Converte e valida o formulario. Devolve (valores, erro)."""
    valores: dict[str, Any] = {}
    for chave, (padrao, tipo) in CAMPOS.items():
        bruto = (dados.get(chave) or "").strip()
        if tipo is int:
            try:
                numero = int(bruto)
            except ValueError:
                return {}, f"Valor invalido em '{chave}'."
            minimo, maximo = _LIMITES[chave]
            if not minimo <= numero <= maximo:
                return {}, f"'{chave}' deve estar entre {minimo} e {maximo}."
            valores[chave] = numero
            continue
        if chave in _URLS_HTTP and not bruto.startswith(("http://", "https://")):
            return {}, "Enderecos do go2rtc devem comecar com http:// ou https://."
        if chave == "go2rtc_rtsp_url" and not bruto.startswith(("rtsp://", "rtsps://")):
            return {}, "A base RTSP deve comecar com rtsp://."
        if chave == "public_base_url" and bruto and not bruto.startswith(("http://", "https://")):
            return {}, "O endereco publico deve comecar com http:// ou https://."
        valores[chave] = bruto.rstrip("/") if chave.endswith("_url") else bruto
    return valores, None


def salvar_config(db: Session, valores: dict[str, Any]) -> None:
    for chave, valor in valores.items():
        registro = db.get(Configuracao, chave)
        if registro is None:
            db.add(Configuracao(chave=chave, valor=str(valor)))
        else:
            registro.valor = str(valor)
