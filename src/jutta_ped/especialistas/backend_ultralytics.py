"""Backend de inferencia do executor sobre Ultralytics: formatos `pt` e `openvino` (deteccao e classificacao).

Importado so quando o executor realmente carrega um modelo (o validador e o instalador nunca importam este modulo,
nem `torch`/`ultralytics`). Saida normalizada, sem objetos do Ultralytics vazando para o contrato:

    {"deteccoes": [(classe, confianca), ...], "probs": {classe: probabilidade}}   (uma chave por tipo de modelo)

Pre-processamento: este backend APLICA o `entrada.formato` declarado ou RECUSA a combinacao (`FormatoNaoSuportado`,
vira E_MODELO_INDISPONIVEL com a razao). Nada e ignorado em silencio:

* `redimensionamento`: `esticar` (redimensiona para largura x altura sem manter proporcao) ou `letterbox` (mantem a
  proporcao e preenche com 114 ate largura x altura, centralizado). A imagem ja chega ao Ultralytics no tamanho
  declarado, entao ele nao a altera (so completa ate multiplo do stride, no `.pt`).
* `cor`: `RGB` (o modelo espera tensores RGB; o Ultralytics converte BGR->RGB sozinho) e `BGR` (trocamos os canais antes,
  de modo que o modelo receba BGR). `GRAY`: recusado (modelos de 1 canal nao sao suportados).
* `normalizacao`: `0-1` (o Ultralytics divide por 255). `0-255`: recusado.
* Classificacao exige largura == altura; modelos OpenVINO com `imgsz` no `metadata.yaml` diferente do declarado sao
  recusados (a forma do modelo exportado e fixa). Tarefas alem de `detect`/`classify` (segmentacao, pose, obb): recusadas.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.jutta_ped.especialistas.executor import FormatoNaoSuportado

# Confianca minima pedida ao modelo: baixa de proposito. Os limiares reais (presente/ausente) sao aplicados
# depois, no mapeamento do atributo; um corte alto aqui transformaria "entre os limiares" em "nao detectado".
CONFIANCA_MINIMA = 0.01
PREENCHIMENTO = 114       # cinza do letterbox do Ultralytics


def validar_formato(formato: dict[str, Any], tarefa: str, imgsz_exportado: tuple[int, int] | None = None) -> None:
    """Levanta `FormatoNaoSuportado` com a razao quando o backend nao honra a declaracao."""
    largura, altura = int(formato["largura"]), int(formato["altura"])
    if formato["cor"] == "GRAY":
        raise FormatoNaoSuportado("cor 'GRAY' (modelos de 1 canal) nao e suportada pelo backend Ultralytics")
    if formato["normalizacao"] != "0-1":
        raise FormatoNaoSuportado(f"normalizacao '{formato['normalizacao']}' nao e suportada (o Ultralytics normaliza em 0-1)")
    if formato["redimensionamento"] not in ("letterbox", "esticar"):
        raise FormatoNaoSuportado(f"redimensionamento '{formato['redimensionamento']}' desconhecido")
    if tarefa not in ("detect", "classify"):
        raise FormatoNaoSuportado(f"tarefa '{tarefa}' do modelo nao e suportada (apenas detect e classify)")
    if tarefa == "classify" and largura != altura:
        raise FormatoNaoSuportado(f"modelo de classificacao exige largura == altura (declarado {largura}x{altura})")
    if imgsz_exportado is not None and tuple(imgsz_exportado) != (altura, largura):
        raise FormatoNaoSuportado(f"o modelo OpenVINO foi exportado para {imgsz_exportado[1]}x{imgsz_exportado[0]} (largura x altura), "
                                  f"mas o manifesto declara {largura}x{altura}")


def preparar_imagem(imagem: Any, formato: dict[str, Any]) -> Any:
    """BGR uint8 (1, 3 ou 4 canais) -> array largura x altura declarados, na ordem de canais que o Ultralytics espera."""
    import cv2
    import numpy as np

    largura, altura = int(formato["largura"]), int(formato["altura"])
    img = np.asarray(imagem)
    if img.dtype != np.uint8:
        raise ValueError("imagem deve ser uint8")
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 1:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    h, w = img.shape[:2]
    if formato["redimensionamento"] == "esticar":
        saida = cv2.resize(img, (largura, altura), interpolation=cv2.INTER_LINEAR)
    else:
        escala = min(largura / w, altura / h)
        nw, nh = max(1, round(w * escala)), max(1, round(h * escala))
        redim = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        saida = np.full((altura, largura, 3), PREENCHIMENTO, np.uint8)
        topo, esq = (altura - nh) // 2, (largura - nw) // 2
        saida[topo:topo + nh, esq:esq + nw] = redim
    if formato["cor"] == "BGR":          # o Ultralytics faz BGR->RGB; entregando RGB aqui, o modelo recebe BGR
        saida = saida[:, :, ::-1].copy()
    return saida


class ModeloUltralytics:
    def __init__(self, modelo: Any, dispositivo: str, formato: dict[str, Any], tarefa: str, lote_exportado: int | None = None,
                 resolucao: Any = None) -> None:
        self._m, self.dispositivo, self._formato, self.tarefa = modelo, dispositivo, formato, tarefa
        self._lote = lote_exportado          # OpenVINO exportado com batch fixo: o lote do executor e fatiado nesse tamanho
        self._imgsz = int(formato["altura"]) if tarefa == "classify" else [int(formato["altura"]), int(formato["largura"])]
        self.dispositivo_pedido, self.dispositivo_efetivo, self._resolucao, self._verificado = dispositivo, None, resolucao, False
        overrides = getattr(modelo, "overrides", None)
        if isinstance(overrides, dict):
            # `names` de um modelo exportado (OpenVINO) monta o preditor com `device=None`; o Ultralytics entao escolhe CUDA e falha
            # ("Invalid device id") se outro modelo do mesmo processo ja rodou com device="cpu" (CUDA_VISIBLE_DEVICES oculta a GPU).
            overrides["device"] = dispositivo if resolucao is None else resolucao.device_ultralytics
        nomes = modelo.names
        self.nomes: list[str] = [str(nomes[i]) for i in sorted(nomes)] if isinstance(nomes, dict) else [str(n) for n in nomes]

    def inferir(self, imagens: list[Any]) -> list[dict[str, Any]]:
        preparadas = [preparar_imagem(im, self._formato) for im in imagens]
        passo = self._lote or len(preparadas) or 1
        resultados: list[Any] = []
        for ini in range(0, len(preparadas), passo):
            device = self.dispositivo if self._resolucao is None else self._resolucao.device_ultralytics
            resultados += list(self._m.predict(preparadas[ini:ini + passo], imgsz=self._imgsz, device=device, conf=CONFIANCA_MINIMA,
                                               half=self.dispositivo.startswith("cuda"), verbose=False))
        if self._resolucao is not None and not self._verificado and preparadas:
            from src.jutta_ped.dispositivo import conferir_efetivo

            # o backend e sempre estrito: dispositivo diferente OU telemetria ilegivel = DispositivoIndisponivel (nunca "conferido" sem ler)
            self.dispositivo_efetivo = conferir_efetivo(self._m, self._resolucao, estrito=True)
            self._verificado = True
        saida: list[dict[str, Any]] = []
        for r in resultados:
            nomes = r.names
            if getattr(r, "probs", None) is not None:
                dados = r.probs.data.detach().cpu().numpy()
                saida.append({"probs": {str(nomes[i]): float(p) for i, p in enumerate(dados)}})
            else:
                caixas = r.boxes
                if caixas is None or len(caixas) == 0:
                    saida.append({"deteccoes": []})
                else:
                    cls = caixas.cls.detach().cpu().numpy().astype(int)
                    conf = caixas.conf.detach().cpu().numpy()
                    saida.append({"deteccoes": [(str(nomes[int(c)]), float(s)) for c, s in zip(cls, conf)]})
        return saida


def _metadata_openvino(pasta: Path) -> dict[str, Any]:
    try:
        import yaml

        dados = yaml.safe_load((pasta / "metadata.yaml").read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else {}
    except Exception:  # noqa: BLE001 -- sem metadata legivel nao ha o que conferir
        return {}


class BackendUltralytics:
    """`carregar` levanta `FormatoNaoSuportado` se o formato declarado nao puder ser honrado e qualquer outra excecao se
    o modelo/dispositivo nao funcionar (o executor tenta o proximo dispositivo)."""

    def carregar(self, modelo: dict[str, Any], pasta: Path, dispositivo: str, formato: dict[str, Any]) -> ModeloUltralytics:
        from ultralytics import YOLO

        caminho = pasta / modelo["arquivo"]
        if modelo["formato"] == "openvino":
            meta = _metadata_openvino(caminho)
            tarefa = meta.get("task") if meta.get("task") in ("detect", "classify") else (meta.get("task") or "detect")
            tamanho = meta.get("imgsz")
            exportado = (int(tamanho[0]), int(tamanho[1])) if isinstance(tamanho, (list, tuple)) and len(tamanho) == 2 else None
            validar_formato(formato, str(tarefa), exportado)
            from src.jutta_ped.dispositivo import resolver_openvino

            resolucao = resolver_openvino(dispositivo)       # estrito: indisponivel = erro; o executor tenta o proximo dispositivo DECLARADO
            m = YOLO(str(caminho), task=str(tarefa))
            lote = meta.get("batch")
            return ModeloUltralytics(m, dispositivo, formato, str(tarefa), int(lote) if isinstance(lote, int) and lote > 0 else None, resolucao)
        else:
            m = YOLO(str(caminho))
            tarefa = str(getattr(m, "task", "detect"))
            validar_formato(formato, tarefa)
        return ModeloUltralytics(m, dispositivo, formato, str(tarefa))
