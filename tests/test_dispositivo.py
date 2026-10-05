"""Contrato de dispositivo: CPU explicita executa em CPU ou falha com explicacao; fallback so quando permitido e sempre informado.

Defeito de origem: o Ultralytics trata `device="cpu"` de um modelo OpenVINO como `AUTO` (pode compilar na GPU) e `intel:gpu` sem o nome
exato (`GPU.0`) tambem cai em `AUTO`. Tudo aqui e deterministico (lista de dispositivos e modelos falsos); a conferencia com o hardware
real esta no roteiro `tools/validar_motor_especialistas.py`."""
from __future__ import annotations

from pathlib import Path

import pytest
from test_especialistas_executor import BackendFalso, fabrica, inst, instalar_ativar, manifesto_bebe, pedido  # noqa: F401

from src.jutta_ped.dispositivo import (
    DispositivoIndisponivel, ModeloOpenVinoEstrito, conferir_efetivo, dispositivos_efetivos, resolver_openvino,
)
from src.jutta_ped.especialistas.executor import E_MODELO, Executor

DISP = ["CPU", "GPU.0", "GPU.1"]


# ------------------------------------------------------------------------------------------------ resolucao

def test_cpu_explicita_vira_dispositivo_openvino_cpu_e_nao_auto() -> None:
    for pedido_ in ("cpu", "CPU", "intel:cpu", "auto", None, ""):
        r = resolver_openvino(pedido_, DISP)
        assert (r.device_ultralytics, r.nome_openvino, r.fallback) == ("intel:CPU", "CPU", None)


def test_gpu_resolve_para_o_nome_exato_e_aceita_indice() -> None:
    assert resolver_openvino("intel:gpu", DISP).device_ultralytics == "intel:GPU.0"
    assert resolver_openvino("gpu", DISP).nome_openvino == "GPU.0"
    assert resolver_openvino("intel:gpu.1", DISP).nome_openvino == "GPU.1"


def test_dispositivo_indisponivel_falha_com_explicacao_sem_fallback() -> None:
    with pytest.raises(DispositivoIndisponivel) as e:
        resolver_openvino("intel:gpu", ["CPU"])
    assert "indisponivel" in str(e.value) and "CPU" in str(e.value)
    with pytest.raises(DispositivoIndisponivel):
        resolver_openvino("intel:gpu.1", ["CPU", "GPU.0"])
    with pytest.raises(DispositivoIndisponivel) as e2:
        resolver_openvino("cuda:0", DISP)
    assert "nao e um dispositivo OpenVINO" in str(e2.value)


def test_fallback_so_com_permissao_e_sempre_informado() -> None:
    r = resolver_openvino("intel:gpu", ["CPU"], permitir_fallback=True)
    assert r.nome_openvino == "CPU" and r.fallback and "fallback permitido" in r.fallback and "intel:gpu" in r.fallback


# ------------------------------------------------------------------------------------------------ proxy do modelo

class _Compilado:
    def __init__(self, dev): self.dev = dev
    def get_property(self, nome): return list(self.dev) if isinstance(self.dev, (list, tuple)) else [self.dev]


SEM_TELEMETRIA = object()          # o modelo infere, mas nao expoe EXECUTION_DEVICES


class YoloFalso:
    """Imita `YOLO(<openvino>)`: registra o `device=` recebido e compila no dispositivo `executa_em` (ex.: o OpenVINO AUTO escolhendo a GPU)."""
    def __init__(self, executa_em_por_device): self.chamadas, self._mapa, self.predictor = [], executa_em_por_device, None
    names = {0: "child"}
    overrides: dict = {}

    def _rodar(self, device):
        self.chamadas.append(device)
        executa = self._mapa.get(device, "CPU")
        compilado = None if executa is SEM_TELEMETRIA else _Compilado(executa)
        self.predictor = type("P", (), {"model": type("M", (), {"ov_compiled_model": compilado})()})()
        return ["resultado"]

    def track(self, *a, device=None, **k): return self._rodar(device)
    def predict(self, *a, device=None, **k): return self._rodar(device)


def test_proxy_traduz_cpu_e_confere_o_efetivo() -> None:
    m = YoloFalso({"intel:CPU": "CPU"})
    p = ModeloOpenVinoEstrito(m, disponiveis=DISP)
    assert p.track(object(), device="cpu") == ["resultado"] and m.chamadas == ["intel:CPU"]
    p.predict(object(), device="cpu")
    assert m.chamadas == ["intel:CPU", "intel:CPU"] and p.names == {0: "child"}            # o resto e delegado


def test_proxy_recusa_quando_o_modelo_executa_em_outro_dispositivo() -> None:
    m = YoloFalso({"intel:CPU": "GPU.0"})                                                  # o que o AUTO fazia
    with pytest.raises(DispositivoIndisponivel) as e:
        ModeloOpenVinoEstrito(m, disponiveis=DISP).track(object(), device="cpu")
    assert "executou em" in str(e.value) and "GPU.0" in str(e.value)


def test_proxy_dispositivo_explicito_indisponivel_falha_e_fallback_permitido_e_registrado() -> None:
    with pytest.raises(DispositivoIndisponivel):
        ModeloOpenVinoEstrito(YoloFalso({}), disponiveis=["CPU"]).track(object(), device="intel:gpu")
    p = ModeloOpenVinoEstrito(YoloFalso({"intel:CPU": "CPU"}), permitir_fallback=True, disponiveis=["CPU"])
    p.track(object(), device="intel:gpu")
    assert len(p.fallbacks) == 1 and "fallback permitido" in p.fallbacks[0]


def test_efetivo_desconhecido_nao_e_inventado() -> None:
    assert dispositivos_efetivos(object()) is None
    with pytest.raises(DispositivoIndisponivel) as e:                                     # estrito (padrao): ilegivel = erro explicito
        conferir_efetivo(object(), resolver_openvino("cpu", DISP))
    assert "EXECUTION_DEVICES" in str(e.value)
    assert conferir_efetivo(object(), resolver_openvino("cpu", DISP), estrito=False) is None


# ------------------------------------------------------------------------------------------------ revisao: identidade completa e telemetria

@pytest.mark.parametrize("pedido_, efetivo", [
    ("cpu", ["CPU", "GPU.0"]),          # AUTO/multi: tambem usa a GPU -> nao e "CPU"
    ("intel:gpu.0", ["GPU.01"]),        # prefixo nao e identidade
    ("intel:gpu.1", ["GPU.0"]),
    ("cpu", ["GPU.0"]),
    ("cpu", ["HETERO:CPU"]),
])
def test_identidade_completa_do_dispositivo_efetivo(pedido_, efetivo) -> None:
    disp = ["CPU", "GPU.0", "GPU.1", "GPU.01"]
    m = YoloFalso({resolver_openvino(pedido_, disp).device_ultralytics: efetivo})
    with pytest.raises(DispositivoIndisponivel) as e:
        ModeloOpenVinoEstrito(m, disponiveis=disp).track(object(), device=pedido_)
    assert "executou em" in str(e.value)


def test_identidade_aceita_so_o_proprio_dispositivo_inclusive_entre_parenteses() -> None:
    for efetivo in (["CPU"], ["(CPU)"], [" cpu "]):
        p = ModeloOpenVinoEstrito(YoloFalso({"intel:CPU": efetivo}), disponiveis=DISP)
        p.track(object(), device="cpu")
        assert p._conferidos["cpu"] == efetivo
    g = ModeloOpenVinoEstrito(YoloFalso({"intel:GPU.0": ["GPU.0"]}), disponiveis=DISP)
    g.track(object(), device="intel:gpu")
    assert g._conferidos["intel:gpu"] == ["GPU.0"]


def test_proxy_estrito_sem_telemetria_falha_e_nao_marca_conferido() -> None:
    m = YoloFalso({"intel:CPU": SEM_TELEMETRIA})
    p = ModeloOpenVinoEstrito(m, disponiveis=DISP)
    with pytest.raises(DispositivoIndisponivel) as e:
        p.track(object(), device="cpu")
    assert "EXECUTION_DEVICES" in str(e.value) and "cpu" not in p._conferidos
    with pytest.raises(DispositivoIndisponivel):                                           # continua falhando: nada foi "aprendido"
        p.track(object(), device="cpu")


def test_proxy_com_fallback_permitido_sem_telemetria_nao_marca_e_reconfere_depois() -> None:
    m = YoloFalso({"intel:CPU": SEM_TELEMETRIA})
    p = ModeloOpenVinoEstrito(m, permitir_fallback=True, disponiveis=DISP)
    p.track(object(), device="cpu")
    assert p.nao_conferidos == 1 and "cpu" not in p._conferidos
    m._mapa["intel:CPU"] = ["GPU.0"]                                                       # a telemetria aparece e mostra divergencia
    with pytest.raises(DispositivoIndisponivel):
        p.track(object(), device="cpu")
    m._mapa["intel:CPU"] = ["CPU"]
    p.track(object(), device="cpu")
    assert p._conferidos["cpu"] == ["CPU"]
    p.track(object(), device="cpu")
    assert p.nao_conferidos == 1                                                           # ja conferido: nao reconfere


# ------------------------------------------------------------------------------------------------ revisao: backend do executor

class _Resultado:
    names = {0: "child"}
    probs = None
    boxes = None


class ModeloBackendFalso:
    """Imita o `YOLO` usado pelo backend do executor: `predict` devolve resultados e deixa (ou nao) a telemetria do compilado."""
    names = {0: "child"}

    def __init__(self, efetivo):
        self.overrides, self._efetivo, self.predictor, self.chamadas = {}, efetivo, None, 0

    def predict(self, imagens, **k):
        self.chamadas += 1
        compilado = None if self._efetivo is SEM_TELEMETRIA else _Compilado(self._efetivo)
        self.predictor = type("P", (), {"model": type("M", (), {"ov_compiled_model": compilado})()})()
        return [_Resultado() for _ in imagens]


def _modelo_backend(efetivo, nomes=None):
    from src.jutta_ped.especialistas.backend_ultralytics import ModeloUltralytics
    formato = {"cor": "RGB", "largura": 64, "altura": 64, "redimensionamento": "letterbox", "normalizacao": "0-1"}
    fake = ModeloBackendFalso(efetivo)
    if nomes is not None:
        fake.names = nomes
    return ModeloUltralytics(fake, "cpu", formato, "detect", None, resolver_openvino("cpu", DISP)), fake


def test_backend_do_executor_confere_identidade_completa() -> None:
    import numpy as np
    for errado in (["CPU", "GPU.0"], ["GPU.0"]):
        m, _ = _modelo_backend(errado)
        with pytest.raises(DispositivoIndisponivel):
            m.inferir([np.zeros((32, 16, 3), np.uint8)])
        assert m._verificado is False and m.dispositivo_efetivo is None
    m, _ = _modelo_backend(["CPU"])
    assert m.inferir([np.zeros((32, 16, 3), np.uint8)]) == [{"deteccoes": []}]
    assert m._verificado is True and m.dispositivo_efetivo == ["CPU"]


def test_backend_do_executor_sem_telemetria_e_erro_e_nunca_conferido() -> None:
    import numpy as np
    m, fake = _modelo_backend(SEM_TELEMETRIA)
    for _ in range(2):
        with pytest.raises(DispositivoIndisponivel) as e:
            m.inferir([np.zeros((32, 16, 3), np.uint8)])
        assert "EXECUTION_DEVICES" in str(e.value)
    assert m._verificado is False and m.dispositivo_efetivo is None and fake.chamadas == 2
    assert m.inferir([]) == []                                                             # sem imagem nao ha inferencia a conferir


def test_executor_transforma_falha_de_conferencia_em_erro_por_pedido(tmp_path: Path, inst) -> None:
    """Pelo executor: o resultado e erro explicito (nunca atributos de um dispositivo nao conferido)."""
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")

    class Backend:
        def carregar(self, modelo, pasta, dispositivo, formato):
            return _modelo_backend(SEM_TELEMETRIA, {0: "bebe_colo", 1: "outra"})[0]

    ex = Executor(inst, backends={"pt": Backend()}, dispositivos_permitidos=("cpu",))
    r = ex.executar("br.teste.bebe", [pedido(1)])[0]
    assert r["estado"] == "erro" and "EXECUTION_DEVICES" in r["erro"]["mensagem"] and r["atributos"] == []
    assert ex.info("br.teste.bebe")["dispositivos_efetivos"] == {"principal": None}


# ------------------------------------------------------------------------------------------------ executor

def test_restricao_do_host_a_cpu_nao_tenta_outro_dispositivo(tmp_path: Path, inst) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")                           # manifesto: cuda, cpu
    backend = BackendFalso()
    ex = Executor(inst, backends={"pt": backend, "openvino": backend}, cuda_probe=lambda: True, dispositivos_permitidos=("cpu",))
    assert ex.executar("br.teste.bebe", [pedido(1)])[0]["estado"] == "ok"
    assert backend.cargas == [("principal", "cpu")]                                         # cuda estava disponivel e NAO foi tentada


def test_restricao_a_cpu_que_falha_nao_cai_para_outro_e_explica(tmp_path: Path, inst) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    backend = BackendFalso(falha_em={"cpu"})
    ex = Executor(inst, backends={"pt": backend, "openvino": backend}, cuda_probe=lambda: True, dispositivos_permitidos=("cpu",))
    r = ex.executar("br.teste.bebe", [pedido(1)])[0]
    assert r["erro"]["codigo"] == E_MODELO and "cpu" in r["erro"]["mensagem"] and backend.cargas == [("principal", "cpu")]


def test_restricao_a_dispositivo_que_o_pacote_nao_declara_e_recusada_com_explicacao(tmp_path: Path, inst) -> None:
    m = manifesto_bebe()
    instalar_ativar(inst, tmp_path, m, b"pesos-a")
    ex = Executor(inst, backends={"pt": BackendFalso()}, dispositivos_permitidos=("intel_gpu",))
    r = ex.executar("br.teste.bebe", [pedido(1)])[0]
    assert r["erro"]["codigo"] == E_MODELO and "declara apenas" in r["erro"]["mensagem"]


def test_fallback_declarado_no_manifesto_e_informado(tmp_path: Path, inst) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    backend = BackendFalso(falha_em={"cuda:0"})
    ex = fabrica(inst, backend, cuda=True)
    assert ex.executar("br.teste.bebe", [pedido(1)])[0]["estado"] == "ok"
    info = ex.info("br.teste.bebe")
    assert info["dispositivos"] == {"principal": "cpu"} and len(info["fallbacks"]["principal"]) == 1 and "cuda:0" in info["fallbacks"]["principal"][0]
    sem = fabrica(inst, BackendFalso(), cuda=False)
    sem.executar("br.teste.bebe", [pedido(1)])
    assert sem.info("br.teste.bebe")["fallbacks"] == {"principal": []}                     # sem fallback = lista vazia, explicita


def test_modelos_diferentes_no_mesmo_processo_nao_misturam_dispositivos() -> None:
    """Regressao dos ambientes com varios modelos: cada modelo resolve o proprio dispositivo; um em CPU nao muda o de outro."""
    gpu, cpu = YoloFalso({"intel:GPU.0": "GPU.0"}), YoloFalso({"intel:CPU": "CPU"})
    a, b = ModeloOpenVinoEstrito(gpu, disponiveis=DISP), ModeloOpenVinoEstrito(cpu, disponiveis=DISP)
    b.track(object(), device="cpu")
    a.track(object(), device="intel:gpu")
    assert cpu.chamadas == ["intel:CPU"] and gpu.chamadas == ["intel:GPU.0"]
