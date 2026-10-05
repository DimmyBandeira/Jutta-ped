"""Fumaca REAL de compatibilidade do classificador crianca/adulto atual com o pacote declarativo (tools/pacote_papel.py).

O QUE PROVA: o MESMO recorte, passado ao modelo atual (caminho legado `_classify_crop`) e ao pacote pelo executor generico, produz as mesmas
confiancas por classe (dentro de tolerancia numerica) => entrada, pre-processamento, classes e saida cabem no contrato.
O QUE NAO PROVA: qualidade de nenhum modelo, equivalencia da migracao (cache por trilha, orcamento, estabilizador de identidade,
promocao de crianca fraca, companhia) nem comparacao com a referencia humana (etapa 02). Pula sem modelos/video locais.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from tools.pacote_papel import ID_PACOTE, MODELO_PADRAO, montar  # noqa: E402

PESSOA = RAIZ / "src" / "models" / "yolo11n_openvino_model"
VIDEOS = [RAIZ / "videos" / n for n in ("Corredor pediatria.mp4", "Corredor um.mp4", "Recepição social.mp4")]
TOLERANCIA = 0.02          # diferenca maxima admitida entre confiancas (ruido de letterbox/arredondamento a 4 casas)
MAX_RECORTES = 12

pytestmark = pytest.mark.skipif(not (MODELO_PADRAO.exists() and PESSOA.exists() and any(v.exists() for v in VIDEOS)),
                                reason="modelos/videos locais ausentes")


def _recortes():
    import cv2
    from ultralytics import YOLO

    from modulo.pediatria.crop_pipeline import PersonCropSpecialistPipeline, crop_frame

    especialista = YOLO(str(MODELO_PADRAO), task="detect")
    pipe = PersonCropSpecialistPipeline(person_model_path=PESSOA, specialist_model=especialista,
                                        specialist_names={int(k): str(v) for k, v in especialista.names.items()})
    saida = []
    for video in (v for v in VIDEOS if v.exists()):
        cap = cv2.VideoCapture(str(video))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        for pos in (0.2, 0.5, 0.8):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * pos))
            ok, quadro = cap.read()
            if not ok:
                continue
            pessoas, _ = pipe._run_or_reuse_detector(quadro, frame_index=len(saida) + 1000, device="cpu", person_confidence=0.20, detector_stride_frames=1)
            for p in pessoas:
                recorte = crop_frame(quadro, p.bbox_xyxy)
                if recorte is not None and min(recorte.shape[:2]) >= 24:
                    saida.append(recorte)
        cap.release()
        if len(saida) >= MAX_RECORTES:
            break
    return pipe, saida[:MAX_RECORTES]


def test_confiancas_do_pacote_coincidem_com_o_caminho_legado(tmp_path: Path) -> None:
    from src.jutta_ped.especialistas.executor import Executor, Pedido
    from src.jutta_ped.especialistas.instalador import Instalador

    pipe, recortes = _recortes()
    if not recortes:
        pytest.skip("nenhuma pessoa detectada nos quadros locais")
    zip_pacote = montar(MODELO_PADRAO, tmp_path / "papel.zip")
    inst = Instalador(tmp_path / "raiz")
    r = inst.instalar(zip_pacote)
    inst.ativar(r.id, r.versao)
    assert r.id == ID_PACOTE
    ex = Executor(inst)                                                     # backend REAL (Ultralytics/OpenVINO), CPU
    resultados = ex.executar(ID_PACOTE, [Pedido(pedido_id=f"p{i}", recorte=c, altura_pessoa_px=c.shape[0], restante_ms=120000) for i, c in enumerate(recortes)])
    assert all(x["estado"] == "ok" for x in resultados), [x["erro"] for x in resultados if x["estado"] != "ok"]
    maior = 0.0
    for recorte, res in zip(recortes, resultados):
        papel, conf_legado = pipe._classify_crop(recorte, device="cpu", accept_confidence=0.0)      # `accept=0`: devolve a melhor classe sem corte
        attrs = {a["nome"]: a["valor"] for a in res["atributos"]}
        pacote = {"child": attrs["confianca_crianca"], "adult": attrs["confianca_adulto"]}
        if papel in pacote:                                                  # a melhor classe do legado e a de maior confianca do pacote
            assert pacote[papel] == max(pacote.values()) or abs(pacote[papel] - max(pacote.values())) <= TOLERANCIA
            maior = max(maior, abs(pacote[papel] - conf_legado))
        else:
            assert max(pacote.values()) <= TOLERANCIA                        # legado "uncertain" sem deteccao
    print(f"[paridade] recortes={len(recortes)} maior_diferenca_de_confianca={maior:.4f}")
    assert maior <= TOLERANCIA, maior
