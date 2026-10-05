"""Instrumentacao do ensaio multicamera (revisao 05/10/2026): janela com dois limites, contadores por diferenca, cena x evento,
prontidao com falha explicita e dispositivo efetivo. Funcoes puras: sem video, sem modelo."""
from __future__ import annotations

from tools.medicao_janela import (
    cenas_e_avaliacoes, classificar, conclusao_chamada, conclusao_evento, delta, dispositivo_efetivo, dividir_por_conclusao, prontidao, taxa,
)


def test_janela_tem_dois_limites_e_a_drenagem_nao_entra_nas_estatisticas() -> None:
    eventos = [{"id": i, "avaliado_em": t} for i, t in enumerate([9.9, 10.0, 15.0, 69.99, 70.0, 71.5])]
    p = dividir_por_conclusao(eventos, 10.0, 70.0, conclusao_evento)
    assert [e["id"] for e in p["antes"]] == [0]                     # aquecimento
    assert [e["id"] for e in p["dentro"]] == [1, 2, 3]              # [t_ini, t_fim)
    assert [e["id"] for e in p["depois"]] == [4, 5]                 # concluiu durante a parada: drenagem, a parte
    assert classificar(70.0, 10.0, 70.0) == "depois" and classificar(10.0, 10.0, 70.0) == "dentro"


def test_chamada_pertence_a_janela_pelo_instante_de_conclusao() -> None:
    comeca_antes_e_termina_dentro = {"t": 9.9, "ms": 200.0}
    comeca_dentro_e_termina_depois = {"t": 69.9, "ms": 200.0}
    p = dividir_por_conclusao([comeca_antes_e_termina_dentro, comeca_dentro_e_termina_depois], 10.0, 70.0, conclusao_chamada)
    assert p["dentro"] == [comeca_antes_e_termina_dentro] and p["depois"] == [comeca_dentro_e_termina_depois] and p["antes"] == []


def test_item_sem_tempo_nao_entra_na_janela() -> None:
    assert dividir_por_conclusao([{"x": 1}], 0.0, 1.0, conclusao_evento) == {"antes": [{"x": 1}], "dentro": [], "depois": []}


def test_contadores_valem_pela_diferenca_e_totais_ficam_separados() -> None:
    ini = {"lidos": 100, "descartados": 80, "despacho": {"enfileirados": 10, "processados": 9}}
    fim = {"lidos": 400, "descartados": 330, "despacho": {"enfileirados": 70, "processados": 69}, "estado": "running"}
    d = delta(ini, fim)
    assert d == {"lidos": 300, "descartados": 250, "despacho": {"enfileirados": 60, "processados": 60}, "estado": "running"}
    assert fim["lidos"] == 400                                        # o total nao e alterado
    assert delta({}, {"n": 5}) == {"n": 5}


def test_cena_nao_e_evento_por_sujeito() -> None:
    contrato2 = [{"tipo": "cena"}] * 3
    contrato1 = [{"trilha_id": 1}, {"trilha_id": 2}, {"trilha_id": 1}, {"trilha_id": 2}]
    assert cenas_e_avaliacoes(contrato2) == {"cenas": 3, "avaliacoes_por_sujeito": 0}
    assert cenas_e_avaliacoes(contrato1) == {"cenas": 0, "avaliacoes_por_sujeito": 4}
    assert taxa(60, 60.0) == 1.0 and taxa(1, 0) is None


def test_prontidao_aponta_quem_nao_aqueceu() -> None:
    estados = {"A": {"quadros": 30, "avaliacoes": 3, "exige_avaliacao": True}, "B": {"quadros": 5, "avaliacoes": 0, "exige_avaliacao": True},
               "C": {"quadros": 30, "avaliacoes": 0, "exige_avaliacao": False}}
    assert prontidao(estados, 20, 2) == {"B": ["quadros 5/20", "avaliacoes 0/2"]}
    assert prontidao({"A": estados["A"]}, 20, 2) == {}


class _Compilado:
    def __init__(self, dispositivos): self._d = dispositivos
    def get_property(self, nome):
        assert nome == "EXECUTION_DEVICES"
        return self._d


class _Backend:
    def __init__(self, device, compilado): self.device, self.ov_compiled_model = device, compilado


class _Modelo:
    def __init__(self, backend): self.predictor = type("P", (), {"model": backend})()


def test_dispositivo_efetivo_nao_confia_no_pedido() -> None:
    cpu_apesar_do_pedido = _Modelo(_Backend("cpu", _Compilado(["(CPU)"])))
    r = dispositivo_efetivo(cpu_apesar_do_pedido, "intel:gpu")
    assert r["execution_devices"] == ["(CPU)"] and r["divergente_do_solicitado"] is True and r["solicitado"] == "intel:gpu"
    gpu = dispositivo_efetivo(_Modelo(_Backend("intel:gpu", _Compilado(["GPU.0"]))), "intel:gpu")
    assert gpu["divergente_do_solicitado"] is False
    assert dispositivo_efetivo(_Modelo(_Backend("cpu", _Compilado(["(CPU)"]))), "cpu")["divergente_do_solicitado"] is False


def test_dispositivo_efetivo_sem_inferencia_previa_diz_que_nao_sabe() -> None:
    r = dispositivo_efetivo(object(), "cpu")
    assert r["execution_devices"] is None and "indisponivel" in r and r["divergente_do_solicitado"] is False
