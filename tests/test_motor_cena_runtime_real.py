"""Laco REAL da sessao -> mestre (dublê) -> Cena -> DespachoSombra -> MotorSombra REAL com receita contrato 2 e relogio controlado.

Prova a cadeia de integracao (inclusive o encerramento da sessao) sem modelo real; dados sinteticos nao provam qualidade."""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from test_especialistas_executor import BackendFalso, fabrica, instalar_ativar, manifesto_bebe, manifesto_cor
from test_motor_sombra import BEBE, COR, SIM_AZUL
from test_pediatria_service_runtime_reconnect import ScriptedCapture, make_bare_session

from src.jutta_ped.especialistas.instalador import Instalador
from src.jutta_ped.motor.despacho import DespachoSombra
from src.jutta_ped.motor.motor import MotorSombra
from src.jutta_ped.service.runtime import SessionStartConfig

RECEITA = {"id": "r.v2", "versao": "1", "contrato": 2,
           "vinculos": [{"especialista": BEBE, "orcamento_ms": 500}, {"especialista": COR, "orcamento_ms": 500}],
           "papeis": {"alvo": {"classe": "person", "combinacao": "todas", "condicoes": [{"especialista": BEBE, "atributo": "bebe_no_colo", "igual": "sim"}]},
                      "ref": {"classe": "person", "combinacao": "todas", "condicoes": [{"especialista": COR, "atributo": "cor_uniforme", "igual": "azul"}]}},
           "relacoes": {"sem_ref": {"tipo": "nenhum_proximo", "sujeito": "alvo", "outro": "ref", "distancia": {"unidade": "altura_sujeito", "maximo": 1.0}}},
           "eventos": [{"id": "isolado", "sujeito": "alvo", "quando_todas": ["sem_ref"], "duracao_s": 3, "lacuna_max_s": 2, "perda_max_s": 5, "encerrar_negativo_s": 2}]}


class Relogio:
    def __init__(self) -> None:
        self.t = 5000.0

    def __call__(self) -> float:
        return self.t


class CapturaComTempos(ScriptedCapture):
    """Entrega quadros e informa o instante de RECEPCAO local de cada um (como o LatestFrameCapture), no relogio controlado."""

    def __init__(self, script, tempos, relogio: Relogio) -> None:
        super().__init__(script)
        self._tempos, self._relogio = list(tempos), relogio
        self.ultimo_recebido_em = None

    def read(self):
        ok, quadro = super().read()
        if ok:
            self.ultimo_recebido_em = self._tempos.pop(0)
            self._relogio.t = self.ultimo_recebido_em
        return ok, quadro


class ServicoComMestre:
    """Dublê do PediatriaService: conta passagens do mestre e do pipeline pediatrico; o mestre devolve 1 pessoa sozinha."""

    def __init__(self) -> None:
        self.mestre = 0
        self.pipeline = 0

    def observar_mestre(self, frame, frame_id):
        self.mestre += 1
        return [SimpleNamespace(bbox_xyxy=(50, 60, 130, 260), track_id=1, confidence=0.9, role="person")]

    def process_frame(self, frame, *, frame_id):
        self.pipeline += 1
        return SimpleNamespace(latency_ms=1.0, state=SimpleNamespace(stable_state="NO_CHILD", children=[]), resolved_detections=[], person_detections=[])


def test_sessao_real_entrega_cenas_ao_motor_com_regras_e_encerra_a_sessao(tmp_path: Path) -> None:
    inst = Instalador(tmp_path / "raiz")
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-bebe")
    instalar_ativar(inst, tmp_path, manifesto_cor(), b"pesos-cor", "modelos/cor.pt")
    relogio, eventos = Relogio(), []
    motor = MotorSombra(fabrica(inst, BackendFalso(tipo_por_arquivo={"modelos/cor.pt": "classificacao"})), habilitado=True, relogio=relogio, destino=eventos.append)
    motor.configurar([RECEITA], {"test_camera": {"receita": "r.v2", "versao": "1"}})
    despacho = DespachoSombra(motor)
    cfg = SessionStartConfig(source=str(tmp_path / "v.mp4"), report_dir=tmp_path, motor_sombra=despacho, reconnect_enabled=False)
    s = make_bare_session(cfg, tmp_path)
    s._geracao_sombra = 7
    s.service = ServicoComMestre()
    quadro = np.zeros((480, 640, 3), np.uint8)
    quadro[60, 50] = SIM_AZUL
    tempos = [5000.0, 5001.0, 5002.0, 5003.0, 5004.0]
    s.capture = CapturaComTempos([(True, quadro.copy()) for _ in tempos] + [(False, None)], tempos, relogio)
    # O despacho roda em outra thread: sincroniza por barreira de ociosidade a cada quadro (o relogio e do teste, nao do tempo real)
    original = s._entregar_cena

    def _entregar_e_esperar(frame, frame_id):
        r = original(frame, frame_id)
        assert despacho.aguardar_ociosa(15)
        return r

    s._entregar_cena = _entregar_e_esperar
    s._run_loop()
    assert despacho.aguardar_ociosa(15)
    assert s.service.mestre == 5 and s.service.pipeline == 5                            # uma passagem do mestre e uma do pipeline por quadro
    assert s.evidence_error_counts["motor_sombra"] == 0 and s.state.popup_count == 0   # operacional intacto
    cenas = [e for e in eventos if e["tipo"] == "cena"]
    assert len(cenas) == 5 and all(e["alerta_real"] is False and e["geracao"] == 7 for e in cenas)
    assert [e["eventos"][0]["estado"] for e in cenas][:3] == ["candidato", "candidato", "candidato"]
    ativo = cenas[3]["eventos"][0]
    assert ativo["estado"] == "ativo" and ativo["transicao"] == "iniciado" and ativo["positivo_s"] == 3.0        # 5000 -> 5003 por TIMESTAMPS
    # a sessao terminou (EOF): o estado das regras foi descartado como `sessao_encerrada` (nao como resolvido)
    assert motor.contadores["evento_encerrado_sessao_encerrada"] == 1
    assert "sombra" not in str(s.state.last_error or "")
    despacho.parar()
    _ = time
