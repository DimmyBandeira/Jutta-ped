"""Hashes canonicos dos pesos (SHA-256). Integridade apenas: NAO prova autoria.

* Arquivo unico (`pt`, `onnx`): SHA-256 dos bytes.
* Pasta (`openvino`): algoritmo `jutta-dir-v1`, reproduzivel em qualquer sistema:

    para cada arquivo regular da pasta, ordenado pelos BYTES UTF-8 do caminho relativo:
        registro = b"F" + uint32_BE(len(caminho)) + caminho_utf8 + uint64_BE(tamanho) + sha256(conteudo)  (32 bytes)
    hash = SHA-256( concatenacao dos registros )

  O caminho e relativo a pasta, com '/' como separador e normalizado em Unicode NFC. Pastas vazias nao
  entram; links simbolicos sao proibidos (o validador os recusa antes). Os comprimentos delimitam cada campo,
  entao nomes diferentes nunca produzem a mesma sequencia de bytes. A ordem de listagem do sistema de
  arquivos (ou das entradas do ZIP) nao influencia o resultado.
"""
from __future__ import annotations

import hashlib
import struct
import unicodedata
from collections.abc import Callable, Iterable

ALGORITMO_PASTA = "jutta-dir-v1"


def sha256_blocos(blocos: Iterable[bytes]) -> str:
    h = hashlib.sha256()
    for b in blocos:
        h.update(b)
    return h.hexdigest()


def hash_pasta(arquivos: Iterable[tuple[str, int, Callable[[], Iterable[bytes]]]]) -> str:
    """`arquivos`: (caminho_relativo, tamanho, funcao_que_devolve_os_blocos_do_conteudo)."""
    registros = []
    for caminho, tamanho, blocos in arquivos:
        nome = unicodedata.normalize("NFC", caminho).encode("utf-8")
        registros.append((nome, tamanho, blocos))
    registros.sort(key=lambda r: r[0])
    h = hashlib.sha256()
    for nome, tamanho, blocos in registros:
        conteudo = hashlib.sha256()
        lido = 0
        for b in blocos():
            conteudo.update(b)
            lido += len(b)
        if lido != tamanho:
            raise ValueError(f"tamanho divergente em {nome.decode('utf-8', 'replace')}: declarado {tamanho}, lido {lido}")
        h.update(b"F" + struct.pack(">I", len(nome)) + nome + struct.pack(">Q", tamanho) + conteudo.digest())
    return h.hexdigest()
