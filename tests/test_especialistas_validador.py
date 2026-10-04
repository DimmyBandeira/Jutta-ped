"""Conformidade do validador estatico de especialistas. Os 'pesos' sao bytes sinteticos: nao sao modelos executaveis."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

from src.jutta_ped.especialistas.cli import main
from src.jutta_ped.especialistas.fontes import Limites
from src.jutta_ped.especialistas.hashing import hash_pasta
from src.jutta_ped.especialistas.validador import validar

PESOS_PT = b"\x80\x02PESOS-SINTETICOS-PT" * 50
XML = b"<net name='sintetico'/>"
BIN = bytes(range(256)) * 8
METADATA = b"names:\n  0: bebe_colo\n  1: bebe_conforto\n"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def hash_ov(arquivos: dict[str, bytes]) -> str:
    return hash_pasta((n, len(b), (lambda x=b: [x])) for n, b in arquivos.items())


def manifesto(**mudancas) -> dict:
    m = {
        "protocolo": 1,
        "pacote": {"id": "br.teste.bebe", "versao": "1.0.0", "nome": "Bebe", "autor": "Equipe"},
        "entrada": {"fonte": "visivel", "alvo": {"tipo": "recorte", "classe_mestre": "person"}, "altura_minima_px": 160, "lote_max": 8,
                    "formato": {"cor": "RGB", "largura": 640, "altura": 640, "redimensionamento": "letterbox", "normalizacao": "0-1"}},
        "modelos": [{"id": "principal", "arquivo": "modelos/bebe.pt", "formato": "pt", "sha256": sha(PESOS_PT), "dispositivos": ["cuda", "cpu"]}],
        "atributos": [
            {"nome": "bebe_no_colo", "tipo": "tri", "validade_ms": 1500,
             "de": {"modelo": "principal", "tipo": "deteccao", "classes": ["bebe_colo"], "agregacao": "maior_confianca",
                    "limiar_presente": {"$parametro": "limiar_presente"}, "limiar_ausente": 0.15}}],
        "parametros": [{"nome": "limiar_presente", "tipo": "numero", "padrao": 0.4, "min": 0.2, "max": 0.9, "editavel_por": "receita"}],
    }
    m.update(mudancas)
    return m


def escrever_pasta(raiz: Path, m: dict, extras: dict[str, bytes] | None = None, pesos: bool = True) -> Path:
    raiz.mkdir(parents=True, exist_ok=True)
    (raiz / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    if pesos:
        (raiz / "modelos").mkdir(exist_ok=True)
        (raiz / "modelos" / "bebe.pt").write_bytes(PESOS_PT)
    for nome, dados in (extras or {}).items():
        alvo = raiz / nome
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(dados)
    return raiz


def escrever_zip(destino: Path, m: dict | bytes, extras: dict[str, bytes] | None = None, pesos: bool = True) -> Path:
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", m if isinstance(m, bytes) else json.dumps(m))
        if pesos:
            z.writestr("modelos/bebe.pt", PESOS_PT)
        for nome, dados in (extras or {}).items():
            info = zipfile.ZipInfo("x")
            info.filename = nome            # grava o nome cru (o construtor trocaria '\' por '/' no Windows)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, dados)
    return destino


def codigos(rel) -> set[str]:
    return {p.codigo for p in rel.problemas}


def manifesto_ov(**mudancas) -> dict:
    m = manifesto(**mudancas)
    ov = {"bebe_ov/modelo.xml": XML, "bebe_ov/modelo.bin": BIN, "bebe_ov/metadata.yaml": METADATA}
    m["modelos"] = [{"id": "principal", "arquivo": "modelos/bebe_ov", "formato": "openvino",
                     "sha256": hash_ov({k.split("/", 1)[1]: v for k, v in ov.items()}), "dispositivos": ["intel_gpu", "cpu"]}]
    return m


EXTRAS_OV = {"modelos/bebe_ov/modelo.xml": XML, "modelos/bebe_ov/modelo.bin": BIN, "modelos/bebe_ov/metadata.yaml": METADATA}


# --- pacotes validos ---------------------------------------------------------------------------------------

def test_pasta_valida_pt_e_conforme_com_escopo_explicito(tmp_path: Path) -> None:
    rel = validar(escrever_pasta(tmp_path / "p", manifesto()))
    assert rel.conformidade_estatica and rel.codigo_saida == 0
    assert rel.hashes["principal"] == sha(PESOS_PT)
    assert any("pickle" in n for n in rel.nao_verificado)
    assert any("nao foi carregado nem executado" in n for n in rel.nao_verificado)
    assert rel.como_dict()["aviso_escopo"].startswith("Conformidade estatica nao significa")


def test_zip_valido_equivale_a_pasta(tmp_path: Path) -> None:
    a = validar(escrever_pasta(tmp_path / "p", manifesto()))
    b = validar(escrever_zip(tmp_path / "p.zip", manifesto()))
    assert b.conformidade_estatica and a.hashes == b.hashes


def test_openvino_valido_em_pasta_e_zip_com_classes_conferidas(tmp_path: Path) -> None:
    m = manifesto_ov()
    assert validar(escrever_pasta(tmp_path / "p", m, EXTRAS_OV, pesos=False)).conformidade_estatica
    rel = validar(escrever_zip(tmp_path / "p.zip", m, EXTRAS_OV, pesos=False))
    assert rel.conformidade_estatica and not any("classes do modelo" in n for n in rel.nao_verificado)


def test_openvino_classe_inexistente_e_reprovada(tmp_path: Path) -> None:
    m = manifesto_ov()
    m["atributos"][0]["de"]["classes"] = ["classe_que_nao_existe"]
    rel = validar(escrever_pasta(tmp_path / "p", m, EXTRAS_OV, pesos=False))
    assert "classe_inexistente" in codigos(rel) and rel.codigo_saida == 2


def test_openvino_sem_metadata_informa_verificacao_nao_feita(tmp_path: Path) -> None:
    extras = {k: v for k, v in EXTRAS_OV.items() if "metadata" not in k}
    m = manifesto_ov()
    m["modelos"][0]["sha256"] = hash_ov({"modelo.xml": XML, "modelo.bin": BIN})
    rel = validar(escrever_pasta(tmp_path / "p", m, extras, pesos=False))
    assert rel.conformidade_estatica and any("sem metadata.yaml" in n for n in rel.nao_verificado)


# --- exemplos invalidos do manual -------------------------------------------------------------------------

def _mut(f):
    m = manifesto()
    f(m)
    return m


@pytest.mark.parametrize("nome,f,codigo", [
    ("sem_sha256", lambda m: m["modelos"][0].pop("sha256"), "schema"),
    ("atributo_duplicado", lambda m: m["atributos"].append(copy.deepcopy(m["atributos"][0])), "atributo_duplicado"),
    ("limiares_invertidos", lambda m: m["atributos"][0]["de"].update(limiar_presente=0.1, limiar_ausente=0.5), "limiares_invertidos"),
    ("sem_validade", lambda m: m["atributos"][0].pop("validade_ms"), "schema"),
    ("validade_zero", lambda m: m["atributos"][0].update(validade_ms=0), "schema"),
    ("sem_cpu", lambda m: m["modelos"][0].update(dispositivos=["cuda"]), "sem_reserva_cpu"),
    ("modelo_inexistente", lambda m: m["atributos"][0]["de"].update(modelo="outro"), "referencia_modelo"),
    ("parametro_inexistente", lambda m: m["parametros"].clear(), "referencia_parametro"),
    ("combinacao_tri_classificacao_com_classes", lambda m: m["atributos"][0]["de"].update(tipo="classificacao"), "combinacao_invalida"),
    ("categoria_com_deteccao", lambda m: m["atributos"][0].update(tipo="categoria"), "combinacao_invalida"),
    ("campo_extra_na_raiz", lambda m: m.update(inesperado=1), "schema"),
    ("parametro_fora_de_faixa", lambda m: m["parametros"][0].update(padrao=0.95), "parametro_faixa"),
    ("pesos_fora_de_modelos", lambda m: m["modelos"][0].update(arquivo="bebe.pt"), "modelo_fora_de_modelos"),
    ("faixa_numero_incoerente", lambda m: m["atributos"].append({"nome": "n", "tipo": "numero", "validade_ms": 100, "faixa": [0, 5],
                                                                 "de": {"modelo": "principal", "tipo": "deteccao", "classes": ["x"], "agregacao": "maior_confianca"}}), "faixa_invalida"),
])
def test_manifestos_invalidos_do_manual(tmp_path: Path, nome, f, codigo) -> None:
    rel = validar(escrever_pasta(tmp_path / nome, _mut(f)))
    assert codigo in codigos(rel), codigos(rel)
    assert rel.codigo_saida in (2, 3)


def test_caminho_do_modelo_fora_do_pacote(tmp_path: Path) -> None:
    m = manifesto()
    m["modelos"][0]["arquivo"] = "../outro/modelo.pt"
    rel = validar(escrever_pasta(tmp_path / "p", m))
    assert rel.codigo_saida in (2, 3) and not rel.conformidade_estatica


def test_arquivo_py_no_pacote_e_recusado(tmp_path: Path) -> None:
    rel = validar(escrever_pasta(tmp_path / "p", manifesto(), {"instalar.py": b"print(1)"}))
    assert "codigo_executavel" in codigos(rel) and rel.codigo_saida == 3


def test_conteudo_com_assinatura_de_executavel_e_recusado(tmp_path: Path) -> None:
    rel = validar(escrever_pasta(tmp_path / "p", manifesto(), {"LEIAME.md": b"MZ\x90\x00 isto nao e markdown"}))
    assert "executavel_detectado" in codigos(rel)


def test_json_com_chave_duplicada_e_constante_nan(tmp_path: Path) -> None:
    bruto = b'{"protocolo": 1, "protocolo": 1}'
    rel = validar(escrever_zip(tmp_path / "a.zip", bruto, pesos=False))
    assert "manifesto_json_invalido" in codigos(rel)
    rel = validar(escrever_zip(tmp_path / "b.zip", b'{"protocolo": NaN}', pesos=False))
    assert "manifesto_json_invalido" in codigos(rel)


# --- hash, ausencia, protocolo ------------------------------------------------------------------------------

def test_hash_incorreto(tmp_path: Path) -> None:
    m = manifesto()
    m["modelos"][0]["sha256"] = "0" * 64
    rel = validar(escrever_pasta(tmp_path / "p", m))
    assert codigos(rel) == {"hash_divergente"} and rel.codigo_saida == 4


def test_arquivo_de_pesos_ausente(tmp_path: Path) -> None:
    rel = validar(escrever_pasta(tmp_path / "p", manifesto(), pesos=False))
    assert "arquivo_ausente" in codigos(rel) and rel.codigo_saida == 3


def test_manifesto_ausente(tmp_path: Path) -> None:
    (tmp_path / "vazio").mkdir()
    rel = validar(tmp_path / "vazio")
    assert codigos(rel) == {"manifesto_ausente"} and rel.codigo_saida == 3


@pytest.mark.parametrize("protocolo", [0, 2, 99])
def test_protocolo_incompativel_nao_presume_compatibilidade_de_menor(tmp_path: Path, protocolo: int) -> None:
    rel = validar(escrever_pasta(tmp_path / f"p{protocolo}", manifesto(protocolo=protocolo)))
    assert codigos(rel) == {"protocolo_incompativel"} and rel.codigo_saida == 5


def test_protocolo_nao_inteiro_e_manifesto_invalido(tmp_path: Path) -> None:
    rel = validar(escrever_pasta(tmp_path / "p", manifesto(protocolo="1")))
    assert rel.codigo_saida == 2


def test_host_com_outro_protocolo_recusa(tmp_path: Path) -> None:
    rel = validar(escrever_pasta(tmp_path / "p", manifesto()), host_protocolo=2)
    assert rel.codigo_saida == 5


# --- fuga de pasta, links e colisoes ---------------------------------------------------------------------

@pytest.mark.parametrize("nome,codigo", [
    ("../evil.txt", "caminho_traversal"),
    ("a/../../evil.txt", "caminho_traversal"),
    ("/abs/evil.txt", "caminho_absoluto"),
    ("C:/evil.txt", "caminho_absoluto"),
    ("C:evil.txt", "caminho_absoluto"),
    ("sub\\evil.txt", "caminho_barra_invertida"),
    ("con.txt", "caminho_nome_reservado"),
    ("modelos/NUL.json", "caminho_nome_reservado"),
    ("LPT1", "caminho_nome_reservado"),
    ("arquivo.txt.", "caminho_final_invalido"),
    ("arquivo.txt:fluxo", "caminho_caractere_proibido"),
    ("a?.txt", "caminho_caractere_proibido"),
    ("a\x01b.txt", "caminho_caractere_controle"),
])
def test_zip_com_nomes_perigosos(tmp_path: Path, nome: str, codigo: str) -> None:
    rel = validar(escrever_zip(tmp_path / "p.zip", manifesto(), {nome: b"x"}))
    assert codigo in codigos(rel), codigos(rel)
    assert rel.codigo_saida == 3


def test_zip_com_link_simbolico(tmp_path: Path) -> None:
    destino = tmp_path / "p.zip"
    escrever_zip(destino, manifesto())
    with zipfile.ZipFile(destino, "a") as z:
        info = zipfile.ZipInfo("atalho.txt")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "modelos/bebe.pt")
    rel = validar(destino)
    assert "link_simbolico" in codigos(rel) and rel.codigo_saida == 3


def test_pasta_com_link_simbolico_ou_juncao(tmp_path: Path) -> None:
    raiz = escrever_pasta(tmp_path / "p", manifesto())
    alvo = tmp_path / "fora"
    alvo.mkdir()
    try:
        os.symlink(alvo, raiz / "atalho", target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("sem permissao para criar link simbolico neste ambiente")
        r = subprocess.run(["cmd", "/c", "mklink", "/J", str(raiz / "atalho"), str(alvo)], capture_output=True)
        if r.returncode != 0:
            pytest.skip("nao foi possivel criar link simbolico nem juncao neste ambiente")
    rel = validar(raiz)
    assert "link_simbolico" in codigos(rel) and rel.codigo_saida == 3


def test_colisao_de_caminhos_por_caixa_e_entrada_duplicada(tmp_path: Path) -> None:
    rel = validar(escrever_zip(tmp_path / "a.zip", manifesto(), {"LEIAME.md": b"a", "leiame.md": b"b"}))
    assert "colisao_de_caminho" in codigos(rel)
    destino = tmp_path / "c.zip"
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(destino, "w") as z:
            z.writestr("manifest.json", json.dumps(manifesto()))
            z.writestr("modelos/bebe.pt", PESOS_PT)
            z.writestr("LEIAME.md", "um")
            z.writestr("LEIAME.md", "dois")
    assert "entrada_duplicada" in codigos(validar(destino))


# --- limites e ZIP invalido ------------------------------------------------------------------------------

def test_limite_de_quantidade_de_arquivos(tmp_path: Path) -> None:
    rel = validar(escrever_pasta(tmp_path / "p", manifesto()), limites=Limites(max_arquivos=1))
    assert "limite_arquivos" in codigos(rel) and rel.codigo_saida == 6


def test_limite_de_tamanho_total_e_por_arquivo(tmp_path: Path) -> None:
    p = escrever_pasta(tmp_path / "p", manifesto())
    assert validar(p, limites=Limites(max_total_bytes=100)).codigo_saida == 6
    assert "limite_arquivo" in codigos(validar(p, limites=Limites(max_arquivo_bytes=100)))


def test_limite_de_profundidade(tmp_path: Path) -> None:
    rel = validar(escrever_pasta(tmp_path / "p", manifesto(), {"a/b/c/d/e.txt": b"x"}), limites=Limites(max_profundidade=3))
    assert "caminho_profundo" in codigos(rel)


def test_razao_de_compressao_suspeita(tmp_path: Path) -> None:
    rel = validar(escrever_zip(tmp_path / "p.zip", manifesto(), {"amostras/lixo.txt": b"\0" * 5_000_000}))
    assert "razao_compressao" in codigos(rel) and rel.codigo_saida == 6


def test_manifesto_maior_que_o_limite(tmp_path: Path) -> None:
    rel = validar(escrever_pasta(tmp_path / "p", manifesto()), limites=Limites(max_manifesto_bytes=50))
    assert "limite_leitura" in codigos(rel) and rel.codigo_saida == 6


def test_zip_invalido_e_zip_criptografado(tmp_path: Path) -> None:
    ruim = tmp_path / "ruim.zip"
    ruim.write_bytes(b"isto nao e um zip")
    rel = validar(ruim)
    assert codigos(rel) == {"zip_invalido"} and rel.codigo_saida == 6


def test_caminho_inexistente_levanta_erro_de_uso(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        validar(tmp_path / "nao_existe")
    assert main(["validar", str(tmp_path / "nao_existe")]) == 1


# --- hash de pasta ------------------------------------------------------------------------------------------

def test_hash_de_pasta_independe_da_ordem_das_entradas(tmp_path: Path) -> None:
    arqs = {"a.xml": b"1", "b.bin": b"22", "sub/c.yaml": b"333"}
    h1 = hash_ov(arqs)
    h2 = hash_ov(dict(reversed(list(arqs.items()))))
    assert h1 == h2
    p1, p2 = tmp_path / "p1", tmp_path / "p2"
    for p, ordem in ((p1, list(arqs)), (p2, list(reversed(list(arqs))))):
        for n in ordem:
            (p / n).parent.mkdir(parents=True, exist_ok=True)
            (p / n).write_bytes(arqs[n])
    from src.jutta_ped.especialistas.fontes import PastaFonte
    def h(p):
        f = PastaFonte(p)
        return hash_pasta((e.caminho, e.tamanho, (lambda c=e.caminho, f=f: f.blocos(c))) for e in f.listar(Limites()).entradas)
    assert h(p1) == h(p2) == h1


def test_hash_de_pasta_delimita_campos_e_depende_do_nome_e_do_conteudo() -> None:
    assert hash_ov({"ab": b"c"}) != hash_ov({"a": b"bc"})
    assert hash_ov({"a": b"x"}) != hash_ov({"b": b"x"})
    assert hash_ov({"a": b"x"}) != hash_ov({"a": b"y"})
    assert hash_ov({"a": b"x", "b": b"y"}) != hash_ov({"a": b"y", "b": b"x"})


def test_zip_com_entradas_em_outra_ordem_tem_o_mesmo_hash_openvino(tmp_path: Path) -> None:
    m = manifesto_ov()
    a = escrever_zip(tmp_path / "a.zip", m, EXTRAS_OV, pesos=False)
    b = escrever_zip(tmp_path / "b.zip", m, dict(reversed(list(EXTRAS_OV.items()))), pesos=False)
    assert validar(a).hashes == validar(b).hashes and validar(a).conformidade_estatica and validar(b).conformidade_estatica


# --- amostras ------------------------------------------------------------------------------------------------

def test_amostra_sem_imagem_e_atributo_desconhecido(tmp_path: Path) -> None:
    esperado = json.dumps({"atributos": [{"nome": "inexistente", "estado": "definido", "valor": "sim"}]}).encode()
    rel = validar(escrever_pasta(tmp_path / "p", manifesto(), {"amostras/001.esperado.json": esperado}))
    assert {"amostra_sem_imagem", "amostra_atributo_desconhecido"} <= codigos(rel)


def test_amostra_valida(tmp_path: Path) -> None:
    esperado = json.dumps({"atributos": [{"nome": "bebe_no_colo", "estado": "definido", "valor": "sim"}]}).encode()
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 20
    rel = validar(escrever_pasta(tmp_path / "p", manifesto(), {"amostras/001.esperado.json": esperado, "amostras/001.png": png}))
    assert rel.conformidade_estatica


# --- CLI: saida JSON, texto e codigos ---------------------------------------------------------------------

def test_cli_json_conforme(tmp_path: Path, capsys) -> None:
    p = escrever_pasta(tmp_path / "p", manifesto())
    assert main(["validar", str(p), "--formato", "json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["conformidade_estatica"] is True and d["codigo_saida"] == 0 and d["problemas"] == []
    assert d["pacote"]["id"] == "br.teste.bebe" and d["hashes_calculados"]["principal"] == sha(PESOS_PT)
    assert d["verificacoes_nao_realizadas"] and "aviso_escopo" in d


def test_cli_texto_declara_escopo_estatico(tmp_path: Path, capsys) -> None:
    p = escrever_pasta(tmp_path / "p", manifesto())
    assert main(["validar", str(p)]) == 0
    saida = capsys.readouterr().out
    assert "CONFORMIDADE ESTATICA" in saida and "NAO aprova o modelo para execucao" in saida


@pytest.mark.parametrize("construir,esperado", [
    (lambda t: escrever_pasta(t / "a", _mut(lambda m: m["modelos"][0].update(sha256="0" * 64))), 4),
    (lambda t: escrever_pasta(t / "b", manifesto(protocolo=2)), 5),
    (lambda t: escrever_pasta(t / "c", _mut(lambda m: m["modelos"][0].update(dispositivos=["cuda"]))), 2),
    (lambda t: escrever_pasta(t / "d", manifesto(), pesos=False), 3),
])
def test_cli_codigos_de_saida(tmp_path: Path, construir, esperado: int) -> None:
    assert main(["validar", str(construir(tmp_path)), "--formato", "json"]) == esperado


def test_precedencia_com_varias_categorias_de_erro(tmp_path: Path, capsys) -> None:
    """Hash errado + arquivo proibido + dispositivo sem cpu: o codigo e o da categoria de maior precedencia (3)."""
    m = _mut(lambda m: (m["modelos"][0].update(sha256="0" * 64, dispositivos=["cuda"])))
    p = escrever_pasta(tmp_path / "p", m, {"x.py": b"1"})
    assert main(["validar", str(p), "--formato", "json"]) == 3
    d = json.loads(capsys.readouterr().out)
    assert {x["categoria"] for x in d["problemas"]} >= {"arquivos"}
    # com 6 presente, 6 vence
    assert main(["validar", str(p), "--formato", "json"]) == 3
    rel = validar(p, limites=Limites(max_arquivos=1))
    assert rel.codigo_saida == 6


def test_saida_e_deterministica(tmp_path: Path, capsys) -> None:
    m = _mut(lambda m: (m["modelos"][0].update(sha256="0" * 64), m["atributos"].append(copy.deepcopy(m["atributos"][0]))))
    p = escrever_pasta(tmp_path / "p", m, {"b.py": b"1", "a.py": b"2"})
    main(["validar", str(p), "--formato", "json"])
    primeira = capsys.readouterr().out
    main(["validar", str(p), "--formato", "json"])
    assert primeira == capsys.readouterr().out


def test_cli_hash_de_arquivo_e_pasta(tmp_path: Path, capsys) -> None:
    f = tmp_path / "w.pt"
    f.write_bytes(PESOS_PT)
    assert main(["hash", str(f)]) == 0
    assert capsys.readouterr().out.strip() == sha(PESOS_PT)
    pasta = tmp_path / "ov"
    pasta.mkdir()
    (pasta / "modelo.xml").write_bytes(XML)
    (pasta / "modelo.bin").write_bytes(BIN)
    assert main(["hash", str(pasta)]) == 0
    assert capsys.readouterr().out.strip() == hash_ov({"modelo.xml": XML, "modelo.bin": BIN})


def test_molde_e_reprovado_ate_ser_preenchido(capsys) -> None:
    molde = Path(__file__).resolve().parents[1] / "especialistas_molde"
    assert main(["validar", str(molde)]) == 2
    assert "sha256" in capsys.readouterr().out


def test_schema_e_independente_de_plugin_schemas() -> None:
    raiz = Path(__file__).resolve().parents[1]
    assert (raiz / "src/jutta_ped/especialistas/esquemas/manifest.v1.schema.json").is_file()
    assert not (raiz / "plugin/schemas/manifest.v1.schema.json").exists()


def test_exemplo_do_manual_valida_contra_o_schema() -> None:
    """Evita que o manual e o schema divirjam: o exemplo completo da secao 3.6 deve ser aceito pelo schema."""
    import re

    from jsonschema import Draft202012Validator

    from src.jutta_ped.especialistas.validador import carregar_schema

    texto = (Path(__file__).resolve().parents[1] / "docs" / "manual_especialista.md").read_text(encoding="utf-8")
    exemplos = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", texto, re.S)]
    completo = next(e for e in exemplos if e.get("pacote", {}).get("id") == "br.jutta.bebe-no-colo")
    assert not list(Draft202012Validator(carregar_schema()).iter_errors(completo))


# --- regressoes da revisao: diretorios de ZIP, numeros fora da capacidade, padroes dos parametros -------------

def _zip_cru(destino: Path, nomes: list[tuple[str, bytes]], m: dict | None = None) -> Path:
    with zipfile.ZipFile(destino, "w") as z:
        z.writestr("manifest.json", json.dumps(m or manifesto()))
        z.writestr("modelos/bebe.pt", PESOS_PT)
        for nome, dados in nomes:
            info = zipfile.ZipInfo("x")
            info.filename = nome
            z.writestr(info, dados)
    return destino


@pytest.mark.parametrize("nome,codigo", [
    ("../escape/", "caminho_traversal"),
    ("a/../../escape/", "caminho_traversal"),
    ("/absoluto/", "caminho_absoluto"),
    ("C:/unidade/", "caminho_absoluto"),
    ("con/", "caminho_nome_reservado"),
    ("pasta./", "caminho_final_invalido"),
    ("fluxo:alt/", "caminho_caractere_proibido"),
    ("barra\\invertida/", "caminho_barra_invertida"),
    ("duplo//", "caminho_traversal"),
])
def test_entradas_de_diretorio_do_zip_tambem_sao_validadas(tmp_path: Path, nome: str, codigo: str) -> None:
    rel = validar(_zip_cru(tmp_path / "p.zip", [(nome, b"")]))
    assert codigo in codigos(rel), codigos(rel)
    assert rel.codigo_saida == 3 and not rel.conformidade_estatica


def test_diretorio_comum_do_zip_continua_aceito(tmp_path: Path) -> None:
    assert validar(_zip_cru(tmp_path / "p.zip", [("amostras/", b""), ("modelos/", b"")])).conformidade_estatica


def test_colisao_entre_arquivo_e_pasta(tmp_path: Path) -> None:
    rel = validar(_zip_cru(tmp_path / "a.zip", [("LEIAME.md", b"a"), ("LEIAME.md/sub.txt", b"b")]))
    assert "colisao_arquivo_pasta" in codigos(rel) and rel.codigo_saida == 3
    # pasta explicita com o mesmo nome do arquivo, e diferenca so de caixa
    rel = validar(_zip_cru(tmp_path / "b.zip", [("notas.txt", b"a"), ("Notas.TXT/", b"")]))
    assert "colisao_arquivo_pasta" in codigos(rel)
    # o proprio arquivo de pesos nao pode virar pasta
    rel = validar(_zip_cru(tmp_path / "c.zip", [("modelos/bebe.pt/extra.txt", b"x")]))
    assert "colisao_arquivo_pasta" in codigos(rel)


def test_pasta_em_disco_com_nome_reservado_ou_colidindo(tmp_path: Path) -> None:
    raiz = escrever_pasta(tmp_path / "p", manifesto())
    try:
        (raiz / "aux").mkdir()          # nome reservado no Windows; no Windows o proprio mkdir pode falhar
    except OSError:
        pytest.skip("o sistema recusa criar a pasta reservada")
    (raiz / "aux" / "x.txt").write_bytes(b"x")
    rel = validar(raiz)
    assert "caminho_nome_reservado" in codigos(rel)


@pytest.mark.parametrize("literal", ["1e400", "-1e400", "1E999"])
def test_numero_fora_da_capacidade_nao_derruba_o_validador(tmp_path: Path, capsys, literal: str) -> None:
    m = manifesto()
    m["atributos"].append({"nome": "contagem", "tipo": "numero", "validade_ms": 100, "faixa": [0, 1],
                           "de": {"modelo": "principal", "tipo": "deteccao", "classes": ["x"], "agregacao": "contagem"}})
    bruto = json.dumps(m).replace('"faixa": [0, 1]', f'"faixa": [0, {literal}]').encode()
    assert literal in bruto.decode()
    destino = tmp_path / "p.zip"
    with zipfile.ZipFile(destino, "w") as z:
        z.writestr("manifest.json", bruto)
        z.writestr("modelos/bebe.pt", PESOS_PT)
    rel = validar(destino)
    assert "manifesto_json_invalido" in codigos(rel) and rel.codigo_saida == 2
    assert main(["validar", str(destino), "--formato", "json"]) == 2
    assert json.loads(capsys.readouterr().out)["codigo_saida"] == 2


def test_numero_inteiro_gigante_e_recusado_sem_excecao(tmp_path: Path) -> None:
    import random

    rng = random.Random(7)
    digitos = "".join(rng.choice("123456789") for _ in range(5000))   # nao comprimivel (evita o limite de razao)
    bruto = ('{"protocolo": 1, "x": ' + digitos + "}").encode()
    rel = validar(escrever_zip(tmp_path / "p.zip", bruto, pesos=False))
    assert rel.codigo_saida == 2


def test_falha_interna_gera_relatorio_controlado(tmp_path: Path, monkeypatch) -> None:
    import src.jutta_ped.especialistas.validador as v

    def explode(*a, **k):
        raise RuntimeError("bug simulado")
    monkeypatch.setattr(v, "_checar_semantica", explode)
    rel = validar(escrever_pasta(tmp_path / "p", manifesto()))
    assert "falha_interna_validador" in codigos(rel) and rel.codigo_saida == 2


def _com_limiares(presente, ausente, params) -> dict:
    m = manifesto()
    m["parametros"] = params
    m["atributos"][0]["de"].update(limiar_presente=presente, limiar_ausente=ausente)
    return m


def _param(nome, padrao, lo=0.1, hi=0.9):
    return {"nome": nome, "tipo": "numero", "padrao": padrao, "min": lo, "max": hi, "editavel_por": "receita"}


def test_padroes_invertidos_de_parametros_sao_recusados(tmp_path: Path) -> None:
    m = _com_limiares({"$parametro": "pres"}, {"$parametro": "aus"}, [_param("pres", 0.4), _param("aus", 0.8)])
    rel = validar(escrever_pasta(tmp_path / "a", m))
    assert "limiares_invertidos" in codigos(rel) and rel.codigo_saida == 2
    # um literal e um parametro
    m = _com_limiares(0.4, {"$parametro": "aus"}, [_param("aus", 0.8)])
    assert "limiares_invertidos" in codigos(validar(escrever_pasta(tmp_path / "b", m)))
    m = _com_limiares({"$parametro": "pres"}, 0.9, [_param("pres", 0.4)])
    assert "limiares_invertidos" in codigos(validar(escrever_pasta(tmp_path / "c", m)))


def test_padroes_coerentes_aceitos_e_faixas_sobrepostas_geram_aviso(tmp_path: Path) -> None:
    m = _com_limiares({"$parametro": "pres"}, {"$parametro": "aus"}, [_param("pres", 0.6), _param("aus", 0.2)])
    rel = validar(escrever_pasta(tmp_path / "a", m))
    assert rel.conformidade_estatica
    assert any("faixas dos parametros permitem" in a for a in rel.avisos)   # min(pres)=0.1 < max(aus)=0.9
    m = _com_limiares({"$parametro": "pres"}, {"$parametro": "aus"}, [_param("pres", 0.8, 0.6, 0.9), _param("aus", 0.2, 0.1, 0.5)])
    rel = validar(escrever_pasta(tmp_path / "b", m))
    assert rel.conformidade_estatica and not rel.avisos     # faixas disjuntas: nao ha como violar
    m = _com_limiares({"$parametro": "mesmo"}, {"$parametro": "mesmo"}, [_param("mesmo", 0.5)])
    assert validar(escrever_pasta(tmp_path / "c", m)).conformidade_estatica


def _zip_com_modo(destino: Path, nome: str, modo: int, dados: bytes = b"") -> Path:
    with zipfile.ZipFile(destino, "w") as z:
        z.writestr("manifest.json", json.dumps(manifesto()))
        z.writestr("modelos/bebe.pt", PESOS_PT)
        info = zipfile.ZipInfo("x")
        info.filename = nome
        info.create_system = 3
        info.external_attr = modo << 16
        z.writestr(info, dados)
    return destino


def test_entrada_de_pasta_marcada_como_link_simbolico_e_recusada(tmp_path: Path) -> None:
    rel = validar(_zip_com_modo(tmp_path / "a.zip", "atalho/", stat.S_IFLNK | 0o777, b"modelos"))
    assert "link_simbolico" in codigos(rel) and rel.codigo_saida == 3


@pytest.mark.parametrize("nome,modo", [
    ("pipe/", stat.S_IFIFO | 0o644),
    ("dispositivo/", stat.S_IFCHR | 0o644),
    ("soquete", stat.S_IFSOCK | 0o644),
    ("arquivo_que_diz_ser_pasta", stat.S_IFDIR | 0o755),
    ("pasta_que_diz_ser_arquivo/", stat.S_IFREG | 0o644),
])
def test_tipos_especiais_sao_recusados_em_arquivos_e_pastas(tmp_path: Path, nome: str, modo: int) -> None:
    rel = validar(_zip_com_modo(tmp_path / "a.zip", nome, modo))
    assert "tipo_especial" in codigos(rel) and rel.codigo_saida == 3


def test_pasta_e_arquivo_comuns_com_metadados_unix_continuam_aceitos(tmp_path: Path) -> None:
    destino = tmp_path / "a.zip"
    with zipfile.ZipFile(destino, "w") as z:
        z.writestr("manifest.json", json.dumps(manifesto()))
        z.writestr("modelos/bebe.pt", PESOS_PT)
        for nome, modo in (("amostras/", stat.S_IFDIR | 0o755), ("LEIAME.md", stat.S_IFREG | 0o644)):
            info = zipfile.ZipInfo("x")
            info.filename = nome
            info.create_system = 3
            info.external_attr = modo << 16
            z.writestr(info, b"" if nome.endswith("/") else b"# ok")
    assert validar(destino).conformidade_estatica
