"""Contrato da Cena (src/jutta_ped/motor/cena.py): validacao, imutabilidade, adaptadores."""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from src.jutta_ped.motor.cena import Cena, CenaInvalida, Deteccao, criar_cena, deteccoes_de_objetos, observacoes_de_cena

BASE = dict(camera_ref="cam-1", geracao=1, quadro_id=1, recebido_em=10.0, largura=640, altura=480)


def cena_com(*dets: Deteccao, **kw) -> Cena:
    return criar_cena(**{**BASE, **kw}, deteccoes=dets)


def D(trilha=1, classe="person", bbox=(10.0, 20.0, 50.0, 120.0), conf=0.9) -> Deteccao:
    return Deteccao(trilha, classe, bbox, conf)


@pytest.mark.parametrize("bbox", [
    (10.0, 10.0, 10.0, 50.0), (10.0, 10.0, 50.0, 10.0),            # largura/altura zero
    (50.0, 10.0, 10.0, 50.0), (10.0, 50.0, 50.0, 10.0),            # invertida
    (float("nan"), 0.0, 5.0, 5.0), (0.0, 0.0, float("inf"), 5.0), (0.0, 0.0, 5.0, float("-inf")),
    (0.0, 0.0, 5.0), [0.0, 0.0, 5.0, 5.0], ("a", 0.0, 5.0, 5.0), (True, 0.0, 5.0, 5.0),
])
def test_bbox_invalida_e_recusada(bbox) -> None:
    with pytest.raises(CenaInvalida):
        cena_com(D(bbox=bbox))


@pytest.mark.parametrize("conf", [-0.01, 1.01, float("nan"), float("inf"), "0.5", None])
def test_confianca_fora_de_zero_a_um_e_recusada(conf) -> None:
    with pytest.raises(CenaInvalida):
        cena_com(D(conf=conf))


def test_limites_da_confianca_sao_aceitos() -> None:
    assert len(cena_com(D(1, conf=0.0), D(2, conf=1.0)).deteccoes) == 2


def test_trilha_repetida_na_mesma_classe_e_recusada_mas_classes_diferentes_com_o_mesmo_id_sao_permitidas() -> None:
    with pytest.raises(CenaInvalida):
        cena_com(D(1), D(1))
    c = cena_com(D(1, "person"), D(1, "dog"))
    assert [(d.trilha_id, d.classe) for d in c.deteccoes] == [(1, "person"), (1, "dog")]


@pytest.mark.parametrize("campo,valor", [
    ("camera_ref", ""), ("camera_ref", None), ("geracao", "1"), ("geracao", True), ("quadro_id", 1.5), ("quadro_id", True),
    ("recebido_em", float("nan")), ("recebido_em", float("inf")), ("recebido_em", "x"), ("recebido_em", True),
    ("largura", 0), ("altura", -1), ("largura", 640.0), ("altura", True),
])
def test_tipos_e_campos_escalares_invalidos(campo, valor) -> None:
    with pytest.raises(CenaInvalida):
        criar_cena(**{**BASE, campo: valor})


@pytest.mark.parametrize("det", [
    Deteccao(True, "person", (0.0, 0.0, 5.0, 5.0)), Deteccao("1", "person", (0.0, 0.0, 5.0, 5.0)),
    Deteccao(1, "", (0.0, 0.0, 5.0, 5.0)), Deteccao(1, None, (0.0, 0.0, 5.0, 5.0)), "nao e deteccao",
])
def test_deteccao_com_tipo_errado_e_recusada(det) -> None:
    with pytest.raises(CenaInvalida):
        criar_cena(**BASE, deteccoes=[det])


def test_cena_e_deteccao_sao_imutaveis() -> None:
    c = cena_com(D(1))
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.geracao = 2  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.deteccoes[0].classe = "x"  # type: ignore[misc]
    assert isinstance(c.deteccoes, tuple)


def test_cena_copia_a_lista_de_entrada() -> None:
    entrada = [D(1)]
    c = criar_cena(**BASE, deteccoes=entrada)
    entrada.append(D(2))
    assert len(c.deteccoes) == 1


def test_cena_vazia_e_valida() -> None:
    c = criar_cena(**BASE)
    assert c.vazia and c.deteccoes == () and c.por_trilha() == {} and observacoes_de_cena(c) == []


def test_idade_s_e_relativa_ao_recebido_em() -> None:
    c = cena_com(recebido_em=10.0)
    assert c.idade_s(12.5) == pytest.approx(2.5)
    assert c.idade_s(10.0) == 0.0


def test_geometria_da_deteccao() -> None:
    d = D(bbox=(10.0, 20.0, 50.0, 120.0))
    assert (d.largura, d.altura) == (40.0, 100.0)


def test_quadro_e_so_uma_referencia() -> None:
    q = object()
    assert cena_com(D(1), quadro=q).quadro is q


def test_deteccoes_de_objetos_descarta_sem_inventar() -> None:
    itens = [
        SimpleNamespace(track_id=3, bbox_xyxy=(0, 0, 10, 20), confidence=0.7),
        SimpleNamespace(track_id=None, bbox_xyxy=(0, 0, 5, 5)),
        SimpleNamespace(track_id=-1, bbox_xyxy=(0, 0, 5, 5)),
        SimpleNamespace(track_id=True, bbox_xyxy=(0, 0, 5, 5)),
        SimpleNamespace(track_id=4, bbox_xyxy=None),
        SimpleNamespace(track_id=5, bbox_xyxy=("a", 0, 5, 5)),
        SimpleNamespace(track_id=6, bbox_xyxy=(0, 0, 5)),
        SimpleNamespace(track_id=7, bbox_xyxy=(0, 0, 5, 5), confidence="x"),
        SimpleNamespace(bbox_xyxy=(0, 0, 5, 5)),
        SimpleNamespace(track_id=8, bbox_xyxy=(1, 2, 3, 4)),                 # sem confianca: usa o padrao 1.0
    ]
    ds = deteccoes_de_objetos(itens, "person")
    assert [(d.trilha_id, d.classe, d.bbox_xyxy, d.confianca) for d in ds] == [
        (3, "person", (0.0, 0.0, 10.0, 20.0), 0.7), (8, "person", (1.0, 2.0, 3.0, 4.0), 1.0)]
    assert deteccoes_de_objetos([], "person") == []


def test_deteccoes_de_objetos_pode_gerar_dado_que_criar_cena_recusa() -> None:
    # o adaptador nao valida geometria: quem monta a Cena (criar_cena) e a fronteira de validacao
    ds = deteccoes_de_objetos([SimpleNamespace(track_id=1, bbox_xyxy=(5, 5, 5, 5), confidence=0.9)], "person")
    assert len(ds) == 1
    with pytest.raises(CenaInvalida):
        criar_cena(**BASE, deteccoes=ds)


def test_observacoes_de_cena() -> None:
    c = cena_com(D(1, conf=0.4), D(2, "dog", bbox=(0.0, 0.0, 9.0, 9.0), conf=0.8), geracao=7, quadro_id=42, recebido_em=99.5)
    obs = observacoes_de_cena(c)
    assert [(o.camera_ref, o.geracao, o.quadro_id, o.trilha_id, o.classe, o.bbox_xyxy, o.capturado_em, o.confianca) for o in obs] == [
        ("cam-1", 7, 42, 1, "person", (10.0, 20.0, 50.0, 120.0), 99.5, 0.4),
        ("cam-1", 7, 42, 2, "dog", (0.0, 0.0, 9.0, 9.0), 99.5, 0.8)]
