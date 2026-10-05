"""Monta, a partir do detector crianca/adulto JA existente no repositorio, um pacote DECLARATIVO de especialista de papel.

Nao treina nem altera pesos: copia o modelo atual (por padrao a pasta OpenVINO v6 usada em producao) para dentro do pacote e escreve o
manifesto (protocolo 1). O pacote so produz ATRIBUTOS (confianca de cada classe no recorte); a decisao de papel, o contexto de
companhia e a persistencia pertencem a mae (receita contrato 2). Ver docs/etapas_motor/03b_cena_regras.md (secao 5).

    python tools/pacote_papel.py [--modelo <pasta-openvino|arquivo.pt>] [--saida runtime/pacotes/papel.zip]

A saida padrao fica em `runtime/` (ignorada pelo git): pesos nunca sao versionados.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.jutta_ped.especialistas.hashing import hash_pasta, sha256_blocos  # noqa: E402

MODELO_PADRAO = ROOT / "src" / "models" / "pediatria_child_detector_v6_jutta_openvino_model"
ID_PACOTE = "br.jutta.papel-pessoa"
VERSAO = "1.0.0"
CLASSES = ("adult", "child")           # nomes de classe do modelo atual (metadata.yaml)


def _blocos(caminho: Path):
    with caminho.open("rb") as f:
        while bloco := f.read(1 << 20):
            yield bloco


def _arquivos(pasta: Path) -> list[tuple[str, Path]]:
    return sorted((p.relative_to(pasta).as_posix(), p) for p in pasta.rglob("*") if p.is_file())


def manifesto(modelo: Path) -> dict:
    """Manifesto do pacote. `altura_minima_px` e `margem` equivalem APROXIMADAMENTE ao recorte atual (ver 03b, secao 5)."""
    if modelo.is_dir():
        formato, arquivo = "openvino", "modelos/papel_openvino_model"
        sha = hash_pasta((rel, p.stat().st_size, (lambda p=p: _blocos(p))) for rel, p in _arquivos(modelo))
        dispositivos = ["intel_gpu", "cpu"]
    else:
        formato, arquivo = "pt", f"modelos/{modelo.name}"
        sha = sha256_blocos(_blocos(modelo))
        dispositivos = ["cuda", "cpu"]
    return {
        "protocolo": 1,
        "pacote": {"id": ID_PACOTE, "versao": VERSAO, "nome": "Papel da pessoa (crianca/adulto) - detector atual empacotado",
                   "autor": "Equipe Jutta (modelo existente; sem retreino)"},
        "entrada": {"fonte": "visivel", "alvo": {"tipo": "recorte", "classe_mestre": "person"}, "altura_minima_px": 24, "lote_max": 1,
                    "formato": {"cor": "RGB", "largura": 640, "altura": 640, "redimensionamento": "letterbox", "normalizacao": "0-1"}},
        "modelos": [{"id": "papel", "arquivo": arquivo, "formato": formato, "sha256": sha, "dispositivos": dispositivos}],
        "atributos": [
            {"nome": f"confianca_{nome}", "tipo": "numero", "validade_ms": 1500, "faixa": [0, 1],
             "de": {"modelo": "papel", "tipo": "deteccao", "classes": [classe], "agregacao": "maior_confianca"}}
            for nome, classe in (("crianca", "child"), ("adulto", "adult"))
        ],
    }


def montar(modelo: Path, saida: Path) -> Path:
    modelo = Path(modelo)
    if not modelo.exists():
        raise FileNotFoundError(f"modelo nao encontrado: {modelo}")
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifesto(modelo), ensure_ascii=False, indent=2))
        if modelo.is_dir():
            for rel, p in _arquivos(modelo):
                z.write(p, f"modelos/papel_openvino_model/{rel}")
        else:
            z.write(modelo, f"modelos/{modelo.name}")
    return saida


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--modelo", type=Path, default=MODELO_PADRAO)
    ap.add_argument("--saida", type=Path, default=ROOT / "runtime" / "pacotes" / f"{ID_PACOTE}-{VERSAO}.zip")
    a = ap.parse_args(argv)
    destino = montar(a.modelo, a.saida)
    print(f"pacote gravado em {destino} (NAO versione: contem pesos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
