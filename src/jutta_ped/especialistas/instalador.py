"""Ciclo local de instalacao de especialistas: validar -> extrair isolado -> registrar -> ativar / reverter.

Sem dependencia do painel: o estado fica em arquivos sob `raiz` (padrao `runtime/especialistas/`):

    registro.json (+ .bak)       estado: versoes instaladas, versao ativa e historico de ativacoes
    pacotes/<id>/<versao>/       arquivos extraidos e validados (imutaveis depois de instalados)
    staging/<token>/             extracao em andamento (sempre descartavel)
    pendentes/<token>.json       marcador de operacao em curso (permite recuperar depois de queda)
    quarentena/                  diretorios orfaos ou reprovados, preservados para analise
    operacoes.jsonl              trilha de auditoria
    .lock                        arquivo de bloqueio do SO (flock/LockFileEx): o dono e quem o mantem aberto

Garantias:
* so entra no registro o que o validador aprovou ANTES e DEPOIS da extracao (mesmos hashes);
* a extracao reaplica os limites, escreve so dentro de `staging` e nunca sobrescreve arquivos;
* mover para `pacotes/` e uma troca atomica de pasta; o registro e gravado atomicamente (tmp + replace);
* uma versao instalada e imutavel: reinstalar o mesmo id+versao com conteudo diferente (pesos OU manifesto OU
  qualquer arquivo) e recusado;
* a identidade do pacote e o hash canonico `jutta-dir-v1` de TODOS os arquivos (inclui manifest.json), mais o
  SHA-256 do manifesto, gravados no registro; ativar, reverter e recuperar conferem essa identidade na hora.

NAO faz: carregar modelos, executar nada do pacote, assinar/autenticar autoria (SHA-256 e integridade).
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from src.jutta_ped.especialistas.fontes import Limites
from src.jutta_ped.especialistas.hashing import hash_pasta, sha256_blocos
from src.jutta_ped.especialistas.validador import Relatorio, _abrir_fonte, validar

RAIZ_PADRAO = Path("runtime") / "especialistas"
HISTORICO_MAX = 10
ESQUEMA_REGISTRO = 2       # 2: cada versao guarda `conteudo_sha256` e `manifesto_sha256`

# Codigos de saida da CLI de instalacao (0 = ok, 1 = uso/E-S de argumento)
COD_REPROVADO = 10      # o validador reprovou o pacote
COD_CONFLITO = 11       # versao ja instalada com pesos diferentes / destino ocupado
COD_BLOQUEIO = 12       # outra operacao em andamento
COD_ES = 13             # falha de leitura/escrita no armazenamento
COD_INTEGRIDADE = 14    # conteudo extraido ou instalado nao confere
COD_ESTADO = 15         # versao inexistente / sem versao anterior valida / registro ilegivel
COD_EM_USO = 16         # operacao recusada porque a versao esta ativa


class ErroInstalacao(Exception):
    def __init__(self, codigo: int, slug: str, mensagem: str, relatorio: Relatorio | None = None) -> None:
        super().__init__(mensagem)
        self.codigo, self.slug, self.mensagem, self.relatorio = codigo, slug, mensagem, relatorio


@dataclass
class AtivoResolvido:
    id: str
    versao: str
    pasta: Path
    identidade: str           # conteudo_sha256 gravado na instalacao


@dataclass
class ResultadoInstalacao:
    id: str
    versao: str
    ja_instalado: bool
    caminho: Path
    hashes: dict[str, str] = field(default_factory=dict)


if os.name == "nt":
    import msvcrt

    def _travar(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _destravar(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:  # pragma: no cover - Linux/macOS
    import fcntl

    def _travar(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _destravar(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _ponto(nome: str) -> None:
    """Ponto de injecao de falha para testes (simula queda do processo). Nao faz nada em producao."""


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _tentar(fn, tentativas: int = 6, espera: float = 0.25):
    """OneDrive/antivirus no Windows seguram arquivos por instantes (PermissionError): repete antes de falhar."""
    for i in range(tentativas):
        try:
            return fn()
        except PermissionError:
            if i == tentativas - 1:
                raise
            time.sleep(espera)


def _substituir(origem: Path, destino: Path) -> None:
    _tentar(lambda: os.replace(origem, destino))


def _remover_arvore(caminho: Path) -> None:
    if caminho.exists():
        _tentar(lambda: shutil.rmtree(caminho))


class Instalador:
    def __init__(self, raiz: Path | str = RAIZ_PADRAO, *, limites: Limites | None = None, host_protocolo: int = 1) -> None:
        self.raiz = Path(raiz)
        self.limites = limites or Limites()
        self.host_protocolo = host_protocolo
        self.pacotes = self.raiz / "pacotes"
        self.staging = self.raiz / "staging"
        self.pendentes = self.raiz / "pendentes"
        self.quarentena = self.raiz / "quarentena"
        self.arq_registro = self.raiz / "registro.json"
        self.arq_bak = self.raiz / "registro.json.bak"
        self.arq_lock = self.raiz / ".lock"
        self.arq_log = self.raiz / "operacoes.jsonl"
        for d in (self.pacotes, self.staging, self.pendentes, self.quarentena):
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ infraestrutura

    @contextmanager
    def _bloqueio(self) -> Iterator[None]:
        """Exclusao mutua por bloqueio do SO sobre `.lock`. Quem mantem o descritor aberto e o dono; se o processo
        morre o SO libera sozinho, entao nao existe 'lock obsoleto' para detectar nem apagar (era a fonte de uma
        corrida em que dois processos viravam donos). O arquivo nunca e removido."""
        fd = os.open(self.arq_lock, os.O_CREAT | os.O_RDWR)
        try:
            _travar(fd)
        except OSError:
            os.close(fd)
            raise ErroInstalacao(COD_BLOQUEIO, "operacao_em_andamento", "outra operacao de instalacao esta em andamento") from None
        try:
            yield
        finally:
            try:
                _destravar(fd)
            finally:
                os.close(fd)

    def _log(self, op: str, **dados: Any) -> None:
        try:
            with open(self.arq_log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": _agora(), "op": op, **dados}, ensure_ascii=False) + "\n")
        except OSError:
            pass   # a trilha e auxiliar: nao derruba a operacao

    @staticmethod
    def _vazio() -> dict[str, Any]:
        return {"esquema": ESQUEMA_REGISTRO, "especialistas": {}}

    def _ler_registro(self) -> dict[str, Any]:
        for arq in (self.arq_registro, self.arq_bak):
            if not arq.exists():
                continue
            try:
                dados = json.loads(arq.read_text(encoding="utf-8"))
                if isinstance(dados, dict) and isinstance(dados.get("especialistas"), dict):
                    return dados
            except (OSError, ValueError):
                pass
            if arq == self.arq_registro:
                self._log("registro_ilegivel", usando="bak")
        if self.arq_registro.exists() or self.arq_bak.exists():
            raise ErroInstalacao(COD_ESTADO, "registro_corrompido", "registro.json e a copia .bak estao ilegiveis; rode 'recuperar'")
        return self._vazio()

    def _gravar_registro(self, reg: dict[str, Any]) -> None:
        tmp = self.raiz / f"registro.{uuid.uuid4().hex}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        if self.arq_registro.exists():
            try:
                shutil.copy2(self.arq_registro, self.arq_bak)
            except OSError:
                pass
        _substituir(tmp, self.arq_registro)

    # ------------------------------------------------------------------ instalar

    def instalar(self, origem: Path | str, *, rotulo: str | None = None) -> ResultadoInstalacao:
        """`rotulo`: nome de exibicao da origem (ex.: nome do arquivo enviado); so informativo, nunca vira caminho."""
        origem = Path(origem)
        rotulo = (rotulo or origem.name)[:80]
        with self._bloqueio():
            self._recuperar_interno()
            rel = validar(origem, host_protocolo=self.host_protocolo, limites=self.limites)
            if not rel.conformidade_estatica:
                self._log("instalar_recusado", origem=str(origem), codigo=rel.codigo_saida)
                raise ErroInstalacao(COD_REPROVADO, "validacao_reprovada",
                                     f"pacote reprovado pelo validador (codigo {rel.codigo_saida}); nada foi instalado", rel)
            pid, ver = rel.pacote["id"], rel.pacote["versao"]
            ident = self._identidade(origem)
            reg = self._ler_registro()
            existente = reg["especialistas"].get(pid, {}).get("versoes", {}).get(ver)
            destino = self.pacotes / pid / ver
            if existente:
                mesmo = (existente.get("estado") == "instalada" and existente.get("hashes") == rel.hashes
                         and existente.get("conteudo_sha256") == ident["conteudo_sha256"])
                if mesmo:
                    return ResultadoInstalacao(pid, ver, True, destino, rel.hashes)
                raise ErroInstalacao(COD_CONFLITO, "versao_imutavel",
                                     f"{pid} {ver} ja esta instalado com outros pesos (ou corrompido); publique uma nova versao")
            token = uuid.uuid4().hex
            stg = self.staging / token
            marcador = self.pendentes / f"{token}.json"
            try:
                stg.mkdir(parents=True)
                self._extrair(origem, stg)
                _ponto("apos_extracao")
                rel2 = validar(stg, host_protocolo=self.host_protocolo, limites=self.limites)
                if not rel2.conformidade_estatica or rel2.hashes != rel.hashes or rel2.pacote != rel.pacote:
                    raise ErroInstalacao(COD_INTEGRIDADE, "extraido_divergente", "o conteudo extraido nao confere com o pacote validado", rel2)
                if self._identidade(stg) != ident:
                    raise ErroInstalacao(COD_INTEGRIDADE, "extraido_divergente", "a identidade do conteudo extraido (arquivos/manifesto) difere da origem")
                if destino.exists():
                    raise ErroInstalacao(COD_CONFLITO, "destino_ocupado", f"{destino} ja existe fora do registro; rode 'recuperar'")
                marcador.write_text(json.dumps({"id": pid, "versao": ver, "staging": str(stg), "destino": str(destino),
                                                "hashes": rel.hashes, **ident, "origem": rotulo, "ts": _agora()}), encoding="utf-8")
                _ponto("apos_marcador")
                destino.parent.mkdir(parents=True, exist_ok=True)
                _substituir(stg, destino)
                _ponto("apos_mover")
                reg["especialistas"].setdefault(pid, {"ativa": None, "historico": [], "versoes": {}})["versoes"][ver] = {
                    "estado": "instalada", "instalado_em": _agora(), "origem": rotulo, "origem_sha256": self._hash_origem(origem),
                    "hashes": rel.hashes, **ident, "nome": rel.pacote["nome"],
                }
                self._gravar_registro(reg)
                _ponto("apos_registro")
                marcador.unlink(missing_ok=True)
            except Exception as exc:
                self._desfazer(stg, destino, marcador, pid, ver)
                if isinstance(exc, ErroInstalacao):
                    self._log("instalar_falhou", id=pid, versao=ver, slug=exc.slug)
                    raise
                self._log("instalar_falhou", id=pid, versao=ver, erro=repr(exc))
                raise ErroInstalacao(COD_ES, "falha_de_es", f"falha ao instalar: {exc}") from exc
            self._log("instalado", id=pid, versao=ver)
            return ResultadoInstalacao(pid, ver, False, destino, rel.hashes)

    def _desfazer(self, stg: Path, destino: Path, marcador: Path, pid: str, ver: str) -> None:
        """Limpeza apos erro (nao apos queda do processo, que e tratada por `recuperar`)."""
        try:
            _remover_arvore(stg)
            if destino.exists() and ver not in self._ler_registro()["especialistas"].get(pid, {}).get("versoes", {}):
                _remover_arvore(destino)
            marcador.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001 -- limpeza e melhor esforco; `recuperar` conclui o que faltar
            pass

    def _identidade(self, origem: Path) -> dict[str, str]:
        """Identidade persistida do pacote: hash canonico de TODOS os arquivos e SHA-256 do manifesto."""
        with _abrir_fonte(origem) as fonte:
            lst = fonte.listar(self.limites)
            conteudo = hash_pasta((e.caminho, e.tamanho, (lambda c=e.caminho: fonte.blocos(c))) for e in lst.entradas)
            manifesto = sha256_blocos(fonte.blocos("manifest.json"))
        return {"conteudo_sha256": conteudo, "manifesto_sha256": manifesto}

    @staticmethod
    def _hash_origem(origem: Path) -> str:
        if origem.is_file():
            with open(origem, "rb") as f:
                return sha256_blocos(iter(lambda: f.read(1 << 20), b""))
        itens = sorted(p for p in origem.rglob("*") if p.is_file())
        return hash_pasta(((p.relative_to(origem).as_posix(), p.stat().st_size, (lambda q=p: _ler_blocos(q))) for p in itens))

    def _extrair(self, origem: Path, stg: Path) -> None:
        base = stg.resolve()
        total = 0
        with _abrir_fonte(origem) as fonte:
            lst = fonte.listar(self.limites)
            for e in lst.entradas:
                alvo = stg / e.caminho
                if base not in alvo.resolve().parents:
                    raise ErroInstalacao(COD_INTEGRIDADE, "caminho_fora_do_destino", f"'{e.caminho}' sairia do diretorio de extracao")
                alvo.parent.mkdir(parents=True, exist_ok=True)
                escrito = 0
                with open(alvo, "xb") as out:
                    for bloco in fonte.blocos(e.caminho):
                        escrito += len(bloco)
                        total += len(bloco)
                        if escrito > e.tamanho or total > self.limites.max_total_bytes:
                            raise ErroInstalacao(COD_INTEGRIDADE, "limite_na_extracao", f"'{e.caminho}' excede o tamanho declarado ou o limite total")
                        out.write(bloco)
                if escrito != e.tamanho:
                    raise ErroInstalacao(COD_INTEGRIDADE, "tamanho_divergente", f"'{e.caminho}': declarado {e.tamanho}, extraido {escrito}")

    # ------------------------------------------------------------------ ativar / reverter / remover

    def _verificar(self, pid: str, ver: str, v: dict[str, Any]) -> bool:
        """Pasta instalada == pacote registrado: validador aprova, hashes dos pesos iguais E identidade (todos os
        arquivos + manifesto) igual a gravada na instalacao. Registro sem identidade (esquema 1) nao e confiavel."""
        pasta = self.pacotes / pid / ver
        if not pasta.is_dir() or not v.get("conteudo_sha256") or not v.get("manifesto_sha256"):
            return False
        rel = validar(pasta, host_protocolo=self.host_protocolo, limites=self.limites)
        if not rel.conformidade_estatica or rel.hashes != v["hashes"]:
            return False
        ident = self._identidade(pasta)
        return ident["conteudo_sha256"] == v["conteudo_sha256"] and ident["manifesto_sha256"] == v["manifesto_sha256"]

    def ativar(self, pid: str, versao: str, *, verificar: bool = True) -> str:
        with self._bloqueio():
            reg = self._ler_registro()
            esp = reg["especialistas"].get(pid)
            v = (esp or {}).get("versoes", {}).get(versao)
            if not v or v.get("estado") != "instalada":
                raise ErroInstalacao(COD_ESTADO, "versao_inexistente", f"{pid} {versao} nao esta instalado (ou esta corrompido)")
            if verificar and not self._verificar(pid, versao, v):
                v["estado"] = "corrompida"
                self._gravar_registro(reg)
                self._log("ativar_integridade", id=pid, versao=versao)
                raise ErroInstalacao(COD_INTEGRIDADE, "integridade_falhou", f"{pid} {versao}: os arquivos instalados nao conferem; marcado como corrompido")
            if esp["ativa"] == versao:
                return versao
            if esp["ativa"]:
                esp["historico"] = (esp["historico"] + [esp["ativa"]])[-HISTORICO_MAX:]
            esp["ativa"] = versao
            self._gravar_registro(reg)
            self._log("ativada", id=pid, versao=versao)
            return versao

    def reverter(self, pid: str) -> str:
        """Volta para a ultima versao anterior valida (a atual continua instalada, mas inativa)."""
        with self._bloqueio():
            reg = self._ler_registro()
            esp = reg["especialistas"].get(pid)
            if not esp:
                raise ErroInstalacao(COD_ESTADO, "versao_inexistente", f"{pid} nao esta instalado")
            while esp["historico"]:
                cand = esp["historico"].pop()
                v = esp["versoes"].get(cand)
                if v and v.get("estado") == "instalada" and self._verificar(pid, cand, v):
                    anterior = esp["ativa"]
                    esp["ativa"] = cand
                    self._gravar_registro(reg)
                    self._log("revertida", id=pid, de=anterior, para=cand)
                    return cand
                if v:
                    v["estado"] = "corrompida"
            self._gravar_registro(reg)
            raise ErroInstalacao(COD_ESTADO, "sem_versao_anterior_valida", f"{pid}: nao ha versao anterior valida para reverter")

    def remover(self, pid: str, versao: str) -> None:
        with self._bloqueio():
            reg = self._ler_registro()
            esp = reg["especialistas"].get(pid)
            if not esp or versao not in esp["versoes"]:
                raise ErroInstalacao(COD_ESTADO, "versao_inexistente", f"{pid} {versao} nao esta instalado")
            if esp["ativa"] == versao:
                raise ErroInstalacao(COD_EM_USO, "versao_ativa", f"{pid} {versao} esta ativa; ative ou reverta para outra antes de remover")
            del esp["versoes"][versao]
            esp["historico"] = [h for h in esp["historico"] if h != versao]
            if not esp["versoes"]:
                del reg["especialistas"][pid]
            self._gravar_registro(reg)          # registro primeiro: sobra de disco vira orfao e e tratada por `recuperar`
            _remover_arvore(self.pacotes / pid / versao)
            self._log("removida", id=pid, versao=versao)

    def listar(self) -> dict[str, Any]:
        return self._ler_registro()["especialistas"]

    def operacoes_recentes(self, n: int = 20) -> list[dict[str, Any]]:
        """Ultimas `n` linhas da trilha de auditoria local (mais recente primeiro). Nunca levanta."""
        try:
            linhas = self.arq_log.read_text(encoding="utf-8").splitlines()[-n:]
        except OSError:
            return []
        saida = []
        for linha in reversed(linhas):
            try:
                saida.append(json.loads(linha))
            except ValueError:
                continue
        return saida

    def obter_ativo(self, pid: str, *, verificar: bool = True) -> AtivoResolvido:
        """Versao ativa de `pid`. Com `verificar`, confere a integridade completa (validador + hashes + identidade) e
        falha fechado: nunca devolve uma pasta que nao confere. Somente leitura (nao marca nada no registro)."""
        esp = self._ler_registro()["especialistas"].get(pid)
        if not esp or not esp.get("ativa"):
            raise ErroInstalacao(COD_ESTADO, "sem_versao_ativa", f"{pid} nao tem versao ativa")
        ver = esp["ativa"]
        v = esp["versoes"].get(ver, {})
        if v.get("estado") != "instalada":
            raise ErroInstalacao(COD_ESTADO, "versao_ativa_invalida", f"{pid} {ver} esta {v.get('estado', 'ausente')}; reverta ou ative outra versao")
        if verificar and not self._verificar(pid, ver, v):
            raise ErroInstalacao(COD_INTEGRIDADE, "integridade_falhou", f"{pid} {ver}: os arquivos instalados nao conferem com o registro")
        return AtivoResolvido(pid, ver, self.pacotes / pid / ver, v.get("conteudo_sha256", ""))

    def caminho_ativo(self, pid: str) -> Path | None:
        """Pasta da versao ativa, ou None. E o que o host (executor) deve usar."""
        esp = self._ler_registro()["especialistas"].get(pid)
        return self.pacotes / pid / esp["ativa"] if esp and esp.get("ativa") else None

    # ------------------------------------------------------------------ recuperacao

    def recuperar(self) -> list[str]:
        with self._bloqueio():
            return self._recuperar_interno()

    def _recuperar_interno(self) -> list[str]:
        """Deixa o armazenamento consistente apos queda/falha. Idempotente. Devolve as acoes tomadas."""
        acoes: list[str] = []
        try:
            reg = self._ler_registro()
        except ErroInstalacao:
            reg = self._reconstruir()
            acoes.append("registro reconstruido a partir de pacotes/ (nenhuma versao ativa)")
        for s in sorted(self.staging.iterdir()):
            _remover_arvore(s) if s.is_dir() else s.unlink(missing_ok=True)
            acoes.append(f"staging descartado: {s.name}")
        alterado = False
        concluidos: list[Path] = []     # marcadores so saem DEPOIS de o registro ser persistido
        for m in sorted(self.pendentes.glob("*.json")):
            try:
                d = json.loads(m.read_text(encoding="utf-8"))
                pid, ver, destino = d["id"], d["versao"], Path(d["destino"])
            except (OSError, ValueError, KeyError):
                m.unlink(missing_ok=True)
                acoes.append(f"marcador ilegivel removido: {m.name}")
                continue
            registrado = ver in reg["especialistas"].get(pid, {}).get("versoes", {})
            if not registrado and destino.is_dir():
                rel = validar(destino, host_protocolo=self.host_protocolo, limites=self.limites)
                ident = self._identidade(destino) if rel.conformidade_estatica else {}
                if (rel.conformidade_estatica and rel.hashes == d.get("hashes") and ident
                        and ident["conteudo_sha256"] == d.get("conteudo_sha256")
                        and ident["manifesto_sha256"] == d.get("manifesto_sha256")):
                    reg["especialistas"].setdefault(pid, {"ativa": None, "historico": [], "versoes": {}})["versoes"][ver] = {
                        "estado": "instalada", "instalado_em": _agora(), "origem": Path(d.get("origem", "")).name,
                        "origem_sha256": None, "hashes": rel.hashes, **ident, "nome": rel.pacote.get("nome", ""),
                    }
                    alterado = True
                    acoes.append(f"instalacao concluida na recuperacao: {pid} {ver}")
                else:
                    self._quarentenar(destino)
                    acoes.append(f"instalacao interrompida reprovada e enviada a quarentena: {pid} {ver}")
            concluidos.append(m)
        # versoes registradas sem diretorio; ativa invalida -> volta ao historico valido
        for pid, esp in reg["especialistas"].items():
            for ver, v in esp["versoes"].items():
                if v.get("estado") == "instalada" and not (self.pacotes / pid / ver).is_dir():
                    v["estado"] = "ausente"
                    alterado = True
                    acoes.append(f"versao sem arquivos marcada como ausente: {pid} {ver}")
            ativa = esp.get("ativa")
            if ativa and esp["versoes"].get(ativa, {}).get("estado") != "instalada":
                esp["ativa"] = None
                alterado = True
                while esp["historico"]:
                    cand = esp["historico"].pop()
                    v = esp["versoes"].get(cand)
                    if v and v.get("estado") == "instalada" and self._verificar(pid, cand, v):
                        esp["ativa"] = cand
                        break
                acoes.append(f"versao ativa invalida em {pid}; ativa agora: {esp['ativa']}")
        # diretorios orfaos (em disco, fora do registro)
        for pasta_id in sorted(p for p in self.pacotes.iterdir() if p.is_dir()):
            for pasta_ver in sorted(p for p in pasta_id.iterdir() if p.is_dir()):
                if pasta_ver.name not in reg["especialistas"].get(pasta_id.name, {}).get("versoes", {}):
                    self._quarentenar(pasta_ver)
                    acoes.append(f"diretorio orfao enviado a quarentena: {pasta_id.name}/{pasta_ver.name}")
        if alterado:
            self._gravar_registro(reg)
        for m in concluidos:           # registro persistido (ou nada a registrar): agora o marcador pode sair
            m.unlink(missing_ok=True)
        for a in acoes:
            self._log("recuperacao", acao=a)
        return acoes

    def _quarentenar(self, pasta: Path) -> None:
        destino = self.quarentena / f"{pasta.parent.name}__{pasta.name}__{uuid.uuid4().hex[:8]}"
        _substituir(pasta, destino)

    def _reconstruir(self) -> dict[str, Any]:
        reg = self._vazio()
        for pasta_id in sorted(p for p in self.pacotes.iterdir() if p.is_dir()):
            for pasta_ver in sorted(p for p in pasta_id.iterdir() if p.is_dir()):
                rel = validar(pasta_ver, host_protocolo=self.host_protocolo, limites=self.limites)
                if rel.conformidade_estatica and rel.pacote.get("id") == pasta_id.name and rel.pacote.get("versao") == pasta_ver.name:
                    reg["especialistas"].setdefault(pasta_id.name, {"ativa": None, "historico": [], "versoes": {}})["versoes"][pasta_ver.name] = {
                        "estado": "instalada", "instalado_em": _agora(), "origem": "(reconstruido)", "origem_sha256": None,
                        "hashes": rel.hashes, **self._identidade(pasta_ver), "nome": rel.pacote["nome"],
                    }
        self._gravar_registro(reg)
        return reg


def _ler_blocos(caminho: Path) -> Iterator[bytes]:
    with open(caminho, "rb") as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                return
            yield b
