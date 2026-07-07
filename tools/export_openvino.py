"""Exporta modelo YOLO .pt para OpenVINO IR, pronto para rodar em CPU sem GPU.

Modos:
  Especialista (padrao):
    python tools/export_openvino.py --model src/models/pediatria_child_detector_v5.pt

  Generico nivel-1 (COCO person):
    python tools/export_openvino.py --generic
    python tools/export_openvino.py --generic --generic-model yolov8s  --imgsz 320

O modo generico baixa o modelo COCO via ultralytics, copia para src/models/ e exporta.
Recomendado imgsz=320 para CPU antiga (i5 2a geracao) ou imgsz=416 para CPU mais recente.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _download_generic_to_models(model_name: str) -> Path:
    """Baixa modelo COCO via ultralytics e copia para src/models/."""
    from ultralytics import YOLO

    dest = ROOT / "src" / "models" / f"{model_name}.pt"
    if dest.is_file():
        print(f"[OK] Modelo ja existe em: {dest}")
        return dest

    print(f"Baixando {model_name}.pt via ultralytics ...")
    tmp = YOLO(f"{model_name}.pt")
    src_pt = Path(tmp.ckpt_path)
    if not src_pt.is_file():
        print(f"[ERRO] Nao encontrou o .pt baixado em: {src_pt}")
        sys.exit(1)

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_pt, dest)
    print(f"Copiado para: {dest}")
    return dest


def _export(model_path: Path, imgsz: int, force: bool) -> None:
    expected_out = model_path.parent / f"{model_path.stem}_openvino_model"
    if expected_out.is_dir():
        if not force:
            print(f"[AVISO] Exportacao ja existe: {expected_out}")
            print("Use --force para re-exportar. Saindo.")
            sys.exit(0)
        shutil.rmtree(expected_out)
        print(f"Pasta anterior removida: {expected_out}")

    print(f"Carregando {model_path.name} ...")
    from ultralytics import YOLO

    model = YOLO(str(model_path))
    classes = set(model.names.values()) if hasattr(model, "names") else set()
    if "person" in classes:
        print(f"Modo: generico COCO — classes: {sorted(classes)[:8]}...")
    elif {"adult", "child"}.issubset(classes):
        print(f"Modo: especialista pediatrico — classes: {sorted(classes)}")
    else:
        print(f"[AVISO] Classes inesperadas: {classes}. Exportando assim mesmo.")

    print(f"Exportando para OpenVINO IR (imgsz={imgsz}) ...")
    output = model.export(format="openvino", imgsz=imgsz, half=False)

    print(f"\nExportado em: {output}")
    print(
        "\nPara usar no popup basta selecionar a pasta no combo 'Modelo'."
        "\nO sistema detecta a pasta _openvino_model e carrega automaticamente."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exporta modelo YOLO para OpenVINO IR (CPU sem GPU)."
    )
    parser.add_argument(
        "--model",
        default="src/models/pediatria_child_detector_v5.pt",
        help="Caminho do .pt a exportar (modo especialista).",
    )
    parser.add_argument(
        "--generic",
        action="store_true",
        help="Baixa e exporta modelo COCO generico (nivel 1, person→adult).",
    )
    parser.add_argument(
        "--generic-model",
        default="yolov8n",
        metavar="NAME",
        help="Nome do modelo COCO a usar no modo --generic (padrao: yolov8n).",
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=0,
        help=(
            "Resolucao de entrada gravada no modelo. "
            "Padrao: 320 para generico (CPU antiga) ou 480 para especialista."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Remove exportacao existente e re-exporta.",
    )
    args = parser.parse_args()

    if args.generic:
        model_path = _download_generic_to_models(args.generic_model)
        imgsz = args.imgsz if args.imgsz > 0 else 320
    else:
        model_path = (ROOT / args.model).resolve()
        if not model_path.is_file():
            print(f"[ERRO] Modelo nao encontrado: {model_path}")
            sys.exit(1)
        imgsz = args.imgsz if args.imgsz > 0 else 480

    _export(model_path, imgsz, args.force)


if __name__ == "__main__":
    main()
