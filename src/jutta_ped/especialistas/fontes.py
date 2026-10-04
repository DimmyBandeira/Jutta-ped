"""Leitura segura de pacotes de especialista (pasta ou ZIP).

Nada aqui executa ou importa conteudo do pacote: so lista entradas, verifica nomes e le bytes com limites.
Os limites sao a POLITICA INICIAL (docs/manual_especialista.md, secao 12) e podem ser ajustados pela equipe.
"""
from __future__ import annotations

import os
import re
import stat
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

MIB = 1024 * 1024

EXTENSOES_PERMITIDAS = frozenset(
    {".json", ".md", ".txt", ".jpg", ".jpeg", ".png", ".pt", ".xml", ".bin", ".onnx", ".yaml", ".yml"}
)
# Extensoes que sinalizam codigo/instalador: mensagem especifica em vez de "tipo nao permitido".
EXTENSOES_EXECUTAVEIS = frozenset(
    {".py", ".pyc", ".pyd", ".pyw", ".dll", ".exe", ".so", ".dylib", ".bat", ".cmd", ".ps1", ".sh", ".js",
     ".vbs", ".msi", ".jar", ".com", ".scr", ".lnk"}
)
RESERVADOS_WINDOWS = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"com{i}" for i in "0123456789¹²³"}
    | {f"lpt{i}" for i in "0123456789¹²³"}
)
_CARACTERES_PROIBIDOS = set('<>:"|?*')
_REPARSE_POINT = 0x400   # FILE_ATTRIBUTE_REPARSE_POINT (symlink/junction no Windows)


@dataclass(frozen=True)
class Limites:
    max_total_bytes: int = 500 * MIB
    max_arquivo_bytes: int = 400 * MIB
    max_arquivos: int = 200
    max_profundidade: int = 20
    max_razao_compressao: int = 50
    max_manifesto_bytes: int = 256 * 1024
    max_caminho: int = 240
    max_componente: int = 100

    def como_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


class ErroPacote(Exception):
    """Falha estrutural do pacote, com categoria de saida (3 = arquivos, 6 = limites/ZIP) e codigo estavel."""

    def __init__(self, categoria: int, codigo: str, mensagem: str, caminho: str = "") -> None:
        super().__init__(mensagem)
        self.categoria, self.codigo, self.mensagem, self.caminho = categoria, codigo, mensagem, caminho


@dataclass(frozen=True)
class Problema:
    categoria: int
    codigo: str
    caminho: str
    mensagem: str


@dataclass
class Entrada:
    caminho: str        # relativo, separador '/'
    tamanho: int        # declarado (ZIP) ou real (pasta)
    tamanho_comprimido: int = 0


@dataclass
class Listagem:
    entradas: list[Entrada] = field(default_factory=list)
    problemas: list[Problema] = field(default_factory=list)
    diretorios: list[str] = field(default_factory=list)   # entradas de pasta explicitas (ZIP) ou reais (pasta)


def problemas_de_caminho(nome: str, limites: Limites) -> list[tuple[str, str]]:
    """Devolve [(codigo, mensagem)] para um nome relativo de arquivo. Vazio = aceitavel."""
    achados: list[tuple[str, str]] = []
    if not nome:
        return [("caminho_vazio", "nome de entrada vazio")]
    if any(ord(c) < 32 or ord(c) == 127 for c in nome):
        achados.append(("caminho_caractere_controle", "o nome contem caractere de controle"))
    if "\\" in nome:
        achados.append(("caminho_barra_invertida", "use apenas '/' como separador (barra invertida e perigosa no Windows)"))
    if nome.startswith("/") or re.match(r"^[A-Za-z]:", nome) or nome.startswith("//"):
        achados.append(("caminho_absoluto", "caminho absoluto ou com letra de unidade"))
    if len(nome) > limites.max_caminho:
        achados.append(("caminho_longo", f"caminho com mais de {limites.max_caminho} caracteres"))
    partes = nome.split("/")
    if len([p for p in partes if p]) > limites.max_profundidade:
        achados.append(("caminho_profundo", f"mais de {limites.max_profundidade} niveis de pasta"))
    for parte in partes:
        if parte in ("", ".", ".."):
            achados.append(("caminho_traversal", f"componente invalido '{parte}' (vazio, '.' ou '..')"))
            continue
        if len(parte) > limites.max_componente:
            achados.append(("caminho_componente_longo", f"componente com mais de {limites.max_componente} caracteres"))
        if _CARACTERES_PROIBIDOS & set(parte):
            achados.append(("caminho_caractere_proibido", f"'{parte}' contem caractere proibido no Windows (<>:\"|?*; ':' inclui fluxos alternativos)"))
        base = parte.split(".", 1)[0].strip().casefold()
        if base in RESERVADOS_WINDOWS:
            achados.append(("caminho_nome_reservado", f"'{parte}' e nome de dispositivo reservado no Windows"))
        if parte.endswith((".", " ")):
            achados.append(("caminho_final_invalido", f"'{parte}' termina em ponto ou espaco (o Windows altera o nome)"))
    return achados


def _chave(caminho: str) -> str:
    return unicodedata.normalize("NFC", caminho).casefold()


def verificar_entradas(entradas: list[Entrada], limites: Limites, diretorios: list[str] | None = None) -> list[Problema]:
    """Regras que valem para pasta e ZIP: nomes (de arquivos E de pastas), tipos, colisoes e limites."""
    problemas: list[Problema] = []
    # Pastas: as explicitas e os pais implicitos de cada arquivo. Nome de pasta tambem pode ser perigoso
    # ('../escape/', 'con/') e um arquivo nao pode ter o mesmo nome de uma pasta (conflito na extracao).
    pastas: dict[str, str] = {}
    for d in diretorios or []:
        for codigo, msg in problemas_de_caminho(d, limites):
            problemas.append(Problema(3, codigo, d + "/", msg))
        pastas.setdefault(_chave(d), d)
    for e in entradas:
        partes = e.caminho.split("/")
        for i in range(1, len(partes)):
            pai = "/".join(partes[:i])
            pastas.setdefault(_chave(pai), pai)
    if len(entradas) > limites.max_arquivos:
        problemas.append(Problema(6, "limite_arquivos", "", f"{len(entradas)} arquivos (maximo {limites.max_arquivos})"))
    total = sum(e.tamanho for e in entradas)
    if total > limites.max_total_bytes:
        problemas.append(Problema(6, "limite_total", "", f"{total} bytes descompactados (maximo {limites.max_total_bytes})"))
    vistos: dict[str, str] = {}
    for e in entradas:
        for codigo, msg in problemas_de_caminho(e.caminho, limites):
            problemas.append(Problema(3, codigo, e.caminho, msg))
        ext = os.path.splitext(e.caminho)[1].lower()
        if ext in EXTENSOES_EXECUTAVEIS:
            problemas.append(Problema(3, "codigo_executavel", e.caminho, "pacote declarativo nao pode conter codigo executavel"))
        elif ext not in EXTENSOES_PERMITIDAS:
            problemas.append(Problema(3, "tipo_nao_permitido", e.caminho, f"extensao '{ext or '(nenhuma)'}' nao permitida"))
        if e.tamanho > limites.max_arquivo_bytes:
            problemas.append(Problema(6, "limite_arquivo", e.caminho, f"{e.tamanho} bytes (maximo {limites.max_arquivo_bytes} por arquivo)"))
        if e.tamanho_comprimido > 0 and e.tamanho / e.tamanho_comprimido > limites.max_razao_compressao:
            problemas.append(Problema(6, "razao_compressao", e.caminho, f"razao de compressao acima de {limites.max_razao_compressao}:1"))
        chave = _chave(e.caminho)
        if chave in pastas:
            problemas.append(Problema(3, "colisao_arquivo_pasta", e.caminho, f"o arquivo tem o mesmo nome da pasta '{pastas[chave]}/' (conflito na extracao)"))
        if chave in vistos:
            codigo = "entrada_duplicada" if vistos[chave] == e.caminho else "colisao_de_caminho"
            problemas.append(Problema(3, codigo, e.caminho, f"conflita com '{vistos[chave]}' (mesmo nome sem diferenciar maiusculas/normalizacao)"))
        else:
            vistos[chave] = e.caminho
    return problemas


class Fonte:
    """Interface comum de PastaFonte e ZipFonte."""

    nome: str = ""

    def listar(self, limites: Limites) -> Listagem:  # pragma: no cover - interface
        raise NotImplementedError

    def abrir(self, caminho: str) -> BinaryIO:  # pragma: no cover - interface
        raise NotImplementedError

    def fechar(self) -> None:
        pass

    def ler_limitado(self, caminho: str, maximo: int) -> bytes:
        """Le ate `maximo` bytes; acima disso e erro (nunca confia no tamanho declarado)."""
        with self.abrir(caminho) as f:
            dados = f.read(maximo + 1)
        if len(dados) > maximo:
            raise ErroPacote(6, "limite_leitura", f"'{caminho}' excede {maximo} bytes", caminho)
        return dados

    def blocos(self, caminho: str, tamanho_bloco: int = 1 * MIB) -> Iterator[bytes]:
        with self.abrir(caminho) as f:
            while True:
                b = f.read(tamanho_bloco)
                if not b:
                    return
                yield b

    def __enter__(self) -> "Fonte":
        return self

    def __exit__(self, *exc: object) -> None:
        self.fechar()


class PastaFonte(Fonte):
    def __init__(self, raiz: Path) -> None:
        self.raiz = Path(raiz)
        self.nome = self.raiz.name

    def listar(self, limites: Limites) -> Listagem:
        lst = Listagem()
        pilha = [(self.raiz, 0)]
        while pilha:
            atual, nivel = pilha.pop()
            try:
                itens = sorted(os.scandir(atual), key=lambda d: d.name)
            except OSError as exc:
                lst.problemas.append(Problema(3, "pasta_ilegivel", str(atual), str(exc)))
                continue
            for item in itens:
                rel = Path(item.path).relative_to(self.raiz).as_posix()
                st = item.stat(follow_symlinks=False)
                if item.is_symlink() or getattr(st, "st_file_attributes", 0) & _REPARSE_POINT:
                    lst.problemas.append(Problema(3, "link_simbolico", rel, "links simbolicos e juncoes nao sao permitidos"))
                elif stat.S_ISDIR(st.st_mode):
                    lst.diretorios.append(rel)
                    if nivel + 1 > limites.max_profundidade:
                        lst.problemas.append(Problema(3, "caminho_profundo", rel, f"mais de {limites.max_profundidade} niveis de pasta"))
                    else:
                        pilha.append((Path(item.path), nivel + 1))
                elif stat.S_ISREG(st.st_mode):
                    lst.entradas.append(Entrada(rel, st.st_size))
                else:
                    lst.problemas.append(Problema(3, "tipo_especial", rel, "so arquivos e pastas comuns sao permitidos"))
        lst.entradas.sort(key=lambda e: e.caminho)
        return lst

    def abrir(self, caminho: str) -> BinaryIO:
        return open(self.raiz / caminho, "rb")


class ZipFonte(Fonte):
    def __init__(self, arquivo: Path) -> None:
        self.arquivo = Path(arquivo)
        self.nome = self.arquivo.name
        try:
            self._zip = zipfile.ZipFile(self.arquivo)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ErroPacote(6, "zip_invalido", f"nao e um ZIP valido: {exc}") from exc
        self._infos: dict[str, zipfile.ZipInfo] = {}

    def fechar(self) -> None:
        self._zip.close()

    def listar(self, limites: Limites) -> Listagem:
        lst = Listagem()
        for info in self._zip.infolist():
            # `orig_filename`: no Windows o zipfile troca '\' por '/' em `filename`, escondendo o nome real gravado.
            nome = info.orig_filename
            # Tipo/link primeiro, ANTES de separar arquivo de pasta: uma entrada "atalho/" marcada como link
            # simbolico nos metadados nao pode passar como pasta comum.
            modo = (info.external_attr >> 16) & 0xFFFF
            formato = stat.S_IFMT(modo) if modo else 0
            if formato == stat.S_IFLNK:
                lst.problemas.append(Problema(3, "link_simbolico", nome, "links simbolicos nao sao permitidos"))
                continue
            if formato not in (0, stat.S_IFREG, stat.S_IFDIR) or (formato == stat.S_IFDIR and not info.is_dir()) or (formato == stat.S_IFREG and info.is_dir()):
                lst.problemas.append(Problema(3, "tipo_especial", nome, "so arquivos e pastas comuns sao permitidos"))
                continue
            if info.is_dir():
                lst.diretorios.append(nome[:-1] if nome.endswith("/") else nome)
                continue
            if info.flag_bits & 0x1:
                lst.problemas.append(Problema(6, "zip_criptografado", nome, "entradas criptografadas nao sao suportadas"))
                continue
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                lst.problemas.append(Problema(6, "zip_compressao_nao_suportada", nome, "use apenas STORED ou DEFLATE"))
                continue
            lst.entradas.append(Entrada(nome, info.file_size, info.compress_size))
            self._infos.setdefault(nome, info)
        return lst

    def abrir(self, caminho: str) -> BinaryIO:
        return self._zip.open(self._infos[caminho])
