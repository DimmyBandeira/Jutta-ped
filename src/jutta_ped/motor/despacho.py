"""Despacho DESACOPLADO da sombra: o laco da camera so enfileira; a execucao roda numa thread propria.

Fila limitada por camera com descarte do mais ANTIGO (fica o quadro mais recente): especialista lento, destino de rastro
bloqueado ou lock do motor ocupado atrasam apenas a sombra, nunca a publicacao operacional. `processar` e nao bloqueante.
Ciclo de vida: `parar()` descarta o que esta pendente, nao aceita mais nada e junta a thread (com prazo); item de geracao
(sessao) anterior a ja vista por camera nunca e processado. Parar duas vezes e seguro.
Ainda e uma ponte em processo unico; fila compartilhada com justica entre cameras e nos remotos pertencem a etapa 04.
"""
from __future__ import annotations

import threading
from collections import Counter, OrderedDict
from collections.abc import Sequence
from typing import Any

from src.jutta_ped.motor.cena import Cena
from src.jutta_ped.motor.motor import MotorSombra
from src.jutta_ped.motor.observacao import Observacao

PRAZO_JUNCAO_S = 5.0


class DespachoSombra:
    seguro_para_ao_vivo = True              # o runtime so aceita motor SINCRONO em fonte de arquivo

    def __init__(self, motor: MotorSombra, *, max_pendentes_por_camera: int = 1, nome: str = "motor-sombra") -> None:
        self.motor = motor
        self._max = max(1, int(max_pendentes_por_camera))
        self._pendentes: OrderedDict[str, list[tuple[int, Any, Any, Cena | None]]] = OrderedDict()
        self._geracao_max: dict[str, int] = {}
        self._encerrar: dict[str, str] = {}              # camera_ref -> motivo: sessao terminou; o worker descarta o estado das regras
        self._cond = threading.Condition()
        self._parar = False
        self._executando = False
        self.contadores: Counter[str] = Counter()
        self._thread = threading.Thread(target=self._laco, name=nome, daemon=True)
        self._thread.start()

    @property
    def habilitado(self) -> bool:
        return self.motor.habilitado

    @property
    def viva(self) -> bool:
        return self._thread.is_alive()

    @property
    def parado(self) -> bool:
        return self._parar

    @property
    def descartados(self) -> int:
        c = self.contadores
        return c["descartado_fila_cheia"] + c["descartado_geracao_obsoleta"] + c["recusado_geracao_obsoleta"] + c["descartado_ao_parar"]

    def processar(self, camera_ref: str, geracao: int, quadro: Any, observacoes: Sequence[Observacao]) -> list:
        """Nao bloqueante. Devolve [] (os eventos saem pelo `destino` do motor). O chamador nao deve alterar `quadro` depois."""
        return self._enfileirar(camera_ref, geracao, (geracao, quadro, list(observacoes), None))

    def processar_cena(self, cena: Cena) -> list:
        """Como `processar`, mas entrega a Cena inteira a `motor.processar_cena(cena)`. Nao bloqueante.

        Mesma fila limitada por camera, compartilhada com `processar`: as cenas de uma camera chegam ao motor EM ORDEM de
        entrada. Descartar por fila cheia so remove AMOSTRAS (o mais antigo): para regras temporais isso apenas aumenta
        os intervalos entre amostras (a duracao e medida por `recebido_em`, nunca por contagem de quadros).
        """
        return self._enfileirar(cena.camera_ref, cena.geracao, (cena.geracao, None, None, cena))

    def encerrar_camera(self, camera_ref: str, motivo: str = "sessao_encerrada") -> None:
        """Nao bloqueante: a sessao da camera terminou. O worker, DEPOIS de processar o que ja estava pendente dela, manda o
        motor descartar o estado das regras (os eventos ativos sao reportados como encerrados por descarte, nao como resolvidos)."""
        with self._cond:
            if self._parar:
                return
            self._encerrar[camera_ref] = motivo
            self._cond.notify_all()

    def encerrar_sessao(self, geracao: int) -> None:
        """A sessao `geracao` terminou: encerra as cameras cuja geracao vigente ainda e essa (uma sessao antiga terminando
        atrasada nao encerra a nova). Nao bloqueante."""
        with self._cond:
            refs = [ref for ref, g in self._geracao_max.items() if g == geracao]
        for ref in refs:
            self.encerrar_camera(ref, "sessao_encerrada")

    def _enfileirar(self, camera_ref: str, geracao: int, item: tuple[int, Any, Any, Cena | None]) -> list:
        if self._parar or not self.motor.habilitado:
            return []
        with self._cond:
            if self._parar:
                return []
            vista = self._geracao_max.get(camera_ref)
            if vista is not None and geracao < vista:
                self.contadores["recusado_geracao_obsoleta"] += 1          # sessao anterior chegando atrasada
                return []
            fila = self._pendentes.setdefault(camera_ref, [])
            if vista is None or geracao > vista:
                self._geracao_max[camera_ref] = geracao
                if fila:                                                     # pendentes de uma sessao que ja acabou
                    self.contadores["descartado_geracao_obsoleta"] += len(fila)
                    fila.clear()
            fila.append(item)
            self.contadores["enfileirado"] += 1
            while len(fila) > self._max:
                fila.pop(0)
                self.contadores["descartado_fila_cheia"] += 1
            self._cond.notify_all()
        return []

    def _laco(self) -> None:
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._parar or any(self._pendentes.values()) or bool(self._encerrar))
                if self._parar:
                    return
                pendentes = [c for c, f in self._pendentes.items() if f]
                if not pendentes:                                  # so ha encerramentos a aplicar
                    ref, motivo = self._encerrar.popitem()
                    self._executando = True
                    camera = geracao = quadro = obs = cena = None
                else:
                    ref = motivo = None
                    camera = pendentes[0]
                    geracao, quadro, obs, cena = self._pendentes[camera].pop(0)
                    self._pendentes.move_to_end(camera)            # revezamento simples entre cameras
                    self._executando = True
            if ref is not None:
                try:
                    fn = getattr(self.motor, "encerrar_camera", None)
                    if fn is not None:
                        fn(ref, motivo)
                    self.contadores["encerrado"] += 1
                except Exception:  # noqa: BLE001
                    self.contadores["erro_no_motor"] += 1
                finally:
                    with self._cond:
                        self._executando = False
                        self._cond.notify_all()
                continue
            try:
                if cena is not None:
                    self.motor.processar_cena(cena)
                else:
                    self.motor.processar(camera, geracao, quadro, obs)
                self.contadores["processado"] += 1
            except Exception:  # noqa: BLE001 -- a sombra nunca derruba a si mesma
                self.contadores["erro_no_motor"] += 1
            finally:
                with self._cond:
                    self._executando = False
                    self._cond.notify_all()

    def aguardar_ociosa(self, timeout: float = 5.0) -> bool:
        """Util em testes/encerramento: espera a fila esvaziar E o item em execucao terminar. False se estourar `timeout`."""
        with self._cond:
            return self._cond.wait_for(lambda: self._parar or (not any(self._pendentes.values()) and not self._encerrar and not self._executando), timeout)

    def parar(self, timeout: float | None = PRAZO_JUNCAO_S) -> bool:
        """Descarta o pendente, recusa novos itens e junta a thread. Idempotente. True se a thread terminou dentro do prazo
        (um item em execucao e concluido; nada novo comeca). Chamado de dentro do proprio worker, nao junta."""
        with self._cond:
            if not self._parar:
                self._parar = True
                self.contadores["descartado_ao_parar"] += sum(len(f) for f in self._pendentes.values())
                self._pendentes.clear()
            self._cond.notify_all()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=timeout)
        return not self._thread.is_alive()
