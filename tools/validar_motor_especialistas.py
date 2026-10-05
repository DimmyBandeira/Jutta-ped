"""Ensaio funcional do motor de especialistas sobre RTSP REAL (SimulaterDSA ja publicado), em banco e pasta isolados.

    python tools/validar_motor_especialistas.py --fase mestre         # nivel 1: mae, mestre e recortes (sem especialista pediatrico)
    python tools/validar_motor_especialistas.py --fase especialistas  # nivel 2: tres cameras, pacotes instalados/ativados, receitas, sombra
    python tools/validar_motor_especialistas.py --fase tudo [--duracao 60]

PONTO DE ENTRADA SEPARADO DO PAINEL: o painel real (`mount_panel`) e usado EM PROCESSO (TestClient), sem servidor HTTP, sem go2rtc e
sem navegador. Tudo que o painel faz por rota e feito por rota (importar cenario SimulaterDSA, criar receita, salvar configuracao da
camera, ligar sombra); `Instalador`, `Executor`, `GerenteSombra`/`PortaSombra`, `MotorSombra` e `PediatriaSessionManager`/sessao
headless sao os de producao. A unica diferenca: a fonte e o RTSP do simulador (`rtsp://127.0.0.1:8554/<nome>`) lido direto, nao o
restream do go2rtc. Isto comprova o MOTOR; nao comprova o fluxo de configuracao pelo navegador nem a cadeia go2rtc.

Honestidade das evidencias (cada item do relatorio traz `natureza`):
  real          = modelo real + video real do simulador;
  deterministico = pacote de conformidade com BACKEND DETERMINISTICO (sem pesos; so a camera C); nao mede qualidade de modelo nenhum.
Nada e inserido no cache nem no feed: todo resultado vem do ciclo normal (Cena -> porta -> despacho -> executor -> receita -> evento).
Nao altera o simulador nem o banco operacional. Saida em runtime/ (ignorado pelo git): pesos, quadros e relatorio ficam la.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import statistics
import sys
import time
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from tools.medicao_janela import (  # noqa: E402
    cenas_e_avaliacoes, conclusao_chamada, conclusao_evento, delta, dispositivo_efetivo, dividir_por_conclusao, prontidao, taxa,
)

FONTES = {"A": "002_normal_01", "B": "001_bebe_colo_bebe_no_be_01", "C": "003_bebecolo_01"}   # caminhos publicados pelo simulador
CENARIO = Path(r"C:\Users\drb-d\Downloads\SimulaterDSA_v0.5.0\rtsp_sim\dist\SimulaterDSA\cenarios\perinatal.yaml")
BASE_RTSP = "rtsp://127.0.0.1:8554"
PACOTE_REAL = "br.jutta.papel-pessoa"
PACOTE_DET = "br.jutta.conformidade-porte"


# ---------------------------------------------------------------------------------------------------------- pacote de conformidade

class ModeloDeterministico:
    """Backend de CONFORMIDADE: classifica o recorte pela ALTURA em pixels (grande se >= 400). Sem pesos, sem qualidade a medir."""
    nomes = ["pequeno", "grande"]
    dispositivo = "cpu"

    def inferir(self, imagens: list[Any]) -> list[dict[str, Any]]:
        saida = []
        for im in imagens:
            grande = min(1.0, im.shape[0] / 400.0)
            saida.append({"probs": {"pequeno": 1.0 - grande, "grande": grande}})
        return saida


class BackendRoteador:
    """Executor real para o pacote real; backend determinstico so para o arquivo `conformidade_porte.pt` (identificado no relatorio)."""

    def __init__(self) -> None:
        from src.jutta_ped.especialistas.backend_ultralytics import BackendUltralytics
        self._real = BackendUltralytics()

    def carregar(self, modelo, pasta, dispositivo, formato):
        if modelo["arquivo"].endswith("conformidade_porte.pt"):
            assert (pasta / modelo["arquivo"]).exists()
            return ModeloDeterministico()
        return self._real.carregar(modelo, pasta, dispositivo, formato)


def montar_pacote_conformidade(destino: Path) -> Path:
    from src.jutta_ped.especialistas.hashing import sha256_blocos
    pesos = b"SEM PESOS: backend deterministico de conformidade (classifica pela altura do recorte)"
    manifesto = {
        "protocolo": 1,
        "pacote": {"id": PACOTE_DET, "versao": "1.0.0", "nome": "Conformidade (porte pela altura do recorte)", "autor": "Equipe Jutta (conformidade, sem modelo)"},
        "entrada": {"fonte": "visivel", "alvo": {"tipo": "recorte", "classe_mestre": "person"}, "altura_minima_px": 24, "lote_max": 8,
                    "formato": {"cor": "RGB", "largura": 224, "altura": 224, "redimensionamento": "esticar", "normalizacao": "0-1"}},
        "modelos": [{"id": "porte", "arquivo": "modelos/conformidade_porte.pt", "formato": "pt", "sha256": sha256_blocos(iter([pesos])), "dispositivos": ["cpu"]}],
        "atributos": [{"nome": "porte", "tipo": "categoria", "validade_ms": 1500, "categorias": ["pequeno", "grande"],
                       "de": {"modelo": "porte", "tipo": "classificacao", "agregacao": "classe_mais_provavel"}}],
        "custo": {"ms_por_recorte": 1, "hardware": "determinstico (sem modelo)"},
    }
    destino.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifesto, ensure_ascii=False, indent=2))
        z.writestr("modelos/conformidade_porte.pt", pesos)
    return destino


# ---------------------------------------------------------------------------------------------------------- medicao

class ExecutorMedido:
    """Decorador do Executor REAL: delega tudo e registra, por chamada, camera/pacote/pedidos/tempo. Nao altera resultados."""

    def __init__(self, real: Any) -> None:
        self._real = real
        self.chamadas: list[dict[str, Any]] = []
        self._vistos: set[str] = set()

    def __getattr__(self, nome: str) -> Any:
        return getattr(self._real, nome)

    def executar(self, pid: str, pedidos):
        t_ini_call = time.monotonic()
        t0 = time.perf_counter()
        res = self._real.executar(pid, pedidos)
        ms = (time.perf_counter() - t0) * 1000.0
        primeira = pid not in self._vistos
        self._vistos.add(pid)
        self.chamadas.append({"camera_ref": pedidos[0].camera_ref if pedidos else None, "pacote": pid, "n": len(pedidos), "ms": ms,
                              "primeira_do_pacote": primeira, "estados": Counter(r["estado"] for r in res),
                              "erros": Counter(r["erro"]["codigo"] for r in res if r["estado"] != "ok"),
                              "inferencia_ms": [r["tempos"]["inferencia_ms"] for r in res if r["estado"] == "ok"],
                              "fila_ms": [r["tempos"]["fila_ms"] for r in res if r["estado"] == "ok"], "t": t_ini_call})
        return res


def pct(valores: list[float], p: float) -> float | None:
    if not valores:
        return None
    v = sorted(valores)
    return round(v[min(len(v) - 1, int(round(p / 100.0 * (len(v) - 1))))], 1)


def resumo_ms(valores: list[float]) -> dict[str, Any]:
    v = [x for x in valores if x is not None]
    return {"n": len(v), "p50": pct(v, 50), "p95": pct(v, 95), "max": round(max(v), 1) if v else None,
            "media": round(statistics.fmean(v), 1) if v else None}


# ---------------------------------------------------------------------------------------------------------- ambiente

def definicoes_receitas() -> dict[str, dict[str, Any]]:
    """Receitas do ensaio (JSON do contrato publico): contrato 2 para o pacote real; contrato 1 para o de conformidade."""
    papel = {"contrato": 2,
             "vinculos": [{"especialista": PACOTE_REAL, "orcamento_ms": 500, "margem_recorte": 0.1}],
             "papeis": {
                 "crianca": {"classe": "person", "combinacao": "todas", "condicoes": [
                     {"especialista": PACOTE_REAL, "atributo": "confianca_crianca", "minimo": 0.6},
                     {"especialista": PACOTE_REAL, "atributo": "confianca_crianca", "maior_que": {"especialista": PACOTE_REAL, "atributo": "confianca_adulto"}}]},
                 "adulto": {"classe": "person", "combinacao": "todas", "condicoes": [
                     {"especialista": PACOTE_REAL, "atributo": "confianca_adulto", "minimo": 0.6},
                     {"especialista": PACOTE_REAL, "atributo": "confianca_adulto", "maior_que": {"especialista": PACOTE_REAL, "atributo": "confianca_crianca"}}]}},
             "relacoes": {"sem_adulto_perto": {"tipo": "nenhum_proximo", "sujeito": "crianca", "outro": "adulto",
                                               "distancia": {"metrica": "ponto_de_apoio", "unidade": "altura_sujeito", "maximo": 1.75}}},
             "eventos": [{"id": "crianca_sem_adulto_perto", "sujeito": "crianca", "quando_todas": ["sem_adulto_perto"],
                          "duracao_s": 3.0, "lacuna_max_s": 2.0, "perda_max_s": 10.0, "encerrar_negativo_s": 1.0}]}
    porte = {"vinculos": [{"especialista": PACOTE_DET, "orcamento_ms": 500, "margem_recorte": 0.0}],
             "regra": {"combinacao": "todas", "condicoes": [{"especialista": PACOTE_DET, "atributo": "porte", "igual": "grande"}]}}
    return {"ensaio-papel-contrato2": papel, "ensaio-porte-contrato1": porte}


class Ambiente:
    def __init__(self, pasta: Path, dispositivo: str) -> None:
        import httpx  # noqa: F401
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from src.jutta_ped.painel.panel import mount_panel

        self.pasta, self.dispositivo = pasta, dispositivo
        senha = secrets.token_urlsafe(18)
        os.environ["JUTTA_ADMIN_PASSWORD"] = senha
        self.app = FastAPI()
        self.database = mount_panel(self.app, runtime_dir=pasta, db_url=f"sqlite:///{(pasta / 'painel.db').as_posix()}", background=False,
                                    evidence_roots=[pasta / "evidencias"], especialistas_backends={"pt": (b := BackendRoteador()), "openvino": b})
        self.servicos = self.app.state.painel
        self.instalador = self.app.state.especialistas
        self.medido = ExecutorMedido(self.servicos.executor)
        if dispositivo == "cpu":
            self.servicos.executor.dispositivos_permitidos = ("cpu",)      # CPU explicita tambem para os especialistas: sem fallback
        self.servicos.sombra.executor = self.medido                       # decorador: o Executor real continua sendo o que executa
        self.client = TestClient(self.app, follow_redirects=False)
        self.client.__enter__()
        self.post("/login", {"login": "admin", "senha": senha}, "/login")
        self.camera_ids: dict[str, int] = {}

    def token(self, caminho: str) -> str:
        html = self.client.get(caminho).text
        m = re.search(r'name="csrf_token" value="([^"]+)"', html) or re.search(r'"X-CSRF-Token": "([^"]+)"', html)
        assert m, f"sem csrf em {caminho}"
        return m.group(1)

    def post(self, caminho: str, dados: dict, pagina_token: str = "/painel/cameras", **kw):
        return self.client.post(caminho, data={**dados, "csrf_token": self.token(pagina_token)}, **kw)

    def importar_cenario(self) -> None:
        r = self.client.post("/painel/cameras/simulater-dsa", data={"host": "127.0.0.1"},
                             files={"arquivo_yaml": (CENARIO.name, CENARIO.read_bytes(), "application/x-yaml")},
                             headers={"X-CSRF-Token": self.token("/painel/cameras"), "HX-Request": "true"})
        assert r.status_code == 200, r.text[:300]
        from sqlalchemy import select
        from src.jutta_ped.painel.models import Camera
        with self.database.session() as db:
            por_nome = {c.nome: c.id for c in db.scalars(select(Camera)).all()}
        self.camera_ids = {k: por_nome[v] for k, v in FONTES.items()}

    def instalar_pacotes(self) -> dict[str, Any]:
        from tools.pacote_papel import montar
        info = {}
        t0 = time.perf_counter()
        zip_real = montar(ROOT / "src/models/pediatria_child_detector_v6_jutta_openvino_model", self.pasta / "pacotes" / "papel.zip")
        zip_det = montar_pacote_conformidade(self.pasta / "pacotes" / "conformidade.zip")
        for rotulo, z in (("real", zip_real), ("deterministico", zip_det)):
            res = self.instalador.instalar(z)
            self.instalador.ativar(res.id, res.versao)
            info[res.id] = {"versao": res.versao, "natureza": rotulo, "estado": "instalado e ATIVO (nao e alerta operacional)"}
        info["tempo_instalacao_s"] = round(time.perf_counter() - t0, 2)
        return info

    def criar_receitas(self) -> dict[str, int]:
        ids = {}
        for nome, definicao in definicoes_receitas().items():
            r = self.post("/painel/receitas/importar", {"nome": nome, "descricao": "ensaio funcional do motor (sombra)", "definicao_json": json.dumps(definicao)},
                          "/painel/receitas")
            assert r.status_code == 303, (nome, r.status_code, r.text[:400])
            from sqlalchemy import select
            from src.jutta_ped.painel.models import ReceitaVersao
            with self.database.session() as db:
                ids[nome] = db.scalars(select(ReceitaVersao.id).order_by(ReceitaVersao.id.desc())).first()
        return ids

    def salvar_config(self, cam: int, **campos) -> int:
        r = self.post(f"/painel/cameras/{cam}/analise", {k: str(v) for k, v in campos.items()}, f"/painel/cameras/{cam}/analise")
        return r.status_code

    def sombra(self, cam: int, ligar: bool) -> None:
        r = self.post(f"/painel/cameras/{cam}/sombra", {"acao": "ligar" if ligar else "desligar"})
        assert r.status_code == 303, r.status_code

    def fechar(self) -> None:
        try:
            self.servicos.parar()
        finally:
            self.client.__exit__(None, None, None)
            self.database.dispose()


# ---------------------------------------------------------------------------------------------------------- fase 1: mestre/crops

def fase_mestre(env: Ambiente, quadros: int, fonte: str = "A") -> dict[str, Any]:
    """Mestre REAL (detector de pessoas do runtime, SEM carregar o classificador pediatrico) -> Cena -> ROI/recorte; invariantes
    verificados quadro a quadro sobre o RTSP da fonte indicada. Cena vazia e alvo fora da ROI sao exercitados contra o motor real (sem veredito)."""
    import cv2
    from modulo.pediatria.crop_pipeline import PersonCropSpecialistPipeline
    from src.jutta_ped.motor.ajustes import ajustes_de_dict, efetivos, marcar_roi
    from src.jutta_ped.motor.cena import criar_cena, deteccoes_de_objetos
    from src.jutta_ped.motor.diagnostico import DiagnosticoMestre
    from src.jutta_ped.motor.observacao import extrair_recorte
    from src.jutta_ped.service.runtime import DEFAULT_PERSON_MODEL_PATH, open_capture_source

    url = f"{BASE_RTSP}/{FONTES[fonte]}"
    t0 = time.perf_counter()
    pipeline = PersonCropSpecialistPipeline(person_model_path=DEFAULT_PERSON_MODEL_PATH, specialist_model=None, specialist_names={})
    classificou = {"n": 0}
    pipeline._classify_crop = lambda *a, **k: classificou.__setitem__("n", classificou["n"] + 1)    # sentinela: nada pode classificar
    carga_s = time.perf_counter() - t0
    captura = open_capture_source(url)
    assert captura.isOpened(), f"nao abriu {url}"
    configs = {
        "margens 20%/10% min 8": {"crop": {"margem_h": 0.20, "margem_v": 0.10, "minimo_px": 8}},
        "margens 90%/60% (crop encosta nas bordas)": {"crop": {"margem_h": 0.90, "margem_v": 0.60, "minimo_px": 8}},
        "margens 2%/2% min 40 px (domina o minimo)": {"crop": {"margem_h": 0.02, "margem_v": 0.02, "minimo_px": 40}},
        "ROI central 30-70% (borda)": {"crop": {"margem_h": 0.10, "margem_v": 0.10, "minimo_px": 8},
                                       "roi": {"x1": 0.3, "y1": 0.0, "x2": 0.7, "y2": 1.0, "criterio": "ponto_de_apoio"}},
        "ROI metade esquerda 0-50% (criterio ponto de apoio)": {"crop": {"margem_h": 0.10, "margem_v": 0.10, "minimo_px": 8},
                                                                "roi": {"x1": 0.0, "y1": 0.0, "x2": 0.5, "y2": 1.0, "criterio": "ponto_de_apoio"}},
        "ROI minuscula no canto (alvo fora)": {"crop": {"margem_h": 0.10, "margem_v": 0.10, "minimo_px": 8},
                                              "roi": {"x1": 0.0, "y1": 0.0, "x2": 0.05, "y2": 0.05, "criterio": "ponto_de_apoio"}},
    }
    falhas: list[str] = []
    saida: dict[str, Any] = {"fonte": url, "natureza": "real (video do simulador + detector de pessoas real, sem classificador pediatrico)",
                             "carga_detector_s": round(carga_s, 2), "configs": {}}
    cena_id = 0
    for rotulo, cfg in configs.items():
        aj = efetivos(ajustes_de_dict(cfg))
        diag = DiagnosticoMestre()
        diag.definir_ajustes(aj)
        c = Counter()
        ref, geracao = "cam-ensaio-A", 1
        ultimo_id = -1
        for _ in range(quadros):
            ok, frame = captura.read()
            if not ok or frame is None:
                falhas.append(f"{rotulo}: leitura falhou")
                break
            cena_id += 1
            pessoas = pipeline.detectar_pessoas(frame, frame_index=cena_id, device=env.dispositivo if env.dispositivo != "auto" else "cpu", person_confidence=0.20)
            alt, larg = frame.shape[:2]
            cena = marcar_roi(criar_cena(ref, geracao, cena_id, time.monotonic(), larg, alt, deteccoes_de_objetos(pessoas, "person"), quadro=frame), aj.roi)
            diag.processar_cena(cena)
            c["quadros"] += 1
            if cena_id <= ultimo_id:
                falhas.append("quadro_id nao cresce")
            ultimo_id = cena_id
            if cena.camera_ref != ref or cena.geracao != geracao:
                falhas.append("identidade da cena")
            if not cena.deteccoes:
                c["cenas_vazias"] += 1
            trilhas = [d.trilha_id for d in cena.deteccoes]
            if len(trilhas) != len(set(trilhas)):
                falhas.append(f"trilha repetida no quadro {cena_id}")
            for d in cena.deteccoes:
                c["deteccoes"] += 1
                x1, y1, x2, y2 = d.bbox_xyxy
                if not (-1 <= x1 < x2 <= larg + 1 and -1 <= y1 < y2 <= alt + 1):
                    falhas.append(f"bbox fora do quadro: {d.bbox_xyxy}")
                if not d.na_roi:
                    c["fora_da_roi"] += 1
                    continue
                rec = extrair_recorte(frame, d.bbox_xyxy, 0.0, margem_h=aj.crop.margem_h, margem_v=aj.crop.margem_v, minimo_px=aj.crop.minimo_px or 0)
                if rec is None:
                    falhas.append("recorte nulo para deteccao valida")
                    continue
                c["recortes"] += 1
                (ox, oy), (bx1, by1, bx2, by2) = rec.origem_xy, rec.bbox_quadro
                h, w = rec.imagem.shape[:2]
                # transformacao quadro<->crop e conteudo
                if rec.bbox_no_recorte != (bx1 - ox, by1 - oy, bx2 - ox, by2 - oy) or rec.para_quadro(*rec.para_recorte(bx1, by1)) != (bx1, by1):
                    falhas.append("transformacao quadro-crop")
                if not np.array_equal(rec.imagem, frame[oy:oy + h, ox:ox + w]):
                    falhas.append("pixels do crop diferem do quadro")
                # folgas: esperado = max(minimo, int(fracao*dim da caixa externa)); so pode ser menor se o crop encostou na borda
                wx = int(-(-x2 // 1)) - int(x1 // 1)
                wy = int(-(-y2 // 1)) - int(y1 // 1)
                esperado = (max(aj.crop.minimo_px or 0, int(aj.crop.margem_h * wx)), max(aj.crop.minimo_px or 0, int(aj.crop.margem_h * wx)),
                            max(aj.crop.minimo_px or 0, int(aj.crop.margem_v * wy)), max(aj.crop.minimo_px or 0, int(aj.crop.margem_v * wy)))
                limite = (bx1, larg - bx2, by1, alt - by2)                        # distancia da caixa a cada borda do quadro
                for lado, got, want, lim in zip(("esq", "dir", "topo", "base"), rec.folgas_px, esperado, limite):
                    if got != min(want, lim):
                        falhas.append(f"folga {lado}: obtida {got}, esperada {min(want, lim)} (pedida {want}, ate a borda {lim})")
                    if got < want:
                        c[f"folga_{lado}_encostou_na_borda"] += 1
                    if (aj.crop.minimo_px or 0) and got < min(aj.crop.minimo_px, lim):
                        falhas.append(f"folga {lado} abaixo do minimo em px")
                if h > alt or w > larg:
                    falhas.append("crop maior que o quadro")
            res = diag.resumo()
            for s in res["sujeitos"]:
                if (not s["na_roi"]) and (s["recorte"] is not None or s["motivo_sem_recorte"] != "fora_da_roi"):
                    falhas.append("fora da ROI deveria ficar sem recorte, com motivo")
        saida["configs"][rotulo] = dict(c)
    captura.release()
    saida["classificacoes_pediatricas_chamadas"] = classificou["n"]
    if classificou["n"]:
        falhas.append("o classificador pediatrico foi chamado pelo mestre")
    # cena vazia / alvo fora da ROI diante do MOTOR real: nenhum veredito inventado
    saida["sem_conclusao_inventada"] = _sem_conclusao_inventada(env)
    saida["falhas"] = sorted(set(falhas))
    saida["aprovado"] = not saida["falhas"] and saida["sem_conclusao_inventada"]["aprovado"] and saida["configs"][next(iter(saida["configs"]))].get("recortes", 0) > 0
    return saida


def _sem_conclusao_inventada(env: Ambiente) -> dict[str, Any]:
    """Motor REAL com o pacote real ativo e receita contrato 2 (papeis): cena vazia -> evento sem sujeito e sem veredito; alvo fora da
    ROI -> papel indeterminado `fora_da_roi` e o especialista NAO e chamado. Frame sintetico (so serve para exercitar a logica)."""
    from src.jutta_ped.motor.cena import Deteccao, criar_cena
    from src.jutta_ped.motor.motor import MotorSombra
    from src.jutta_ped.especialistas.executor import contrato_leve
    from sqlalchemy import select
    from src.jutta_ped.painel.models import ReceitaVersao
    with env.database.session() as db:
        definicao = json.loads(db.scalars(select(ReceitaVersao).order_by(ReceitaVersao.id)).first().definicao_json)
    eventos: list[dict] = []
    env.medido.contrato(PACOTE_REAL)                                  # falha aqui mostra a causa real (carga/integridade), nao "sem versao ativa"
    motor = MotorSombra(env.medido, habilitado=True, destino=eventos.append)
    motor.configurar([definicao], {"cam-x": {"receita": definicao["id"], "versao": definicao["versao"]}})
    quadro = np.zeros((480, 640, 3), np.uint8)
    antes = len(env.medido.chamadas)
    t = time.monotonic()
    motor.processar_cena(criar_cena("cam-x", 1, 1, t, 640, 480, [], quadro=quadro))
    vazia = eventos[-1]
    time.sleep(0.05)                                                  # o relogio monotonico do Windows tem passo grosso: Cena 2 precisa ser mais nova
    motor.processar_cena(criar_cena("cam-x", 1, 2, time.monotonic(), 640, 480, [Deteccao(7, "person", (100, 100, 200, 400), 0.9, na_roi=False)], quadro=quadro))
    fora = eventos[-1]
    papeis = fora["sujeitos"][0]["papeis"]
    ok = (vazia["sujeitos"] == [] and vazia["alerta_real"] is False and papeis == {"crianca": "indeterminado", "adulto": "indeterminado"}
          and len(env.medido.chamadas) == antes and not any(e["estado"] == "ativo" for e in vazia["eventos"] + fora["eventos"]))
    return {"aprovado": bool(ok), "cena_vazia": {"sujeitos": vazia["sujeitos"], "eventos": vazia["eventos"]},
            "alvo_fora_da_roi_papeis": papeis, "chamadas_ao_especialista": len(env.medido.chamadas) - antes, "natureza": "motor real, quadro sintetico"}


# ---------------------------------------------------------------------------------------------------------- fase 2: tres cameras

def fase_especialistas(env: Ambiente, duracao: float, receitas: dict[str, int], cameras: str = "ABC") -> dict[str, Any]:
    from sqlalchemy import select
    from src.jutta_ped.painel.models import CameraConfigVersao, EventoPediatria
    from src.jutta_ped.service.runtime import PediatriaSessionManager, SessionStartConfig
    from src.jutta_ped.motor.ajustes import ajustes_de_dict

    a, b, c = (env.camera_ids[k] for k in "ABC")
    pap, porte = receitas["ensaio-papel-contrato2"], receitas["ensaio-porte-contrato1"]
    cfgs = {
        "A": dict(especialista=PACOTE_REAL, receita_versao_id=pap, crop_margem_h=12, crop_margem_v=8, crop_minimo_px=8, exec_intervalo_min_s=0.4),
        "B": dict(especialista=PACOTE_REAL, receita_versao_id=pap, crop_margem_h=25, crop_margem_v=15, crop_minimo_px=16, exec_intervalo_min_s=1.0,
                  roi_ativa="on", roi_x1=0, roi_y1=0, roi_x2=100, roi_y2=100),
        "C": dict(especialista=PACOTE_DET, receita_versao_id=porte, crop_margem_h=5, crop_margem_v=5, crop_minimo_px=8, exec_intervalo_min_s=0.4),
    }
    ids = dict(zip("ABC", (a, b, c)))
    for k in cameras:
        assert env.salvar_config(ids[k], **cfgs[k]) == 303, f"config {k} recusada"
        env.sombra(ids[k], True)
    gerente = env.servicos.sombra
    manager = PediatriaSessionManager()
    gerente.definir_ia_rodando(lambda cid: any(s.get("camera_id") == f"cam_{cid}" and s.get("status") == "running" for s in manager.sessions()))
    t0 = time.perf_counter()
    gerente.reconcile()                                           # monta motor/despacho e CARREGA os modelos (fora da medicao estavel)
    carga_s = time.perf_counter() - t0
    aquecimento_ms = _aquecer(env, {cfgs[k]["especialista"] for k in cameras})
    estados = {k: gerente.estado(ids[k]) for k in cameras}
    assert all(e["estado"] in {"aguardando", "ativa"} for e in estados.values()), estados
    inicio_chamadas = len(env.medido.chamadas)
    for k in cameras:
        cam = ids[k]
        manager.start_session(SessionStartConfig(source=f"{BASE_RTSP}/{FONTES[k]}", camera_id=f"cam_{cam}", force_cpu=env.dispositivo == "cpu",
                                                 requested_device="auto" if env.dispositivo == "auto" else env.dispositivo,
                                                 report_dir=env.pasta / "evidencias", analysis_fps=5.0, motor_sombra=gerente.porta(cam)))
    sessoes = {k: next(x for x in manager._sessions.values() if x.camera_id == f"cam_{ids[k]}") for k in cameras}   # noqa: SLF001

    def snapshot() -> dict[str, dict[str, Any]]:
        """Contadores ACUMULADOS desde a partida de cada camera, num instante; so valem pela diferenca entre dois snapshots."""
        saida = {}
        for k in cameras:
            sess, slot = sessoes[k], gerente._slots[ids[k]]       # noqa: SLF001
            d = slot.despacho
            saida[k] = {"captura": {"lidos": getattr(sess.capture, "lidos", 0), "descartados": getattr(sess.capture, "dropped", 0)},
                        "sessao": {"quadros_processados": sess.state.frames_processed, "erros_motor_sombra": sess.evidence_error_counts.get("motor_sombra", 0),
                                   "popups_caminho_operacional_antigo": sess.state.popup_count},
                        "porta": {"cenas_aceitas": slot.porta.recebidos, "cenas_descartadas_por_cadencia": slot.porta.descartadas_cadencia},
                        "despacho": {"enfileirados": d.contadores["enfileirado"], "processados": d.contadores["processado"], "descartados": d.descartados} if d else {},
                        "motor": dict(slot.motor.contadores) if slot.motor else {}}
        return saida

    # PRONTIDAO: a janela so comeca quando cada sessao mostrou atividade (quadros processados pelo detector + classificador da propria sessao)
    # e cada camera concluiu avaliacoes na sombra. Prazo maximo; falha explicita (nunca medir "mesmo assim").
    prazo = time.monotonic() + 120.0
    while True:
        snap = snapshot()
        faltam = prontidao({k: {"quadros": snap[k]["sessao"]["quadros_processados"], "avaliacoes": snap[k]["despacho"].get("processados", 0), "exige_avaliacao": True}
                            for k in cameras}, MIN_QUADROS_PRONTIDAO, MIN_AVALIACOES_PRONTIDAO)
        if not faltam:
            break
        if time.monotonic() > prazo:
            for sid in [s["session_id"] for s in manager.sessions()]:
                manager.stop_session(sid)
            raise RuntimeError(f"sessoes nao ficaram prontas em 120 s: {faltam}")
        time.sleep(1.0)
    t_ini = time.monotonic()
    snap_ini = snapshot()                                         # limite inferior da janela e snapshot sao do mesmo instante (+- ms)
    time.sleep(duracao)
    t_fim = time.monotonic()
    snap_fim = snapshot()
    for sid in [s["session_id"] for s in manager.sessions()]:     # a parada e sequencial: o que concluir daqui em diante e DRENAGEM
        manager.stop_session(sid)
    t_parada = time.monotonic()
    efetivo = _dispositivos_efetivos(env, sessoes, cameras)
    janela_s = t_fim - t_ini
    todas_chamadas = [x for x in env.medido.chamadas[inicio_chamadas:] if x["camera_ref"] != "aquecimento"]
    rel: dict[str, Any] = {
        "janela": {"t_ini": t_ini, "t_fim": t_fim, "duracao_s": round(janela_s, 2), "parada_concluida_em": t_parada,
                   "populacao": "por CONCLUSAO no relogio monotonico: evento por avaliado_em; chamada por t+ms. [t_ini, t_fim) = dentro; depois = drenagem (fora das estatisticas)"},
        "prontidao": {"minimo_quadros": MIN_QUADROS_PRONTIDAO, "minimo_avaliacoes_na_sombra": MIN_AVALIACOES_PRONTIDAO,
                      "o_que_prova": "cada sessao processou quadros pelo detector+classificador proprios e cada camera concluiu avaliacoes na sombra; nao prova estado termico/estacionario do hardware"},
        "aquecimento_explicito_ms_por_pacote": aquecimento_ms, "cameras_na_condicao": cameras, "carga_modelos_painel_s": round(carga_s, 2),
        "dispositivos_efetivos": efetivo, "cameras": {}}
    for k in cameras:
        cam = ids[k]
        slot = gerente._slots[cam]                              # noqa: SLF001
        porta = slot.porta
        arq = env.pasta / "sombra" / f"camera-{cam}.jsonl"
        todos = [json.loads(x) for x in arq.read_text(encoding="utf-8").splitlines()] if arq.exists() else []
        pe = dividir_por_conclusao(todos, t_ini, t_fim, conclusao_evento)
        eventos = pe["dentro"]
        pc = dividir_por_conclusao([x for x in todas_chamadas if x["camera_ref"] == porta.ref], t_ini, t_fim, conclusao_chamada)
        estaveis = pc["dentro"]
        with env.database.session() as db:
            v = db.scalars(select(CameraConfigVersao).where(CameraConfigVersao.camera_id == cam).order_by(CameraConfigVersao.versao.desc())).first()
            cfg_json, cfg_hash, cfg_versao = json.loads(v.ajustes_json), v.config_hash, v.versao
        idade = [(e["avaliado_em"] - e["capturado_em"]) * 1000.0 for e in eventos]
        erros = Counter()
        for e in eventos:
            for vv in (e.get("erros") or {}).values():
                erros[vv if isinstance(vv, str) else str(vv)] += 1
        estouros = sum(n for kk, n in erros.items() if kk in {"E_PRAZO_ESGOTADO", "atrasado"})
        ce = cenas_e_avaliacoes(eventos)
        d = delta(snap_ini[k], snap_fim[k])
        n_aval = ce["cenas"] + ce["avaliacoes_por_sujeito"]
        rel["cameras"][k] = {
            "camera_id": cam, "fonte": f"{BASE_RTSP}/{FONTES[k]}",
            "natureza": "real (modelo OpenVINO real, pacote br.jutta.papel-pessoa)" if k != "C" else "DETERMINISTICO (pacote de conformidade, sem modelo; so mede o caminho)",
            "configuracao_efetiva": {"versao": cfg_versao, "hash": cfg_hash, "ajustes": cfg_json},
            "pacote": {"id": cfgs[k]["especialista"], "versao": "1.0.0"}, "receita_versao_id": cfgs[k]["receita_versao_id"],
            "receita": next((e["receita"] for e in eventos if "receita" in e), None), "estado_sombra": gerente.estado(cam),
            "totais_desde_a_partida_da_camera(NAO usar para taxas da janela)": snap_fim[k],
            "deltas_na_janela": d,
            "taxas_na_janela_por_s": {"quadros_lidos": taxa(d["captura"]["lidos"], janela_s), "quadros_processados_pela_sessao": taxa(d["sessao"]["quadros_processados"], janela_s),
                                      "descartados_pela_captura": taxa(d["captura"]["descartados"], janela_s), "cenas_aceitas_pela_porta": taxa(d["porta"]["cenas_aceitas"], janela_s),
                                      "cenas_avaliadas": taxa(ce["cenas"], janela_s), "avaliacoes_por_sujeito": taxa(ce["avaliacoes_por_sujeito"], janela_s)},
            "avaliacoes_na_janela": {**ce, "por_veredito_ou_tipo": dict(Counter(e.get("veredito") or e.get("tipo") for e in eventos)),
                                     "erros_e_motivos": dict(erros), "alerta_real_true": sum(1 for e in eventos if e.get("alerta_real")),
                                     "modo_diferente_de_sombra": sum(1 for e in eventos if e.get("modo") != "sombra")},
            "populacao_por_conclusao": {"eventos": {p: len(v) for p, v in pe.items()}, "chamadas_ao_executor": {p: len(v) for p, v in pc.items()}},
            "identidade_ok": all(e.get("camera_ref") == porta.ref for e in todos),
            "idade_da_observacao_na_avaliacao_ms": resumo_ms(idade),
            "especialista_ms_na_janela": resumo_ms([x["ms"] for x in estaveis]),
            "inferencia_ms_reportada_pelo_executor": resumo_ms([v for x in estaveis for v in x["inferencia_ms"]]),
            "estouros_de_prazo": {"ocorrencias": estouros, "avaliacoes": n_aval, "fracao": round(estouros / n_aval, 3) if n_aval else None},
        }
    rel["hipotese"] = HIPOTESE
    fr = {k: c["estouros_de_prazo"]["fracao"] for k, c in rel["cameras"].items()}
    p95 = {k: c["idade_da_observacao_na_avaliacao_ms"]["p95"] for k, c in rel["cameras"].items()}
    rel["veredito_da_hipotese"] = {"fracao_de_estouros": fr, "idade_p95_ms": p95,
                                   "sustentada": all(f is not None and f <= 0.05 for f in fr.values()) and all(p is not None and p < 500 for p in p95.values())}
    rel["definicoes"] = {
        "janela": "ver `janela`; as taxas usam a duracao [t_ini,t_fim) e os DELTAS de contadores, nunca os totais",
        "quadros_lidos/descartados": "captura ao vivo (leitor continuo; a IA le sempre o mais recente; descartado = sobrescrito sem ser analisado)",
        "cenas_aceitas_pela_porta": "Cenas que passaram pela cadencia (intervalo_min_s) e foram entregues ao diagnostico/alvo",
        "cenas_avaliadas vs avaliacoes_por_sujeito": "contrato 2 = um evento por Cena; contrato 1 = um evento por pessoa. Nao sao comparaveis entre contratos diferentes",
        "idade_da_observacao_na_avaliacao_ms": "avaliado_em - capturado_em (captura = recepcao local do quadro); inclui espera na fila + execucao",
        "especialista_ms": "tempo de parede de Executor.executar por chamada concluida na janela (aquecimento explicito fica fora)",
        "estouros_de_prazo": "ocorrencias de E_PRAZO_ESGOTADO ou atrasado nos erros dos eventos / numero de avaliacoes (um evento pode listar varias)",
        "orcamento": "500 ms por vinculo, desde a RECEPCAO do quadro; nao foi alterado",
    }
    rel["alertas_operacionais_no_banco"] = env.contar_eventos()
    return rel


MIN_QUADROS_PRONTIDAO = 30
MIN_AVALIACOES_PRONTIDAO = 3
HIPOTESE = ("H: com as cadencias declaradas (A=0,4 s; B=1,0 s; C=0,4 s), tres cameras simultaneas e janela fechada apos prontidao, "
            "a fracao de avaliacoes com estouro de prazo e <= 5 % e a idade p95 da observacao < 500 ms em CADA camera. "
            "Refutada se qualquer camera passar de 5 % ou 500 ms. Uma rodada nao decide capacidade: so testa a instrumentacao e a hipotese.")


def _dispositivos_efetivos(env: Ambiente, sessoes: dict[str, Any], cameras: str) -> dict[str, Any]:
    """Hardware realmente usado (OpenVINO compilado), por modelo, depois de inferir: especialistas do executor e modelos de cada sessao."""
    saida: dict[str, Any] = {"openvino_available_devices": None, "especialistas": {}, "sessoes": {}}
    try:
        import openvino as ov
        saida["openvino_available_devices"] = ov.Core().available_devices
    except Exception as exc:  # noqa: BLE001
        saida["openvino_available_devices"] = f"indisponivel: {exc}"
    for pid, carregado in list(env.servicos.executor._carregados.items()):       # noqa: SLF001
        for mid, m in carregado.modelos.items():
            if isinstance(m, ModeloDeterministico):
                saida["especialistas"][f"{pid}/{mid}"] = {"solicitado": "cpu", "execution_devices": "n/a (backend deterministico em Python, sem modelo)"}
            else:
                saida["especialistas"][f"{pid}/{mid}"] = {**dispositivo_efetivo(getattr(m, "_m", None), m.dispositivo), "fallbacks_ate_o_dispositivo_usado": carregado.fallbacks.get(mid)}
    for k in cameras:
        s = sessoes[k]
        saida["sessoes"][k] = {"dispositivo_pedido_pela_sessao": s.device,
                               "detector_de_pessoas": dispositivo_efetivo(getattr(s.service.crop_pipeline, "person_model", None), s.device),
                               "classificador_pediatrico": dispositivo_efetivo(getattr(s.service.runner, "model", None), s.device)}
    return saida


def _aquecer(env: Ambiente, pacotes: set[str], rodadas: int = 8) -> dict[str, list[float]]:
    """Aquecimento EXPLICITO e separado da janela estavel: chamadas reais ao Executor com recortes sinteticos (compilacao/primeira
    inferencia do dispositivo). Nao entram em nenhum feed nem cache; so existem para que a medicao estavel nao carregue o aquecimento."""
    from src.jutta_ped.especialistas.executor import Pedido
    saida: dict[str, list[float]] = {}
    for pid in sorted(pacotes):
        tempos = []
        for i in range(rodadas):
            recorte = np.random.default_rng(i).integers(0, 255, (256, 128, 3), dtype=np.uint8)
            t0 = time.perf_counter()
            res = env.medido.executar(pid, [Pedido(pedido_id=f"aquecimento-{i}", recorte=recorte, camera_ref="aquecimento", altura_pessoa_px=256, restante_ms=None)])
            assert res[0]["estado"] == "ok", res[0]
            tempos.append(round((time.perf_counter() - t0) * 1000.0, 1))
        saida[pid] = tempos
    return saida


def _contar_eventos(self) -> int:
    from sqlalchemy import func, select
    from src.jutta_ped.painel.models import EventoPediatria
    with self.database.session() as db:
        return db.scalar(select(func.count()).select_from(EventoPediatria)) or 0


Ambiente.contar_eventos = _contar_eventos


# ---------------------------------------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fase", choices=("mestre", "especialistas", "tudo"), default="tudo")
    ap.add_argument("--duracao", type=float, default=45.0, help="segundos de analise estavel na fase 2")
    ap.add_argument("--quadros", type=int, default=40, help="quadros por configuracao na fase 1")
    ap.add_argument("--cameras", default="ABC", help="subconjunto na fase 2 (ex.: A para a linha de base de uma camera so)")
    ap.add_argument("--dispositivo", choices=("auto", "cpu"), default="auto")
    args = ap.parse_args()
    if not CENARIO.exists():
        print(f"cenario nao encontrado: {CENARIO}")
        return 2
    import httpx
    try:
        n = len(httpx.get("http://127.0.0.1:9997/v3/paths/list", timeout=3).json()["items"])
    except Exception as exc:  # noqa: BLE001
        print(f"simulador indisponivel em 127.0.0.1:9997 ({exc}); este roteiro nao o inicia")
        return 2
    pasta = ROOT / "runtime" / f"validacao_motor_{time.strftime('%Y%m%d_%H%M%S')}"
    pasta.mkdir(parents=True)
    env = Ambiente(pasta, args.dispositivo)
    resultado: dict[str, Any] = {"pasta": str(pasta), "simulador_fontes": n, "dispositivo_solicitado": args.dispositivo, "fontes": FONTES,
                                 "pontos_de_entrada": "painel real em processo (sem HTTP/go2rtc/navegador); RTSP do simulador lido direto"}
    try:
        env.importar_cenario()
        resultado["pacotes"] = env.instalar_pacotes()
        receitas = env.criar_receitas()
        resultado["receitas_versao_id"] = receitas
        if args.fase in ("mestre", "tudo"):
            resultado["fase1_mestre"] = {k: fase_mestre(env, args.quadros, k) for k in ("A", "B")}
        if args.fase in ("especialistas", "tudo"):
            resultado["fase2_especialistas"] = fase_especialistas(env, args.duracao, receitas, args.cameras)
    finally:
        env.fechar()
    (pasta / "resultado.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))
    return 0 if all(v.get("aprovado") for v in resultado.get("fase1_mestre", {"x": {"aprovado": True}}).values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
