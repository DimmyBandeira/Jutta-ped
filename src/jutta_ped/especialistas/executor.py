"""Executor local minimo de especialistas declarativos (protocolo 1).

Resolve a versao ATIVA no registro (via `Instalador`), confere a integridade, carrega os modelos declarados e
processa recortes devolvendo atributos no formato do contrato (manual do especialista, secao 4). Nao conhece a
pediatria: tudo vem do manifesto do pacote ativo. Nao liga alertas, regras, receitas nem camadas de rede.

Ciclo de vida:
* `executar(id, pedidos)` le o registro a cada chamada (barato) e recarrega sozinho quando a versao ativa ou a
  identidade do pacote muda (ativar/reverter). A integridade (validador + hashes + identidade) e conferida ao
  CARREGAR, nao a cada pedido; adulteracao posterior so e vista na proxima carga (falha fechada: nada roda se
  a conferencia falhar).
* Cada pedido tem resultado proprio, na mesma ordem; erro de um pedido nao derruba os outros.

Backends: `pt` e `openvino` via Ultralytics, que aplicam o `entrada.formato` declarado ou RECUSAM a combinacao
(`FormatoNaoSuportado` -> E_MODELO_INDISPONIVEL); `onnx` ainda nao e executavel.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from src.jutta_ped.especialistas.instalador import COD_INTEGRIDADE, ErroInstalacao, Instalador

CONTAGEM_CONFIANCA_MINIMA = 0.25      # atributo `numero` por `contagem`: so conta deteccoes com confianca >= isto

E_DESCONHECIDO = "E_ESPECIALISTA_DESCONHECIDO"
E_VERSAO = "E_VERSAO_INCOMPATIVEL"
E_RECORTE = "E_RECORTE_INVALIDO"
E_MODELO = "E_MODELO_INDISPONIVEL"
E_PRAZO = "E_PRAZO_ESGOTADO"
E_INTEGRIDADE = "E_INTEGRIDADE"
E_PARAMETRO = "E_PARAMETRO_INVALIDO"
E_INTERNO = "E_INTERNO"


class FormatoNaoSuportado(Exception):
    """O backend nao consegue honrar o `entrada.formato` declarado: recusa explicitamente (nao ignora campos)."""


class ErroExecucao(Exception):
    def __init__(self, codigo: str, mensagem: str) -> None:
        super().__init__(mensagem)
        self.codigo, self.mensagem = codigo, mensagem


class ModeloCarregado(Protocol):
    nomes: list[str]
    dispositivo: str

    def inferir(self, imagens: list[Any]) -> list[dict[str, Any]]: ...


class Backend(Protocol):
    def carregar(self, modelo: dict[str, Any], pasta: Path, dispositivo: str, formato: dict[str, Any]) -> ModeloCarregado: ...


@dataclass
class Pedido:
    """Campos do pedido (manual 4.1). `recorte` e a imagem (ndarray BGR do OpenCV); o transporte a codifica fora daqui."""

    pedido_id: str
    recorte: Any
    tentativa: int = 1
    camera_ref: str = ""
    geracao: int = 0
    trilha_id: int = 0
    quadro_id: int = 0
    fonte: str = "visivel"
    altura_pessoa_px: int | None = None
    restante_ms: float | None = None          # orcamento que sobrou na emissao; None = sem prazo
    parametros: dict[str, float] = field(default_factory=dict)
    config_hash: str | None = None


@dataclass
class EspecialistaCarregado:
    id: str
    versao: str
    pasta: Path
    manifesto: dict[str, Any]
    modelos: dict[str, ModeloCarregado]
    hashes: dict[str, str]
    identidade: str


def calcular_config_hash(hashes_modelos: dict[str, str], parametros_efetivos: dict[str, float]) -> str:
    """Identifica pesos + parametros efetivos de uma chamada (manual 4.1, `config_hash`)."""
    canonico = json.dumps({"modelos": dict(sorted(hashes_modelos.items())), "parametros": dict(sorted(parametros_efetivos.items()))},
                          sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def cuda_disponivel() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available() and torch.cuda.device_count() > 0)
    except Exception:  # noqa: BLE001 -- sem torch/CUDA o executor segue em CPU
        return False


def _backends_padrao() -> dict[str, Backend]:
    from src.jutta_ped.especialistas.backend_ultralytics import BackendUltralytics

    b = BackendUltralytics()
    return {"pt": b, "openvino": b}


def contrato_de_manifesto(manifesto: dict[str, Any], pid: str, versao: str, identidade: str) -> dict[str, Any]:
    """Contrato (o que o host precisa saber para despachar e consumir) a partir do manifesto + identidade. Funcao PURA:
    nao carrega modelos nem toca o disco (usada pelo executor e pela validacao leve do painel)."""
    entrada = manifesto["entrada"]
    return {"id": pid, "versao": versao, "identidade": identidade, "hashes": {m["id"]: m["sha256"] for m in manifesto["modelos"]},
            "entrada": {"fonte": entrada["fonte"], "alvo": dict(entrada["alvo"]), "altura_minima_px": entrada.get("altura_minima_px", 0),
                        "lote_max": entrada.get("lote_max", 1)},
            "atributos": {a["nome"]: {"tipo": a["tipo"], "validade_ms": a["validade_ms"], "categorias": list(a.get("categorias", []))} for a in manifesto["atributos"]},
            "parametros": {p["nome"]: {"min": p["min"], "max": p["max"], "padrao": p["padrao"]} for p in manifesto.get("parametros", [])}}


def contrato_leve(instalador: Instalador, pid: str) -> dict[str, Any] | None:
    """Contrato da versao ATIVA lendo so o manifesto (sem verificar hashes nem carregar modelos). None se nao ha ativo
    utilizavel (sem ativo, versao invalida ou manifesto ilegivel)."""
    try:
        ativo = instalador.obter_ativo(pid, verificar=False)
        manifesto = json.loads((ativo.pasta / "manifest.json").read_text(encoding="utf-8"))
        return contrato_de_manifesto(manifesto, ativo.id, ativo.versao, ativo.identidade)
    except (ErroInstalacao, OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


class Executor:
    def __init__(self, instalador: Instalador, backends: dict[str, Backend] | None = None, *,
                 cuda_probe: Callable[[], bool] = cuda_disponivel) -> None:
        self.instalador = instalador
        self._backends = backends
        self._cuda_probe = cuda_probe
        self._carregados: dict[str, EspecialistaCarregado] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ carga

    def _backend(self, formato: str) -> Backend:
        if self._backends is None:
            self._backends = _backends_padrao()
        b = self._backends.get(formato)
        if b is None:
            raise ErroExecucao(E_MODELO, f"formato '{formato}' ainda nao e executavel por este executor")
        return b

    def _candidatos(self, modelo: dict[str, Any]) -> list[str]:
        saida: list[str] = []
        for d in modelo["dispositivos"]:
            if d == "cuda" and modelo["formato"] == "pt" and self._cuda_probe():
                saida.append("cuda:0")
            elif d == "intel_gpu" and modelo["formato"] == "openvino":
                saida.append("intel:gpu")
            elif d == "cpu":
                saida.append("cpu")
        return saida or ["cpu"]

    def _carregar(self, ativo) -> EspecialistaCarregado:
        manifesto = json.loads((ativo.pasta / "manifest.json").read_text(encoding="utf-8"))
        formato = manifesto["entrada"]["formato"]
        modelos: dict[str, ModeloCarregado] = {}
        for m in manifesto["modelos"]:
            backend = self._backend(m["formato"])
            falhas: list[str] = []
            carregado = None
            for dispositivo in self._candidatos(m):
                try:
                    carregado = backend.carregar(m, ativo.pasta, dispositivo, formato)
                    break
                except FormatoNaoSuportado as exc:       # independe do dispositivo: nao adianta tentar outro
                    raise ErroExecucao(E_MODELO, f"formato de entrada nao suportado por este executor: {exc}") from None
                except Exception as exc:  # noqa: BLE001 -- tenta o proximo dispositivo declarado
                    falhas.append(f"{dispositivo}: {type(exc).__name__}: {exc}")
            if carregado is None:
                raise ErroExecucao(E_MODELO, f"modelo '{m['id']}' nao carregou ({'; '.join(falhas)})")
            modelos[m["id"]] = carregado
        self._conferir_classes(manifesto, modelos)
        hashes = {m["id"]: m["sha256"] for m in manifesto["modelos"]}
        return EspecialistaCarregado(ativo.id, ativo.versao, ativo.pasta, manifesto, modelos, hashes, ativo.identidade)

    @staticmethod
    def _conferir_classes(manifesto: dict[str, Any], modelos: dict[str, ModeloCarregado]) -> None:
        """Fecha a lacuna do validador estatico (que nao inspeciona classes de .pt): confere ao carregar."""
        for a in manifesto["atributos"]:
            de = a["de"]
            pedidas = set(de.get("classes", [])) | ({de["classe"]} if "classe" in de else set()) | (set(a.get("categorias", [])) if a["tipo"] == "categoria" else set())
            ausentes = sorted(pedidas - set(modelos[de["modelo"]].nomes))
            if ausentes:
                raise ErroExecucao(E_MODELO, f"atributo '{a['nome']}': classes {ausentes} nao existem no modelo '{de['modelo']}' "
                                             f"(classes do modelo: {sorted(modelos[de['modelo']].nomes)})")

    def _resolver(self, pid: str) -> EspecialistaCarregado:
        try:
            leve = self.instalador.obter_ativo(pid, verificar=False)
        except ErroInstalacao as exc:
            raise ErroExecucao(E_DESCONHECIDO, exc.mensagem) from None
        with self._lock:
            atual = self._carregados.get(pid)
            if atual and atual.versao == leve.versao and atual.identidade == leve.identidade:
                return atual
            try:
                ativo = self.instalador.obter_ativo(pid, verificar=True)       # integridade completa so ao (re)carregar
            except ErroInstalacao as exc:
                self._carregados.pop(pid, None)
                raise ErroExecucao(E_INTEGRIDADE if exc.codigo == COD_INTEGRIDADE else E_DESCONHECIDO, exc.mensagem) from None
            carregado = self._carregar(ativo)
            self._carregados[pid] = carregado
            return carregado

    def descarregar(self, pid: str | None = None) -> None:
        with self._lock:
            if pid is None:
                self._carregados.clear()
            else:
                self._carregados.pop(pid, None)

    def info(self, pid: str) -> dict[str, Any]:
        e = self._resolver(pid)
        return {"id": e.id, "versao": e.versao, "dispositivos": {k: m.dispositivo for k, m in e.modelos.items()},
                "atributos": [a["nome"] for a in e.manifesto["atributos"]]}

    def contrato(self, pid: str) -> dict[str, Any]:
        """O que o host precisa saber para despachar e consumir: identidade/versao ativas, entrada e validade por atributo.

        Resolve (e recarrega, se a versao ativa mudou) como `executar`; levanta `ErroExecucao` se nao houver ativo.
        """
        e = self._resolver(pid)
        return contrato_de_manifesto(e.manifesto, e.id, e.versao, e.identidade)

    # ------------------------------------------------------------------ execucao

    def executar(self, pid: str, pedidos: Sequence[Pedido]) -> list[dict[str, Any]]:
        t0 = time.perf_counter()
        try:
            esp = self._resolver(pid)
        except ErroExecucao as exc:
            return [self._erro(p, pid, None, exc.codigo, exc.mensagem) for p in pedidos]
        except Exception as exc:  # noqa: BLE001
            return [self._erro(p, pid, None, E_INTERNO, f"{type(exc).__name__}: {exc}") for p in pedidos]
        entrada = esp.manifesto["entrada"]
        custo = (esp.manifesto.get("custo") or {}).get("ms_por_recorte")
        resultados: list[dict[str, Any] | None] = [None] * len(pedidos)
        a_processar: list[int] = []
        params_por_pedido: dict[int, dict[str, float]] = {}

        for i, p in enumerate(pedidos):
            erro = self._validar_pedido(p, esp, entrada, t0, custo)
            if erro is not None:
                resultados[i] = erro
                continue
            try:
                params = self._parametros_efetivos(esp.manifesto, p.parametros)
                self._checar_limiares(esp.manifesto, params)
            except ErroExecucao as exc:
                resultados[i] = self._erro(p, pid, esp.versao, exc.codigo, exc.mensagem)
                continue
            esperado = calcular_config_hash(esp.hashes, params)
            if p.config_hash and p.config_hash != esperado:
                resultados[i] = self._erro(p, pid, esp.versao, E_VERSAO, "config_hash do pedido difere do pacote/parametros ativos")
                continue
            minimo = entrada.get("altura_minima_px", 0)
            if p.altura_pessoa_px is not None and p.altura_pessoa_px < minimo:
                resultados[i] = self._resultado(p, esp, [self._indeterminado(a["nome"], "recorte_pequeno") for a in esp.manifesto["atributos"]], 0.0, 0.0)
                continue
            params_por_pedido[i] = params
            a_processar.append(i)

        lote_max = entrada.get("lote_max", 1)
        for ini in range(0, len(a_processar), lote_max):
            # Prazo ANTES do lote, pedido a pedido: o orcamento de cada um e consumido pelo tempo ja gasto (carga, lotes
            # anteriores). Vencido -> nao processa. Com `custo` declarado, tambem exige que o restante o comporte.
            agora_ms = (time.perf_counter() - t0) * 1000.0
            indices = []
            for i in a_processar[ini:ini + lote_max]:
                restante = None if pedidos[i].restante_ms is None else pedidos[i].restante_ms - agora_ms
                if restante is not None and (restante <= 0 or (custo is not None and restante < custo)):
                    resultados[i] = self._erro(pedidos[i], pid, esp.versao, E_PRAZO, "orcamento vencido ou insuficiente antes de processar o lote")
                else:
                    indices.append(i)
            if not indices:
                continue
            imagens = [pedidos[i].recorte for i in indices]
            fila_ms = agora_ms
            t1 = time.perf_counter()
            try:
                saidas = {mid: m.inferir(imagens) for mid, m in esp.modelos.items() if self._usa_modelo(esp.manifesto, mid)}
            except Exception as exc:  # noqa: BLE001
                for i in indices:
                    resultados[i] = self._erro(pedidos[i], pid, esp.versao, E_INTERNO, f"falha na inferencia: {type(exc).__name__}: {exc}")
                continue
            fim_ms = (time.perf_counter() - t0) * 1000.0
            inferencia_ms = (time.perf_counter() - t1) * 1000.0 / len(indices)
            for k, i in enumerate(indices):
                if pedidos[i].restante_ms is not None and fim_ms > pedidos[i].restante_ms:
                    # Resultado VENCIDO: o tempo acabou durante a inferencia. Nao e entregue como valido (o host o
                    # descartaria; aqui garantimos que nunca saia como `ok`).
                    resultados[i] = self._erro(pedidos[i], pid, esp.versao, E_PRAZO, f"resultado vencido: concluido em {fim_ms:.0f} ms com orcamento de {pedidos[i].restante_ms:.0f} ms")
                    continue
                por_modelo = {mid: s[k] for mid, s in saidas.items()}
                resultados[i] = self._resultado(pedidos[i], esp, self._atributos(esp.manifesto, por_modelo, params_por_pedido[i]), fila_ms, inferencia_ms)
        return [r for r in resultados if r is not None]

    @staticmethod
    def _usa_modelo(manifesto: dict[str, Any], mid: str) -> bool:
        return any(a["de"]["modelo"] == mid for a in manifesto["atributos"])

    # ------------------------------------------------------------------ validacoes

    def _validar_pedido(self, p: Pedido, esp: EspecialistaCarregado, entrada: dict[str, Any], t0: float, custo: float | None) -> dict[str, Any] | None:
        if p.fonte != entrada["fonte"]:
            return self._erro(p, esp.id, esp.versao, E_RECORTE, f"fonte '{p.fonte}' diferente da exigida pelo especialista ('{entrada['fonte']}')")
        r = p.recorte
        if not hasattr(r, "shape") or getattr(r, "ndim", 0) not in (2, 3) or getattr(r, "size", 0) == 0:
            return self._erro(p, esp.id, esp.versao, E_RECORTE, "recorte ausente, vazio ou nao e uma imagem")
        if str(getattr(r, "dtype", "")) != "uint8":
            return self._erro(p, esp.id, esp.versao, E_RECORTE, "o recorte deve ser uint8 (BGR, como o OpenCV)")
        if r.ndim == 3 and r.shape[2] not in (1, 3, 4):
            return self._erro(p, esp.id, esp.versao, E_RECORTE, "o recorte deve ter 1, 3 ou 4 canais")
        if p.restante_ms is not None and p.restante_ms - (time.perf_counter() - t0) * 1000.0 <= 0:
            return self._erro(p, esp.id, esp.versao, E_PRAZO, "orcamento (restante_ms) esgotado antes de processar")
        return None

    @staticmethod
    def _parametros_efetivos(manifesto: dict[str, Any], dados: dict[str, float]) -> dict[str, float]:
        declarados = {p["nome"]: p for p in manifesto.get("parametros", [])}
        efetivos = {n: float(p["padrao"]) for n, p in declarados.items()}
        for nome, valor in (dados or {}).items():
            p = declarados.get(nome)
            if p is None:
                raise ErroExecucao(E_PARAMETRO, f"parametro desconhecido: '{nome}'")
            if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor != valor or not (p["min"] <= valor <= p["max"]):
                raise ErroExecucao(E_PARAMETRO, f"parametro '{nome}'={valor!r} fora da faixa [{p['min']}, {p['max']}]")
            efetivos[nome] = float(valor)
        return efetivos

    @staticmethod
    def _limiar(valor: Any, params: dict[str, float]) -> float | None:
        if valor is None:
            return None
        return params[valor["$parametro"]] if isinstance(valor, dict) else float(valor)

    def _checar_limiares(self, manifesto: dict[str, Any], params: dict[str, float]) -> None:
        """Dever do host (manual 12.4): com os valores EFETIVOS, limiar_ausente <= limiar_presente."""
        for a in manifesto["atributos"]:
            lp, la = self._limiar(a["de"].get("limiar_presente"), params), self._limiar(a["de"].get("limiar_ausente"), params)
            if lp is not None and la is not None and la > lp:
                raise ErroExecucao(E_PARAMETRO, f"atributo '{a['nome']}': limiar_ausente efetivo ({la}) maior que limiar_presente ({lp})")

    # ------------------------------------------------------------------ mapeamento de atributos (manual 12.3)

    def _atributos(self, manifesto: dict[str, Any], saidas: dict[str, dict[str, Any]], params: dict[str, float]) -> list[dict[str, Any]]:
        return [self._atributo(a, saidas[a["de"]["modelo"]], params) for a in manifesto["atributos"]]

    def _atributo(self, a: dict[str, Any], saida: dict[str, Any], params: dict[str, float]) -> dict[str, Any]:
        de, nome = a["de"], a["nome"]
        deteccoes = saida.get("deteccoes", [])
        probs = saida.get("probs", {})
        if (de["tipo"] == "deteccao" and "probs" in saida) or (de["tipo"] == "classificacao" and "deteccoes" in saida):
            return self._indeterminado(nome, "modelo_indisponivel")      # tipo do modelo nao confere com o mapeamento
        if a["tipo"] == "tri":
            conf = max((c for k, c in deteccoes if k in de["classes"]), default=0.0) if de["tipo"] == "deteccao" else float(probs.get(de["classe"], 0.0))
            lp, la = self._limiar(de["limiar_presente"], params), self._limiar(de["limiar_ausente"], params)
            if conf >= lp:
                return {"nome": nome, "estado": "definido", "valor": "sim", "confianca": round(conf, 4)}
            if conf < la:
                return {"nome": nome, "estado": "definido", "valor": "nao", "confianca": round(conf, 4)}
            return self._indeterminado(nome, "baixa_confianca")
        if a["tipo"] == "numero":
            if de["agregacao"] == "contagem":
                valor: float = sum(1 for k, c in deteccoes if k in de["classes"] and c >= CONTAGEM_CONFIANCA_MINIMA)
            elif de["agregacao"] == "maior_confianca":
                valor = max((c for k, c in deteccoes if k in de["classes"]), default=0.0)
            else:
                valor = float(probs.get(de["classe"], 0.0))
            lo, hi = a["faixa"]
            if not (lo <= valor <= hi):
                return self._indeterminado(nome, "fora_da_faixa")
            return {"nome": nome, "estado": "definido", "valor": round(valor, 4) if isinstance(valor, float) else valor}
        # categoria: classe mais provavel entre as `categorias`
        candidatas = {c: float(probs.get(c, 0.0)) for c in a["categorias"]}
        melhor = max(candidatas, key=candidatas.get)
        return {"nome": nome, "estado": "definido", "valor": melhor, "confianca": round(candidatas[melhor], 4)}

    @staticmethod
    def _indeterminado(nome: str, motivo: str) -> dict[str, Any]:
        return {"nome": nome, "estado": "indeterminado", "motivo": motivo}

    # ------------------------------------------------------------------ resultados

    @staticmethod
    def _base(p: Pedido, pid: str, versao: str | None) -> dict[str, Any]:
        return {"pedido_id": p.pedido_id, "tentativa": p.tentativa, "geracao": p.geracao, "trilha_id": p.trilha_id,
                "quadro_id": p.quadro_id, "especialista": {"id": pid, **({"versao": versao} if versao else {})}}

    def _resultado(self, p: Pedido, esp: EspecialistaCarregado, atributos: list[dict[str, Any]], fila_ms: float, inferencia_ms: float) -> dict[str, Any]:
        return {**self._base(p, esp.id, esp.versao), "estado": "ok", "atributos": atributos,
                "tempos": {"fila_ms": round(fila_ms, 2), "inferencia_ms": round(inferencia_ms, 2)}, "erro": None}

    def _erro(self, p: Pedido, pid: str, versao: str | None, codigo: str, mensagem: str) -> dict[str, Any]:
        return {**self._base(p, pid, versao), "estado": "erro", "atributos": [], "tempos": {"fila_ms": 0.0, "inferencia_ms": 0.0},
                "erro": {"codigo": codigo, "mensagem": mensagem}}
