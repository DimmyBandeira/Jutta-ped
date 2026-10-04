"""Ciclo local de instalacao: validar -> extrair isolado -> registrar -> ativar/reverter -> recuperar.
Pesos sinteticos (nao sao modelos executaveis)."""
from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

import src.jutta_ped.especialistas.instalador as inst_mod
from src.jutta_ped.especialistas.cli import main
from src.jutta_ped.especialistas.fontes import Limites
from src.jutta_ped.especialistas.instalador import (
    COD_BLOQUEIO,
    COD_CONFLITO,
    COD_EM_USO,
    COD_ESTADO,
    COD_INTEGRIDADE,
    COD_REPROVADO,
    ErroInstalacao,
    Instalador,
)
from test_especialistas_validador import PESOS_PT, escrever_pasta, escrever_zip, manifesto, sha


class Queda(BaseException):
    """Simula a morte do processo: BaseException nao e capturada pelos `except Exception` do instalador."""


def pacote_zip(tmp_path: Path, nome: str, versao: str = "1.0.0", pesos: bytes = PESOS_PT) -> Path:
    m = manifesto()
    m["pacote"]["versao"] = versao
    m["modelos"][0]["sha256"] = sha(pesos)
    destino = tmp_path / nome
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(m))
        z.writestr("modelos/bebe.pt", pesos)
        z.writestr("LEIAME.md", f"# {versao}")
    return destino


@pytest.fixture
def inst(tmp_path: Path) -> Instalador:
    return Instalador(tmp_path / "raiz")


def falhar_em(monkeypatch, ponto: str) -> None:
    def _p(nome: str) -> None:
        if nome == ponto:
            raise Queda(nome)
    monkeypatch.setattr(inst_mod, "_ponto", _p)


# --- instalar ---------------------------------------------------------------------------------------------

def test_instalar_zip_extrai_registra_e_nao_ativa(tmp_path: Path, inst: Instalador) -> None:
    r = inst.instalar(pacote_zip(tmp_path, "a.zip"))
    assert (r.id, r.versao, r.ja_instalado) == ("br.teste.bebe", "1.0.0", False)
    assert (r.caminho / "modelos" / "bebe.pt").read_bytes() == PESOS_PT
    esp = inst.listar()["br.teste.bebe"]
    assert esp["ativa"] is None and esp["versoes"]["1.0.0"]["estado"] == "instalada"
    assert esp["versoes"]["1.0.0"]["hashes"]["principal"] == sha(PESOS_PT)
    assert inst.caminho_ativo("br.teste.bebe") is None
    assert not list(inst.staging.iterdir()) and not list(inst.pendentes.iterdir())


def test_instalar_pasta(tmp_path: Path, inst: Instalador) -> None:
    r = inst.instalar(escrever_pasta(tmp_path / "pasta", manifesto()))
    assert (r.caminho / "manifest.json").is_file()


def test_pacote_reprovado_nao_deixa_rastro(tmp_path: Path, inst: Instalador) -> None:
    ruim = escrever_zip(tmp_path / "ruim.zip", manifesto(protocolo=2))
    with pytest.raises(ErroInstalacao) as e:
        inst.instalar(ruim)
    assert e.value.codigo == COD_REPROVADO and e.value.relatorio.codigo_saida == 5
    assert inst.listar() == {} and not any(inst.pacotes.iterdir())


def test_reinstalar_igual_e_idempotente_e_pesos_diferentes_sao_recusados(tmp_path: Path, inst: Instalador) -> None:
    inst.instalar(pacote_zip(tmp_path, "a.zip"))
    assert inst.instalar(pacote_zip(tmp_path, "b.zip")).ja_instalado is True
    with pytest.raises(ErroInstalacao) as e:
        inst.instalar(pacote_zip(tmp_path, "c.zip", pesos=PESOS_PT + b"alterado"))
    assert e.value.codigo == COD_CONFLITO and e.value.slug == "versao_imutavel"
    assert (inst.pacotes / "br.teste.bebe" / "1.0.0" / "modelos" / "bebe.pt").read_bytes() == PESOS_PT


def test_extracao_respeita_limites_mesmo_que_o_zip_minta(tmp_path: Path) -> None:
    p = pacote_zip(tmp_path, "a.zip")
    inst = Instalador(tmp_path / "r", limites=Limites(max_total_bytes=len(PESOS_PT) - 1))
    with pytest.raises(ErroInstalacao) as e:
        inst.instalar(p)
    assert e.value.codigo == COD_REPROVADO       # reprovado ja pelo validador (limite total)
    assert not any(inst.staging.iterdir())


def test_extracao_nunca_sai_do_staging(tmp_path: Path, inst: Instalador, monkeypatch) -> None:
    """Defesa em profundidade: mesmo que a validacao deixasse passar um nome perigoso, a extracao recusa."""
    from src.jutta_ped.especialistas.fontes import Entrada, Listagem

    class FonteMa:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def listar(self, lim): return Listagem(entradas=[Entrada("../fugiu.txt", 1)])
        def blocos(self, c): yield b"x"

    monkeypatch.setattr(inst_mod, "_abrir_fonte", lambda origem: FonteMa())
    stg = inst.staging / "t"
    stg.mkdir()
    with pytest.raises(ErroInstalacao) as e:
        inst._extrair(tmp_path, stg)
    assert e.value.slug == "caminho_fora_do_destino" and not (inst.staging / "fugiu.txt").exists()


# --- ativar / reverter / remover --------------------------------------------------------------------------

def test_ativar_trocar_e_reverter(tmp_path: Path, inst: Instalador) -> None:
    inst.instalar(pacote_zip(tmp_path, "a.zip", "1.0.0"))
    inst.instalar(pacote_zip(tmp_path, "b.zip", "1.1.0", pesos=PESOS_PT + b"v2"))
    assert inst.ativar("br.teste.bebe", "1.0.0") == "1.0.0"
    assert inst.ativar("br.teste.bebe", "1.0.0") == "1.0.0"       # idempotente, sem inflar o historico
    inst.ativar("br.teste.bebe", "1.1.0")
    esp = inst.listar()["br.teste.bebe"]
    assert esp["ativa"] == "1.1.0" and esp["historico"] == ["1.0.0"]
    assert inst.caminho_ativo("br.teste.bebe") == inst.pacotes / "br.teste.bebe" / "1.1.0"
    assert inst.reverter("br.teste.bebe") == "1.0.0"
    esp = inst.listar()["br.teste.bebe"]
    assert esp["ativa"] == "1.0.0" and "1.1.0" in esp["versoes"]    # a atual continua instalada, inativa
    with pytest.raises(ErroInstalacao) as e:
        inst.reverter("br.teste.bebe")
    assert e.value.slug == "sem_versao_anterior_valida"


def test_ativar_versao_corrompida_e_recusado_e_marcado(tmp_path: Path, inst: Instalador) -> None:
    r = inst.instalar(pacote_zip(tmp_path, "a.zip"))
    (r.caminho / "modelos" / "bebe.pt").write_bytes(b"adulterado")
    with pytest.raises(ErroInstalacao) as e:
        inst.ativar("br.teste.bebe", "1.0.0")
    assert e.value.codigo == COD_INTEGRIDADE
    assert inst.listar()["br.teste.bebe"]["versoes"]["1.0.0"]["estado"] == "corrompida"
    assert inst.listar()["br.teste.bebe"]["ativa"] is None
    with pytest.raises(ErroInstalacao) as e2:
        inst.ativar("br.teste.bebe", "1.0.0")
    assert e2.value.codigo == COD_ESTADO


def test_reverter_pula_versao_anterior_corrompida(tmp_path: Path, inst: Instalador) -> None:
    for i, v in enumerate(("1.0.0", "1.1.0", "1.2.0")):
        inst.instalar(pacote_zip(tmp_path, f"{v}.zip", v, pesos=PESOS_PT + bytes([i])))
        inst.ativar("br.teste.bebe", v)
    (inst.pacotes / "br.teste.bebe" / "1.1.0" / "modelos" / "bebe.pt").write_bytes(b"x")
    assert inst.reverter("br.teste.bebe") == "1.0.0"
    assert inst.listar()["br.teste.bebe"]["versoes"]["1.1.0"]["estado"] == "corrompida"


def test_remover_recusa_a_versao_ativa(tmp_path: Path, inst: Instalador) -> None:
    inst.instalar(pacote_zip(tmp_path, "a.zip", "1.0.0"))
    inst.instalar(pacote_zip(tmp_path, "b.zip", "1.1.0", pesos=PESOS_PT + b"2"))
    inst.ativar("br.teste.bebe", "1.1.0")
    with pytest.raises(ErroInstalacao) as e:
        inst.remover("br.teste.bebe", "1.1.0")
    assert e.value.codigo == COD_EM_USO
    inst.remover("br.teste.bebe", "1.0.0")
    assert not (inst.pacotes / "br.teste.bebe" / "1.0.0").exists()
    assert "1.0.0" not in inst.listar()["br.teste.bebe"]["versoes"]


# --- queda e recuperacao -----------------------------------------------------------------------------------

def test_queda_apos_extracao_nao_deixa_nada_instalado(tmp_path: Path, inst: Instalador, monkeypatch) -> None:
    falhar_em(monkeypatch, "apos_extracao")
    with pytest.raises(Queda):
        inst.instalar(pacote_zip(tmp_path, "a.zip"))
    assert any(inst.staging.iterdir())                       # sobrou extracao parcial
    monkeypatch.undo()
    acoes = inst.recuperar()
    assert any("staging descartado" in a for a in acoes)
    assert inst.listar() == {} and not any(inst.staging.iterdir())
    assert inst.instalar(pacote_zip(tmp_path, "a.zip")).ja_instalado is False   # e instala normalmente depois


def test_queda_depois_de_mover_e_antes_de_registrar_conclui_na_recuperacao(tmp_path: Path, inst: Instalador, monkeypatch) -> None:
    falhar_em(monkeypatch, "apos_mover")
    with pytest.raises(Queda):
        inst.instalar(pacote_zip(tmp_path, "a.zip"))
    assert (inst.pacotes / "br.teste.bebe" / "1.0.0").is_dir() and inst.listar() == {}
    monkeypatch.undo()
    acoes = inst.recuperar()
    assert any("instalacao concluida na recuperacao" in a for a in acoes)
    assert inst.listar()["br.teste.bebe"]["versoes"]["1.0.0"]["estado"] == "instalada"
    assert not any(inst.pendentes.iterdir())
    assert inst.ativar("br.teste.bebe", "1.0.0") == "1.0.0"


def test_queda_depois_de_registrar_so_limpa_o_marcador(tmp_path: Path, inst: Instalador, monkeypatch) -> None:
    falhar_em(monkeypatch, "apos_registro")
    with pytest.raises(Queda):
        inst.instalar(pacote_zip(tmp_path, "a.zip"))
    assert any(inst.pendentes.iterdir())
    monkeypatch.undo()
    inst.recuperar()
    assert not any(inst.pendentes.iterdir()) and "1.0.0" in inst.listar()["br.teste.bebe"]["versoes"]


def test_instalacao_interrompida_com_conteudo_adulterado_vai_para_quarentena(tmp_path: Path, inst: Instalador, monkeypatch) -> None:
    falhar_em(monkeypatch, "apos_mover")
    with pytest.raises(Queda):
        inst.instalar(pacote_zip(tmp_path, "a.zip"))
    (inst.pacotes / "br.teste.bebe" / "1.0.0" / "modelos" / "bebe.pt").write_bytes(b"lixo")
    monkeypatch.undo()
    acoes = inst.recuperar()
    assert any("quarentena" in a for a in acoes)
    assert inst.listar() == {} and any(inst.quarentena.iterdir())


def test_diretorio_orfao_vai_para_quarentena_sem_apagar(tmp_path: Path, inst: Instalador) -> None:
    orfao = inst.pacotes / "br.x.y" / "9.9.9"
    orfao.mkdir(parents=True)
    (orfao / "x.txt").write_text("dados")
    inst.recuperar()
    assert not orfao.exists() and any(inst.quarentena.rglob("x.txt"))


def test_versao_registrada_sem_arquivos_e_ativa_volta_ao_historico(tmp_path: Path, inst: Instalador) -> None:
    inst.instalar(pacote_zip(tmp_path, "a.zip", "1.0.0"))
    inst.instalar(pacote_zip(tmp_path, "b.zip", "1.1.0", pesos=PESOS_PT + b"2"))
    inst.ativar("br.teste.bebe", "1.0.0")
    inst.ativar("br.teste.bebe", "1.1.0")
    import shutil
    shutil.rmtree(inst.pacotes / "br.teste.bebe" / "1.1.0")
    acoes = inst.recuperar()
    esp = inst.listar()["br.teste.bebe"]
    assert esp["versoes"]["1.1.0"]["estado"] == "ausente" and esp["ativa"] == "1.0.0"
    assert any("ativa agora: 1.0.0" in a for a in acoes)


def test_registro_corrompido_usa_bak_e_depois_reconstroi(tmp_path: Path, inst: Instalador) -> None:
    inst.instalar(pacote_zip(tmp_path, "a.zip", "1.0.0"))
    inst.instalar(pacote_zip(tmp_path, "b.zip", "1.1.0", pesos=PESOS_PT + b"2"))     # gera o .bak com a 1.0.0
    inst.arq_registro.write_text("{ ilegivel", encoding="utf-8")
    assert "1.0.0" in inst.listar()["br.teste.bebe"]["versoes"]                    # .bak
    inst.arq_bak.write_text("tambem ilegivel", encoding="utf-8")
    with pytest.raises(ErroInstalacao) as e:
        inst.listar()
    assert e.value.slug == "registro_corrompido"
    acoes = inst.recuperar()
    assert any("reconstruido" in a for a in acoes)
    esp = inst.listar()["br.teste.bebe"]
    assert set(esp["versoes"]) == {"1.0.0", "1.1.0"} and esp["ativa"] is None


def test_registro_e_gravado_de_forma_atomica(tmp_path: Path, inst: Instalador, monkeypatch) -> None:
    inst.instalar(pacote_zip(tmp_path, "a.zip", "1.0.0"))
    antes = inst.arq_registro.read_text(encoding="utf-8")

    def falha(*a, **k):
        raise OSError("disco cheio simulado")
    monkeypatch.setattr(inst_mod, "_substituir", falha)
    with pytest.raises(ErroInstalacao):
        inst.instalar(pacote_zip(tmp_path, "b.zip", "1.1.0", pesos=PESOS_PT + b"2"))
    monkeypatch.undo()
    assert inst.arq_registro.read_text(encoding="utf-8") == antes                  # intacto
    assert not (inst.pacotes / "br.teste.bebe" / "1.1.0").exists()                # e sem sobra no disco


# --- exclusao mutua -----------------------------------------------------------------------------------------

def test_bloqueio_impede_operacao_concorrente_e_lock_obsoleto_e_ignorado(tmp_path: Path, inst: Instalador) -> None:
    inst.arq_lock.write_text(json.dumps({"pid": os.getpid(), "desde": "x"}), encoding="utf-8")   # dono vivo (este processo)
    with pytest.raises(ErroInstalacao) as e:
        inst.instalar(pacote_zip(tmp_path, "a.zip"))
    assert e.value.codigo == COD_BLOQUEIO
    inst.arq_lock.write_text(json.dumps({"pid": 2**30, "desde": "x"}), encoding="utf-8")        # dono morto
    assert inst.instalar(pacote_zip(tmp_path, "a.zip")).ja_instalado is False
    assert not inst.arq_lock.exists()                                                           # sempre liberado


def test_lock_e_liberado_apos_erro(tmp_path: Path, inst: Instalador) -> None:
    with pytest.raises(ErroInstalacao):
        inst.instalar(escrever_zip(tmp_path / "ruim.zip", manifesto(protocolo=2)))
    assert not inst.arq_lock.exists()


# --- CLI ---------------------------------------------------------------------------------------------------------

def test_cli_ciclo_completo(tmp_path: Path, capsys) -> None:
    raiz = str(tmp_path / "raiz")
    z1, z2 = pacote_zip(tmp_path, "a.zip", "1.0.0"), pacote_zip(tmp_path, "b.zip", "2.0.0", pesos=PESOS_PT + b"2")
    assert main(["instalar", str(z1), "--raiz", raiz]) == 0
    assert main(["instalar", str(z2), "--raiz", raiz]) == 0
    assert main(["ativar", "br.teste.bebe", "1.0.0", "--raiz", raiz]) == 0
    assert main(["ativar", "br.teste.bebe", "2.0.0", "--raiz", raiz]) == 0
    assert main(["reverter", "br.teste.bebe", "--raiz", raiz]) == 0
    assert main(["remover", "br.teste.bebe", "1.0.0", "--raiz", raiz]) == COD_EM_USO
    assert main(["remover", "br.teste.bebe", "2.0.0", "--raiz", raiz]) == 0
    capsys.readouterr()
    assert main(["listar", "--raiz", raiz]) == 0
    assert json.loads(capsys.readouterr().out)["br.teste.bebe"]["ativa"] == "1.0.0"
    assert main(["recuperar", "--raiz", raiz]) == 0


def test_cli_codigos_de_erro(tmp_path: Path) -> None:
    raiz = str(tmp_path / "raiz")
    assert main(["instalar", str(escrever_zip(tmp_path / "r.zip", manifesto(protocolo=2))), "--raiz", raiz]) == COD_REPROVADO
    assert main(["ativar", "br.nao.existe", "1.0.0", "--raiz", raiz]) == COD_ESTADO
    assert main(["reverter", "br.nao.existe", "--raiz", raiz]) == COD_ESTADO
