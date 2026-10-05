"""05f: `DiagnosticoMestre` (so memoria) e os consumidores independentes da `PortaSombra` (cadencia, ROI, ajustes a quente, geracao).

Relogio controlado; sem sleeps como sincronizacao."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from test_motor_cena_mestre import (
    MotorCenaFalso,
    ServicoMestreFalso,
    pessoa,
    roda,
)

from src.jutta_ped.motor.ajustes import AjustesCamera, Execucao, MargemCrop, Roi, ajustes_de_dict, efetivos
from src.jutta_ped.motor.cena import Cena, Deteccao, criar_cena
from src.jutta_ped.motor.diagnostico import DiagnosticoMestre
from src.jutta_ped.painel.sombra_service import PortaSombra

W, H = 640, 480


class Relogio:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def quadro_xadrez() -> np.ndarray:
    q = np.random.RandomState(3).randint(0, 255, (H, W, 3), dtype=np.uint8)
    return q


def cena(qid: int = 1, itens=((1, 50, 60, 130, 260, True),), *, t: float | None = None, ger: int = 1, quadro="auto", ref: str = "cam-x") -> Cena:
    dets = [Deteccao(tr, "person", (float(x1), float(y1), float(x2), float(y2)), 0.9, na_roi=dentro) for tr, x1, y1, x2, y2, dentro in itens]
    q = quadro_xadrez() if isinstance(quadro, str) else quadro
    return criar_cena(ref, ger, qid, float(qid) if t is None else t, W, H, dets, q)


# --- DiagnosticoMestre ----------------------------------------------------------------------------------------------------------------

def test_sem_dado_resumo_vazio_e_jpegs_none() -> None:
    d = DiagnosticoMestre(Relogio())
    r = d.resumo()
    assert r["ativo"] is False and r["recebido_ha_s"] is None and r["sujeitos"] == [] and r["roi"] is None
    assert r["contadores"] == {"cenas": 0, "vazias": 0, "recusadas": 0} and r["quadro_id"] is None
    assert d.jpeg_quadro() is None and d.jpeg_recorte(1) is None
    json.dumps(r)


def test_resumo_no_formato_do_contrato_com_recorte_dos_ajustes_efetivos() -> None:
    rel = Relogio(100.0)
    d = DiagnosticoMestre(rel)
    d.definir_ajustes(ajustes_de_dict({"perfil": "pediatria_legado", "roi": {"x1": 0.1, "y1": 0.1, "x2": 0.9, "y2": 0.9}}))
    d.processar_cena(cena(7, [(1, 50, 60, 130, 260, True), (2, 400, 300, 440, 340, False)], t=100.0, ger=4))
    rel.t = 102.5
    r = d.resumo()
    assert (r["ativo"], r["recebido_ha_s"], r["quadro_id"], r["geracao"], r["largura"], r["altura"]) == (True, 2.5, 7, 4, W, H)
    assert r["roi"]["criterio"] == "ponto_de_apoio" and r["roi"]["px"] == [64.0, 48.0, 576.0, 432.0]
    assert r["ajustes"]["perfil"] == "pediatria_legado" and r["ajustes"]["crop"] == {"margem": None, "margem_h": 0.12, "margem_v": 0.08, "minimo_px": 8}
    assert r["contadores"] == {"cenas": 1, "vazias": 0, "recusadas": 0}
    dentro, fora = r["sujeitos"]
    assert dentro == {"trilha_id": 1, "classe": "person", "bbox_xyxy": [50.0, 60.0, 130.0, 260.0], "confianca": 0.9, "na_roi": True,
                      "recorte": {"origem_xy": [41, 44], "tamanho": [98, 232], "bbox_no_recorte": [9, 16, 89, 216],
                                  "folgas_px": {"esq": 9, "dir": 9, "topo": 16, "base": 16}}, "motivo_sem_recorte": None}
    assert fora["na_roi"] is False and fora["recorte"] is None and fora["motivo_sem_recorte"] == "fora_da_roi"
    json.dumps(r)


def test_recorte_vazio_sem_quadro_e_borda() -> None:
    d = DiagnosticoMestre(Relogio())
    d.processar_cena(cena(1, [(1, 700, 10, 800, 60, True), (2, 0, 0, 50, 50, True)]))
    a, b = d.resumo()["sujeitos"]
    assert a["recorte"] is None and a["motivo_sem_recorte"] == "recorte_vazio_ou_invalido"
    assert b["recorte"]["origem_xy"] == [0, 0] and b["recorte"]["folgas_px"] == {"esq": 0, "dir": 0, "topo": 0, "base": 0}
    d.processar_cena(cena(2, [(1, 10, 10, 50, 50, True)], quadro=None))
    s = d.resumo()["sujeitos"][0]
    assert s["recorte"] is None and s["motivo_sem_recorte"] == "sem_quadro"
    assert d.jpeg_quadro() is None and d.jpeg_recorte(1) is None


def test_contadores_cena_vazia_e_geracao_menor_recusada() -> None:
    d = DiagnosticoMestre(Relogio())
    assert d.processar_cena(cena(1, [], ger=2)) == []
    assert d.resumo()["sujeitos"] == [] and d.resumo()["ativo"] is True
    d.processar_cena(cena(2, [(1, 10, 10, 50, 50, True)], ger=1))                      # geracao menor: recusada, nao substitui
    r = d.resumo()
    assert r["geracao"] == 2 and r["quadro_id"] == 1 and r["contadores"] == {"cenas": 1, "vazias": 1, "recusadas": 1}
    d.processar_cena(cena(3, [(1, 10, 10, 50, 50, True)], ger=3))
    r = d.resumo()
    assert r["geracao"] == 3 and r["quadro_id"] == 3 and r["contadores"] == {"cenas": 2, "vazias": 1, "recusadas": 1} and len(r["sujeitos"]) == 1
    d.limpar()
    r = d.resumo()
    assert r["ativo"] is False and r["contadores"] == {"cenas": 0, "vazias": 0, "recusadas": 0}
    d.processar_cena(cena(1, [], ger=1))                                               # apos limpar, qualquer geracao volta a valer
    assert d.resumo()["geracao"] == 1


def decodificar(b: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR)
    assert img is not None
    return img


def test_jpeg_quadro_decodifica_reduz_para_960_e_desenha_roi_e_caixas() -> None:
    q = np.zeros((1080, 1920, 3), np.uint8)
    d = DiagnosticoMestre(Relogio())
    d.definir_ajustes(AjustesCamera(roi=Roi(0.25, 0.25, 0.75, 0.75)))
    d.processar_cena(criar_cena("c", 1, 1, 0.0, 1920, 1080, [Deteccao(1, "person", (800, 400, 1000, 900), 0.8, na_roi=True),
                                                           Deteccao(2, "person", (100, 100, 300, 500), 0.5, na_roi=False)], q))
    img = decodificar(d.jpeg_quadro())
    assert img.shape == (540, 960, 3)
    cores = {tuple(int(v) for v in px) for px in img.reshape(-1, 3)[::37]}
    assert len(cores) > 1                                                              # foi desenhado sobre o quadro preto
    assert img[134:138, 240:720].any() and img[403:407, 240:720].any()                  # linhas da ROI em y=0.25*540 e y=0.75*540


def test_jpeg_quadro_pequeno_nao_e_ampliado_e_cinza_funciona() -> None:
    d = DiagnosticoMestre(Relogio())
    d.processar_cena(criar_cena("c", 1, 1, 0.0, 200, 100, [Deteccao(1, "person", (10, 10, 60, 90))], np.zeros((100, 200), np.uint8)))
    assert decodificar(d.jpeg_quadro()).shape == (100, 200, 3)


def test_jpeg_recorte_e_o_recorte_atual_do_sujeito() -> None:
    q = quadro_xadrez()
    d = DiagnosticoMestre(Relogio())
    d.definir_ajustes(AjustesCamera(crop=MargemCrop(margem_h=0.0, margem_v=0.0)))
    d.processar_cena(cena(1, [(1, 100, 100, 200, 300, True), (2, 300, 100, 350, 200, False)], quadro=q))
    img = decodificar(d.jpeg_recorte(1))
    assert img.shape == (200, 100, 3)
    assert abs(float(img.astype(int).mean()) - float(q[100:300, 100:200].astype(int).mean())) < 12          # jpeg com perda
    d.definir_ajustes(AjustesCamera(crop=MargemCrop(margem=0.1)))
    assert decodificar(d.jpeg_recorte(1)).shape == (240, 120, 3)
    assert d.jpeg_recorte(2) is None and d.jpeg_recorte(99) is None                   # fora da ROI / inexistente


def test_processar_cena_guarda_so_a_referencia_sem_copiar_o_quadro() -> None:
    q = quadro_xadrez()
    c = cena(1, quadro=q)
    d = DiagnosticoMestre(Relogio())
    d.processar_cena(c)
    assert d._cena is c and d._cena.quadro is q


def test_nada_e_escrito_em_disco(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    antes = {p for p in tmp_path.rglob("*")}
    abertos: list = []
    original = open
    monkeypatch.setattr("builtins.open", lambda *a, **k: (abertos.append(a), original(*a, **k))[1])
    d = DiagnosticoMestre(Relogio())
    d.definir_ajustes(AjustesCamera(roi=Roi(0, 0, 1, 1)))
    d.processar_cena(cena(1))
    d.resumo(), d.jpeg_quadro(), d.jpeg_recorte(1)
    d.limpar()
    assert {p for p in tmp_path.rglob("*")} == antes and abertos == []


def test_thread_safe_sob_leitura_e_escrita_concorrentes() -> None:
    d = DiagnosticoMestre(Relogio())
    erros: list[Exception] = []
    parar = threading.Event()

    def leitor() -> None:
        try:
            while not parar.is_set():
                d.resumo()
                d.jpeg_recorte(1)
        except Exception as exc:  # noqa: BLE001
            erros.append(exc)

    ts = [threading.Thread(target=leitor) for _ in range(3)]
    for t in ts:
        t.start()
    for i in range(200):
        d.processar_cena(cena(i + 1, quadro=None if i % 7 == 0 else "auto"))
    parar.set()
    for t in ts:
        t.join(5)
    assert erros == [] and d.resumo()["contadores"]["cenas"] == 200


# --- PortaSombra: consumidores independentes ---------------------------------------------------------------------------------------------

class Consumidor:
    def __init__(self, falha: bool = False) -> None:
        self.cenas: list[Cena] = []
        self.falha = falha

    def processar_cena(self, c: Cena) -> list:
        self.cenas.append(c)
        if self.falha:
            raise RuntimeError("boom")
        return [{"ok": len(self.cenas)}]


def test_porta_ativa_depende_de_algum_consumidor() -> None:
    p = PortaSombra(1)
    assert p.ativa is False and p.diagnostico is None and p.ajustes is None
    diag = Consumidor()
    assert p.definir_diagnostico(diag) is None and p.ativa is True and p.diagnostico is diag
    assert p.definir_diagnostico(None) is diag and p.ativa is False
    alvo = Consumidor()
    p.definir_alvo(alvo)
    assert p.ativa is True
    p.definir_alvo(None)
    assert p.ativa is False


def test_diagnostico_recebe_a_cena_mesmo_sem_alvo_com_ref_interna_e_geracao_sobe() -> None:
    p = PortaSombra(5)
    diag = Consumidor()
    p.definir_diagnostico(diag)
    assert p.processar_cena(cena(1, ger=2)) == []
    assert [(c.camera_ref, c.quadro_id) for c in diag.cenas] == [("cam-5", 1)] and p.geracao_vigente == 2 and p.recebidos == 1
    p.processar_cena(cena(2, ger=1))                                                    # geracao antiga: recusada para os dois
    assert len(diag.cenas) == 1 and p.recusados_geracao == 1


def test_sem_consumidor_nada_e_feito_mas_a_geracao_sobe() -> None:
    p = PortaSombra(1)
    novas: list[int] = []
    p.ao_nova_geracao = novas.append
    assert p.processar_cena(cena(1, ger=1)) == [] and p.processar_cena(cena(2, ger=2)) == []
    assert p.geracao_vigente == 2 and novas == [2] and p.recebidos == 0


def test_dois_consumidores_independentes_falha_de_um_nao_afeta_o_outro() -> None:
    p = PortaSombra(1)
    diag, alvo = Consumidor(falha=True), Consumidor()
    p.definir_diagnostico(diag)
    p.definir_alvo(alvo)
    assert p.processar_cena(cena(1)) == [{"ok": 1}]
    assert len(alvo.cenas) == 1 and len(diag.cenas) == 1 and p.falhas_diagnostico == 1 and p.falhas_alvo == 0
    diag2, alvo2 = Consumidor(), Consumidor(falha=True)
    p.definir_diagnostico(diag2)
    p.definir_alvo(alvo2)
    assert p.processar_cena(cena(2)) == []
    assert len(diag2.cenas) == 1 and len(alvo2.cenas) == 1 and p.falhas_alvo == 1 and p.falhas_diagnostico == 1


def test_consumidores_veem_a_mesma_cena_marcada() -> None:
    p = PortaSombra(1)
    diag, alvo = Consumidor(), Consumidor()
    p.definir_diagnostico(diag)
    p.definir_alvo(alvo)
    p.definir_ajustes(AjustesCamera(roi=Roi(0.5, 0.0, 1.0, 1.0)))
    p.processar_cena(cena(1, [(1, 10, 10, 100, 200, True), (2, 400, 10, 500, 200, True)]))
    assert [d.na_roi for d in diag.cenas[0].deteccoes] == [False, True] and diag.cenas[0] is alvo.cenas[0]


def test_roi_so_marca_nao_descarta_e_ajustes_a_quente() -> None:
    p = PortaSombra(1)
    alvo = Consumidor()
    p.definir_alvo(alvo)
    p.processar_cena(cena(1, [(1, 10, 10, 100, 200, True)]))
    assert alvo.cenas[-1].deteccoes[0].na_roi is True                                   # sem ajustes: tudo na ROI
    ajustes = efetivos(AjustesCamera(roi=Roi(0.5, 0.0, 1.0, 1.0)))
    assert p.definir_ajustes(ajustes) is None and p.ajustes is ajustes
    p.processar_cena(cena(2, [(1, 10, 10, 100, 200, True)]))
    assert len(alvo.cenas[-1].deteccoes) == 1 and alvo.cenas[-1].deteccoes[0].na_roi is False
    assert p.definir_ajustes(None) is ajustes
    p.processar_cena(cena(3, [(1, 10, 10, 100, 200, False)]))
    assert alvo.cenas[-1].deteccoes[0].na_roi is False                                  # sem ROI a Cena passa como veio (nao forca True)


def porta_com_cadencia(intervalo: float) -> tuple[PortaSombra, Consumidor, Consumidor]:
    p = PortaSombra(1)
    diag, alvo = Consumidor(), Consumidor()
    p.definir_diagnostico(diag)
    p.definir_alvo(alvo)
    p.definir_ajustes(AjustesCamera(execucao=Execucao(intervalo_min_s=intervalo)))
    return p, diag, alvo


def test_cadencia_pela_ultima_encaminhada_e_nao_pela_ultima_recebida() -> None:
    p, diag, alvo = porta_com_cadencia(1.0)
    for qid, t in ((1, 10.0), (2, 10.4), (3, 10.8), (4, 11.0), (5, 11.5), (6, 12.0), (7, 12.99), (8, 13.0)):
        p.processar_cena(cena(qid, t=t))
    assert [c.quadro_id for c in alvo.cenas] == [1, 4, 6, 8] == [c.quadro_id for c in diag.cenas]
    assert p.descartadas_cadencia == 4 and p.recebidos == 4


def test_cadencia_zero_ou_ausente_nao_descarta_e_primeira_de_geracao_nova_sempre_passa() -> None:
    p, diag, alvo = porta_com_cadencia(0.0)
    for qid in (1, 2, 3):
        p.processar_cena(cena(qid, t=10.0))
    assert len(alvo.cenas) == 3 and p.descartadas_cadencia == 0
    p, diag, alvo = porta_com_cadencia(5.0)
    p.processar_cena(cena(1, t=10.0, ger=1))
    p.processar_cena(cena(2, t=10.1, ger=1))                                             # descartada
    p.processar_cena(cena(3, t=10.2, ger=2))                                             # geracao nova: passa, mesmo dentro do intervalo
    p.processar_cena(cena(4, t=10.3, ger=2))                                             # descartada de novo
    assert [c.quadro_id for c in alvo.cenas] == [1, 3] and p.descartadas_cadencia == 2


def test_cena_fora_de_ordem_nao_e_descartada_pela_cadencia_nem_regride_a_referencia() -> None:
    p, _, alvo = porta_com_cadencia(1.0)
    p.processar_cena(cena(1, t=10.0))
    p.processar_cena(cena(2, t=9.0))                                                     # mais antiga: o motor decide (cena_fora_de_ordem)
    p.processar_cena(cena(3, t=10.5))                                                    # ainda perto de 10.0: descartada
    assert [c.quadro_id for c in alvo.cenas] == [1, 2] and p.descartadas_cadencia == 1


def test_trocar_ajustes_reinicia_a_cadencia() -> None:
    p, _, alvo = porta_com_cadencia(10.0)
    p.processar_cena(cena(1, t=10.0))
    p.processar_cena(cena(2, t=10.1))
    p.definir_ajustes(AjustesCamera(execucao=Execucao(intervalo_min_s=10.0)))
    p.processar_cena(cena(3, t=10.2))
    assert [c.quadro_id for c in alvo.cenas] == [1, 3]


def test_cadencia_descarta_antes_de_marcar_roi_e_de_entregar() -> None:
    p, diag, alvo = porta_com_cadencia(1.0)
    p.definir_ajustes(AjustesCamera(roi=Roi(0, 0, 0.5, 1), execucao=Execucao(intervalo_min_s=1.0)))
    p.processar_cena(cena(1, t=10.0))
    n = len(diag.cenas)
    assert p.processar_cena(cena(2, t=10.5)) == [] and len(diag.cenas) == n and p.descartadas_cadencia == 1


def test_processar_antigo_caminho_continua_igual() -> None:
    p = PortaSombra(2)
    visto = []
    p.definir_alvo(SimpleNamespace(processar=lambda ref, g, q, obs: visto.append((ref, g, len(obs))) or ["x"]))
    assert p.processar("outra", 1, None, []) == ["x"] and visto == [("cam-2", 1, 0)]


# --- runtime: gating -------------------------------------------------------------------------------------------------------------------

def test_runtime_nao_roda_o_mestre_com_porta_sem_consumidor(tmp_path: Path) -> None:
    svc = ServicoMestreFalso([[pessoa(1)]])
    porta = PortaSombra(1)
    s = roda(tmp_path, svc, porta, quadros=3, pasta="semc")
    assert svc.n_mestre == 0 and svc.n_proc == 3 and s.evidence_error_counts["motor_sombra"] == 0 and s.state.frames_processed == 3


def test_runtime_roda_o_mestre_quando_ha_diagnostico_ou_alvo(tmp_path: Path) -> None:
    svc, porta, diag = ServicoMestreFalso([[pessoa(1)]]), PortaSombra(1), DiagnosticoMestre()
    porta.definir_diagnostico(diag)
    roda(tmp_path, svc, porta, quadros=3, pasta="d")
    assert svc.n_mestre == 3 and diag.resumo()["contadores"]["cenas"] == 3 and diag.resumo()["quadro_id"] == 3
    svc2, porta2, alvo = ServicoMestreFalso([[pessoa(1)]]), PortaSombra(1), MotorCenaFalso()
    porta2.definir_alvo(alvo)
    roda(tmp_path, svc2, porta2, quadros=2, pasta="a")
    assert svc2.n_mestre == 2 and len(alvo.cenas) == 2


def test_runtime_motor_sem_atributo_ativa_continua_como_antes(tmp_path: Path) -> None:
    svc, motor = ServicoMestreFalso([[pessoa(1)]]), MotorCenaFalso()
    assert not hasattr(motor, "ativa")
    roda(tmp_path, svc, motor, quadros=2, pasta="x")
    assert svc.n_mestre == 2 and len(motor.cenas) == 2


def test_runtime_ativa_dinamica_liga_e_desliga_a_quente(tmp_path: Path) -> None:
    svc, porta = ServicoMestreFalso([[pessoa(1)]]), PortaSombra(1)
    roda(tmp_path, svc, porta, quadros=2, pasta="off")
    assert svc.n_mestre == 0
    porta.definir_diagnostico(DiagnosticoMestre())
    roda(tmp_path, svc, porta, quadros=2, pasta="on")
    assert svc.n_mestre == 2


def test_imagens_do_mesmo_quadro_do_resumo_mesmo_depois_de_chegar_cena_nova() -> None:
    """Regressao da validacao REAL: o resumo mostrou a trilha X e, quando o navegador pediu o recorte, a cena ja era outra (404).
    Com `quadro_id` o diagnostico serve a imagem do MESMO quadro (as ultimas cenas ficam retidas)."""
    import numpy as np

    from src.jutta_ped.motor.cena import Deteccao, criar_cena
    from src.jutta_ped.motor.diagnostico import CENAS_RETIDAS, DiagnosticoMestre

    d = DiagnosticoMestre()
    q = np.full((300, 400, 3), 120, np.uint8)
    d.processar_cena(criar_cena("c", 1, 10, 1.0, 400, 300, [Deteccao(7, "person", (50.0, 50.0, 150.0, 250.0), 0.9)], q))
    d.processar_cena(criar_cena("c", 1, 11, 2.0, 400, 300, [Deteccao(8, "person", (200.0, 50.0, 300.0, 250.0), 0.9)], q))      # a trilha 7 sumiu
    assert d.jpeg_recorte(7) is None and d.jpeg_recorte(8) is not None                  # sem id: so a ultima cena
    assert d.jpeg_recorte(7, quadro_id=10) is not None and d.jpeg_quadro(10) is not None  # com id: o quadro que o resumo mostrou
    assert d.jpeg_recorte(8, quadro_id=10) is None and d.jpeg_recorte(7, quadro_id=999) is None
    for i in range(CENAS_RETIDAS + 2):                                                  # memoria limitada
        d.processar_cena(criar_cena("c", 1, 100 + i, 3.0 + i, 400, 300, [], q))
    assert d.jpeg_quadro(10) is None
    d.limpar()
    assert d.jpeg_quadro(100 + CENAS_RETIDAS + 1) is None
