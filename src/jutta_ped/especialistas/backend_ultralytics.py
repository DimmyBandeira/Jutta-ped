"""Backend de inferencia do executor sobre Ultralytics: formatos `pt` e `openvino` (deteccao e classificacao).

Importado so quando o executor realmente carrega um modelo (o validador e o instalador nunca importam este modulo,
nem `torch`/`ultralytics`). Saida normalizada, sem objetos do Ultralytics vazando para o contrato:

    {"deteccoes": [(classe, confianca), ...], "probs": {classe: probabilidade}}   (uma chave por tipo de modelo)

O pre-processamento (redimensionar/letterbox/normalizar/cor) e o do proprio Ultralytics; de `entrada.formato` o
backend usa apenas `largura`/`altura` (-> `imgsz`). `cor` e `normalizacao` valem para backends que as apliquem.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# Confianca minima pedida ao modelo: baixa de proposito. Os limiares reais (presente/ausente) sao aplicados
# depois, no mapeamento do atributo; um corte alto aqui transformaria "entre os limiares" em "nao detectado".
CONFIANCA_MINIMA = 0.01


class ModeloUltralytics:
    def __init__(self, modelo: Any, dispositivo: str, imgsz: int) -> None:
        self._m, self.dispositivo, self._imgsz = modelo, dispositivo, imgsz
        nomes = modelo.names
        self.nomes: list[str] = [str(nomes[i]) for i in sorted(nomes)] if isinstance(nomes, dict) else [str(n) for n in nomes]

    def inferir(self, imagens: list[Any]) -> list[dict[str, Any]]:
        resultados = self._m.predict(imagens, imgsz=self._imgsz, device=self.dispositivo, conf=CONFIANCA_MINIMA,
                                     half=self.dispositivo.startswith("cuda"), verbose=False)
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


class BackendUltralytics:
    """`carregar` levanta excecao se o modelo/dispositivo nao funcionar; o executor tenta o proximo dispositivo."""

    def carregar(self, modelo: dict[str, Any], pasta: Path, dispositivo: str, imgsz: int) -> ModeloUltralytics:
        from ultralytics import YOLO

        caminho = pasta / modelo["arquivo"]
        if modelo["formato"] == "openvino":
            m = YOLO(str(caminho), task=_tarefa_openvino(caminho))
        else:
            m = YOLO(str(caminho))
        carregado = ModeloUltralytics(m, dispositivo, imgsz)
        return carregado


def _tarefa_openvino(pasta: Path) -> str:
    """A pasta OpenVINO exportada pelo Ultralytics traz `metadata.yaml` com `task`; padrao `detect`."""
    meta = pasta / "metadata.yaml"
    try:
        import yaml

        dados = yaml.safe_load(meta.read_text(encoding="utf-8"))
        tarefa = dados.get("task") if isinstance(dados, dict) else None
        return tarefa if tarefa in ("detect", "classify") else "detect"
    except Exception:  # noqa: BLE001 -- sem metadata legivel assume deteccao
        return "detect"
