from __future__ import annotations

from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class Cifra:
    """Cifra simetrica (Fernet) para segredos guardados no banco (senhas de camera, tokens de destino)."""

    def __init__(self, chave: bytes) -> None:
        self._fernet = Fernet(chave)

    @classmethod
    def carregar_ou_criar(cls, path: Path) -> "Cifra":
        if path.exists():
            chave = path.read_bytes().strip()
            if chave:
                return cls(chave)
        path.parent.mkdir(parents=True, exist_ok=True)
        chave = Fernet.generate_key()
        path.write_bytes(chave)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return cls(chave)

    def cifrar(self, texto: str) -> str:
        return self._fernet.encrypt(texto.encode("utf-8")).decode("ascii")

    def decifrar(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise ValueError("Segredo ilegivel (chave de cifra diferente da usada para gravar).") from exc
