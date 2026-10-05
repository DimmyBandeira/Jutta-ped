"""A receita pediatrica de exemplo (contrato 2, SOMBRA) e valida contra o pacote de papel montado do classificador atual.

Valida CONTRATO (estrutura, atributos, tipos, operadores). Nao prova equivalencia com o caminho operacional nem qualidade (etapa 02)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
from tools.pacote_papel import ID_PACOTE, MODELO_PADRAO, montar  # noqa: E402

from src.jutta_ped.especialistas.executor import contrato_leve  # noqa: E402
from src.jutta_ped.especialistas.instalador import Instalador  # noqa: E402
from src.jutta_ped.motor.receita import validar_receita  # noqa: E402

RECEITA = RAIZ / "docs" / "etapas_motor" / "exemplos" / "receita_pediatria_sombra_contrato2.json"

pytestmark = pytest.mark.skipif(not MODELO_PADRAO.exists(), reason="modelo local ausente")


def _definicao() -> dict:
    d = json.loads(RECEITA.read_text(encoding="utf-8"))
    return {**d, "id": "pediatria-sombra", "versao": "1"}


def test_receita_pediatrica_de_exemplo_valida_contra_o_pacote_de_papel(tmp_path: Path) -> None:
    inst = Instalador(tmp_path / "raiz")
    r = inst.instalar(montar(MODELO_PADRAO, tmp_path / "papel.zip"))
    inst.ativar(r.id, r.versao)
    contrato = contrato_leve(inst, ID_PACOTE)
    assert contrato["entrada"]["alvo"] == {"tipo": "recorte", "classe_mestre": "person"} and set(contrato["atributos"]) == {"confianca_crianca", "confianca_adulto"}
    assert validar_receita(_definicao(), {ID_PACOTE: contrato}) == []


def test_receita_pediatrica_e_recusada_se_o_pacote_perder_um_atributo(tmp_path: Path) -> None:
    inst = Instalador(tmp_path / "raiz")
    r = inst.instalar(montar(MODELO_PADRAO, tmp_path / "papel.zip"))
    inst.ativar(r.id, r.versao)
    contrato = contrato_leve(inst, ID_PACOTE)
    del contrato["atributos"]["confianca_adulto"]
    problemas = validar_receita(_definicao(), {ID_PACOTE: contrato})
    assert problemas and any("confianca_adulto" in p for p in problemas)
