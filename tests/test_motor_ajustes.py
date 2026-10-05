"""05f: ajustes por camera (validacao, precedencia, hash, perfis), `selecionar_roi`/`marcar_roi` e `receita_efetiva`. Funcoes puras: sem relogio."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest
from test_motor_regras_motor import BEBE, VEIC, Mundo, receita_cena
from test_motor_sombra import receita as receita_v1

from src.jutta_ped.motor.ajustes import (
    PERFIS,
    AjustesCamera,
    Execucao,
    MargemCrop,
    Roi,
    ajustes_de_dict,
    ajustes_para_dict,
    efetivos,
    hash_ajustes,
    marcar_roi,
    perfis_para_painel,
    problemas_de_ajustes,
    receita_efetiva,
    selecionar_roi,
)
from src.jutta_ped.motor.cena import Deteccao, criar_cena
from src.jutta_ped.motor.receita import validar_receita

# --- validacao ------------------------------------------------------------------------------------------------------------------------

COMPLETO = {"perfil": "generico", "roi": {"x1": 0.1, "y1": 0.2, "x2": 0.9, "y2": 1.0, "criterio": "intersecao", "intersecao_min": 0.4},
            "crop": {"margem": 0.1, "margem_h": 0.12, "margem_v": 0.08, "minimo_px": 8},
            "execucao": {"orcamento_ms": 800, "validade_max_ms": 1500, "intervalo_min_s": 0.5}}


def test_vazio_e_valido_e_ida_e_volta() -> None:
    assert ajustes_de_dict({}) == AjustesCamera()
    a = ajustes_de_dict(COMPLETO)
    assert a.roi == Roi(0.1, 0.2, 0.9, 1.0, "intersecao", 0.4) and a.crop == MargemCrop(0.1, 0.12, 0.08, 8)
    assert a.execucao == Execucao(800.0, 1500.0, 0.5)
    assert ajustes_de_dict(ajustes_para_dict(a)) == a
    assert ajustes_de_dict(json.loads(json.dumps(ajustes_para_dict(a)))) == a
    assert ajustes_de_dict({"roi": None, "crop": None, "execucao": None, "perfil": None}) == AjustesCamera()


@pytest.mark.parametrize("d, trecho", [
    (None, "objeto"), ([], "objeto"), ("x", "objeto"),
    ({"extra": 1}, "desconhecidas"),
    ({"perfil": "nao_existe"}, "perfil desconhecido"), ({"perfil": 3}, "perfil desconhecido"),
    ({"roi": 3}, "roi deve ser"), ({"roi": {"x1": 0.1}}, "roi exige"),
    ({"roi": {"x1": 0.1, "y1": 0, "x2": 0.5, "y2": 1, "z": 1}}, "desconhecidas"),
    ({"roi": {"x1": -0.1, "y1": 0, "x2": 0.5, "y2": 1}}, "[0, 1]"),
    ({"roi": {"x1": 0, "y1": 0, "x2": 1.1, "y2": 1}}, "[0, 1]"),
    ({"roi": {"x1": 0.5, "y1": 0, "x2": 0.5, "y2": 1}}, "x2 deve ser maior"),
    ({"roi": {"x1": 0.6, "y1": 0, "x2": 0.5, "y2": 1}}, "x2 deve ser maior"),
    ({"roi": {"x1": 0, "y1": 0.7, "x2": 0.5, "y2": 0.7}}, "y2 deve ser maior"),
    ({"roi": {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "criterio": "metade"}}, "criterio"),
    ({"roi": {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "intersecao_min": 0}}, "(0, 1]"),
    ({"roi": {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "intersecao_min": 1.01}}, "(0, 1]"),
    ({"roi": {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "intersecao_min": True}}, "(0, 1]"),
    ({"roi": {"x1": True, "y1": 0, "x2": 1, "y2": 1}}, "[0, 1]"),
    ({"roi": {"x1": "0.1", "y1": 0, "x2": 1, "y2": 1}}, "[0, 1]"),
    ({"roi": {"x1": math.nan, "y1": 0, "x2": 1, "y2": 1}}, "[0, 1]"),
    ({"roi": {"x1": 0, "y1": 0, "x2": math.inf, "y2": 1}}, "[0, 1]"),
    ({"crop": 3}, "crop deve ser"), ({"crop": {"x": 1}}, "desconhecidas"),
    ({"crop": {"margem": -0.01}}, "[0, 1]"), ({"crop": {"margem_h": 1.5}}, "[0, 1]"), ({"crop": {"margem_v": math.nan}}, "[0, 1]"),
    ({"crop": {"margem": True}}, "[0, 1]"), ({"crop": {"margem": "0.1"}}, "[0, 1]"),
    ({"crop": {"minimo_px": -1}}, "minimo_px"), ({"crop": {"minimo_px": 201}}, "minimo_px"), ({"crop": {"minimo_px": 8.5}}, "minimo_px"),
    ({"crop": {"minimo_px": 8.0}}, "minimo_px"), ({"crop": {"minimo_px": True}}, "minimo_px"), ({"crop": {"minimo_px": "8"}}, "minimo_px"),
    ({"execucao": 3}, "execucao deve ser"), ({"execucao": {"x": 1}}, "desconhecidas"),
    ({"execucao": {"orcamento_ms": 0}}, "orcamento_ms"), ({"execucao": {"orcamento_ms": -5}}, "orcamento_ms"),
    ({"execucao": {"orcamento_ms": 60001}}, "orcamento_ms"), ({"execucao": {"orcamento_ms": math.inf}}, "orcamento_ms"),
    ({"execucao": {"orcamento_ms": True}}, "orcamento_ms"), ({"execucao": {"orcamento_ms": "500"}}, "orcamento_ms"),
    ({"execucao": {"validade_max_ms": 0}}, "validade_max_ms"), ({"execucao": {"validade_max_ms": 3_600_001}}, "validade_max_ms"),
    ({"execucao": {"validade_max_ms": math.nan}}, "validade_max_ms"),
    ({"execucao": {"intervalo_min_s": -0.1}}, "intervalo_min_s"), ({"execucao": {"intervalo_min_s": 60.1}}, "intervalo_min_s"),
    ({"execucao": {"intervalo_min_s": False}}, "intervalo_min_s"),
])
def test_invalidos_levantam_value_error_com_o_problema(d, trecho) -> None:
    with pytest.raises(ValueError) as exc:
        ajustes_de_dict(d)
    assert trecho in str(exc.value)
    assert any(trecho in x for x in problemas_de_ajustes(d))


def test_limites_validos_e_varios_problemas_de_uma_vez() -> None:
    ok = {"roi": {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "intersecao_min": 1}, "crop": {"margem": 0, "minimo_px": 200, "margem_h": 1},
          "execucao": {"orcamento_ms": 60000, "validade_max_ms": 3_600_000, "intervalo_min_s": 0}}
    a = ajustes_de_dict(ok)
    assert a.execucao == Execucao(60000.0, 3_600_000.0, 0.0) and a.crop.minimo_px == 200 and a.roi.intersecao_min == 1.0
    ps = problemas_de_ajustes({"perfil": "x", "crop": {"margem": 2, "minimo_px": -3}, "execucao": {"orcamento_ms": 0}, "roi": {"x1": 1, "y1": 0, "x2": 0, "y2": 1}})
    assert len(ps) >= 5


def test_tipos_numericos_viram_float_e_o_hash_e_estavel() -> None:
    a = ajustes_de_dict({"crop": {"margem": 1}, "execucao": {"orcamento_ms": 500}})
    b = ajustes_de_dict({"execucao": {"orcamento_ms": 500.0}, "crop": {"margem": 1.0}})
    assert a == b and hash_ajustes(a) == hash_ajustes(b) and hash_ajustes(a).startswith("sha256:")
    assert hash_ajustes(a) != hash_ajustes(ajustes_de_dict({"crop": {"margem": 0.5}, "execucao": {"orcamento_ms": 500}}))
    assert hash_ajustes(AjustesCamera()) == hash_ajustes(ajustes_de_dict({}))
    assert hash_ajustes(ajustes_de_dict(COMPLETO)) == hash_ajustes(ajustes_de_dict(copy.deepcopy(COMPLETO)))


def test_ajustes_sao_imutaveis() -> None:
    a = ajustes_de_dict(COMPLETO)
    with pytest.raises(Exception):
        a.perfil = "x"                                  # type: ignore[misc]
    with pytest.raises(Exception):
        a.roi.x1 = 0.5                                  # type: ignore[misc]


# --- perfis ---------------------------------------------------------------------------------------------------------------------------

def test_perfis_conferidos_com_o_legado() -> None:
    assert PERFIS["generico"].crop == MargemCrop(margem=0.10)
    assert PERFIS["pediatria_legado"].crop == MargemCrop(margem_h=0.12, margem_v=0.08, minimo_px=8)
    lista = {p["nome"]: p for p in perfis_para_painel()}
    assert set(lista) == {"generico", "pediatria_legado"} and all(p["descricao"] for p in lista.values())
    assert ajustes_de_dict(lista["pediatria_legado"]["ajustes"]).perfil == "pediatria_legado"
    ef = efetivos(AjustesCamera(perfil="pediatria_legado"))
    assert (ef.crop.margem_h, ef.crop.margem_v, ef.crop.minimo_px) == (0.12, 0.08, 8)
    ef = efetivos(AjustesCamera(perfil="generico"))
    assert (ef.crop.margem_h, ef.crop.margem_v, ef.crop.minimo_px) == (0.10, 0.10, 0)


# --- precedencia ----------------------------------------------------------------------------------------------------------------------

def crop_ef(a: AjustesCamera | None, vinculo=None) -> tuple:
    c = efetivos(a, vinculo).crop
    return c.margem, c.margem_h, c.margem_v, c.minimo_px


def test_precedencia_camera_perfil_receita_padrao() -> None:
    assert crop_ef(None) == (None, 0.0, 0.0, 0)                                                             # padrao 0
    assert crop_ef(None, {"margem_recorte": 0.3}) == (None, 0.3, 0.3, 0)                                    # receita
    assert crop_ef(AjustesCamera(perfil="generico"), {"margem_recorte": 0.3}) == (None, 0.10, 0.10, 0)     # perfil > receita
    assert crop_ef(AjustesCamera(perfil="generico", crop=MargemCrop(margem=0.2)), {"margem_recorte": 0.3}) == (None, 0.2, 0.2, 0)   # camera > perfil
    assert crop_ef(AjustesCamera(crop=MargemCrop(margem=0.0)), {"margem_recorte": 0.3}) == (None, 0.0, 0.0, 0)      # explicito zero vence
    # margem unica explicita define h e v, mesmo sobre um perfil com h/v proprios (nunca soma)
    assert crop_ef(AjustesCamera(perfil="pediatria_legado", crop=MargemCrop(margem=0.2))) == (None, 0.2, 0.2, 8)
    # eixo explicito vence a margem unica; o outro eixo usa a margem unica
    assert crop_ef(AjustesCamera(crop=MargemCrop(margem=0.2, margem_h=0.05))) == (None, 0.05, 0.2, 0)
    # eixo explicito de um lado, o outro cai no perfil
    assert crop_ef(AjustesCamera(perfil="pediatria_legado", crop=MargemCrop(margem_h=0.3))) == (None, 0.3, 0.08, 8)
    assert crop_ef(AjustesCamera(perfil="pediatria_legado", crop=MargemCrop(minimo_px=0))) == (None, 0.12, 0.08, 0)
    assert crop_ef(AjustesCamera(crop=MargemCrop(minimo_px=5)), {"margem_recorte": 0.1}) == (None, 0.1, 0.1, 5)


def test_precedencia_execucao() -> None:
    assert efetivos(None).execucao == Execucao(None, None, 0.0)
    assert efetivos(None, {}).execucao.orcamento_ms == 500.0                                                 # padrao da receita
    assert efetivos(None, {"orcamento_ms": 700}).execucao.orcamento_ms == 700.0
    assert efetivos(AjustesCamera(execucao=Execucao(orcamento_ms=900.0)), {"orcamento_ms": 700}).execucao.orcamento_ms == 900.0
    ex = efetivos(AjustesCamera(execucao=Execucao(validade_max_ms=1000.0, intervalo_min_s=2.0))).execucao
    assert ex.validade_max_ms == 1000.0 and ex.intervalo_min_s == 2.0


def test_efetivos_e_idempotente_e_preserva_perfil_e_roi() -> None:
    a = ajustes_de_dict(COMPLETO)
    e1 = efetivos(a)
    assert e1.perfil == "generico" and e1.roi == a.roi and efetivos(e1) == e1
    assert ajustes_de_dict(ajustes_para_dict(e1)) == e1                    # o efetivo tambem e uma entrada valida


# --- selecionar_roi -------------------------------------------------------------------------------------------------------------------

L, A = 1000, 500
ROI = Roi(0.2, 0.2, 0.8, 0.8)                         # px: x 200..800, y 100..400


def sel(roi, bbox, w=L, h=A) -> bool:
    return selecionar_roi(roi, bbox, w, h)


def test_sem_roi_e_quadro_inteiro_ate_caixa_estranha() -> None:
    assert sel(None, (0, 0, 10, 10)) and sel(None, (-50, -50, -10, -10)) and sel(None, (1, 1, 1, 1))


def test_ponto_de_apoio_centro_da_borda_inferior_com_bordas_inclusivas() -> None:
    assert sel(ROI, (400, 0, 600, 300))                                  # apoio (500, 300)
    assert not sel(ROI, (400, 0, 600, 450))                              # pes abaixo da ROI, tronco dentro
    assert sel(ROI, (400, 0, 600, 400))                                  # y2 == borda inferior da ROI: inclusivo
    assert sel(ROI, (100, 0, 300, 300))                                  # apoio x == 200 (borda esquerda): inclusivo
    assert not sel(ROI, (100, 0, 299.9, 300))                            # apoio x = 199.95
    assert sel(ROI, (700, 0, 900, 300))                                  # apoio x == 800 (borda direita): inclusivo
    assert not sel(ROI, (700, 0, 900.2, 300))
    assert not sel(ROI, (400, 0, 600, 99.9)) and sel(ROI, (400, 0, 600, 100))      # borda superior inclusiva


def test_centro() -> None:
    r = Roi(0.2, 0.2, 0.8, 0.8, "centro")
    assert sel(r, (400, 150, 600, 450))                                   # centro (500, 300)
    assert not sel(r, (400, 0, 600, 150))                                 # centro (500, 75): fora (o apoio, 150, estaria dentro)
    assert sel(ROI, (400, 0, 600, 150)) and not sel(r, (400, 0, 600, 150))


def test_intersecao_pela_area_da_caixa() -> None:
    r = Roi(0.2, 0.2, 0.8, 0.8, "intersecao", 0.5)
    assert sel(r, (400, 100, 600, 300))                                   # 100%
    assert sel(r, (700, 100, 900, 300))                                   # metade da largura dentro: exatamente 0.5 -> passa (>=)
    assert not sel(r, (701, 100, 901, 300))                               # um pouco menos de 0.5
    assert not sel(r, (850, 100, 950, 300)) and not sel(r, (800, 100, 900, 300))      # so toca a borda: area zero
    assert sel(Roi(0.2, 0.2, 0.8, 0.8, "intersecao", 1.0), (200, 100, 800, 400))
    assert not sel(Roi(0.2, 0.2, 0.8, 0.8, "intersecao", 1.0), (199, 100, 800, 400))
    # caixa GRANDE com a ROI toda dentro: fracao pequena da caixa
    assert not sel(Roi(0.4, 0.4, 0.6, 0.6, "intersecao", 0.5), (0, 0, 1000, 500))


def test_intersecao_pequena_fracao_documentada() -> None:
    assert not sel(Roi(0.4, 0.4, 0.6, 0.6, "intersecao", 0.1), (0, 0, 1000, 500))     # 200*100 / 500000 = 4%
    assert sel(Roi(0.4, 0.4, 0.6, 0.6, "intersecao", 0.04), (0, 0, 1000, 500))        # exatamente 4% (tolerancia numerica)


def test_caixa_parcialmente_fora_do_quadro_usa_so_a_parte_visivel() -> None:
    roi_baixo = Roi(0.0, 0.8, 1.0, 1.0)                                   # faixa inferior: y 400..500
    assert sel(roi_baixo, (400, 300, 600, 650))                           # pes abaixo do quadro: apoio recortado em y=500 -> dentro (borda inclusiva)
    assert sel(Roi(0, 0, 1, 1), (-100, -100, 50, 50))                     # canto: parte visivel dentro
    # intersecao: a parte visivel e a base da fracao
    r = Roi(0.0, 0.0, 0.5, 1.0, "intersecao", 0.9)
    assert sel(r, (-300, 0, 300, 100))                                    # visivel x 0..300 -> 100% dentro da ROI (0..500)
    assert not sel(r, (-300, 0, 300, 100), w=500, h=A)          # w=500: visivel 0..300 de 0..250 da ROI -> 83% < 90%
    assert not sel(roi_baixo, (-500, 0, -10, 450))                        # inteiramente fora do quadro
    assert not sel(roi_baixo, (400, 600, 600, 700))                       # inteiramente abaixo do quadro
    assert not sel(roi_baixo, (1000, 400, 1200, 500))                     # x1 == largura: nada visivel


def test_roi_minuscula_e_quadro_pequeno() -> None:
    tiny = Roi(0.5, 0.5, 0.501, 0.501)                                    # 1 x 0.5 px em 1000x500
    assert sel(tiny, (400, 0, 600, 250.1)) and not sel(tiny, (400, 0, 600, 251)) and sel(tiny, (402, 0, 600, 250.1)) and not sel(tiny, (403, 0, 600, 250.1))
    assert sel(Roi(0.5, 0.5, 0.501, 0.501, "centro"), (499.5, 249.5, 500.5, 250.5))
    assert not sel(Roi(0.5, 0.5, 0.501, 0.501, "intersecao", 0.5), (400, 100, 600, 400))


def test_resolucao_diferente_a_roi_acompanha_a_cena() -> None:
    caixa_rel = (0.4, 0.1, 0.6, 0.6)
    for w, h in ((1000, 500), (640, 480), (1920, 1080), (100, 100)):
        dentro = (caixa_rel[0] * w, caixa_rel[1] * h, caixa_rel[2] * w, caixa_rel[3] * h)
        assert selecionar_roi(ROI, dentro, w, h), (w, h)
        fora = (0.05 * w, 0.05 * h, 0.15 * w, 0.15 * h)
        assert not selecionar_roi(ROI, fora, w, h), (w, h)
    assert not sel(ROI, (400, 0, 600, 300), w=0, h=0)


@pytest.mark.parametrize("bbox", [None, (1, 2, 3), (math.nan, 0, 10, 10), (0, 0, math.inf, 10), ("a", 0, 1, 1), (10, 10, 10, 20), (10, 10, 5, 20)])
def test_caixa_invalida_nao_pertence(bbox) -> None:
    assert sel(ROI, bbox) is False


# --- marcar_roi -----------------------------------------------------------------------------------------------------------------------

def test_marcar_roi_nao_descarta_nada_e_preserva_o_resto() -> None:
    quadro = object()
    cena = criar_cena("c", 3, 7, 12.5, L, A, [Deteccao(1, "person", (400, 0, 600, 300), 0.9), Deteccao(2, "person", (0, 0, 50, 50), 0.4),
                                              Deteccao(3, "veiculo", (700, 0, 900, 450), 0.7)], quadro)
    nova = marcar_roi(cena, ROI)
    assert [(d.trilha_id, d.na_roi) for d in nova.deteccoes] == [(1, True), (2, False), (3, False)]
    assert nova.quadro is quadro and (nova.camera_ref, nova.geracao, nova.quadro_id, nova.recebido_em) == ("c", 3, 7, 12.5)
    assert all(d.na_roi for d in cena.deteccoes)                                # a original nao muda (imutavel)
    assert [d.confianca for d in nova.deteccoes] == [0.9, 0.4, 0.7]
    assert marcar_roi(cena, None) is cena
    assert marcar_roi(criar_cena("c", 1, 1, 0.0, L, A, []), ROI).deteccoes == ()


# --- receita_efetiva ------------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mundo(tmp_path_factory: pytest.TempPathFactory) -> Mundo:
    return Mundo(tmp_path_factory.mktemp("ajustes"))


def contratos(mundo: Mundo, r: dict) -> dict:
    return mundo.motor._contratos(r)


def condicoes(r: dict) -> list[dict]:
    if r.get("contrato") == 2:
        return [c for p in r["papeis"].values() for c in p["condicoes"]]
    return r["regra"]["condicoes"]


def vinculos_por_id(r: dict) -> dict:
    return {v["especialista"]: v for v in r["vinculos"]}


@pytest.mark.parametrize("fabrica", [receita_v1, receita_cena], ids=["contrato1", "contrato2"])
def test_receita_efetiva_nao_muta_e_continua_valida(mundo: Mundo, fabrica) -> None:
    r = fabrica()
    original = copy.deepcopy(r)
    cts = contratos(mundo, r)
    assert validar_receita(r, cts) == []
    aj = ajustes_de_dict({"perfil": "pediatria_legado", "crop": {"margem": 0.2}, "execucao": {"orcamento_ms": 1234, "validade_max_ms": 1000}})
    saida = receita_efetiva(r, aj, cts)
    assert r == original and saida is not r
    assert validar_receita(saida, cts) == []
    for v in saida["vinculos"]:
        assert v["margem_recorte"] == 0.2 and v["orcamento_ms"] == 1234
    assert all(c["validade_ms"] <= 1000 for c in condicoes(saida))
    saida["vinculos"][0]["x"] = 1                                           # a copia e profunda: nao afeta a entrada
    assert r == original


def test_receita_efetiva_sem_ajustes_ou_vazios_e_identica(mundo: Mundo) -> None:
    r = receita_cena()
    cts = contratos(mundo, r)
    assert receita_efetiva(r, None, cts) == r
    assert receita_efetiva(r, AjustesCamera(), cts) == r
    assert receita_efetiva(r, AjustesCamera(roi=Roi(0, 0, 1, 1), execucao=Execucao(intervalo_min_s=1.0)), cts) == r


def test_receita_efetiva_margem_hv_usa_o_maior_como_limite_superior(mundo: Mundo) -> None:
    r = receita_v1()
    saida = receita_efetiva(r, AjustesCamera(perfil="pediatria_legado"), contratos(mundo, r))
    assert all(v["margem_recorte"] == 0.12 for v in saida["vinculos"])
    assert validar_receita(saida, contratos(mundo, r)) == []


def test_receita_efetiva_margem_da_receita_vale_sem_perfil_e_camera_zero_vence(mundo: Mundo) -> None:
    r = receita_v1()
    r["vinculos"][0]["margem_recorte"] = 0.3
    cts = contratos(mundo, r)
    assert receita_efetiva(r, AjustesCamera(), cts)["vinculos"][0]["margem_recorte"] == 0.3
    assert receita_efetiva(r, AjustesCamera(perfil="generico"), cts)["vinculos"][0]["margem_recorte"] == 0.10
    assert receita_efetiva(r, AjustesCamera(crop=MargemCrop(margem=0.0)), cts)["vinculos"][0]["margem_recorte"] == 0.0
    # o outro vinculo (sem margem na receita) nao ganha o campo quando a margem efetiva e 0
    assert "margem_recorte" not in receita_efetiva(r, AjustesCamera(), cts)["vinculos"][1]


def test_validade_so_aperta_nunca_amplia_nunca_zero(mundo: Mundo) -> None:
    r = receita_cena()                                                      # papel alvo: validade_ms 1500 declarada; carro: sem declarar
    cts = contratos(mundo, r)
    val_attr = {pid: {n: a["validade_ms"] for n, a in c["atributos"].items()} for pid, c in cts.items()}
    # 1) apertar abaixo da declarada e da do atributo
    s1 = receita_efetiva(r, AjustesCamera(execucao=Execucao(validade_max_ms=400.0)), cts)
    assert [c["validade_ms"] for c in condicoes(s1)] == [400, 400] and validar_receita(s1, cts) == []
    # 2) maior que a declarada: mantem a declarada; maior que o atributo: nao cria campo
    s2 = receita_efetiva(r, AjustesCamera(execucao=Execucao(validade_max_ms=3_000_000.0)), cts)
    assert s2 == r
    # 3) entre a declarada (1500) e a do atributo da outra condicao
    s3 = receita_efetiva(r, AjustesCamera(execucao=Execucao(validade_max_ms=2000.0)), cts)
    alvo, carro = (s3["papeis"]["alvo"]["condicoes"][0], s3["papeis"]["carro"]["condicoes"][0])
    assert alvo["validade_ms"] == 1500                                       # nunca amplia a declarada
    attr_carro = val_attr[VEIC]["usa_cracha"]
    assert carro.get("validade_ms", attr_carro) == min(2000, attr_carro)
    assert val_attr[BEBE]["bebe_no_colo"] >= 1500 and validar_receita(s3, cts) == []
    # 4) numero nao inteiro continua positivo e valido
    s4 = receita_efetiva(r, AjustesCamera(execucao=Execucao(validade_max_ms=0.5)), cts)
    assert all(0 < c["validade_ms"] <= 1 for c in condicoes(s4))


def test_validade_sem_contrato_do_especialista_so_usa_a_do_ajuste_ou_a_declarada() -> None:
    r = {"id": "x", "versao": "1", "vinculos": [{"especialista": "a"}], "regra": {"combinacao": "todas", "condicoes": [
        {"especialista": "a", "atributo": "n", "igual": "sim"}, {"especialista": "a", "atributo": "m", "igual": "sim", "validade_ms": 100}]}}
    s = receita_efetiva(r, AjustesCamera(execucao=Execucao(validade_max_ms=700.0)), {})
    assert [c["validade_ms"] for c in s["regra"]["condicoes"]] == [700, 100]
    assert "validade_ms" not in r["regra"]["condicoes"][0]


def test_receita_efetiva_maior_que_e_operadores_numericos_tambem_apertam(mundo: Mundo) -> None:
    from test_motor_regras_motor import CMPA, CMPB, receita_cmp
    for op in ({"minimo": 0.5}, {"maior_que": {"especialista": CMPB, "atributo": "prob_verde"}}):
        r = receita_cmp(op)
        cts = contratos(mundo, r)
        s = receita_efetiva(r, AjustesCamera(execucao=Execucao(validade_max_ms=250.0)), cts)
        assert s["papeis"]["alvo"]["condicoes"][0]["validade_ms"] == 250 and validar_receita(s, cts) == []
        assert CMPA in vinculos_por_id(s)


def test_receita_nao_dict_ou_sem_condicoes_nao_quebra() -> None:
    assert receita_efetiva({}, AjustesCamera(execucao=Execucao(validade_max_ms=5.0)), {}) == {}
    assert receita_efetiva({"vinculos": "x", "regra": 3}, AjustesCamera(perfil="generico", execucao=Execucao(validade_max_ms=5.0)), None) == {"vinculos": "x", "regra": 3}


def test_pacote_de_ajustes_nao_depende_de_disco(tmp_path: Path) -> None:
    antes = set(tmp_path.iterdir())
    efetivos(ajustes_de_dict(COMPLETO))
    assert set(tmp_path.iterdir()) == antes
