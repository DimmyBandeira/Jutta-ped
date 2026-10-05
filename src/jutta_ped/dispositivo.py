"""Contrato de dispositivo para modelos OpenVINO executados pelo Ultralytics.

Defeito de origem: o Ultralytics so escolhe o dispositivo OpenVINO pelo texto `intel:<nome>`; qualquer outro valor (inclusive
`device="cpu"`) vira `AUTO`, e o OpenVINO pode entao compilar o modelo na GPU. Um pedido explicito de CPU nao era honrado, e um
`intel:gpu` sem o nome exato (`GPU.0`) caia em `AUTO` com um aviso. Aqui:

* pedido **explicito** -> o dispositivo pedido ou `DispositivoIndisponivel` com a explicacao; nunca troca em silencio;
* **fallback** so com `permitir_fallback=True`, e sempre devolvido em `Resolucao.fallback` (quem chama deve registrar/mostrar);
* o dispositivo **efetivo** (`EXECUTION_DEVICES` do modelo compilado) e lido e conferido depois da primeira inferencia.

Modulo folha (so biblioteca padrao; `openvino`/`torch` sao importados sob demanda).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class DispositivoIndisponivel(RuntimeError):
    """O dispositivo explicitamente pedido nao existe ou o modelo executou em outro."""


@dataclass(frozen=True)
class Resolucao:
    pedido: str
    device_ultralytics: str          # valor para `device=` das chamadas (`intel:<NOME>`)
    nome_openvino: str               # nome no OpenVINO, ex.: "CPU", "GPU.0"
    fallback: str | None = None      # motivo, se o pedido foi trocado (so com permitir_fallback)


def disponiveis_openvino() -> list[str]:
    import openvino as ov

    return list(ov.Core().available_devices)


def _normalizar(pedido: str | None) -> str:
    p = str(pedido or "").strip().lower()
    return "cpu" if p in {"", "auto"} else p


def resolver_openvino(pedido: str | None, disponiveis: list[str] | None = None, *, permitir_fallback: bool = False) -> Resolucao:
    """`cpu` -> CPU; `intel:gpu`/`gpu` -> primeira GPU (ou `intel:gpu.N` exata); `intel:npu` etc. Indisponivel: erro (ou CPU, informado)."""
    p = _normalizar(pedido)
    d = list(disponiveis) if disponiveis is not None else disponiveis_openvino()
    if p == "cpu" or p == "intel:cpu":
        alvo = "CPU"
    else:
        base = p.split(":", 1)[1] if p.startswith("intel:") else p
        if base.startswith("cuda"):
            raise DispositivoIndisponivel(f"'{pedido}' nao e um dispositivo OpenVINO (modelo OpenVINO nao executa em CUDA)")
        alvo = base.upper()
        if alvo in d:
            pass
        else:
            candidatos = sorted(x for x in d if x.upper().startswith(alvo.split(".")[0]) and "." in x) if "." not in alvo else []
            alvo = candidatos[0] if candidatos else alvo
    if alvo in d:
        return Resolucao(str(pedido), f"intel:{alvo}", alvo)
    motivo = f"dispositivo '{pedido}' indisponivel (OpenVINO oferece: {', '.join(d) or 'nenhum'})"
    if not permitir_fallback:
        raise DispositivoIndisponivel(motivo)
    return Resolucao(str(pedido), "intel:CPU", "CPU", fallback=f"{motivo}; executando em CPU (fallback permitido)")


def dispositivos_efetivos(modelo_ultralytics: Any) -> list[str] | None:
    """`EXECUTION_DEVICES` do modelo OpenVINO compilado (so existe depois de uma inferencia). None se nao for possivel saber."""
    try:
        compilado = getattr(modelo_ultralytics.predictor.model, "ov_compiled_model", None)
        return [str(x) for x in compilado.get_property("EXECUTION_DEVICES")] if compilado is not None else None
    except Exception:  # noqa: BLE001 -- sem inferencia previa / backend diferente: nao se sabe
        return None


def identidade(nome: str) -> str:
    """Identidade COMPLETA de um dispositivo OpenVINO: `(CPU)` -> `CPU`, ` gpu.0 ` -> `GPU.0`. Sem prefixo nem substring."""
    return str(nome).strip().strip("()").strip().upper()


def conferir_efetivo(modelo_ultralytics: Any, resolucao: Resolucao, *, estrito: bool = True) -> list[str] | None:
    """Confere que o modelo compilado executa EXATAMENTE no dispositivo resolvido: o conjunto efetivo tem de ser so ele
    (`["CPU", "GPU.0"]` nao serve para CPU; `GPU.01` nao e `GPU.0`). Divergencia = `DispositivoIndisponivel` sempre.
    Telemetria ausente: no modo estrito tambem e erro (nao se declara conferido o que nao se conseguiu ler); fora dele devolve None,
    e quem chama NAO pode marcar o dispositivo como conferido."""
    efetivo = dispositivos_efetivos(modelo_ultralytics)
    alvo = identidade(resolucao.nome_openvino)
    if efetivo is None:
        if estrito:
            raise DispositivoIndisponivel(f"pedido '{resolucao.pedido}' ({alvo}): nao foi possivel ler EXECUTION_DEVICES do modelo apos a "
                                          "inferencia; o dispositivo efetivo nao pode ser conferido")
        return None
    if {identidade(e) for e in efetivo} != {alvo}:
        raise DispositivoIndisponivel(f"pedido '{resolucao.pedido}' ({alvo}) mas o modelo executou em {efetivo}")
    return efetivo


class ModeloOpenVinoEstrito:
    """Envolve um `YOLO(<pasta openvino>)`: traduz `device=` pelo contrato acima em `track`/`predict` e confere o efetivo na 1a chamada
    de cada dispositivo. Todo o resto e delegado (`names`, `overrides`, `predictor`...). Para modelos `.pt` nao se usa."""

    def __init__(self, modelo: Any, *, permitir_fallback: bool = False, disponiveis: list[str] | None = None) -> None:
        self._m, self._permitir, self._disponiveis = modelo, permitir_fallback, disponiveis
        self._resolvidos: dict[str, Resolucao] = {}
        self._conferidos: dict[str, list[str]] = {}       # so entra aqui o que foi CONFERIDO com telemetria lida
        self.fallbacks: list[str] = []
        self.nao_conferidos = 0                           # chamadas sem telemetria fora do modo estrito (nunca viram "conferido")

    def __getattr__(self, nome: str) -> Any:
        return getattr(self._m, nome)

    def _chamar(self, metodo: str, args: tuple, kwargs: dict[str, Any]) -> Any:
        pedido = _normalizar(kwargs.get("device"))
        r = self._resolvidos.get(pedido)
        if r is None:
            r = self._resolvidos[pedido] = resolver_openvino(pedido, self._disponiveis, permitir_fallback=self._permitir)
            if r.fallback:
                self.fallbacks.append(r.fallback)
        kwargs = {**kwargs, "device": r.device_ultralytics}
        saida = getattr(self._m, metodo)(*args, **kwargs)
        if pedido not in self._conferidos:
            # estrito = sem fallback permitido: telemetria ausente e erro. Com fallback permitido, segue sem marcar e tenta de novo depois.
            efetivo = conferir_efetivo(self._m, r, estrito=not self._permitir)
            if efetivo is None:
                self.nao_conferidos += 1
            else:
                self._conferidos[pedido] = efetivo
        return saida

    def track(self, *args: Any, **kwargs: Any) -> Any:
        return self._chamar("track", args, kwargs)

    def predict(self, *args: Any, **kwargs: Any) -> Any:
        return self._chamar("predict", args, kwargs)
