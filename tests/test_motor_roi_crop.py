"""05f: recorte com folgas por eixo/minimo (paridade com o legado `crop_frame`), `Recorte.folgas_px`, e sujeitos fora da ROI no motor.

Relogio controlado (`Relogio.t`), sem sleeps."""
from __future__ import annotations

import itertools
from pathlib import Path

import numpy as np
import pytest
from test_motor_regras_motor import BEBE, VEIC, Mundo, linha, receita_cena, sujeito
from test_motor_sombra import ALTURA, LARGURA, NAO_AZUL, SIM_AZUL, quadro
from test_motor_sombra import receita as receita_v1

from modulo.pediatria.crop_pipeline import crop_frame
from src.jutta_ped.motor.ajustes import AjustesCamera, MargemCrop
from src.jutta_ped.motor.cena import Deteccao, criar_cena
from src.jutta_ped.motor.observacao import Recorte, ajustar_bbox, extrair_recorte, geometria_recorte

W, H = 200, 150
RNG = np.random.RandomState(0)
QUADRO = RNG.randint(0, 255, (H, W, 3), dtype=np.uint8)
LEGADO = dict(margem_h=0.12, margem_v=0.08, minimo_px=8)


def mesma(a, b) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and a.shape == b.shape and bool((a == b).all()))


# --- caminho antigo intacto -----------------------------------------------------------------------------------------------------------

def antigo(quadro_, bbox, margem):
    """Copia da implementacao anterior de extrair_recorte (arredondamento por round)."""
    base = ajustar_bbox(quadro_, bbox)
    if base is None or not (0.0 <= margem <= 1.0):
        return None
    x1, y1, x2, y2 = base
    largura, altura = quadro_.shape[1], quadro_.shape[0]
    mx, my = int(round((x2 - x1) * margem)), int(round((y2 - y1) * margem))
    cx1, cy1, cx2, cy2 = max(0, x1 - mx), max(0, y1 - my), min(largura, x2 + mx), min(altura, y2 + my)
    return quadro_[cy1:cy2, cx1:cx2], (cx1, cy1), base, (x1 - cx1, y1 - cy1, x2 - cx1, y2 - cy1)


@pytest.mark.parametrize("margem", [0.0, 0.05, 0.1, 0.25, 0.5, 1.0])
def test_so_margem_mantem_o_comportamento_antigo(margem) -> None:
    xs = [-10, 0, 7, 50, 180, 199, 230]
    for x1, x2, y1, y2 in itertools.product(xs, xs, xs, xs):
        if x2 <= x1 or y2 <= y1:
            continue
        bb = (x1 + 0.4, y1 + 0.6, x2 + 0.5, y2)
        esperado, r = antigo(QUADRO, bb, margem), extrair_recorte(QUADRO, bb, margem)
        assert (esperado is None) == (r is None)
        if r is not None:
            assert mesma(esperado[0], r.imagem) and r.origem_xy == esperado[1] and r.bbox_quadro == esperado[2] and r.bbox_no_recorte == esperado[3]


def test_margem_invalida_ou_bbox_ruim_devolve_none() -> None:
    assert extrair_recorte(QUADRO, (10, 10, 50, 50), 1.5) is None and extrair_recorte(QUADRO, (10, 10, 50, 50), -0.1) is None
    assert extrair_recorte(QUADRO, (10, 10, 50, 50), float("nan")) is None
    assert extrair_recorte(QUADRO, (10, 10, 50, 50), margem_h=1.2) is None and extrair_recorte(QUADRO, (10, 10, 50, 50), margem_v=-1) is None
    assert extrair_recorte(QUADRO, (10, 10, 50, 50), minimo_px=-1) is None and extrair_recorte(QUADRO, (10, 10, 50, 50), minimo_px=2.5) is None  # type: ignore[arg-type]
    assert extrair_recorte(QUADRO, (300, 10, 400, 50), **LEGADO) is None and extrair_recorte(None, (1, 1, 5, 5), **LEGADO) is None


# --- paridade com o legado -------------------------------------------------------------------------------------------------------------

def test_paridade_com_crop_frame_em_grade_de_bboxes_inteiras() -> None:
    xs = [-30, -5, 0, 3, 20, 100, 180, 195, 199, 200, 230]
    n = visiveis = 0
    for x1, x2, y1, y2 in itertools.product(xs, xs, xs, xs):
        if x2 <= x1 or y2 <= y1:
            continue
        bb = (x1, y1, x2, y2)
        r = extrair_recorte(QUADRO, bb, **LEGADO)
        legado = crop_frame(QUADRO, bb)
        if ajustar_bbox(QUADRO, bb) is None:
            assert r is None                      # sem parte visivel: a mae recusa (ver teste da diferenca documentada)
            continue
        visiveis += 1
        assert mesma(legado, None if r is None else r.imagem), bb
        n += 1
    assert n == visiveis and n > 1000


def test_diferenca_documentada_caixa_sem_parte_visivel_perto_da_borda() -> None:
    """Unica divergencia com o legado em bboxes inteiras: `crop_frame` recorta mesmo uma caixa 100% FORA do quadro quando a folga a traz
    para dentro (ex.: x1=-30..-5 com folga >= 8 alcanca o quadro); a mae devolve None (nao ha nada visivel para o especialista)."""
    bb = (-30, -30, -5, -5)
    assert crop_frame(QUADRO, bb).shape[:2] == (3, 3)
    assert extrair_recorte(QUADRO, bb, **LEGADO) is None
    # sem diferenca de 1 px em x2/y2: ambos usam fim EXCLUSIVO e limite `largura` (nao `largura - 1`)
    bb = (150, 100, 200, 150)
    assert mesma(crop_frame(QUADRO, bb), extrair_recorte(QUADRO, bb, **LEGADO).imagem)
    assert extrair_recorte(QUADRO, bb, **LEGADO).imagem.shape[:2] == (50 + 8, 50 + 8)


def test_bbox_fracionaria_difere_no_maximo_poucos_pixels_do_legado() -> None:
    """O legado TRUNCA (`int`) cada coordenada; a mae arredonda PARA FORA (floor/ceil) para nao perder pixel tocado pela caixa.
    Para caixas fracionarias isso pode acrescentar ate 1 px por lado + 1 px na folga (aqui, tolerancia de 3 px por eixo)."""
    rs = np.random.RandomState(1)
    for _ in range(500):
        x1, y1 = rs.uniform(0, 150), rs.uniform(0, 100)
        bb = (x1, y1, x1 + rs.uniform(1, 80), y1 + rs.uniform(1, 80))
        r, legado = extrair_recorte(QUADRO, bb, **LEGADO), crop_frame(QUADRO, bb)
        assert r is not None and legado is not None
        assert abs(r.imagem.shape[0] - legado.shape[0]) <= 3 and abs(r.imagem.shape[1] - legado.shape[1]) <= 3
    # inteira disfarcada de float: identica
    assert mesma(crop_frame(QUADRO, (30, 20, 90, 120)), extrair_recorte(QUADRO, (30.0, 20.0, 90.0, 120.0), **LEGADO).imagem)


# --- folgas ---------------------------------------------------------------------------------------------------------------------------

def test_folga_por_lado_e_max_do_minimo_e_do_truncamento() -> None:
    bb = (60, 50, 160, 130)                                                 # 100 x 80
    r = extrair_recorte(QUADRO, bb, **LEGADO)
    assert r.folgas_px == (12, 12, 8, 8) and r.origem_xy == (48, 42) and r.imagem.shape[:2] == (80 + 16, 100 + 24)
    assert r.bbox_quadro == bb and r.bbox_no_recorte == (12, 8, 112, 88) and r.quadro_wh == (W, H)
    assert r.altura_alvo_px == 80
    r = extrair_recorte(QUADRO, bb, margem_h=0.0, margem_v=0.0, minimo_px=0)             # nova via, sem folga
    assert r.folgas_px == (0, 0, 0, 0) and r.imagem.shape[:2] == (80, 100)
    r = extrair_recorte(QUADRO, (60, 50, 62, 52), **LEGADO)                                # caixa minuscula: so o minimo
    assert r.folgas_px == (8, 8, 8, 8)
    r = extrair_recorte(QUADRO, (0, 0, 100, 100), margem_h=0.333, margem_v=0.0)            # truncamento: int(33.3)=33
    assert r.folgas_px == (0, 33, 0, 0)


def test_eixo_sem_fracao_explicita_usa_a_margem() -> None:
    bb = (60, 50, 160, 130)
    r = extrair_recorte(QUADRO, bb, 0.1, margem_h=0.2)
    assert r.folgas_px == (20, 20, 8, 8)                                    # h=0.2*100, v=0.1*80
    r = extrair_recorte(QUADRO, bb, 0.1, minimo_px=12)
    assert r.folgas_px == (12, 12, 12, 12)                                  # max(12, int(10)) e max(12, int(8))
    r = extrair_recorte(QUADRO, bb, 0.1, minimo_px=5)
    assert r.folgas_px == (10, 10, 8, 8)


def test_folgas_apos_o_ajuste_ao_quadro_e_transformacao_preservada() -> None:
    bb = (0, 0, 40, 40)
    r = extrair_recorte(QUADRO, bb, **LEGADO)
    assert r.folgas_px == (0, 8, 0, 8) and r.origem_xy == (0, 0)
    bb = (170, 120, 230, 190)                                               # sai do quadro: bbox ajustada (170,120,200,150)
    r = extrair_recorte(QUADRO, bb, **LEGADO)
    assert r.bbox_quadro == (170, 120, 200, 150) and r.folgas_px[1] == 0 and r.folgas_px[3] == 0 and r.folgas_px[0] == 8 and r.folgas_px[2] == 8
    x, y = r.para_quadro(*r.bbox_no_recorte[:2])
    assert (x, y) == (170, 120) and r.para_recorte(170, 120) == r.bbox_no_recorte[:2]
    assert mesma(r.imagem, QUADRO[r.origem_xy[1]:r.origem_xy[1] + r.imagem.shape[0], r.origem_xy[0]:r.origem_xy[0] + r.imagem.shape[1]])
    g = geometria_recorte(QUADRO, bb, **LEGADO)
    assert g == ((170, 120, 200, 150), (162, 112, 200, 150))


def test_recorte_e_copia_e_construcao_antiga_continua_valida() -> None:
    r = extrair_recorte(QUADRO, (10, 10, 50, 50), **LEGADO)
    r.imagem[:] = 0
    assert QUADRO[10:50, 10:50].any()                                       # o quadro nao foi alterado
    antigo_ = Recorte(QUADRO[:2, :2], (0, 0), (0, 0, 2, 2), (0, 0, 2, 2), (W, H))             # sem folgas_px: default
    assert antigo_.folgas_px == (0, 0, 0, 0)


# --- motor: sujeitos fora da ROI ------------------------------------------------------------------------------------------------------

def cena_roi(t: float, itens, *, cam: str = "cam1", ger: int = 1, sem_quadro: bool = False):
    """itens = (trilha, classe, x, y, bgr_ou_b2, na_roi). Caixas 80x200; pixel (x, y) leva o que o modelo falso le."""
    q = quadro()
    dets = []
    for tr, classe, x, y, cor, dentro in itens:
        if classe == "person":
            q[y, x] = cor
        else:
            q[y, x] = (0, 0, 0)
            q[y, x + 1, 0] = cor
        dets.append(Deteccao(tr, classe, (x, y, x + 80, y + 200), na_roi=dentro))
    return criar_cena(cam, ger, int(t * 1000), t, LARGURA, ALTURA, dets, None if sem_quadro else q)


@pytest.fixture
def mundo(tmp_path: Path) -> Mundo:
    m = Mundo(tmp_path)
    m.configurar([receita_cena(), receita_v1()], {"cam1": ("r.cena", "1"), "cam2": ("r.teste", "1")})
    return m


def rodar(w: Mundo, t: float, itens, **kw) -> dict:
    w.rel.t = t
    saida = w.motor.processar_cena(cena_roi(t, itens, **kw))
    assert len(saida) == 1
    return saida[0]


def test_contrato2_sujeito_fora_da_roi_e_indeterminado_sem_especialista(mundo: Mundo) -> None:
    ev = rodar(mundo, 100.0, [(1, "person", 50, 60, SIM_AZUL, False), (7, "veiculo", 150, 60, 250, True)])
    p = sujeito(ev, 1)
    assert p["papeis"] == {"alvo": "indeterminado"}
    assert p["detalhes"]["alvo"][0]["motivo"] == "fora_da_roi" and p["detalhes"]["alvo"][0]["resultado"] == "indeterminado"
    assert {k[:2] for k in mundo.motor._cache["cam1"]} == {(7, VEIC)}          # nada foi enviado ao especialista da pessoa fora da ROI
    assert ev["descartes"] == {"fora_da_roi": 1} and mundo.motor.contadores["sujeito_fora_da_roi"] == 1
    assert ev["erros"] == {}


def test_candidato_proximo_fora_da_roi_impede_nenhum_proximo_falso() -> None:
    from src.jutta_ped.motor.regras import relacao_3v
    assert relacao_3v("nenhum_proximo", False, True) == "indeterminado"        # semantica da relacao (contrato 2)


def test_relacao_com_candidato_fora_da_roi_fica_indeterminada_e_nao_sim_nem_nao(tmp_path: Path) -> None:
    m = Mundo(tmp_path)
    r = receita_cena()
    r["relacoes"]["perto_carro"] = {"tipo": "nenhum_proximo", "sujeito": "alvo", "outro": "carro", "distancia": {"unidade": "pixels", "maximo": 100}}
    m.configurar([r], {"cam1": ("r.cena", "1")})
    # carro FORA da ROI, a 20 px da pessoa (que esta dentro e 'sim')
    ev = rodar(m, 100.0, [(1, "person", 50, 60, SIM_AZUL, True), (7, "veiculo", 150, 60, 250, False)])
    assert sujeito(ev, 7, "veiculo")["papeis"] == {"carro": "indeterminado"}
    assert sujeito(ev, 1)["relacoes"]["perto_carro"]["estado"] == "indeterminado"
    assert sujeito(ev, 1)["relacoes"]["perto_carro"]["proximos_indeterminados"] == [7]
    # o mesmo carro DENTRO e 'nao' (cracha ausente): ai sim "nenhum proximo" -> sim
    ev = rodar(m, 100.5, [(1, "person", 50, 60, SIM_AZUL, True), (7, "veiculo", 150, 60, 10, True)])
    assert sujeito(ev, 1)["relacoes"]["perto_carro"]["estado"] == "sim"


def test_fora_da_roi_pausa_o_tempo_nao_conta_nem_zera(mundo: Mundo) -> None:
    par = lambda dentro: [(1, "person", 50, 60, SIM_AZUL, dentro), (7, "veiculo", 150, 60, 250, True)]       # noqa: E731
    for t in (100.0, 100.5, 101.0):
        ev = rodar(mundo, t, par(True))
    assert linha(ev)["estado"] == "candidato" and linha(ev)["positivo_s"] == 1.0
    ev = rodar(mundo, 101.5, par(False))                                       # fora da ROI: indeterminado -> pausa
    assert linha(ev)["positivo_s"] == 1.0 and linha(ev)["estado"] == "candidato"
    ev = rodar(mundo, 102.0, par(True))
    assert linha(ev)["positivo_s"] == 1.0


def test_cena_inteira_fora_da_roi_avanca_o_tempo_e_nao_executa_nada(mundo: Mundo) -> None:
    fora = [(1, "person", 50, 60, SIM_AZUL, False), (7, "veiculo", 150, 60, 250, False)]
    antes = list(mundo.backend.lotes)
    for t in (100.0, 100.5, 101.0):
        ev = rodar(mundo, t, fora)
        assert {sujeito(ev, 1)["papeis"]["alvo"], sujeito(ev, 7, "veiculo")["papeis"]["carro"]} == {"indeterminado"}
    assert mundo.backend.lotes == antes and mundo.motor._cache["cam1"] == {}
    assert mundo.motor.contadores["cena_avaliada"] == 3


def test_todos_na_roi_o_evento_e_identico_ao_de_antes(tmp_path: Path) -> None:
    a, b = Mundo(tmp_path, "a"), Mundo(tmp_path, "b")
    for w in (a, b):
        w.configurar([receita_cena()], {"cam1": ("r.cena", "1")})
    itens = [(1, "person", 50, 60, SIM_AZUL, True), (7, "veiculo", 150, 60, 250, True)]
    sem_flag = [(1, "person", 50, 60, SIM_AZUL, True), (7, "veiculo", 150, 60, 250, True)]
    ea, eb = rodar(a, 100.0, itens), rodar(b, 100.0, sem_flag)
    assert ea == eb and ea["descartes"] == {} and "fora_da_roi" not in str(ea)
    assert Deteccao(1, "person", (0, 0, 1, 1)).na_roi is True                        # padrao preserva tudo


def test_contrato1_fora_da_roi_nao_gera_evento_nem_recorte(mundo: Mundo) -> None:
    mundo.rel.t = 100.0
    ev = mundo.motor.processar_cena(cena_roi(100.0, [(1, "person", 50, 60, SIM_AZUL, True), (2, "person", 300, 100, SIM_AZUL, False)], cam="cam2"))
    assert [e["trilha_id"] for e in ev] == [1] and ev[0]["veredito"] == "sim"
    assert {k[0] for k in mundo.motor._cache["cam2"]} == {1}
    assert mundo.motor.contadores["sujeito_fora_da_roi"] == 1
    mundo.rel.t = 100.5
    assert mundo.motor.processar_cena(cena_roi(100.5, [(2, "person", 300, 100, SIM_AZUL, False)], cam="cam2")) == []     # tudo fora: sem eventos
    assert mundo.motor._geracao["cam2"] == 1                                         # mas a geracao/camera segue valida
    mundo.rel.t = 101.0
    assert mundo.motor.processar_cena(cena_roi(101.0, [(3, "person", 50, 60, NAO_AZUL, True)], cam="cam2", ger=2))[0]["veredito"] == "nao"
    assert mundo.motor._geracao["cam2"] == 2


# --- motor: ajustes de crop ------------------------------------------------------------------------------------------------------------

def test_definir_ajustes_muda_o_recorte_enviado_ao_especialista(mundo: Mundo) -> None:
    capturados: list[tuple[int, int]] = []
    original = mundo.motor.executor.executar

    def espia(pid, pedidos):
        capturados.extend(tuple(p.recorte.shape[:2]) for p in pedidos)
        return original(pid, pedidos)

    mundo.motor.executor.executar = espia                                      # type: ignore[method-assign]
    rodar(mundo, 100.0, [(1, "person", 50, 60, SIM_AZUL, True)])
    assert capturados == [(200, 80)]                                           # sem ajustes: margem_recorte 0 da receita
    mundo.motor.definir_ajustes("cam1", AjustesCamera(perfil="pediatria_legado"))
    assert mundo.motor._cache["cam1"] == {}                                    # mudar o crop descarta atributos antigos
    rodar(mundo, 100.5, [(1, "person", 50, 60, SIM_AZUL, True)])
    assert capturados[-1] == (200 + 2 * 16, 80 + 2 * 9)                        # v: max(8, int(200*0.08)=16) por lado; h: max(8, int(80*0.12)=9)
    mundo.motor.definir_ajustes("cam1", None)
    rodar(mundo, 101.0, [(1, "person", 50, 60, SIM_AZUL, True)])
    assert capturados[-1] == (200, 80)
