"""Sombra por camera ligada/desligada a quente, SEM reiniciar a analise operacional.

* `PortaSombra`: objeto unico por camera entregue a `SessionStartConfig.motor_sombra` quando a IA da camera e INICIADA e que
  nunca mais e trocado. Ligar, desligar ou reconfigurar a sombra so troca o ALVO da porta (um `DespachoSombra`); sem alvo a
  porta nao faz nada. E barata e nao bloqueante (o laco da camera nunca espera pela sombra).
* `GerenteSombra`: reconcilia o banco (camera.sombra_ativa + receita associada) com os alvos das portas. Constroi motor e
  despacho FORA do caminho de requisicao e do laco da camera (carregar modelo e lento). Eventos de sombra vao so para o
  `BufferSombra` em memoria e para o rastro `.jsonl` proprio: nunca para `PainelEventos`, destinos ou `events.jsonl`.
"""
from __future__ import annotations

import dataclasses
import itertools
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from src.jutta_ped.especialistas.executor import ErroExecucao, Executor, contrato_leve
from src.jutta_ped.motor.ajustes import AjustesCamera, ajustes_de_dict, efetivos, marcar_roi, receita_efetiva
from src.jutta_ped.motor.diagnostico import DiagnosticoMestre
from src.jutta_ped.motor.despacho import DespachoSombra
from src.jutta_ped.motor.motor import MotorSombra, RastroJsonl
from src.jutta_ped.motor.receita import validar_receita
from src.jutta_ped.painel.db import Database
from src.jutta_ped.painel.models import Camera, CameraConfigVersao, Receita, ReceitaVersao

logger = logging.getLogger("jutta_ped.painel.sombra")

BUFFER_MAXIMO = 50
REPETIR_ERRO_APOS_S = 30.0
_sequencia = itertools.count(1)


def ref_da_camera(camera_id: int) -> str:
    """Referencia INTERNA fixa da camera no motor (independe do nome de stream/sessao)."""
    return f"cam-{camera_id}"


class PortaSombra:
    seguro_para_ao_vivo = True              # so enfileira (DespachoSombra): pode ficar no laco de uma fonte ao vivo

    def __init__(self, camera_id: int) -> None:
        self.camera_id = camera_id
        self.ref = ref_da_camera(camera_id)
        self._lock = threading.Lock()
        self._alvo: Any = None
        self._diagnostico: Any = None                # 05f: consumidor opcional (objeto com `processar_cena`), independente do alvo
        self._ajustes: Any = None                    # 05f: AjustesCamera EFETIVOS (ROI + cadencia) ou None
        self._ultima_encaminhada: float | None = None    # recebido_em da ultima Cena encaminhada (cadencia), da geracao vigente
        self._geracao_cadencia: int | None = None
        self.descartadas_cadencia = 0
        self.falhas_diagnostico = 0
        self.falhas_alvo = 0
        self.recebidos = 0
        self.geracao_vigente: int | None = None      # maior geracao (sessao) aceita; resultados de geracao menor sao historico
        self.recusados_geracao = 0
        self.ao_nova_geracao: Any = None             # callback(geracao): chamado ao ACEITAR uma geracao maior (barato; no laco da camera)

    @property
    def alvo(self) -> Any:
        with self._lock:
            return self._alvo

    @property
    def diagnostico(self) -> Any:
        with self._lock:
            return self._diagnostico

    @property
    def ajustes(self) -> Any:
        with self._lock:
            return self._ajustes

    @property
    def ativa(self) -> bool:
        """Ha algum consumidor (alvo ou diagnostico)? O runtime so monta/entrega a Cena quando verdadeiro."""
        with self._lock:
            return self._alvo is not None or self._diagnostico is not None

    def definir_diagnostico(self, diagnostico: Any) -> Any:
        """Troca (ou zera, com None) o consumidor de diagnostico a quente. Devolve o anterior."""
        with self._lock:
            anterior, self._diagnostico = self._diagnostico, diagnostico
        return anterior

    def definir_ajustes(self, ajustes: Any) -> Any:
        """Troca (ou zera, com None) os ajustes efetivos a quente (ROI e cadencia). A cadencia recomeca (a proxima Cena passa)."""
        with self._lock:
            anterior, self._ajustes = self._ajustes, ajustes
            self._ultima_encaminhada = None
        return anterior

    def definir_alvo(self, alvo: Any) -> Any:
        """Troca (ou zera, com None) o alvo a quente. Devolve o anterior (o chamador o encerra)."""
        with self._lock:
            anterior, self._alvo = self._alvo, alvo
        return anterior

    def _admitir(self, geracao: int) -> tuple[bool, Any]:
        """Regra de geracao compartilhada por `processar` e `processar_cena`. Devolve (aceita, alvo)."""
        nova = False
        with self._lock:
            alvo = self._alvo
            if self.geracao_vigente is not None and geracao < self.geracao_vigente:
                self.recusados_geracao += 1                 # quadro de uma sessao que ja acabou chegando atrasado
                return False, None
            if self.geracao_vigente is None or geracao > self.geracao_vigente:
                nova = self.geracao_vigente is not None
                self.geracao_vigente = geracao              # a partir daqui o que estiver EM EXECUCAO da geracao anterior e historico
        if nova and self.ao_nova_geracao is not None:
            try:
                self.ao_nova_geracao(geracao)
            except Exception:  # noqa: BLE001 -- limpeza do feed nunca pode afetar o laco da camera
                pass
        return True, alvo

    def processar(self, camera_ref: str, geracao: int, quadro: Any, observacoes: Any) -> list:
        aceita, alvo = self._admitir(geracao)
        if not aceita or alvo is None:
            return []
        self.recebidos += 1
        reescritas = [dataclasses.replace(o, camera_ref=self.ref) for o in observacoes]
        return alvo.processar(self.ref, geracao, quadro, reescritas)

    def encerrar_sessao(self, geracao: int) -> None:
        """A sessao `geracao` terminou: so encerra o estado das regras se ELA ainda e a vigente (uma sessao antiga terminando
        atrasada nao pode encerrar a nova). Nao bloqueante."""
        with self._lock:
            alvo, vigente = self._alvo, self.geracao_vigente
        if alvo is not None and vigente == geracao and hasattr(alvo, "encerrar_camera"):
            alvo.encerrar_camera(self.ref, "sessao_encerrada")

    def processar_cena(self, cena: Any) -> list:
        """Cena do mestre. Ordem, sem bloquear: (1) gate de geracao; (2) cadencia (`execucao.intervalo_min_s` por `recebido_em`, contra a
        ultima Cena ENCAMINHADA; a primeira de uma geracao nova sempre passa); (3) marcacao de ROI (`na_roi`); (4) entrega a DOIS consumidores
        independentes: `diagnostico` (recebe mesmo sem `alvo`) e `alvo` (despacho). Excecao de um consumidor e contida e contada, e nao
        afeta o outro. `camera_ref` e reescrito para a referencia interna."""
        aceita, alvo = self._admitir(cena.geracao)
        if not aceita:
            return []
        with self._lock:
            diag, ajustes = self._diagnostico, self._ajustes
            if alvo is None and diag is None:
                return []
            intervalo = getattr(getattr(ajustes, "execucao", None), "intervalo_min_s", None) or 0.0
            ultima = self._ultima_encaminhada
            if self._geracao_cadencia != cena.geracao:
                self._geracao_cadencia, ultima = cena.geracao, None          # geracao nova: a primeira sempre passa
                self._ultima_encaminhada = None
            if intervalo > 0 and ultima is not None and 0 <= cena.recebido_em - ultima < intervalo:
                self.descartadas_cadencia += 1
                return []
            if ultima is None or cena.recebido_em > ultima:
                self._ultima_encaminhada = cena.recebido_em
            self.recebidos += 1
        roi = getattr(ajustes, "roi", None)
        pronta = dataclasses.replace(marcar_roi(cena, roi), camera_ref=self.ref)
        if diag is not None:
            try:
                diag.processar_cena(pronta)
            except Exception:  # noqa: BLE001 -- diagnostico nunca afeta o alvo nem o laco
                with self._lock:
                    self.falhas_diagnostico += 1
        if alvo is None:
            return []
        try:
            return alvo.processar_cena(pronta)
        except Exception:  # noqa: BLE001 -- falha do alvo nao afeta o diagnostico (ja entregue) nem o laco
            with self._lock:
                self.falhas_alvo += 1
            return []


def _formatar_cena(evento: dict[str, Any]) -> dict[str, Any]:
    """Evento `tipo:"cena"` (receita contrato 2): sujeitos com papeis/relacoes e estado dos eventos de persistencia."""
    receita = evento.get("receita") or {}
    sujeitos = [{"trilha_id": s.get("trilha_id"), "classe": s.get("classe"), "papeis": dict(s.get("papeis") or {}),
                 "relacoes": {rid: (r.get("estado") if isinstance(r, dict) else r) for rid, r in (s.get("relacoes") or {}).items()}}
                for s in evento.get("sujeitos", [])]
    eventos = [{"id": e.get("id"), "trilha_id": e.get("trilha_id"), "estado": e.get("estado"), "positivo_s": e.get("positivo_s"),
                "idade_ultima_evidencia_s": e.get("idade_ultima_evidencia_s"), "sustentacao_perdida": bool(e.get("sustentacao_perdida")),
                "transicao": e.get("transicao"), "motivo": e.get("motivo")} for e in evento.get("eventos", [])]
    descartados = [{"id": e.get("id"), "trilha_id": e.get("trilha_id"), "estado": "encerrado", "positivo_s": e.get("positivo_s"),
                    "idade_ultima_evidencia_s": e.get("idade_ultima_evidencia_s"), "sustentacao_perdida": False,
                    "transicao": "encerrado", "motivo": e.get("motivo")} for e in evento.get("encerramentos_por_descarte", [])]
    todos = eventos + descartados
    resumo = "ativo" if any(e["estado"] == "ativo" for e in eventos) else ("candidato" if any(e["estado"] == "candidato" for e in eventos) else "inativo")
    return {"seq": next(_sequencia), "camera_nome": None, "geracao": evento.get("geracao"), "tipo": "cena", "trilha_id": None,
            "quadro_id": evento.get("quadro_id"), "veredito": resumo, "idade_max_ms": None, "condicoes": [],
            "sujeitos": sujeitos, "eventos_regra": todos,
            "incompatibilidades": {k: list(v) for k, v in (evento.get("incompatibilidades") or {}).items()},
            "erros": dict(evento.get("erros") or {}), "receita": f"{receita.get('id')} v{receita.get('versao')}",
            "config_hash": receita.get("config_hash"),
            "versoes": [{"id": e.get("id"), "versao": e.get("versao")} for e in evento.get("especialistas", [])],
            "quando": datetime.now().strftime("%H:%M:%S")}


def formatar_evento(evento: dict[str, Any]) -> dict[str, Any]:
    """Evento do motor -> formato do contrato do feed (`sombra/feed`). `camera_nome` e preenchido por quem le."""
    if evento.get("tipo") == "cena":
        return _formatar_cena(evento)
    condicoes = [{"especialista": c.get("especialista"), "atributo": c.get("atributo"), "igual": c.get("igual"), "resultado": c.get("resultado"),
                  "motivo": c.get("motivo"), "idade_ms": c.get("idade_ms"), "reutilizado": bool(c.get("reutilizado")),
                  "falha_recente": c.get("falha_recente")} for c in evento.get("condicoes", [])]
    idades = [c["idade_ms"] for c in condicoes if isinstance(c["idade_ms"], (int, float))]
    receita = evento.get("receita") or {}
    return {"seq": next(_sequencia), "camera_nome": None, "geracao": evento.get("geracao"), "trilha_id": evento.get("trilha_id"), "quadro_id": evento.get("quadro_id"),
            "veredito": evento.get("veredito"), "idade_max_ms": max(idades) if idades else None, "condicoes": condicoes,
            "incompatibilidades": {k: list(v) for k, v in (evento.get("incompatibilidades") or {}).items()},
            "erros": dict(evento.get("erros") or {}), "receita": f"{receita.get('id')} v{receita.get('versao')}",
            "config_hash": receita.get("config_hash"),
            "versoes": [{"id": e.get("id"), "versao": e.get("versao")} for e in evento.get("especialistas", [])],
            "quando": datetime.now().strftime("%H:%M:%S")}


class BufferSombra:
    """Ultimos eventos de sombra de UMA camera, em memoria (limitado), ja no formato do feed."""

    def __init__(self, maximo: int = BUFFER_MAXIMO) -> None:
        self._itens: deque[dict[str, Any]] = deque(maxlen=maximo)
        self._lock = threading.Lock()

    def __call__(self, evento: dict[str, Any]) -> None:
        formatado = formatar_evento(evento)
        with self._lock:
            self._itens.append(formatado)

    def eventos(self) -> list[dict[str, Any]]:
        """Mais recentes primeiro."""
        with self._lock:
            return list(reversed(self._itens))

    def limpar(self) -> None:
        with self._lock:
            self._itens.clear()

    def descartar_geracoes_antes(self, geracao: int) -> int:
        """Nova sessao aceita: o feed passa a mostrar so a geracao vigente (as anteriores ficam no rastro). Devolve quantos sairam."""
        with self._lock:
            restantes = [e for e in self._itens if e.get("geracao") == geracao]
            sairam = len(self._itens) - len(restantes)
            self._itens.clear()
            self._itens.extend(restantes)
        return sairam

    def __len__(self) -> int:
        with self._lock:
            return len(self._itens)


@dataclasses.dataclass
class _Slot:
    camera_id: int
    porta: PortaSombra
    buffer: BufferSombra
    despacho: DespachoSombra | None = None
    motor: MotorSombra | None = None
    assinatura: tuple | None = None
    base: str = "desligada"             # desligada | montada | erro  ("aguardando"/"ativa" dependem da IA, ver `estado`)
    erro: str | None = None
    tentativa_em: float = 0.0
    decisao: int = 0                    # incrementa a cada desligamento explicito: monta em andamento e descartada
    montagem: int | None = None         # identidade da montagem VIGENTE; callbacks de montagens aposentadas nao publicam no feed
    tardios: int = 0                    # eventos de montagens aposentadas (nao vao ao feed; o rastro os marca `aposentado`)
    diagnostico: DiagnosticoMestre | None = None     # 05f: so existe com `cameras.diagnostico_ativo`; independe da sombra e de receita
    ajustes_chave: tuple | None = None               # (config_hash, receita) dos ajustes ja entregues a porta/diagnostico


@dataclasses.dataclass(frozen=True)
class _ConfigCamera:
    """Config vigente da camera lida do banco (so o que a reconciliacao usa)."""
    versao: int
    config_hash: str
    ajustes_json: str

    def ajustes(self) -> AjustesCamera | None:
        try:
            return ajustes_de_dict(json.loads(self.ajustes_json))
        except (ValueError, TypeError):
            return None


def _vinculo_unico(definicao: Any) -> dict[str, Any] | None:
    vs = definicao.get("vinculos") if isinstance(definicao, dict) else None
    vs = [v for v in vs if isinstance(v, dict)] if isinstance(vs, list) else []
    return vs[0] if len(vs) == 1 else None


class GerenteSombra:
    def __init__(self, database: Database, instalador: Any, executor: Executor, runtime_dir: Path, *,
                 ia_rodando: Callable[[int], bool] | None = None, buffer_maximo: int = BUFFER_MAXIMO,
                 relogio: Callable[[], float] = time.monotonic) -> None:
        self.database, self.instalador, self.executor = database, instalador, executor
        self._pasta = Path(runtime_dir) / "sombra"
        self._ia_rodando = ia_rodando or (lambda _cid: False)
        self._buffer_maximo, self._relogio = buffer_maximo, relogio
        self._slots: dict[int, _Slot] = {}
        self._aposentados: list[DespachoSombra] = []
        self._lock = threading.RLock()
        self._parado = False
        self._rev = 0                       # revisao da configuracao: sobe a cada mutacao commitada (ver `invalidar`)
        self._montagens = itertools.count(1)

    def invalidar(self) -> None:
        """Uma mutacao (receita, associacao, sombra, camera) acabou de ser COMMITADA: qualquer reconciliacao cujo snapshot do banco
        foi lido antes disto esta superada e nao pode publicar nada (ela e refeita com dados novos)."""
        with self._lock:
            self._rev += 1

    def definir_ia_rodando(self, fn: Callable[[int], bool]) -> None:
        self._ia_rodando = fn

    # ------------------------------------------------------------------ porta (entregue ao iniciar a IA)

    def _slot(self, camera_id: int) -> _Slot:
        with self._lock:
            s = self._slots.get(camera_id)
            if s is None:
                s = self._slots[camera_id] = _Slot(camera_id, PortaSombra(camera_id), BufferSombra(self._buffer_maximo))
                s.porta.ao_nova_geracao = s.buffer.descartar_geracoes_antes
            return s

    def porta(self, camera_id: int) -> PortaSombra:
        """Provider para `IaOrquestrador.sombra_provider`: SEMPRE a mesma instancia por camera."""
        return self._slot(camera_id).porta

    def diagnostico(self, camera_id: int) -> DiagnosticoMestre | None:
        """Diagnostico do mestre da camera (None se desligado ou ainda nao criado). So memoria."""
        with self._lock:
            s = self._slots.get(camera_id)
            return s.diagnostico if s is not None else None

    def ia_rodando(self, camera_id: int) -> bool:
        return bool(self._ia_rodando(camera_id))

    # ------------------------------------------------------------------ leitura

    def estado(self, camera_id: int) -> dict[str, Any]:
        with self._lock:
            s = self._slots.get(camera_id)
            if s is None or s.base == "desligada":
                return {"estado": "desligada", "erro": None}
            if s.base == "erro":
                return {"estado": "erro", "erro": s.erro}
        return {"estado": "ativa" if self._ia_rodando(camera_id) else "aguardando", "erro": None}

    def contadores(self, camera_id: int) -> dict[str, int]:
        with self._lock:
            s = self._slots.get(camera_id)
            d = s.despacho if s else None
        if d is None:
            return {"enfileirados": 0, "descartados": 0, "processados": 0}
        c = d.contadores
        return {"enfileirados": c["enfileirado"], "descartados": d.descartados, "processados": c["processado"]}

    def eventos(self, camera_id: int | None = None, limite: int = 30) -> list[dict[str, Any]]:
        """Eventos recentes (mais novos primeiro), com `camera_id` (o nome o chamador resolve)."""
        with self._lock:
            slots = [s for s in self._slots.values() if camera_id is None or s.camera_id == camera_id]
        todos = [{**e, "camera_id": s.camera_id} for s in slots for e in s.buffer.eventos()]
        todos.sort(key=lambda e: e["seq"], reverse=True)
        return todos[:limite]

    def ativas(self) -> int:
        with self._lock:
            return sum(1 for s in self._slots.values() if s.base == "montada")

    def threads_vivas(self) -> int:
        with self._lock:
            return sum(1 for s in self._slots.values() if s.despacho is not None and s.despacho.viva) + sum(1 for d in self._aposentados if d.viva)

    # ------------------------------------------------------------------ ciclo de vida

    def _aposentar(self, despacho: DespachoSombra | None) -> None:
        if despacho is None:
            return
        if not despacho.parar(timeout=0.3):                 # item em execucao: termina sozinho; juntado depois
            with self._lock:
                self._aposentados.append(despacho)

    def _juntar_aposentados(self, timeout: float) -> None:
        with self._lock:
            pendentes, self._aposentados = self._aposentados, []
        for d in pendentes:
            if not d.parar(timeout=timeout):
                with self._lock:
                    self._aposentados.append(d)

    def desligar_agora(self, camera_id: int) -> None:
        """Barato e sincrono (usado pelas rotas): a porta para de alimentar a sombra e o despacho e encerrado."""
        with self._lock:
            self._rev += 1
            s = self._slots.get(camera_id)
            if s is None:
                return
            s.decisao += 1
        self._desmontar(s, limpar=True)

    def _desmontar(self, s: _Slot, *, limpar: bool) -> None:
        with self._lock:
            s.porta.definir_alvo(None)
            antigo, s.despacho, s.motor, s.assinatura = s.despacho, None, None, None
            s.base, s.erro, s.montagem = "desligada", None, None
        self._aposentar(antigo)
        if limpar:
            s.buffer.limpar()

    def parar_tudo(self) -> None:
        """Encerramento do servico: nenhum despacho fica vivo."""
        with self._lock:
            self._parado = True
            slots = list(self._slots.values())
        for s in slots:
            self._desmontar(s, limpar=False)
            self._soltar_diagnostico(s)
        self._juntar_aposentados(timeout=5.0)

    # ------------------------------------------------------------------ 05f: ajustes da camera e diagnostico do mestre (leves)

    @staticmethod
    def _configs_vigentes(db: Any) -> dict[int, _ConfigCamera]:
        saida: dict[int, _ConfigCamera] = {}
        for cid, versao, h, aj in db.execute(select(CameraConfigVersao.camera_id, CameraConfigVersao.versao, CameraConfigVersao.config_hash,
                                                    CameraConfigVersao.ajustes_json).order_by(CameraConfigVersao.camera_id, CameraConfigVersao.versao)):
            saida[cid] = _ConfigCamera(versao, h, aj)
        return saida

    def _soltar_diagnostico(self, s: _Slot) -> None:
        with self._lock:
            antigo, s.diagnostico = s.diagnostico, None
            s.porta.definir_diagnostico(None)
        if antigo is not None:
            antigo.limpar()                                        # desligar = esquecer a memoria

    def _aplicar_leve(self, cid: int, diag_ativo: bool, cfg: _ConfigCamera | None, definicao: Any, vid: int | None, rev0: int | None) -> bool:
        """Entrega os ajustes efetivos (ROI e cadencia) a porta e liga/desliga o diagnostico. Barato e sem carregar nada; roda mesmo
        com a sombra desligada. False = o snapshot foi superado (nada foi aplicado)."""
        ajustes = cfg.ajustes() if cfg is not None else None
        efetivo = efetivos(ajustes, _vinculo_unico(definicao)) if ajustes is not None else None
        chave = (cfg.config_hash, vid) if cfg is not None else None
        antigo = None
        with self._lock:
            if rev0 is not None and self._rev != rev0:
                return False
            s = self._slots.get(cid)
            if s is None:
                if not diag_ativo and cfg is None:
                    return True
                s = self._slot(cid)
            mudou = s.ajustes_chave != chave
            if mudou:
                s.porta.definir_ajustes(efetivo)
                s.ajustes_chave = chave
            if diag_ativo:
                if s.diagnostico is None:
                    s.diagnostico = DiagnosticoMestre()
                    s.diagnostico.definir_ajustes(efetivo)
                    s.porta.definir_diagnostico(s.diagnostico)
                elif mudou:
                    s.diagnostico.limpar()                          # config nova: cena/contadores antigos nao valem
                    s.diagnostico.definir_ajustes(efetivo)
            elif s.diagnostico is not None:
                antigo, s.diagnostico = s.diagnostico, None
                s.porta.definir_diagnostico(None)
        if antigo is not None:
            antigo.limpar()
        return True

    def aplicar_rapido(self, camera_id: int) -> None:
        """Chamado pelas rotas DEPOIS do commit: aplica na hora ajustes e diagnostico da camera (nao monta nem reinicia nada).
        A montagem da sombra continua sendo do laco de reconciliacao."""
        try:
            with self.database.session() as db:
                c = db.get(Camera, camera_id)
                if c is None:
                    return
                diag, vid = bool(c.diagnostico_ativo), c.receita_versao_id
                cfg = self._configs_vigentes(db).get(camera_id)
                v = db.get(ReceitaVersao, vid) if vid else None
                try:
                    definicao = json.loads(v.definicao_json) if v is not None else None
                except ValueError:
                    definicao = None
            self._aplicar_leve(camera_id, diag, cfg, definicao, vid, None)
        except Exception:  # noqa: BLE001 -- a reconciliacao periodica cobre
            logger.exception("Falha ao aplicar ajustes/diagnostico da camera %s", camera_id)

    # ------------------------------------------------------------------ reconciliacao (roda no laco proprio)

    def reconcile(self) -> None:
        """Alinha a sombra com o banco. O snapshot e vinculado a uma revisao: se uma mutacao for commitada depois de le-lo, o ciclo
        e abandonado antes de publicar qualquer coisa e refeito (ate 3 vezes; o proximo ciclo periodico cobre o resto)."""
        for _ in range(3):
            if self._parado:
                return
            with self._lock:
                rev0 = self._rev
            if self._ciclo(rev0):
                return

    def _ciclo(self, rev0: int) -> bool:
        self._juntar_aposentados(timeout=0.2)
        with self.database.session() as db:
            cameras = [(c.id, c.sombra_ativa, c.receita_versao_id, bool(c.diagnostico_ativo)) for c in db.scalars(select(Camera).order_by(Camera.id)).unique().all()]
            configs = self._configs_vigentes(db)
            ids_versoes = {v for _c, _a, v, _d in cameras if v}
            versoes: dict[int, tuple[str, int, dict[str, Any] | None, str]] = {}
            if ids_versoes:
                for v, nome in db.execute(select(ReceitaVersao, Receita.nome).join(Receita, Receita.id == ReceitaVersao.receita_id).where(ReceitaVersao.id.in_(ids_versoes))):
                    try:
                        definicao = json.loads(v.definicao_json)
                    except ValueError:
                        definicao = None
                    versoes[v.id] = (nome, v.versao, definicao, v.config_hash)
        existentes = {cid for cid, _a, _v, _d in cameras}
        with self._lock:
            sumiram = [s for cid, s in self._slots.items() if cid not in existentes]
        for s in sumiram:
            with self._lock:
                if self._rev != rev0:
                    return False
                s.decisao += 1
            self._desmontar(s, limpar=True)
            self._soltar_diagnostico(s)
        for cid, ativa, vid, diag in cameras:
            if self._parado:
                return True
            try:
                if not self._reconciliar_camera(cid, ativa, vid, versoes.get(vid) if vid else None, rev0, diag=diag, cfg=configs.get(cid)):
                    return False                                   # snapshot superado: refaz com dados novos
            except Exception:  # noqa: BLE001 -- uma camera nunca impede as outras
                logger.exception("Falha ao reconciliar a sombra da camera %s", cid)
        return True

    def _reconciliar_camera(self, cid: int, ativa: bool, vid: int | None, versao: tuple[str, int, dict[str, Any] | None, str] | None, rev0: int,
                            *, diag: bool = False, cfg: _ConfigCamera | None = None) -> bool:
        """False = o snapshot (rev0) foi superado por uma mutacao posterior: nada foi publicado."""
        if not self._aplicar_leve(cid, diag, cfg, versao[2] if versao else None, vid, rev0):
            return False
        if not ativa:
            with self._lock:
                if self._rev != rev0:
                    return False
                s = self._slots.get(cid)
                existe = s is not None and s.base != "desligada"
                if existe:
                    s.decisao += 1
            if existe:
                self._desmontar(s, limpar=True)
            return True
        if self._rev != rev0:
            return False
        s = self._slot(cid)
        if vid is None or versao is None or versao[2] is None:
            return self._marcar_erro(s, "Sem receita associada." if vid is None else "Versão da receita ilegível.", rev0)
        _nome, _num, definicao, config_hash = versao
        contratos = {v.get("especialista"): contrato_leve(self.instalador, v.get("especialista")) for v in definicao.get("vinculos", []) if isinstance(v, dict) and isinstance(v.get("especialista"), str)}
        problemas = validar_receita(definicao, contratos)
        if problemas:
            return self._marcar_erro(s, "; ".join(problemas)[:300], rev0)
        ajustes = cfg.ajustes() if cfg is not None else None
        if ajustes is not None:                          # margem/orcamento/validade da camera aplicados a receita (copia; a original nao muda)
            try:
                definicao = receita_efetiva(definicao, ajustes, contratos)
            except Exception as exc:  # noqa: BLE001
                return self._marcar_erro(s, f"Ajustes da câmera inaplicáveis: {exc}"[:300], rev0)
        # a identidade de montagem inclui o hash da config da camera: config nova = montagem nova (estado/atributos zerados)
        assinatura = (vid, config_hash, cfg.config_hash if cfg is not None else None,
                      tuple(sorted((pid, c["versao"], c["identidade"]) for pid, c in contratos.items() if c)))
        with self._lock:
            if self._rev != rev0:
                return False
            if s.despacho is not None and s.assinatura == assinatura and not s.despacho.parado:
                s.base, s.erro = "montada", None
                return True
            if s.base == "erro" and s.assinatura == assinatura and self._relogio() - s.tentativa_em < REPETIR_ERRO_APOS_S:
                return True                                         # falha de carga recente: nao martela o disco/modelo
            decisao = s.decisao
        return self._montar(s, definicao, assinatura, decisao, rev0, ajustes)

    def _marcar_erro(self, s: _Slot, texto: str, rev0: int | None = None) -> bool:
        with self._lock:
            if rev0 is not None and self._rev != rev0:
                return False
            s.porta.definir_alvo(None)
            antigo, s.despacho, s.motor = s.despacho, None, None
            s.assinatura, s.base, s.erro, s.montagem = None, "erro", texto, None
        self._aposentar(antigo)
        return True

    def _montar(self, s: _Slot, definicao: dict[str, Any], assinatura: tuple, decisao: int, rev0: int, ajustes: AjustesCamera | None = None) -> bool:
        """Carrega modelos (lento!) e constroi motor + despacho. So roda no laco de reconciliacao."""
        try:
            for v in definicao["vinculos"]:
                try:
                    self.executor.contrato(v["especialista"])
                except ErroExecucao as exc:
                    raise RuntimeError(f"{v['especialista']}: {exc.mensagem}") from None
            rastro = RastroJsonl(self._pasta / f"camera-{s.camera_id}.jsonl")
            mid = next(self._montagens)

            def destino(evento: dict[str, Any]) -> None:
                # So a montagem VIGENTE publica no feed. Um processamento que terminou depois de desligar/reconfigurar nao volta
                # ao feed como atual: fica so no rastro, marcado `aposentado`, e e contado em `s.tardios`.
                with self._lock:
                    motivo = None
                    if s.montagem != mid:
                        motivo = "montagem_aposentada"
                    elif evento.get("geracao") != s.porta.geracao_vigente:
                        motivo = "geracao_obsoleta"              # a sessao mudou enquanto este trabalho executava
                    if motivo is None:
                        s.buffer(evento)
                    else:
                        s.tardios += 1
                try:
                    rastro(evento if motivo is None else {**evento, "aposentado": True, "motivo_aposentado": motivo})
                except Exception:  # noqa: BLE001 -- disco cheio etc.: o buffer em memoria continua
                    logger.warning("Falha ao gravar o rastro de sombra da camera %s", s.camera_id)

            motor = MotorSombra(self.executor, habilitado=True, destino=destino)
            motor.configurar([definicao], {s.porta.ref: {"receita": definicao["id"], "versao": definicao["versao"]}})
            if ajustes is not None:                      # crop da camera (h/v/minimo); sem config = caminho antigo, inalterado
                motor.definir_ajustes(s.porta.ref, ajustes)
            despacho = DespachoSombra(motor, nome=f"motor-sombra-{s.camera_id}")
        except Exception as exc:  # noqa: BLE001 -- ValueError da configuracao, falha de carga do modelo...
            with self._lock:
                if s.decisao != decisao or self._rev != rev0:
                    return self._rev == rev0
                s.tentativa_em = self._relogio()
            if not self._marcar_erro(s, f"{type(exc).__name__}: {exc}"[:300], rev0):
                return False
            with self._lock:
                s.assinatura = assinatura
            return True
        with self._lock:
            superada = self._rev != rev0
            if s.decisao != decisao or self._parado or superada:     # desligada/mutada/servico parado durante a carga
                descartar, antigo = despacho, None
            else:
                descartar = None
                s.porta.definir_alvo(despacho)
                antigo, s.despacho, s.motor, s.assinatura, s.base, s.erro, s.montagem = s.despacho, despacho, motor, assinatura, "montada", None, mid
                if antigo is not None:
                    s.buffer.limpar()            # config/receita nova: o feed mostra so a vigente (a anterior fica no rastro, como na troca de geracao)
        if descartar is not None:
            descartar.parar(timeout=1.0)
        self._aposentar(antigo)
        return not superada


def estado_para_camera(gerente: GerenteSombra | None, camera: Any) -> tuple[str, str | None]:
    """(estado, erro) da sombra para exibir: desligada | aguardando | ativa | erro. Ligada no banco mas ainda nao
    reconciliada conta como `aguardando`; sem IA rodando tambem (nao e erro)."""
    if not camera.sombra_ativa:
        return "desligada", None
    e = gerente.estado(camera.id) if gerente is not None else {"estado": "desligada", "erro": None}
    if e["estado"] == "desligada":
        return "aguardando", None
    return e["estado"], e["erro"]


def visao_feed(db: Any, gerente: GerenteSombra | None, camera_id: int | None = None, limite: int = 30) -> dict[str, Any]:
    """Contexto `sombra` do contrato: eventos recentes (diagnostico, NAO sao alertas) + estado/contadores por camera com sombra ligada."""
    cameras = list(db.scalars(select(Camera).order_by(Camera.nome)).unique().all())
    nomes = {c.id: c.nome for c in cameras}
    eventos = []
    if gerente is not None:
        for e in gerente.eventos(camera_id, limite):
            item = {k: v for k, v in e.items() if k not in ("seq", "camera_id")}
            item["camera_nome"] = nomes.get(e["camera_id"], f"câmera {e['camera_id']}")
            eventos.append(item)
    linhas = []
    for c in cameras:
        if not c.sombra_ativa or (camera_id is not None and c.id != camera_id):
            continue
        estado, erro = estado_para_camera(gerente, c)
        contadores = gerente.contadores(c.id) if gerente is not None else {"enfileirados": 0, "descartados": 0, "processados": 0}
        linhas.append({"id": c.id, "nome": c.nome, "estado": estado, "erro": erro, **contadores})
    return {"eventos": eventos, "cameras": linhas}
