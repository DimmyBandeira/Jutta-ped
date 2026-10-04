"""Executor local minimo: especialista ativo -> integridade -> carga -> atributos. Backend FALSO e deterministico
(pesos sinteticos): valida o contrato e o ciclo de vida, nao a qualidade de nenhum modelo. Ha um teste de fumaca com
modelo real que pula sozinho se o arquivo local nao existir."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from src.jutta_ped.especialistas.cli import main
from src.jutta_ped.especialistas.executor import (
    E_DESCONHECIDO,
    E_INTEGRIDADE,
    E_MODELO,
    E_PARAMETRO,
    E_PRAZO,
    E_RECORTE,
    E_VERSAO,
    Executor,
    Pedido,
    calcular_config_hash,
)
from src.jutta_ped.especialistas.instalador import Instalador
from test_especialistas_validador import sha

RAIZ = Path(__file__).resolve().parents[1]


# --- backend falso ------------------------------------------------------------------------------------------------

class ModeloFalso:
    """Le a 'saida do modelo' dos primeiros pixels da imagem: (0,0)=(b,g,r), (0,1)=(b2,_,_). Deterministico."""

    def __init__(self, nomes: list[str], tipo: str, dispositivo: str, chamadas: list) -> None:
        self.nomes, self.tipo, self.dispositivo, self._chamadas = nomes, tipo, dispositivo, chamadas

    def inferir(self, imagens: list) -> list[dict]:
        self._chamadas.append(len(imagens))
        saida = []
        for im in imagens:
            b, g, r = (int(v) for v in im[0, 0])
            if self.tipo == "deteccao":
                saida.append({"deteccoes": [("bebe_colo", b / 255)] * g})
            else:
                saida.append({"probs": {"azul": b / 255, "verde": g / 255, "outro": r / 255, "cracha": int(im[0, 1, 0]) / 255}})
        return saida


class BackendFalso:
    def __init__(self, tipo_por_arquivo: dict[str, str] | None = None, nomes: dict[str, list[str]] | None = None, falha_em: set[str] | None = None) -> None:
        self.tipo_por_arquivo, self.nomes, self.falha_em = tipo_por_arquivo or {}, nomes or {}, falha_em or set()
        self.cargas: list[tuple[str, str]] = []
        self.lotes: list[int] = []

    def carregar(self, modelo, pasta, dispositivo, imgsz):
        self.cargas.append((modelo["id"], dispositivo))
        if dispositivo in self.falha_em:
            raise RuntimeError(f"sem {dispositivo}")
        assert (pasta / modelo["arquivo"]).exists()
        tipo = self.tipo_por_arquivo.get(modelo["arquivo"], "deteccao")
        nomes = self.nomes.get(modelo["arquivo"], ["bebe_colo", "outra"] if tipo == "deteccao" else ["azul", "verde", "outro", "cracha"])
        return ModeloFalso(nomes, tipo, dispositivo, self.lotes)


def img(b: int = 0, g: int = 0, r: int = 0, b2: int = 0, altura: int = 200, largura: int = 100) -> np.ndarray:
    a = np.zeros((altura, largura, 3), np.uint8)
    a[0, 0] = (b, g, r)
    a[0, 1, 0] = b2
    return a


def pedido(n: int, recorte=None, **kw) -> Pedido:
    return Pedido(pedido_id=f"p-{n}", recorte=img() if recorte is None else recorte, trilha_id=n, quadro_id=100 + n, **kw)


# --- pacotes de teste (nenhum e pediatrico) --------------------------------------------------------------------

def manifesto_bebe(versao: str = "1.0.0", **extra) -> dict:
    return {
        "protocolo": 1,
        "pacote": {"id": "br.teste.bebe", "versao": versao, "nome": "Bebe", "autor": "Equipe"},
        "entrada": {"fonte": "visivel", "alvo": {"tipo": "recorte", "classe_mestre": "person"}, "altura_minima_px": 160, "lote_max": 3,
                    "formato": {"cor": "RGB", "largura": 640, "altura": 640, "redimensionamento": "letterbox", "normalizacao": "0-1"}},
        "modelos": [{"id": "principal", "arquivo": "modelos/bebe.pt", "formato": "pt", "sha256": "", "dispositivos": ["cuda", "cpu"]}],
        "atributos": [
            {"nome": "bebe_no_colo", "tipo": "tri", "validade_ms": 1500,
             "de": {"modelo": "principal", "tipo": "deteccao", "classes": ["bebe_colo"], "agregacao": "maior_confianca",
                    "limiar_presente": {"$parametro": "limiar_presente"}, "limiar_ausente": 0.15}},
            {"nome": "qtd_bebes", "tipo": "numero", "validade_ms": 1500, "faixa": [0, 3],
             "de": {"modelo": "principal", "tipo": "deteccao", "classes": ["bebe_colo"], "agregacao": "contagem"}},
        ],
        "parametros": [{"nome": "limiar_presente", "tipo": "numero", "padrao": 0.4, "min": 0.2, "max": 0.9, "editavel_por": "receita"}],
        "custo": {"ms_por_recorte": 20, "hardware": "sintetico"},
        **extra,
    }


def manifesto_cor(versao: str = "1.0.0") -> dict:
    return {
        "protocolo": 1,
        "pacote": {"id": "br.teste.uniforme", "versao": versao, "nome": "Uniforme", "autor": "Equipe"},
        "entrada": {"fonte": "visivel", "alvo": {"tipo": "recorte", "classe_mestre": "person"}, "lote_max": 8,
                    "formato": {"cor": "RGB", "largura": 224, "altura": 224, "redimensionamento": "esticar", "normalizacao": "0-1"}},
        "modelos": [{"id": "cor", "arquivo": "modelos/cor.pt", "formato": "pt", "sha256": "", "dispositivos": ["cpu"]}],
        "atributos": [
            {"nome": "cor_uniforme", "tipo": "categoria", "validade_ms": 5000, "categorias": ["azul", "verde", "outro"],
             "de": {"modelo": "cor", "tipo": "classificacao", "agregacao": "classe_mais_provavel"}},
            {"nome": "usa_cracha", "tipo": "tri", "validade_ms": 5000,
             "de": {"modelo": "cor", "tipo": "classificacao", "classe": "cracha", "agregacao": "probabilidade",
                    "limiar_presente": 0.6, "limiar_ausente": 0.2}},
            {"nome": "prob_azul", "tipo": "numero", "validade_ms": 5000, "faixa": [0, 1],
             "de": {"modelo": "cor", "tipo": "classificacao", "classe": "azul", "agregacao": "probabilidade"}},
        ],
    }


def empacotar(tmp: Path, nome: str, m: dict, pesos: bytes, arquivo: str = "modelos/bebe.pt") -> Path:
    m = json.loads(json.dumps(m))
    m["modelos"][0]["arquivo"] = arquivo
    m["modelos"][0]["sha256"] = sha(pesos)
    destino = tmp / nome
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(m))
        z.writestr(arquivo, pesos)
    return destino


@pytest.fixture
def inst(tmp_path: Path) -> Instalador:
    return Instalador(tmp_path / "raiz")


def instalar_ativar(inst: Instalador, tmp: Path, m: dict, pesos: bytes, arquivo: str = "modelos/bebe.pt") -> None:
    r = inst.instalar(empacotar(tmp, f"{m['pacote']['id']}-{m['pacote']['versao']}.zip", m, pesos, arquivo))
    inst.ativar(r.id, r.versao)


def fabrica(inst, backend, cuda: bool = False) -> Executor:
    return Executor(inst, backends={"pt": backend, "openvino": backend}, cuda_probe=lambda: cuda)


# --- resolucao da versao ativa -------------------------------------------------------------------------------------

def test_sem_versao_ativa_ou_so_instalada_responde_desconhecido(tmp_path: Path, inst: Instalador) -> None:
    ex = fabrica(inst, BackendFalso())
    r = ex.executar("br.teste.bebe", [pedido(1)])[0]
    assert r["estado"] == "erro" and r["erro"]["codigo"] == E_DESCONHECIDO
    inst.instalar(empacotar(tmp_path, "a.zip", manifesto_bebe(), b"pesos-a"))            # instalado, mas inativo
    assert ex.executar("br.teste.bebe", [pedido(1)])[0]["erro"]["codigo"] == E_DESCONHECIDO


def test_resultado_segue_o_contrato_do_manual(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    r = fabrica(inst, BackendFalso()).executar("br.teste.bebe", [pedido(7, img(b=230, g=1), altura_pessoa_px=300, restante_ms=400)])[0]
    assert {"pedido_id", "tentativa", "geracao", "trilha_id", "quadro_id", "especialista", "estado", "atributos", "tempos", "erro"} <= set(r)
    assert r["pedido_id"] == "p-7" and r["trilha_id"] == 7 and r["quadro_id"] == 107
    assert r["especialista"] == {"id": "br.teste.bebe", "versao": "1.0.0"} and r["estado"] == "ok" and r["erro"] is None
    assert r["tempos"]["inferencia_ms"] >= 0 and "fila_ms" in r["tempos"]
    json.dumps(r)                                                                     # serializavel (contrato sem estado)


# --- semantica dos atributos (manual 12.3) --------------------------------------------------------------------

def attrs(r: dict) -> dict:
    return {a["nome"]: a for a in r["atributos"]}


def test_tri_sim_nao_e_indeterminado_com_limiares(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    ex = fabrica(inst, BackendFalso())
    sim, nao_baixo, nao_vazio, indet = ex.executar("br.teste.bebe", [
        pedido(1, img(b=230, g=1)),       # 0.90 >= 0.40
        pedido(2, img(b=10, g=1)),        # 0.04 < 0.15
        pedido(3, img(b=0, g=0)),         # nenhuma deteccao
        pedido(4, img(b=77, g=1)),        # 0.30: entre 0.15 e 0.40
    ])
    assert attrs(sim)["bebe_no_colo"]["valor"] == "sim"
    assert attrs(nao_baixo)["bebe_no_colo"]["valor"] == "nao" and attrs(nao_vazio)["bebe_no_colo"]["valor"] == "nao"
    a = attrs(indet)["bebe_no_colo"]
    assert a["estado"] == "indeterminado" and a["motivo"] == "baixa_confianca" and "valor" not in a      # indeterminado != nao


def test_numero_contagem_e_fora_da_faixa(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    ex = fabrica(inst, BackendFalso())
    dois, cinco = ex.executar("br.teste.bebe", [pedido(1, img(b=200, g=2)), pedido(2, img(b=200, g=5))])
    assert attrs(dois)["qtd_bebes"]["valor"] == 2
    q = attrs(cinco)["qtd_bebes"]
    assert q["estado"] == "indeterminado" and q["motivo"] == "fora_da_faixa"       # 5 > faixa [0, 3]


def test_parametros_efetivos_alteram_o_limiar_e_sao_validados(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    ex = fabrica(inst, BackendFalso())
    meio = img(b=77, g=1)                                                              # confianca 0.30
    assert attrs(ex.executar("br.teste.bebe", [pedido(1, meio)])[0])["bebe_no_colo"]["estado"] == "indeterminado"
    assert attrs(ex.executar("br.teste.bebe", [pedido(1, meio, parametros={"limiar_presente": 0.25})])[0])["bebe_no_colo"]["valor"] == "sim"
    for ruim in ({"limiar_presente": 0.95}, {"limiar_presente": -1}, {"inexistente": 0.5}, {"limiar_presente": "0.3"}, {"limiar_presente": float("nan")}):
        r = ex.executar("br.teste.bebe", [pedido(1, meio, parametros=ruim)])[0]
        assert r["estado"] == "erro" and r["erro"]["codigo"] == E_PARAMETRO, ruim


def test_limiares_efetivos_invertidos_sao_recusados_pelo_host(tmp_path: Path, inst: Instalador) -> None:
    m = manifesto_bebe()
    m["parametros"][0].update(min=0.05, padrao=0.4)          # permite limiar_presente < limiar_ausente (0.15)
    instalar_ativar(inst, tmp_path, m, b"pesos-a")
    r = fabrica(inst, BackendFalso()).executar("br.teste.bebe", [pedido(1, parametros={"limiar_presente": 0.10})])[0]
    assert r["erro"]["codigo"] == E_PARAMETRO and "limiar_ausente" in r["erro"]["mensagem"]


def test_config_hash_confere_pesos_e_parametros(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    ex = fabrica(inst, BackendFalso())
    hashes = {"principal": sha(b"pesos-a")}
    certo = calcular_config_hash(hashes, {"limiar_presente": 0.4})
    assert ex.executar("br.teste.bebe", [pedido(1, config_hash=certo)])[0]["estado"] == "ok"
    errado = calcular_config_hash(hashes, {"limiar_presente": 0.5})
    assert ex.executar("br.teste.bebe", [pedido(1, config_hash=errado)])[0]["erro"]["codigo"] == E_VERSAO
    com_param = calcular_config_hash(hashes, {"limiar_presente": 0.5})
    assert ex.executar("br.teste.bebe", [pedido(1, parametros={"limiar_presente": 0.5}, config_hash=com_param)])[0]["estado"] == "ok"


# --- pedidos: altura minima, fonte, recorte, prazo, lote --------------------------------------------------------

def test_altura_minima_nao_chama_o_modelo(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    backend = BackendFalso()
    ex = fabrica(inst, backend)
    r = ex.executar("br.teste.bebe", [pedido(1, img(b=230, g=1), altura_pessoa_px=100)])[0]
    assert r["estado"] == "ok" and all(a["estado"] == "indeterminado" and a["motivo"] == "recorte_pequeno" for a in r["atributos"])
    assert backend.lotes == []                                                         # sem inferencia


def test_pedido_invalido_nao_derruba_os_outros(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    ex = fabrica(inst, BackendFalso())
    rs = ex.executar("br.teste.bebe", [
        pedido(1, img(b=230, g=1)),
        pedido(2, None, fonte="termica"),
        pedido(3, np.zeros((0, 0, 3), np.uint8)),
        Pedido(pedido_id="p-4", recorte="nao e imagem"),
        Pedido(pedido_id="p-5", recorte=None),
        pedido(6, img(b=10, g=1)),
    ])
    assert [r["pedido_id"] for r in rs] == ["p-1", "p-2", "p-3", "p-4", "p-5", "p-6"]          # mesma ordem
    assert [r["estado"] for r in rs] == ["ok", "erro", "erro", "erro", "erro", "ok"]
    assert all(r["erro"]["codigo"] == E_RECORTE for r in rs[1:5])


def test_prazo_esgotado_ou_menor_que_o_custo_nao_processa(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    backend = BackendFalso()
    ex = fabrica(inst, backend)
    assert ex.executar("br.teste.bebe", [pedido(1, restante_ms=0)])[0]["erro"]["codigo"] == E_PRAZO
    assert ex.executar("br.teste.bebe", [pedido(1, restante_ms=-5)])[0]["erro"]["codigo"] == E_PRAZO
    assert ex.executar("br.teste.bebe", [pedido(1, restante_ms=5)])[0]["erro"]["codigo"] == E_PRAZO      # custo declarado: 20 ms
    assert backend.lotes == []
    assert ex.executar("br.teste.bebe", [pedido(1, restante_ms=5000)])[0]["estado"] == "ok"
    assert ex.executar("br.teste.bebe", [pedido(1)])[0]["estado"] == "ok"                              # sem prazo declarado


def test_lote_max_divide_o_trabalho_e_preserva_a_ordem(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    backend = BackendFalso()
    rs = fabrica(inst, backend).executar("br.teste.bebe", [pedido(i, img(b=10 * i, g=1)) for i in range(1, 8)])
    assert backend.lotes == [3, 3, 1]
    assert [r["pedido_id"] for r in rs] == [f"p-{i}" for i in range(1, 8)]


# --- carga: integridade, classes, formato, dispositivos -------------------------------------------------------

def test_pacote_adulterado_antes_da_carga_nao_roda(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    arq = inst.pacotes / "br.teste.bebe" / "1.0.0" / "manifest.json"
    m = json.loads(arq.read_text(encoding="utf-8"))
    m["atributos"][0]["validade_ms"] = 999999
    arq.write_text(json.dumps(m), encoding="utf-8")
    backend = BackendFalso()
    r = fabrica(inst, backend).executar("br.teste.bebe", [pedido(1)])[0]
    assert r["erro"]["codigo"] == E_INTEGRIDADE and backend.cargas == []              # falha fechada: nada foi carregado


def test_integridade_e_reconferida_quando_o_executor_recarrega(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    backend = BackendFalso()
    ex = fabrica(inst, backend)
    assert ex.executar("br.teste.bebe", [pedido(1)])[0]["estado"] == "ok"
    (inst.pacotes / "br.teste.bebe" / "1.0.0" / "modelos" / "bebe.pt").write_bytes(b"adulterado")
    assert ex.executar("br.teste.bebe", [pedido(1)])[0]["estado"] == "ok"             # limite documentado: ja carregado
    ex.descarregar("br.teste.bebe")
    assert ex.executar("br.teste.bebe", [pedido(1)])[0]["erro"]["codigo"] == E_INTEGRIDADE


def test_classe_inexistente_no_modelo_e_detectada_na_carga(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    backend = BackendFalso(nomes={"modelos/bebe.pt": ["gato", "cachorro"]})
    r = fabrica(inst, backend).executar("br.teste.bebe", [pedido(1)])[0]
    assert r["erro"]["codigo"] == E_MODELO and "bebe_colo" in r["erro"]["mensagem"]


def test_formato_onnx_ainda_nao_e_executavel(tmp_path: Path, inst: Instalador) -> None:
    m = manifesto_bebe()
    m["modelos"][0]["formato"] = "onnx"
    instalar_ativar(inst, tmp_path, m, b"pesos-onnx", arquivo="modelos/bebe.onnx")
    r = Executor(inst).executar("br.teste.bebe", [pedido(1)])[0]                      # backends padrao
    assert r["erro"]["codigo"] == E_MODELO and "onnx" in r["erro"]["mensagem"]


def test_dispositivo_cai_para_cpu_quando_o_preferido_falha(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    backend = BackendFalso(falha_em={"cuda:0"})
    ex = fabrica(inst, backend, cuda=True)
    assert ex.executar("br.teste.bebe", [pedido(1)])[0]["estado"] == "ok"
    assert backend.cargas == [("principal", "cuda:0"), ("principal", "cpu")]
    assert ex.info("br.teste.bebe")["dispositivos"] == {"principal": "cpu"}


def test_cuda_nao_e_tentada_sem_cuda(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    backend = BackendFalso()
    fabrica(inst, backend, cuda=False).executar("br.teste.bebe", [pedido(1)])
    assert backend.cargas == [("principal", "cpu")]


# --- ciclo de vida: ativar/reverter recarregam sozinhos --------------------------------------------------------

def test_ativar_outra_versao_e_reverter_trocam_o_que_o_executor_usa(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe("1.0.0"), b"pesos-a")
    m2 = manifesto_bebe("1.1.0")
    m2["atributos"][0]["validade_ms"] = 900
    instalar_ativar(inst, tmp_path, m2, b"pesos-b")
    backend = BackendFalso()
    ex = fabrica(inst, backend)
    assert ex.executar("br.teste.bebe", [pedido(1)])[0]["especialista"]["versao"] == "1.1.0"
    assert ex.executar("br.teste.bebe", [pedido(1)])[0]["especialista"]["versao"] == "1.1.0"
    assert len(backend.cargas) == 1                                                    # cache: nao recarrega a cada chamada
    inst.reverter("br.teste.bebe")
    assert ex.executar("br.teste.bebe", [pedido(1)])[0]["especialista"]["versao"] == "1.0.0"
    assert len(backend.cargas) == 2


# --- segundo pacote: a execucao nao depende da pediatria --------------------------------------------------------

def test_segundo_pacote_independente_roda_ao_lado_do_primeiro(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe(), b"pesos-a")
    instalar_ativar(inst, tmp_path, manifesto_cor(), b"pesos-cor", arquivo="modelos/cor.pt")
    backend = BackendFalso(tipo_por_arquivo={"modelos/cor.pt": "classificacao", "modelos/bebe.pt": "deteccao"})
    ex = fabrica(inst, backend)
    cor = ex.executar("br.teste.uniforme", [pedido(1, img(b=20, g=200, r=30, b2=230)), pedido(2, img(b=230, g=10, r=10, b2=20))])
    a1, a2 = attrs(cor[0]), attrs(cor[1])
    assert a1["cor_uniforme"]["valor"] == "verde" and a2["cor_uniforme"]["valor"] == "azul"
    assert a1["usa_cracha"]["valor"] == "sim" and a2["usa_cracha"]["valor"] == "nao"
    assert a2["prob_azul"]["valor"] == pytest.approx(230 / 255, abs=1e-3)
    bebe = ex.executar("br.teste.bebe", [pedido(3, img(b=230, g=1))])[0]               # intercalado, mesmo executor
    assert attrs(bebe)["bebe_no_colo"]["valor"] == "sim" and cor[0]["especialista"]["id"] == "br.teste.uniforme"
    assert set(ex.info("br.teste.uniforme")["atributos"]) == {"cor_uniforme", "usa_cracha", "prob_azul"}


def test_ativar_um_pacote_nao_afeta_o_outro(tmp_path: Path, inst: Instalador) -> None:
    instalar_ativar(inst, tmp_path, manifesto_bebe("1.0.0"), b"pesos-a")
    instalar_ativar(inst, tmp_path, manifesto_cor(), b"pesos-cor", arquivo="modelos/cor.pt")
    backend = BackendFalso(tipo_por_arquivo={"modelos/cor.pt": "classificacao"})
    ex = fabrica(inst, backend)
    ex.executar("br.teste.bebe", [pedido(1)])
    ex.executar("br.teste.uniforme", [pedido(1, img(b=200))])
    cargas = len(backend.cargas)
    instalar_ativar(inst, tmp_path, manifesto_bebe("1.1.0"), b"pesos-b")
    ex.executar("br.teste.uniforme", [pedido(1, img(b=200))])
    assert len(backend.cargas) == cargas                                                # o outro continua em cache
    ex.executar("br.teste.bebe", [pedido(1)])
    assert len(backend.cargas) == cargas + 1


def test_o_executor_nao_contem_nada_da_pediatria() -> None:
    """Analisa o CODIGO (identificadores e literais), ignorando docstrings e comentarios que explicam a ausencia."""
    import ast
    import re

    proibido = re.compile(r"pediatr|crianc|child|adult|CHILD_ALONE|CHILD_SEPARATED", re.IGNORECASE)
    for arq in ("executor.py", "backend_ultralytics.py"):
        arvore = ast.parse((RAIZ / "src" / "jutta_ped" / "especialistas" / arq).read_text(encoding="utf-8"))
        docstrings = {id(n.body[0].value) for n in ast.walk(arvore) if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                      and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        achados = []
        for n in ast.walk(arvore):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
                achados += proibido.findall(n.value)
            elif isinstance(n, ast.Name):
                achados += proibido.findall(n.id)
            elif isinstance(n, ast.Attribute):
                achados += proibido.findall(n.attr)
            elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                achados += proibido.findall(n.name)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                achados += proibido.findall(ast.unparse(n))
        assert not achados, (arq, achados)


# --- CLI ------------------------------------------------------------------------------------------------------

def test_cli_executar_sem_especialista_ativo_e_imagem_invalida(tmp_path: Path, capsys) -> None:
    import cv2

    foto = tmp_path / "f.png"
    cv2.imwrite(str(foto), img(b=100))
    raiz = str(tmp_path / "raiz")
    assert main(["executar", "br.nao.existe", str(foto), "--raiz", raiz]) == 17
    assert json.loads(capsys.readouterr().out)["erro"]["codigo"] == E_DESCONHECIDO
    assert main(["executar", "br.nao.existe", str(tmp_path / "nao_existe.png"), "--raiz", raiz]) == 1


# --- fumaca com modelo real (pula se o arquivo local nao existir) -----------------------------------------------

MODELO_REAL = RAIZ / "src" / "models" / "Bebe-no-colo.pt"
VIDEO = RAIZ / "videos" / "Corredor pediatria.mp4"


@pytest.mark.skipif(not (MODELO_REAL.exists() and VIDEO.exists()), reason="modelo/video locais ausentes")
def test_fumaca_modelo_real_ultralytics(tmp_path: Path, inst: Instalador) -> None:
    import cv2

    pesos = MODELO_REAL.read_bytes()
    m = manifesto_bebe()
    m["entrada"]["formato"].update(largura=960, altura=960)
    m["entrada"]["altura_minima_px"] = 0
    m["atributos"] = [
        {"nome": "bebe_no_colo", "tipo": "tri", "validade_ms": 1500,
         "de": {"modelo": "principal", "tipo": "deteccao", "classes": ["bebe_colo"], "agregacao": "maior_confianca",
                "limiar_presente": 0.4, "limiar_ausente": 0.15}},
        {"nome": "bebe_no_conforto", "tipo": "tri", "validade_ms": 1500,
         "de": {"modelo": "principal", "tipo": "deteccao", "classes": ["bebe_conforto"], "agregacao": "maior_confianca",
                "limiar_presente": 0.4, "limiar_ausente": 0.15}},
        {"nome": "carrinhos", "tipo": "numero", "validade_ms": 1500, "faixa": [0, 10],
         "de": {"modelo": "principal", "tipo": "deteccao", "classes": ["carrinho", "carrinho_perinatal"], "agregacao": "contagem"}},
    ]
    del m["parametros"]
    instalar_ativar(inst, tmp_path, m, pesos)
    cap = cv2.VideoCapture(str(VIDEO))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total > 2:
        cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
    ok, quadro = cap.read()
    cap.release()
    if not ok:
        pytest.skip("o video local nao pode ser lido neste ambiente")
    ex = Executor(inst)                                                                 # backend REAL (Ultralytics)
    r = ex.executar("br.teste.bebe", [Pedido(pedido_id="real-1", recorte=quadro, restante_ms=60000)])[0]
    assert r["estado"] == "ok", r
    assert {a["nome"] for a in r["atributos"]} == {"bebe_no_colo", "bebe_no_conforto", "carrinhos"}
    assert all(a["estado"] in ("definido", "indeterminado") for a in r["atributos"])
    assert r["tempos"]["inferencia_ms"] > 0 and ex.info("br.teste.bebe")["dispositivos"]["principal"] in ("cpu", "cuda:0")
