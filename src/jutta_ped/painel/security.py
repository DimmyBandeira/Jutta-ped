from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def hash_senha(senha: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(senha.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verificar_senha(senha: str, armazenado: str) -> bool:
    try:
        esquema, n, r, p, salt_hex, digest_hex = armazenado.split("$")
        if esquema != "scrypt":
            return False
        esperado = bytes.fromhex(digest_hex)
        digest = hashlib.scrypt(senha.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=len(esperado))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, esperado)


def novo_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_iguais(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


def carregar_ou_criar_segredo(path: Path) -> str:
    """Chave de assinatura da sessao, persistida para sobreviver a reinicios."""
    if path.exists():
        valor = path.read_text(encoding="utf-8").strip()
        if valor:
            return valor
    path.parent.mkdir(parents=True, exist_ok=True)
    valor = secrets.token_hex(32)
    path.write_text(valor, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return valor


class LimitadorLogin:
    """Trava temporaria apos falhas seguidas (por login+ip). Em memoria, por processo."""

    def __init__(self, max_falhas: int = 5, bloqueio_segundos: float = 60.0, relogio=time.monotonic) -> None:
        self.max_falhas = max_falhas
        self.bloqueio_segundos = bloqueio_segundos
        self._relogio = relogio
        self._falhas: dict[tuple[str, str], tuple[int, float]] = {}

    def bloqueado(self, chave: tuple[str, str]) -> bool:
        falhas, ate = self._falhas.get(chave, (0, 0.0))
        if falhas >= self.max_falhas and self._relogio() < ate:
            return True
        if falhas >= self.max_falhas:
            self._falhas.pop(chave, None)
        return False

    def falhou(self, chave: tuple[str, str]) -> None:
        falhas, _ = self._falhas.get(chave, (0, 0.0))
        self._falhas[chave] = (falhas + 1, self._relogio() + self.bloqueio_segundos)

    def sucesso(self, chave: tuple[str, str]) -> None:
        self._falhas.pop(chave, None)
