"""Limite de tamanho do corpo de uploads, aplicado ANTES do parser multipart.

Uma dependencia do FastAPI nao serve: o FastAPI le e interpreta o corpo (multipart) antes de resolver as
dependencias, entao o 413 saia tarde, depois de o arquivo inteiro ja ter sido recebido e gravado em disco
temporario. Este middleware ASGI puro:

* recusa na hora (sem tocar no corpo) quando `Content-Length` declarado excede o teto;
* sem `Content-Length` (transferencia em pedacos) ou com cabecalho mentiroso, conta os bytes que chegam e, ao
  passar do teto, responde 413 e para de entregar o corpo ao aplicativo (entrega `http.disconnect`), interrompendo
  a leitura; nenhum byte alem do teto chega ao parser.

O teto e lido de `app.state.<chave_estado>` a cada requisicao (permite ajustar sem reiniciar).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


class LimiteCorpoUpload:
    def __init__(self, app: Callable, *, caminho: str, chave_estado: str, padrao: int) -> None:
        self.app, self.caminho, self.chave_estado, self.padrao = app, caminho, chave_estado, padrao

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != self.caminho:
            await self.app(scope, receive, send)
            return
        estado = getattr(scope.get("app"), "state", None)
        teto = int(getattr(estado, self.chave_estado, self.padrao))
        declarado = dict(scope["headers"]).get(b"content-length", b"")
        if declarado.isdigit() and int(declarado) > teto:
            await self._recusar(send, teto)
            return

        total = 0
        encerrado = False

        async def receive_limitado() -> Message:
            nonlocal total, encerrado
            if encerrado:
                return {"type": "http.disconnect"}
            mensagem = await receive()
            if mensagem["type"] == "http.request":
                total += len(mensagem.get("body", b""))
                if total > teto:
                    encerrado = True
                    await self._recusar(send, teto)
                    return {"type": "http.disconnect"}      # o aplicativo para de ler; nada alem do teto e entregue
            return mensagem

        async def send_filtrado(mensagem: Message) -> None:
            if not encerrado:                                 # depois do 413 o aplicativo nao pode responder de novo
                await send(mensagem)

        await self.app(scope, receive_limitado, send_filtrado)

    @staticmethod
    async def _recusar(send: Send, teto: int) -> None:
        corpo = f"Arquivo maior que o limite de {teto // (1024 * 1024)} MiB.".encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8"), (b"content-length", str(len(corpo)).encode()),
                                (b"connection", b"close")]})
        await send({"type": "http.response.body", "body": corpo})
