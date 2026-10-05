"""Validador ESTATICO de pacotes de especialista declarativo (protocolo 1).

Escopo: estrutura, semantica do manifesto, arquivos e hashes. NAO carrega pesos, NAO importa nem executa nada
do pacote, NAO faz inferencia. "Conformidade estatica" significa que o pacote respeita o contrato; nao significa
que o modelo funciona nem que foi aprovado para execucao.

Codigos de saida (docs/manual_especialista.md, secao 7 e 12):
    0 conforme · 1 erro de uso/E-S · 2 manifesto · 3 arquivos · 4 hash · 5 incompatibilidade · 6 limites/ZIP
Com varias categorias de erro, o codigo e o da primeira presente em PRECEDENCIA (6, 3, 5, 2, 4): cada uma
impede a etapa seguinte de ser confiavel (sem ler o pacote nao ha manifesto; sem entender o protocolo nao ha
schema; sem manifesto valido nao ha hashes esperados).
"""
from __future__ import annotations

import json
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from src.jutta_ped.especialistas.fontes import (
    ErroPacote,
    Fonte,
    Limites,
    PastaFonte,
    Problema,
    ZipFonte,
    verificar_entradas,
)
from src.jutta_ped.especialistas.hashing import ALGORITMO_PASTA, hash_pasta, sha256_blocos

PROTOCOLOS_SUPORTADOS = frozenset({1})
PRECEDENCIA = (6, 3, 5, 2, 4)
NOMES_CATEGORIA = {2: "manifesto", 3: "arquivos", 4: "hash", 5: "incompatibilidade", 6: "limites"}

# Combinacoes validas (tipo do atributo, de.tipo) -> regras. Qualquer outra e `combinacao_invalida`.
#   exige/proibe: campos de `de` ou do atributo. `faixa`: restricao sobre a faixa do atributo.
COMBINACOES: dict[tuple[str, str], dict[str, Any]] = {
    ("tri", "deteccao"): {"agregacao": {"maior_confianca"}, "exige": {"classes", "limiar_presente", "limiar_ausente"}, "proibe": {"classe", "faixa", "categorias"}},
    ("tri", "classificacao"): {"agregacao": {"probabilidade"}, "exige": {"classe", "limiar_presente", "limiar_ausente"}, "proibe": {"classes", "faixa", "categorias"}},
    ("numero", "deteccao"): {"agregacao": {"contagem", "maior_confianca"}, "exige": {"classes", "faixa"}, "proibe": {"classe", "categorias", "limiar_presente", "limiar_ausente"}},
    ("numero", "classificacao"): {"agregacao": {"probabilidade"}, "exige": {"classe", "faixa"}, "proibe": {"classes", "categorias", "limiar_presente", "limiar_ausente"}},
    ("categoria", "classificacao"): {"agregacao": {"classe_mais_provavel"}, "exige": {"categorias"}, "proibe": {"classe", "classes", "faixa", "limiar_presente", "limiar_ausente"}},
}

NAO_VERIFICADO_PT = "classes internas e conteudo dos pesos .pt nao inspecionados (formato pickle; carregar exigiria executar codigo)"
NAO_VERIFICADO_ONNX = "classes internas do modelo .onnx nao inspecionadas estaticamente"
NAO_VERIFICADO_COMUM = (
    "o modelo nao foi carregado nem executado: funcionamento, dispositivos reais, custo (ms/recorte) e precisao nao verificados",
    "autoria do pacote nao verificada (SHA-256 mostra integridade, nao quem o produziu)",
    "amostras/*.esperado.json nao foram reproduzidas (nao ha executor nesta versao)",
)


@dataclass
class Relatorio:
    origem: str
    host_protocolo: int
    problemas: list[Problema] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    nao_verificado: list[str] = field(default_factory=list)
    pacote: dict[str, Any] = field(default_factory=dict)
    hashes: dict[str, str] = field(default_factory=dict)
    limites: dict[str, int] = field(default_factory=dict)

    @property
    def conformidade_estatica(self) -> bool:
        return not self.problemas

    @property
    def codigo_saida(self) -> int:
        categorias = {p.categoria for p in self.problemas}
        for c in PRECEDENCIA:
            if c in categorias:
                return c
        return 0

    def adicionar(self, categoria: int, codigo: str, caminho: str, mensagem: str) -> None:
        self.problemas.append(Problema(categoria, codigo, caminho, mensagem))

    def ordenado(self) -> list[Problema]:
        ordem = {c: i for i, c in enumerate(PRECEDENCIA)}
        return sorted(self.problemas, key=lambda p: (ordem[p.categoria], p.caminho, p.codigo, p.mensagem))

    def como_dict(self) -> dict[str, Any]:
        return {
            "conformidade_estatica": self.conformidade_estatica,
            "codigo_saida": self.codigo_saida,
            "aviso_escopo": "Conformidade estatica nao significa modelo aprovado para execucao.",
            "origem": self.origem,
            "host_protocolo": self.host_protocolo,
            "pacote": self.pacote,
            "hashes_calculados": self.hashes,
            "algoritmo_hash_pasta": ALGORITMO_PASTA,
            "problemas": [
                {"categoria": NOMES_CATEGORIA[p.categoria], "codigo": p.codigo, "caminho": p.caminho, "mensagem": p.mensagem}
                for p in self.ordenado()
            ],
            "avisos": sorted(self.avisos),
            "verificacoes_nao_realizadas": sorted(set(self.nao_verificado)),
            "limites": self.limites,
        }


def carregar_schema() -> dict[str, Any]:
    texto = resources.files("src.jutta_ped.especialistas.esquemas").joinpath("manifest.v1.schema.json").read_text(encoding="utf-8")
    return json.loads(texto)


def _sem_chaves_duplicadas(pares: list[tuple[str, Any]]) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for k, v in pares:
        if k in d:
            raise ValueError(f"chave duplicada no JSON: '{k}'")
        d[k] = v
    return d


def _rejeitar_constante(nome: str) -> Any:
    raise ValueError(f"constante nao permitida no JSON: {nome}")


def _float_finito(texto: str) -> float:
    """`1e400` vira infinito em silencio no json do Python; recusa antes da validacao semantica."""
    v = float(texto)
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"numero fora da capacidade de ponto flutuante: {texto}")
    return v


def _abrir_fonte(caminho: Path) -> Fonte:
    if caminho.is_dir():
        return PastaFonte(caminho)
    if caminho.is_file():
        return ZipFonte(caminho)
    raise FileNotFoundError(str(caminho))


def validar(caminho: str | Path, *, host_protocolo: int = 1, limites: Limites | None = None) -> Relatorio:
    """Valida um pacote (pasta ou .zip). Levanta FileNotFoundError/OSError so para erro de uso (codigo 1)."""
    lim = limites or Limites()
    caminho = Path(caminho)
    rel = Relatorio(origem=str(caminho), host_protocolo=host_protocolo, limites=lim.como_dict())
    rel.nao_verificado.extend(NAO_VERIFICADO_COMUM)
    try:
        fonte = _abrir_fonte(caminho)
    except ErroPacote as exc:
        rel.adicionar(exc.categoria, exc.codigo, exc.caminho, exc.mensagem)
        return rel
    with fonte:
        try:
            _validar_fonte(fonte, rel, lim)
        except ErroPacote as exc:
            rel.adicionar(exc.categoria, exc.codigo, exc.caminho, exc.mensagem)
        except Exception as exc:  # noqa: BLE001 -- defesa em profundidade: o validador nunca deve sair sem relatorio
            rel.adicionar(2, "falha_interna_validador", "", f"falha do VALIDADOR (nao necessariamente do pacote): {type(exc).__name__}: {exc}")
    return rel


def _validar_fonte(fonte: Fonte, rel: Relatorio, lim: Limites) -> None:
    listagem = fonte.listar(lim)
    rel.problemas.extend(listagem.problemas)
    rel.problemas.extend(verificar_entradas(listagem.entradas, lim, listagem.diretorios))
    caminhos = {e.caminho for e in listagem.entradas}
    tamanhos = {e.caminho: e.tamanho for e in listagem.entradas}
    if any(p.categoria == 6 for p in rel.problemas):
        return   # limites/ZIP inseguro: nao le conteudo

    if "manifest.json" not in caminhos:
        rel.adicionar(3, "manifesto_ausente", "manifest.json", "manifest.json nao encontrado na raiz do pacote")
        return
    manifesto = _ler_manifesto(fonte, rel, lim)
    if manifesto is None:
        return
    if _checar_protocolo(manifesto, rel):
        return
    if not _checar_schema(manifesto, rel):
        return
    pacote = manifesto["pacote"]
    rel.pacote = {"id": pacote["id"], "versao": pacote["versao"], "nome": pacote["nome"]}
    _checar_semantica(manifesto, rel)
    _checar_arquivos_e_hashes(manifesto, fonte, caminhos, tamanhos, rel)
    _checar_amostras(manifesto, fonte, caminhos, rel, lim)
    _checar_conteudo_suspeito(fonte, caminhos, rel)


def _ler_manifesto(fonte: Fonte, rel: Relatorio, lim: Limites) -> dict[str, Any] | None:
    try:
        bruto = fonte.ler_limitado("manifest.json", lim.max_manifesto_bytes)
    except ErroPacote as exc:
        rel.adicionar(exc.categoria, exc.codigo, exc.caminho, exc.mensagem)
        return None
    except (zipfile.BadZipFile, OSError) as exc:
        rel.adicionar(6, "zip_invalido", "manifest.json", f"falha ao ler: {exc}")
        return None
    try:
        dados = json.loads(bruto.decode("utf-8"), object_pairs_hook=_sem_chaves_duplicadas, parse_constant=_rejeitar_constante,
                           parse_float=_float_finito)
    except (UnicodeDecodeError, ValueError) as exc:
        rel.adicionar(2, "manifesto_json_invalido", "manifest.json", f"JSON invalido: {exc}")
        return None
    if not isinstance(dados, dict):
        rel.adicionar(2, "manifesto_nao_objeto", "manifest.json", "a raiz do manifesto deve ser um objeto JSON")
        return None
    return dados


def _checar_protocolo(m: dict[str, Any], rel: Relatorio) -> bool:
    """True quando o protocolo impede continuar. Politica: igualdade exata com um protocolo suportado."""
    p = m.get("protocolo")
    if isinstance(p, bool) or not isinstance(p, int):
        rel.adicionar(2, "protocolo_ausente_ou_invalido", "protocolo", "campo 'protocolo' deve ser um inteiro")
        return True
    if p not in PROTOCOLOS_SUPORTADOS or p != rel.host_protocolo:
        rel.adicionar(5, "protocolo_incompativel", "protocolo",
                      f"pacote usa protocolo {p}; este host suporta {sorted(PROTOCOLOS_SUPORTADOS)} e foi configurado para {rel.host_protocolo}. "
                      "Protocolo menor NAO e presumido compativel.")
        return True
    return False


def _checar_schema(m: dict[str, Any], rel: Relatorio) -> bool:
    erros = sorted(Draft202012Validator(carregar_schema()).iter_errors(m), key=lambda e: (list(map(str, e.absolute_path)), e.message))
    for e in erros:
        rel.adicionar(2, "schema", "/".join(map(str, e.absolute_path)) or "(raiz)", e.message[:300])
    return not erros


def _numero(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _faixa_limiar(v: Any, params: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """(padrao, minimo, maximo) de um limiar literal ou ligado a parametro; (None,)*3 se a referencia nao existe."""
    if isinstance(v, dict):
        p = params.get(v["$parametro"])
        return (p["padrao"], p["min"], p["max"]) if p else (None, None, None)
    return v, v, v


def _checar_semantica(m: dict[str, Any], rel: Relatorio) -> None:
    add = lambda cod, cam, msg: rel.adicionar(2, cod, cam, msg)   # noqa: E731
    modelos = {}
    for i, mod in enumerate(m["modelos"]):
        if mod["id"] in modelos:
            add("modelo_id_duplicado", f"modelos/{i}/id", f"id de modelo repetido: {mod['id']}")
        modelos[mod["id"]] = mod
        if "cpu" not in mod["dispositivos"]:
            add("sem_reserva_cpu", f"modelos/{i}/dispositivos", "a lista de dispositivos deve incluir 'cpu' (reserva universal)")
        if not mod["arquivo"].startswith("modelos/"):
            add("modelo_fora_de_modelos", f"modelos/{i}/arquivo", "os pesos devem ficar dentro de 'modelos/'")
        ext = mod["arquivo"].rsplit(".", 1)[-1].lower() if "." in mod["arquivo"].rsplit("/", 1)[-1] else ""
        if mod["formato"] == "pt" and ext != "pt":
            add("formato_extensao", f"modelos/{i}/arquivo", "formato 'pt' exige arquivo .pt")
        if mod["formato"] == "onnx" and ext != "onnx":
            add("formato_extensao", f"modelos/{i}/arquivo", "formato 'onnx' exige arquivo .onnx")
        if mod["formato"] == "openvino" and ext in ("pt", "onnx", "xml", "bin"):
            add("formato_extensao", f"modelos/{i}/arquivo", "formato 'openvino' exige a PASTA do modelo (com .xml e .bin), nao um arquivo")

    params = {}
    for i, p in enumerate(m.get("parametros", [])):
        if p["nome"] in params:
            add("parametro_duplicado", f"parametros/{i}/nome", f"parametro repetido: {p['nome']}")
        params[p["nome"]] = p
        if not (p["min"] <= p["padrao"] <= p["max"]):
            add("parametro_faixa", f"parametros/{i}", "exige min <= padrao <= max")
        if p["min"] >= p["max"]:
            add("parametro_faixa", f"parametros/{i}", "exige min < max")
        if not (0 <= p["min"] and p["max"] <= 1):
            add("parametro_dominio", f"parametros/{i}", "no protocolo 1 todo parametro numero e limiar: min e max devem estar em [0, 1]")

    usados: set[str] = set()
    nomes: set[str] = set()
    for i, a in enumerate(m["atributos"]):
        base = f"atributos/{i}"
        if a["nome"] in nomes:
            add("atributo_duplicado", f"{base}/nome", f"atributo repetido: {a['nome']}")
        nomes.add(a["nome"])
        de = a["de"]
        if de["modelo"] not in modelos:
            add("referencia_modelo", f"{base}/de/modelo", f"modelo '{de['modelo']}' nao declarado em 'modelos'")
        regra = COMBINACOES.get((a["tipo"], de["tipo"]))
        if regra is None:
            add("combinacao_invalida", base, f"atributo '{a['tipo']}' com mapeamento '{de['tipo']}' nao existe no protocolo 1")
            continue
        if de["agregacao"] not in regra["agregacao"]:
            add("combinacao_invalida", f"{base}/de/agregacao", f"para {a['tipo']}+{de['tipo']} a agregacao deve ser uma de {sorted(regra['agregacao'])}")
        presentes = {k for k in ("classes", "classe", "limiar_presente", "limiar_ausente") if k in de} | {k for k in ("faixa", "categorias") if k in a}
        for faltando in sorted(regra["exige"] - presentes):
            add("campo_obrigatorio", base, f"{a['tipo']}+{de['tipo']} exige '{faltando}'")
        for sobrando in sorted(regra["proibe"] & presentes):
            add("campo_proibido", base, f"{a['tipo']}+{de['tipo']} nao admite '{sobrando}'")
        if "faixa" in a:
            lo, hi = a["faixa"]
            if lo >= hi:
                add("faixa_invalida", f"{base}/faixa", "faixa exige minimo < maximo")
            if de["agregacao"] in ("probabilidade", "maior_confianca") and a["tipo"] == "numero" and (lo, hi) != (0, 1):
                add("faixa_invalida", f"{base}/faixa", "valor vindo de confianca/probabilidade tem faixa [0, 1]")
            if de["agregacao"] == "contagem" and (lo != 0 or hi != int(hi)):
                add("faixa_invalida", f"{base}/faixa", "contagem exige faixa [0, N] com N inteiro")
        lp, la = de.get("limiar_presente"), de.get("limiar_ausente")
        for chave, v in (("limiar_presente", lp), ("limiar_ausente", la)):
            if isinstance(v, dict):
                nome = v["$parametro"]
                usados.add(nome)
                if nome not in params:
                    add("referencia_parametro", f"{base}/de/{chave}", f"parametro '{nome}' nao declarado em 'parametros'")
        if lp is not None and la is not None:
            (p_pad, p_min, _), (a_pad, _, a_max) = _faixa_limiar(lp, params), _faixa_limiar(la, params)
            if p_pad is not None and a_pad is not None:
                if a_pad > p_pad:
                    add("limiares_invertidos", base, f"limiar_ausente ({a_pad}) maior que limiar_presente ({p_pad}); vale tambem para os valores padrao dos parametros")
                elif a_max is not None and p_min is not None and a_max > p_min and (isinstance(lp, dict) or isinstance(la, dict)):
                    rel.avisos.append(f"{base}: as faixas dos parametros permitem limiar_ausente > limiar_presente; o host deve validar os valores efetivos")
        if a["tipo"] == "categoria" and "categorias" in a and len(set(a["categorias"])) != len(a["categorias"]):
            add("categorias_repetidas", f"{base}/categorias", "categorias repetidas")
    for nome in params:
        if nome not in usados:
            rel.avisos.append(f"parametro '{nome}' nao e referenciado por nenhum atributo (sem efeito)")


def _checar_arquivos_e_hashes(m: dict[str, Any], fonte: Fonte, caminhos: set[str], tamanhos: dict[str, int], rel: Relatorio) -> None:
    for i, mod in enumerate(m["modelos"]):
        arq = mod["arquivo"]
        base = f"modelos/{i}"
        if arq.endswith("/"):
            arq = arq.rstrip("/")
        pol = _problemas_caminho_rapido(arq)
        if pol:
            rel.adicionar(3, "modelo_caminho_invalido", f"{base}/arquivo", pol)
            continue
        if mod["formato"] == "openvino":
            filhos = sorted(c for c in caminhos if c.startswith(arq + "/"))
            if not filhos:
                rel.adicionar(3, "arquivo_ausente", arq, "pasta OpenVINO inexistente ou vazia")
                continue
            if not arq.rstrip("/").endswith("_openvino_model"):
                rel.adicionar(3, "openvino_nome", arq, "a pasta OpenVINO deve se chamar '*_openvino_model' (convencao do Ultralytics: com outro nome o modelo nao carrega)")
                continue
            nomes = [c[len(arq) + 1:] for c in filhos]
            xmls = [n for n in nomes if "/" not in n and n.endswith(".xml")]
            bins = [n for n in nomes if "/" not in n and n.endswith(".bin")]
            if len(xmls) != 1 or len(bins) != 1 or xmls[0][:-4] != bins[0][:-4]:
                rel.adicionar(3, "openvino_estrutura", arq, "a pasta deve ter exatamente um .xml e um .bin com o mesmo nome-base")
                continue
            try:
                registros = [(n, tamanhos[f"{arq}/{n}"], (lambda cc=f"{arq}/{n}": fonte.blocos(cc))) for n in nomes]
                h = hash_pasta(registros)
            except (ValueError, zipfile.BadZipFile, OSError) as exc:
                rel.adicionar(6, "zip_invalido", arq, f"falha ao ler os arquivos do modelo: {exc}")
                continue
            _classes_openvino(m, mod, fonte, f"{arq}/metadata.yaml", caminhos, rel, i)
        else:
            if arq not in caminhos:
                rel.adicionar(3, "arquivo_ausente", arq, "arquivo de pesos nao encontrado no pacote")
                continue
            try:
                h = sha256_blocos(fonte.blocos(arq))
            except (zipfile.BadZipFile, OSError) as exc:
                rel.adicionar(6, "zip_invalido", arq, f"falha ao ler: {exc}")
                continue
            rel.nao_verificado.append(NAO_VERIFICADO_PT if mod["formato"] == "pt" else NAO_VERIFICADO_ONNX)
        rel.hashes[mod["id"]] = h
        if h != mod["sha256"]:
            rel.adicionar(4, "hash_divergente", arq, f"sha256 calculado {h} difere do declarado {mod['sha256']}")


def _problemas_caminho_rapido(arq: str) -> str:
    from src.jutta_ped.especialistas.fontes import problemas_de_caminho
    achados = problemas_de_caminho(arq, Limites())
    return "; ".join(msg for _, msg in achados)


def _classes_do_atributo(a: dict[str, Any]) -> set[str]:
    de = a["de"]
    s: set[str] = set(de.get("classes", []))
    if "classe" in de:
        s.add(de["classe"])
    if a["tipo"] == "categoria":
        s |= set(a.get("categorias", []))
    return s


def _classes_openvino(m: dict[str, Any], mod: dict[str, Any], fonte: Fonte, meta: str, caminhos: set[str], rel: Relatorio, idx: int) -> None:
    """Se a pasta OpenVINO traz metadata.yaml (formato Ultralytics), confere os nomes de classe. YAML e lido com
    safe_load (dados, nunca codigo). Sem metadata.yaml, a verificacao nao e feita e isso e informado."""
    if meta not in caminhos:
        rel.nao_verificado.append(f"classes do modelo '{mod['id']}' (OpenVINO) nao verificadas: sem metadata.yaml")
        return
    try:
        dados = yaml.safe_load(fonte.ler_limitado(meta, 256 * 1024))
        nomes = dados.get("names") if isinstance(dados, dict) else None
        if isinstance(nomes, dict):
            conhecidas = {str(v) for v in nomes.values()}
        elif isinstance(nomes, list):
            conhecidas = {str(v) for v in nomes}
        else:
            raise ValueError("sem 'names'")
    except Exception:  # noqa: BLE001 -- metadata ilegivel nao reprova o pacote; so deixa de verificar
        rel.nao_verificado.append(f"classes do modelo '{mod['id']}' (OpenVINO) nao verificadas: metadata.yaml ilegivel ou sem 'names'")
        return
    for i, a in enumerate(m["atributos"]):
        if a["de"]["modelo"] != mod["id"]:
            continue
        for c in sorted(_classes_do_atributo(a) - conhecidas):
            rel.adicionar(2, "classe_inexistente", f"atributos/{i}/de", f"classe '{c}' nao existe no modelo '{mod['id']}' (classes: {sorted(conhecidas)})")


def _checar_amostras(m: dict[str, Any], fonte: Fonte, caminhos: set[str], rel: Relatorio, lim: Limites) -> None:
    nomes_attr = {a["nome"] for a in m["atributos"]}
    imagens = {c for c in caminhos if c.startswith("amostras/") and c.lower().endswith((".jpg", ".jpeg", ".png"))}
    for c in sorted(caminhos):
        if not (c.startswith("amostras/") and c.endswith(".esperado.json")):
            continue
        base = c[: -len(".esperado.json")]
        if not any(base + ext in imagens or (base + ext).lower() in {i.lower() for i in imagens} for ext in (".jpg", ".jpeg", ".png")):
            rel.adicionar(3, "amostra_sem_imagem", c, "nao ha imagem correspondente (.jpg/.jpeg/.png com o mesmo nome-base)")
        try:
            dados = json.loads(fonte.ler_limitado(c, lim.max_manifesto_bytes).decode("utf-8"), object_pairs_hook=_sem_chaves_duplicadas,
                               parse_constant=_rejeitar_constante, parse_float=_float_finito)
        except (ErroPacote, ValueError, UnicodeDecodeError, OSError, zipfile.BadZipFile) as exc:
            rel.adicionar(3, "amostra_invalida", c, f"JSON ilegivel: {exc}")
            continue
        lista = dados.get("atributos") if isinstance(dados, dict) else None
        if not isinstance(lista, list):
            rel.adicionar(3, "amostra_invalida", c, "esperado: objeto com a lista 'atributos' (formato do resultado, manual 4.2)")
            continue
        for item in lista:
            n = item.get("nome") if isinstance(item, dict) else None
            if n not in nomes_attr:
                rel.adicionar(2, "amostra_atributo_desconhecido", c, f"atributo '{n}' nao existe no manifesto")


_ASSINATURAS = (b"MZ", b"\x7fELF", b"#!", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")


def _checar_conteudo_suspeito(fonte: Fonte, caminhos: set[str], rel: Relatorio) -> None:
    """Texto/imagem com cabecalho de executavel ou imagem cujo cabecalho nao confere com a extensao.
    Pesos (.pt/.bin/.onnx) ficam de fora: bytes aleatorios geram falso positivo."""
    for c in sorted(caminhos):
        ext = c.rsplit(".", 1)[-1].lower() if "." in c else ""
        if ext in ("pt", "bin", "onnx"):
            continue
        try:
            with fonte.abrir(c) as f:
                inicio = f.read(8)
        except (ErroPacote, OSError, zipfile.BadZipFile):
            continue
        if any(inicio.startswith(a) for a in _ASSINATURAS):
            rel.adicionar(3, "executavel_detectado", c, "o arquivo comeca com assinatura de executavel/script")
        elif ext in ("jpg", "jpeg") and not inicio.startswith(b"\xff\xd8"):
            rel.adicionar(3, "imagem_inconsistente", c, "extensao .jpg mas o cabecalho nao e JPEG")
        elif ext == "png" and not inicio.startswith(b"\x89PNG"):
            rel.adicionar(3, "imagem_inconsistente", c, "extensao .png mas o cabecalho nao e PNG")
