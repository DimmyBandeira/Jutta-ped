"""Logica pura da bateria de referencia (estatistica, fonte ritmada, avaliador de episodios, regra do GIL, plano, relatorio).
A medicao em si (modelos, camera real) e diagnostico e nao roda aqui, exceto uma fumaca curta que pula sem os ativos locais."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.bateria.episodios import avaliar
from tools.bateria.escala import veredito
from tools.bateria.estatistica import percentil, resumir
from tools.bateria.fonte_ritmada import CapturaRitmada
from tools.bateria.matriz import plano
from tools.bateria.perfil import classificar_stats

RAIZ = Path(__file__).resolve().parents[1]


# --- estatistica -----------------------------------------------------------------------------------------------

def test_percentil_interpola_e_trata_bordas() -> None:
    assert percentil([], 50) is None
    assert percentil([7], 95) == 7
    assert percentil([1, 2, 3, 4], 50) == 2.5
    assert percentil([10, 20, 30, 40, 50], 95) == pytest.approx(48.0)
    assert percentil([5, 1, 3], 0) == 1 and percentil([5, 1, 3], 100) == 5


def test_resumir() -> None:
    assert resumir([]) == {"n": 0, "media": None, "p50": None, "p95": None, "max": None}
    r = resumir([1, 2, 3, 4, 100])
    assert r["n"] == 5 and r["media"] == 22.0 and r["p50"] == 3.0 and r["max"] == 100.0


# --- fonte ritmada -----------------------------------------------------------------------------------------------

class CapFalsa:
    def __init__(self, n: int, fps: float = 10.0) -> None:
        self.n, self.i, self._fps, self.voltou = n, 0, fps, 0

    def isOpened(self):  # noqa: N802
        return True

    def get(self, prop):
        return self._fps

    def set(self, prop, v):
        self.i = int(v)
        self.voltou += 1
        return True

    def read(self):
        if self.i >= self.n:
            return False, None
        self.i += 1
        import numpy as np

        return True, np.full((2, 2, 3), self.i, np.uint8)

    def release(self):
        pass


class Relogio:
    def __init__(self) -> None:
        self.t = 0.0
        self.dormidos: list[float] = []

    def agora(self) -> float:
        return self.t

    def dormir(self, s: float) -> None:
        self.dormidos.append(s)
        self.t += s


def test_fonte_ritmada_mantem_o_ritmo_nativo_em_laco_e_marca_a_entrega() -> None:
    rel = Relogio()
    cap = CapturaRitmada("x", captura=CapFalsa(3, fps=10.0), relogio=rel.agora, dormir=rel.dormir)
    instantes, ids = [], []
    for _ in range(7):                              # 3 quadros + volta ao inicio
        ok, q = cap.read()
        assert ok
        instantes.append(round(rel.agora(), 3))
        ids.append(id(q))
    assert instantes == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6])      # 10 fps exatos
    assert cap.voltas == 2 and cap.entregues == 7 and cap.fps == 10.0
    assert all(cap.prod[i] is not None for i in ids[-3:])


def test_fonte_ritmada_ressincroniza_quando_muito_atrasada_e_limita_memoria() -> None:
    rel = Relogio()
    cap = CapturaRitmada("x", captura=CapFalsa(1000, fps=10.0), relogio=rel.agora, dormir=rel.dormir, memoria=4)
    cap.read()
    rel.t += 5.0                                    # consumidor parou por 5 s
    cap.read()
    antes = len(rel.dormidos)
    cap.read()
    assert len(rel.dormidos) == antes + 1 and rel.dormidos[-1] == pytest.approx(0.1)      # sem "divida" acumulada
    for _ in range(20):
        cap.read()
    assert len(cap.prod) <= 4


def test_fonte_sem_laco_termina() -> None:
    rel = Relogio()
    cap = CapturaRitmada("x", captura=CapFalsa(1), loop=False, relogio=rel.agora, dormir=rel.dormir)
    assert cap.read()[0] and cap.read() == (False, None)


# --- perfil ---------------------------------------------------------------------------------------------------------

def test_classificar_stats_separa_python_puro_envoltorio_nativo_e_builtin() -> None:
    stats = {
        ("C:/x/ultralytics/engine/predictor.py", 10, "predict"): (1, 1, 2.0, 5.0, {}),
        ("C:/x/modulo/pediatria/crop_pipeline.py", 5, "run"): (1, 1, 1.0, 1.0, {}),
        ("C:/x/openvino/_ov_api.py", 119, "infer"): (1, 1, 5.0, 5.0, {}),          # envolve chamada nativa: NAO e bytecode
        ("~", 0, "{built-in method cv2.resize}"): (1, 1, 2.0, 2.0, {}),
    }
    r = classificar_stats(stats)
    assert r["tempo_total_s"] == 10.0 and r["pct_python_puro"] == 30.0
    assert r["pct_envoltorio_de_inferencia_nativa"] == 50.0 and r["pct_nativo_builtin"] == 20.0
    assert r["pct_python_por_pacote"] == {"ultralytics": 20.0, "modulo/pediatria": 10.0}
    assert r["top_envoltorio"][0]["funcao"].endswith("(infer)") and r["top_python"][0]["funcao"].endswith("(predict)")
    assert r["top_nativo"][0]["funcao"].startswith("{built-in method cv2")


# --- regra pre-registrada do GIL --------------------------------------------------------------------------------------

@pytest.mark.parametrize("threads,processos,cpu,esperado", [
    (20.0, 30.0, 55.0, "consistente com o GIL como gargalo"),       # razao 1.5, CPU sobrando
    (20.0, 30.0, 92.0, "limitado por CPU (nao pelo GIL)"),           # razao alta mas CPU saturada
    (20.0, 20.5, 50.0, "sem diferenca relevante"),                   # razao 1.025
    (20.0, 24.0, 50.0, "inconclusivo"),                              # razao 1.2
    (0.0, 10.0, 50.0, "inconclusivo (dados insuficientes)"),
    (20.0, 30.0, None, "inconclusivo (dados insuficientes)"),
])
def test_veredito_gil(threads, processos, cpu, esperado) -> None:
    assert veredito(threads, processos, cpu)["conclusao"] == esperado


# --- plano da matriz ---------------------------------------------------------------------------------------------------

def test_plano_da_matriz() -> None:
    itens = plano([1, 2, 4], [0.0, 5.0], [1, 4], repeticoes=2)
    assert len(itens) == 2 * (3 * 2 + 2)
    so_novas = plano([1], [5.0], [], repeticoes=2, repeticao_inicial=3)
    assert [i["id"] for i in so_novas] == ["c1_fps5_auto_r3", "c1_fps5_auto_r4"]
    assert itens[0] == {"cameras": 1, "analysis_fps": 0.0, "dispositivo": "auto", "repeticao": 1, "id": "c1_fps0_auto_r1"}
    assert {i["id"] for i in itens if i["dispositivo"] == "cuda"} == {"c1_fps5_cuda_r1", "c4_fps5_cuda_r1", "c1_fps5_cuda_r2", "c4_fps5_cuda_r2"}
    assert len({i["id"] for i in itens}) == len(itens)


# --- avaliador de episodios -----------------------------------------------------------------------------------------

def _analise(alertas: list[tuple[float, str, int]], sha: str = "abc") -> dict:
    return {"video": "v.dav", "sha256": sha, "alertas": [{"t_s": t, "estado": e, "trilha": tr, "quadro": 0} for t, e, tr in alertas],
            "suprimidos": [], "trilhas_distintas_nos_alertas": sorted({tr for _, _, tr in alertas})}


# --- relatorio ---------------------------------------------------------------------------------------------------------

def _ensaio(cameras: int, fps: float) -> dict:
    res = lambda x: {"n": 9, "media": x, "p50": x, "p95": x * 1.5, "max": x * 2}  # noqa: E731
    cams = {f"bat{i}": {"fonte": f"v{i}.mp4", "fps_fonte": 24.0, "carga_inicial_s": 10.0 + i, "primeiro_quadro_s": 12.0, "quadros_analisados": 50,
                        "fps_efetivo": 5.0, "quadros_lidos_da_fonte": 240, "quadros_descartados": 190, "latencia_processamento_ms": res(30.0),
                        "idade_na_publicacao_ms": res(40.0), "idade_em_uso_ms": res(120.0)} for i in range(cameras)}
    return {"config": {"cameras": cameras, "analysis_fps": fps, "dispositivo_pedido": "auto", "aquecimento_s": 15, "estavel_s": 45, "janela_real_s": 45.0,
                       "deslocamento_video": 0, "fontes": ["v0.mp4"]},
            "colocacao": {"bat0": {"dispositivo": "cpu", "backend": "openvino", "motivo": "openvino_cpu", "analysis_fps_alvo": fps or None}},
            "fases": {"importacao_s": 0.1, "carga_inicial_s": {}, "primeiro_quadro_s": {}, "parada_s": 0.2},
            "agregado": {"fps_efetivo_total": 5.0 * cameras, "quadros_descartados_total": 190 * cameras, "cpu_processo_pct_do_total": res(30.0),
                         "cpu_processo_nucleos_equivalentes": res(4.8), "cpu_sistema_pct": res(40.0), "rss_mb": res(1000.0), "rss_pico_mb": 1200.0,
                         "gpu_util_pct": res(3.0), "vram_usada_mb": res(900.0), "vram_base_mb": 800.0, "vram_delta_medio_mb": 100.0,
                         "vram_pico_mb": 950.0, "nucleos_logicos": 16, "amostras_de_recursos_na_janela": 45},
            "cameras": cams, "eventos": {"popup_alert": 0, "popup_suppressed": 0}}


def test_relatorio_transcreve_dados_e_declara_limitacoes(tmp_path: Path) -> None:
    from tools.bateria.relatorio import montar

    (tmp_path / "ensaios").mkdir()
    indice = []
    for c in (1, 2):
        nome = f"c{c}_fps5_auto_r1"
        (tmp_path / "ensaios" / f"{nome}.json").write_text(json.dumps(_ensaio(c, 5.0)), encoding="utf-8")
        indice.append({"id": nome, "cameras": c, "analysis_fps": 5.0, "dispositivo": "auto", "repeticao": 1, "arquivo": f"{nome}.json", "status": "ok"})
    indice.append({"id": "c4_fps5_auto_r1", "cameras": 4, "analysis_fps": 5.0, "dispositivo": "auto", "repeticao": 1, "arquivo": None, "status": "timeout"})
    (tmp_path / "ensaios" / "indice.json").write_text(json.dumps(indice), encoding="utf-8")
    (tmp_path / "leitura.md").write_text("LEITURA-DE-TESTE", encoding="utf-8")
    (tmp_path / "episodios").mkdir()
    (tmp_path / "episodios" / "avaliacao_v.json").write_text(json.dumps(avaliar(_analise([(1.0, "CHILD_ALONE", 1)]), None)), encoding="utf-8")
    md = montar(tmp_path)
    assert "1 cam · fps 5 · cpu · r1" in md and "2 cam · fps 5 · cpu · r1" in md
    assert "c4_fps5_auto_r1 (timeout)" in md                     # ensaio que falhou aparece, nao e escondido
    assert "LEITURA-DE-TESTE" in md and "## 6. Limitações" in md
    assert "sem_anotacao" in md and "cobertura integral" in md          # proveniencia e cobertura aparecem na tabela de qualidade
    assert "Só **anotação oficial**" in md and "indisponível (não estimado)" in md


# --- hash de modelo -------------------------------------------------------------------------------------------------

def test_hash_modelo_arquivo_e_pasta_estaveis(tmp_path: Path) -> None:
    from tools.bateria.ambiente import hash_modelo

    f = tmp_path / "m.pt"
    f.write_bytes(b"abc")
    assert hash_modelo(f)["sha256"] == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    d = tmp_path / "ov"
    d.mkdir()
    (d / "a.xml").write_bytes(b"1")
    (d / "a.bin").write_bytes(b"2")
    h = hash_modelo(d)
    assert h["tipo"] == "pasta" and h["algoritmo"] == "jutta-dir-v1" and h["bytes"] == 2 and h == hash_modelo(d)


# --- fumaca do ensaio (pula sem os ativos locais) ---------------------------------------------------------------------

VIDEO = RAIZ / "videos" / "Corredor pediatria.mp4"
MODELOS = RAIZ / "src" / "models" / "pediatria_child_detector_v6_jutta_openvino_model"


@pytest.mark.skipif(not (VIDEO.exists() and MODELOS.exists()), reason="video/modelos locais ausentes")
def test_fumaca_ensaio_curto_gera_todas_as_secoes(tmp_path: Path) -> None:
    import subprocess
    import sys

    saida = tmp_path / "e.json"
    r = subprocess.run([sys.executable, str(RAIZ / "tools" / "bateria_referencia.py"), "ensaio", "--cameras", "1", "--analysis-fps", "5",
                        "--aquecimento", "2", "--estavel", "5", "--videos", str(VIDEO), "--arquivo", str(saida)],
                       cwd=RAIZ, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-800:]
    e = json.loads(saida.read_text(encoding="utf-8"))
    assert {"config", "colocacao", "fases", "agregado", "cameras", "eventos"} <= set(e)
    c = e["cameras"]["bat0"]
    assert c["carga_inicial_s"] > 0 and c["primeiro_quadro_s"] >= 0 and c["quadros_analisados"] > 0 and c["fps_efetivo"] > 0
    assert c["idade_em_uso_ms"]["n"] > 0 and c["idade_na_publicacao_ms"]["n"] > 0
    assert e["agregado"]["rss_mb"]["n"] > 0 and e["colocacao"]["bat0"]["backend"] in ("openvino", "pytorch")
    assert e["validade"]["valido"] is True and e["estado_final_das_sessoes"]["bat0"]["status"] == "running"


# --- regressoes: reabrir o arquivo no laco, ensaio invalido sinalizado, perfil -----------------------------------------

def test_fonte_ritmada_com_arquivo_real_reabre_no_fim_do_laco(tmp_path: Path) -> None:
    import cv2
    import numpy as np

    arq = tmp_path / "mini.mp4"
    w = cv2.VideoWriter(str(arq), cv2.VideoWriter_fourcc(*"mp4v"), 20.0, (64, 48))
    for i in range(4):
        w.write(np.full((48, 64, 3), 40 * i, np.uint8))
    w.release()
    rel = Relogio()
    cap = CapturaRitmada(arq, relogio=rel.agora, dormir=rel.dormir)
    lidos = [cap.read()[0] for _ in range(11)]                 # 4 quadros + 2 voltas completas
    assert all(lidos) and cap.voltas == 2 and cap.entregues == 11
    cap.release()


def test_relatorio_marca_ensaio_invalido_e_o_exclui_da_variacao(tmp_path: Path) -> None:
    from tools.bateria.relatorio import montar

    (tmp_path / "ensaios").mkdir()
    bom, ruim = _ensaio(2, 0.0), _ensaio(2, 0.0)
    ruim["validade"] = {"valido": False, "problemas": ["bat0: 0 quadros analisados na janela estavel (fonte=x.dav, estado=finished)"]}
    ruim["cameras"]["bat0"]["fps_efetivo"] = 0.0
    bom["validade"] = {"valido": True, "problemas": []}
    indice = []
    for r, e in ((1, bom), (2, ruim)):
        nome = f"c2_fps0_auto_r{r}"
        (tmp_path / "ensaios" / f"{nome}.json").write_text(json.dumps(e), encoding="utf-8")
        indice.append({"id": nome, "cameras": 2, "analysis_fps": 0.0, "dispositivo": "auto", "repeticao": r, "arquivo": f"{nome}.json", "status": "ok"})
    (tmp_path / "ensaios" / "indice.json").write_text(json.dumps(indice), encoding="utf-8")
    md = montar(tmp_path)
    assert "⚠ INVÁLIDO · 2 cam" in md and "Ensaios inválidos" in md and "0 quadros analisados" in md
    assert "### Variação entre repetições" not in md            # sobrou 1 valido: nao ha repeticao para comparar
