"""Motor em sombra: observacoes de um quadro -> recortes -> especialistas ativos -> cache com validade -> receita -> evento de sombra.

Garantias:
* Desabilitado por padrao (`habilitado=False`): nao recorta, nao executa, nao emite nada. A habilitacao e explicita.
* Nunca emite alerta operacional: so eventos com `modo="sombra"` e `alerta_real=False`, entregues a `destino`.
* Resultado de sessao (geracao) anterior ou mais antigo que o ja guardado e rejeitado; trocar de geracao limpa a camera.
* Trocar a versao/identidade ativa de um especialista descarta os atributos dele (sem mistura de versoes).
* Atributo ausente, expirado, com erro, atrasado ou indeterminado vira INDETERMINADO na receita (nunca "nao").
* O orcamento de cada pedido e o da vinculacao menos o tempo desde a CAPTURA.
Execucao sincrona no chamador (uma camera por vez); fila compartilhada e nos remotos ficam para etapas futuras.
"""
from __future__ import annotations

import dataclasses
import json
import math
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from src.jutta_ped.especialistas.executor import ErroExecucao, Executor, Pedido
from src.jutta_ped.motor.ajustes import AjustesCamera, efetivos
from src.jutta_ped.motor.cena import Cena, observacoes_de_cena
from src.jutta_ped.motor.observacao import Observacao, extrair_recorte
from src.jutta_ped.motor.receita import (
    INDETERMINADO,
    NAO,
    ORCAMENTO_PADRAO_MS,
    SIM,
    combinar,
    contrato_da_receita,
    hash_config,
    validar_receita,
    validar_vinculo,
)
from src.jutta_ped.motor.regras import M_GERACAO, M_RECONFIGURADO, M_SESSAO, AvaliadorRegras

VERSAO_EVENTO = 1
PODA_APOS_S = 60.0
POLITICA_CACHE = "manter_ate_expirar"     # atributo valido segue valendo ate expirar; erro novo nao renova nem apaga (aparece no evento)


class RastroJsonl:
    """Destino de eventos de sombra: uma linha JSON por evento, num arquivo PROPRIO (nunca o log de alertas reais)."""

    def __init__(self, caminho: Path | str) -> None:
        self.caminho = Path(caminho)
        self._lock = threading.Lock()

    def __call__(self, evento: dict[str, Any]) -> None:
        with self._lock:
            self.caminho.parent.mkdir(parents=True, exist_ok=True)
            with self.caminho.open("a", encoding="utf-8") as f:
                f.write(json.dumps(evento, ensure_ascii=False) + "\n")


class MotorSombra:
    def __init__(self, executor: Executor, *, habilitado: bool = False, relogio: Callable[[], float] = time.monotonic,
                 destino: Callable[[dict[str, Any]], None] | None = None, fontes: tuple[str, ...] = ("visivel",)) -> None:
        self.executor, self.habilitado, self._relogio, self._destino, self._fontes = executor, habilitado, relogio, destino, fontes
        self._receitas: dict[tuple[str, str], dict[str, Any]] = {}
        self._camera: dict[str, tuple[str, str]] = {}
        self._geracao: dict[str, int] = {}
        self._cache: dict[str, dict[tuple[int, str, str], dict[str, Any]]] = {}
        self._vigente: dict[str, tuple[str, str]] = {}          # especialista -> (versao, identidade) em uso
        self._compat: dict[tuple[str, ...], list[str]] = {}
        self._regras: dict[str, AvaliadorRegras] = {}            # contrato 2: estado temporal por camera (geracao/config vigentes)
        self._regras_hash: dict[str, str] = {}
        self._ajustes: dict[str, AjustesCamera] = {}             # 05f: ajustes de crop por camera (opcional; sem entrada = comportamento antigo)
        self._encerrados: dict[str, list[dict[str, Any]]] = {}    # encerramentos por descarte, a relatar no proximo evento da camera
        self.contadores: Counter[str] = Counter()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ configuracao

    def _contratos(self, receita: Any) -> dict[str, dict[str, Any] | None]:
        saida: dict[str, dict[str, Any] | None] = {}
        vinculos = receita.get("vinculos") if isinstance(receita, dict) else None
        for v in vinculos if isinstance(vinculos, list) else []:
            pid = v.get("especialista") if isinstance(v, dict) else None
            if isinstance(pid, str):
                try:
                    saida[pid] = self.executor.contrato(pid)
                except ErroExecucao:
                    saida[pid] = None
        return saida

    def configurar(self, receitas: Sequence[dict[str, Any]], associacoes: dict[str, dict[str, str]]) -> None:
        """Registra receitas e a associacao camera -> (receita, versao). Valida tudo ANTES de aplicar; ValueError lista os problemas."""
        problemas: list[str] = []
        novas: dict[tuple[str, str], dict[str, Any]] = {}
        for r in receitas:
            ps = validar_receita(r, self._contratos(r), self._fontes)
            if not ps and (r["id"], r["versao"]) in novas:
                ps = ["receita duplicada (mesmo id e versao)"]
            problemas += [f"receita {r.get('id') if isinstance(r, dict) else r!r}: {x}" for x in ps]
            if not ps:
                try:
                    novas[(r["id"], r["versao"])] = json.loads(json.dumps(r))
                except (TypeError, ValueError):
                    problemas.append(f"receita {r['id']}: nao serializavel em JSON")
        novas_cam: dict[str, tuple[str, str]] = {}
        for cam, a in associacoes.items():
            chave = (a.get("receita"), a.get("versao")) if isinstance(a, dict) else (None, None)
            if not isinstance(chave[0], str) or not isinstance(chave[1], str) or chave not in novas:
                problemas.append(f"camera '{cam}': receita/versao {chave} inexistente ou invalida")
            else:
                novas_cam[cam] = chave  # type: ignore[assignment]
        if problemas:
            raise ValueError("; ".join(problemas))
        with self._lock:
            self._receitas, self._camera = novas, novas_cam
            self._cache.clear()                  # atributos de outra receita/parametros/margem nao podem sustentar a nova
            for cam in list(self._regras):       # contrato 2: outra receita/versao/config descarta o estado temporal ("reconfigurado")
                chave = novas_cam.get(cam)
                if chave is None or hash_config(novas[chave], chave) != self._regras_hash.get(cam):
                    self._descartar_regras(cam, M_RECONFIGURADO)
            for cam in [c for c in self._encerrados if c not in novas_cam]:
                del self._encerrados[cam]

    def definir_ajustes(self, camera_ref: str, ajustes: AjustesCamera | None) -> None:
        """(05f) Ajustes de CROP da camera (perfil/margem_h/margem_v/minimo_px; `efetivos` com o vinculo da receita). None remove.
        Mudar o crop descarta os atributos em cache da camera (nao ha mistura de recortes diferentes). A ROI nao entra aqui:
        vem marcada na Cena (`Deteccao.na_roi`). O orcamento/validade chegam pela receita (`receita_efetiva`)."""
        with self._lock:
            antigo = self._ajustes.get(camera_ref)
            if ajustes is None:
                self._ajustes.pop(camera_ref, None)
            else:
                self._ajustes[camera_ref] = ajustes
            if (antigo.crop if antigo else None) != (ajustes.crop if ajustes else None) or (antigo.perfil if antigo else None) != (ajustes.perfil if ajustes else None):
                if camera_ref in self._cache:
                    self._cache[camera_ref] = {}

    # ------------------------------------------------------------------ processamento

    def processar(self, camera_ref: str, geracao: int, quadro: Any, observacoes: Sequence[Observacao]) -> list[dict[str, Any]]:
        """Um quadro de uma camera. Devolve (e entrega a `destino`) um evento de sombra por observacao valida."""
        if not self.habilitado:
            return []
        with self._lock:
            chave = self._camera.get(camera_ref)
            receita = self._receitas.get(chave) if chave else None
            if receita is None:
                return []
            if contrato_da_receita(receita) == 2:
                self.contadores["receita_contrato2_exige_cena"] += 1      # contrato 2 so avanca por `processar_cena`
                return []
            atual = self._geracao.get(camera_ref)
            if atual is not None and geracao < atual:
                self.contadores["descartado_sessao_anterior"] += 1
                return []
            if atual is None or geracao > atual:
                self._geracao[camera_ref] = geracao
                self._cache[camera_ref] = {}
            self._cache.setdefault(camera_ref, {})
            agora = self._relogio()
            validas = self._filtrar_observacoes(camera_ref, geracao, observacoes, agora, por_classe=False)
            ctx = self._despachar(camera_ref, geracao, receita, quadro, validas, agora)
            eventos = [self._avaliar(camera_ref, geracao, receita, chave, o, ctx, self._relogio()) for o in validas]
            self._podar(camera_ref, self._relogio())
        for e in eventos:
            if self._destino is not None:
                try:
                    self._destino(e)
                except Exception:  # noqa: BLE001 -- falha do rastro (disco cheio...) nao derruba o laco da camera
                    self.contadores["falha_ao_gravar_evento"] += 1
        return eventos

    def _filtrar_observacoes(self, camera_ref: str, geracao: int, observacoes: Sequence[Observacao], agora: float, *, por_classe: bool) -> list[Observacao]:
        """Descarta (e conta) observacao malformada, de outra camera/geracao, do futuro ou com trilha repetida no quadro
        (com `por_classe`, a repeticao so conta dentro da mesma classe: IDs de classes diferentes sao sujeitos diferentes)."""
        validas: list[Observacao] = []
        vistas: set[Any] = set()
        for o in observacoes:
            if (not isinstance(o, Observacao) or o.camera_ref != camera_ref or o.geracao != geracao or isinstance(o.trilha_id, bool)
                    or not isinstance(o.trilha_id, int) or not isinstance(o.capturado_em, (int, float)) or not math.isfinite(o.capturado_em)
                    or o.capturado_em > agora + 1e-6 or (o.trilha_id, o.classe if por_classe else None) in vistas):
                self.contadores["observacao_rejeitada"] += 1          # inclui trilha repetida no mesmo quadro
            else:
                vistas.add((o.trilha_id, o.classe if por_classe else None))
                validas.append(o)
        return validas

    def _despachar(self, camera_ref: str, geracao: int, receita: dict[str, Any], quadro: Any, obs: list[Observacao], agora: float) -> dict[str, Any]:
        ctx: dict[str, Any] = {"especialistas": {}, "erros": {}, "descartes": Counter(), "incompatibilidades": {}}
        cache = self._cache[camera_ref]
        for v in receita["vinculos"]:
            pid = v["especialista"]
            try:
                c = self.executor.contrato(pid)
                alvo_decl = c["entrada"]["alvo"]
            except Exception as exc:  # noqa: BLE001 -- integridade/desativacao/manifesto ilegivel: sem ativo confiavel
                ctx["erros"][pid] = getattr(exc, "codigo", f"E_INTERNO:{type(exc).__name__}")
                self._esquecer(pid)
                continue
            ident = (c["versao"], c["identidade"])
            if self._vigente.get(pid) not in (None, ident):                    # ativacao/reversao: nada de misturar versoes
                self._esquecer(pid)
                self.contadores["cache_limpo_por_troca_de_versao"] += 1
            self._vigente[pid] = ident
            ctx["especialistas"][pid] = {"id": pid, "versao": c["versao"], "identidade": c["identidade"]}
            if v.get("versoes_aceitas") is not None and c["versao"] not in v["versoes_aceitas"]:
                ctx["erros"][pid] = "versao_nao_aceita"
                continue
            incompat = self._compatibilidade(receita, pid, c)
            if incompat:
                ctx["erros"][pid] = "E_CONTRATO_INCOMPATIVEL"
                ctx["incompatibilidades"][pid] = incompat
                self._esquecer(pid, manter_vigente=ident)
                continue
            alvo, orcamento = alvo_decl["classe_mestre"], float(v.get("orcamento_ms", ORCAMENTO_PADRAO_MS))
            pedidos: list[Pedido] = []
            enviados: dict[str, Observacao] = {}
            for o in obs:
                if o.classe != alvo:
                    continue
                aj = self._ajustes.get(camera_ref)
                if aj is None:
                    rec = extrair_recorte(quadro, o.bbox_xyxy, float(v.get("margem_recorte", 0.0)))
                else:
                    ef = efetivos(aj, v)
                    rec = extrair_recorte(quadro, o.bbox_xyxy, 0.0, margem_h=ef.crop.margem_h, margem_v=ef.crop.margem_v, minimo_px=ef.crop.minimo_px or 0)
                if rec is None:
                    ctx["descartes"]["recorte_vazio"] += 1
                    ctx["erros"][f"{pid}#{o.trilha_id}"] = "recorte_vazio"
                    continue
                restante = orcamento - (self._relogio() - o.capturado_em) * 1000.0     # relido: vinculos anteriores gastam o mesmo orcamento
                if restante <= 0:
                    ctx["descartes"]["orcamento_vencido_antes_de_enviar"] += 1
                    ctx["erros"][f"{pid}#{o.trilha_id}"] = "atrasado"
                    continue
                p = Pedido(pedido_id=f"{camera_ref}|{geracao}|{o.quadro_id}|{o.trilha_id}|{pid}", recorte=rec.imagem, camera_ref=camera_ref,
                           geracao=geracao, trilha_id=o.trilha_id, quadro_id=o.quadro_id, fonte=c["entrada"]["fonte"],
                           altura_pessoa_px=rec.altura_alvo_px, restante_ms=restante, parametros=dict(v.get("parametros") or {}))
                pedidos.append(p)
                enviados[p.pedido_id] = o
            if not pedidos:
                continue
            try:
                resultados = self.executor.executar(pid, pedidos)
            except Exception as exc:  # noqa: BLE001 -- o executor nao deve derrubar o laco da camera
                ctx["erros"][pid] = f"E_INTERNO:{type(exc).__name__}"
                continue
            fim = self._relogio()
            for r in resultados:
                o = enviados.get(r.get("pedido_id"))
                if o is None:
                    self.contadores["resultado_desconhecido"] += 1
                    continue
                if r["estado"] != "ok":
                    ctx["erros"][f"{pid}#{o.trilha_id}"] = r["erro"]["codigo"]
                    continue
                if (r["geracao"], r["trilha_id"], r["quadro_id"]) != (o.geracao, o.trilha_id, o.quadro_id) or r["especialista"].get("versao") != c["versao"]:
                    self.contadores["resultado_incoerente"] += 1
                    ctx["erros"][f"{pid}#{o.trilha_id}"] = "resultado_incoerente"
                    continue
                if (fim - o.capturado_em) * 1000.0 > orcamento:
                    ctx["erros"][f"{pid}#{o.trilha_id}"] = "atrasado"
                    continue
                self._guardar(cache, pid, c, o, r["atributos"])
        return ctx

    def _esquecer(self, pid: str, manter_vigente: tuple[str, str] | None = None) -> None:
        """Descarta todo atributo do especialista (troca de versao, perda do ativo, incompatibilidade) e atualiza a versao vigente."""
        for cam in self._cache.values():
            for k in [k for k in cam if k[1] == pid]:
                del cam[k]
        if manter_vigente is None:
            self._vigente.pop(pid, None)
        else:
            self._vigente[pid] = manter_vigente

    def _compatibilidade(self, receita: dict[str, Any], pid: str, c: dict[str, Any]) -> list[str]:
        """Problemas da receita frente ao contrato ATIVO agora (memorizado por receita+versao+identidade: custo so na troca)."""
        chave = (receita["id"], receita["versao"], pid, c["versao"], c["identidade"])
        if chave not in self._compat:
            self._compat[chave] = validar_vinculo(receita, pid, c, self._fontes)
        return self._compat[chave]

    def _podar(self, camera_ref: str, agora: float) -> None:
        """Remove do cache da camera entradas expiradas ha mais de PODA_APOS_S (evita crescimento sem limite com trilhas efemeras).
        A tolerancia mantem o motivo 'expirado' (em vez de 'ausente') nos quadros logo apos o vencimento."""
        cache = self._cache[camera_ref]
        for k in [k for k, e in cache.items() if agora - e["capturado_em"] - e["validade_ms"] / 1000.0 > PODA_APOS_S]:
            del cache[k]

    def _guardar(self, cache: dict[tuple[int, str, str], dict[str, Any]], pid: str, c: dict[str, Any], o: Observacao, atributos: list[dict[str, Any]]) -> None:
        for a in atributos:
            k = (o.trilha_id, pid, a["nome"])
            antigo = cache.get(k)
            if antigo is not None and (o.capturado_em, o.quadro_id) <= (antigo["capturado_em"], antigo["quadro_id"]):
                self.contadores["resultado_mais_antigo_rejeitado"] += 1
                continue
            cache[k] = {"atributo": a, "capturado_em": o.capturado_em, "quadro_id": o.quadro_id, "versao": c["versao"],
                        "validade_ms": c["atributos"][a["nome"]]["validade_ms"]}

    # ------------------------------------------------------------------ receita e evento

    @staticmethod
    def _ler(cache: dict[tuple[int, str, str], dict[str, Any]], pid: str, nome: str, trilha: int, validade_cond: Any, ctx: dict[str, Any],
             agora: float) -> tuple[Any, str | None, dict[str, Any] | None]:
        """Valor DEFINIDO e valido do atributo (trilha, especialista, nome) ou (None, motivo_de_indeterminado, entrada)."""
        e = cache.get((trilha, pid, nome))
        if e is None:
            return None, ctx["erros"].get(f"{pid}#{trilha}") or ctx["erros"].get(pid) or "ausente", None
        idade = (agora - e["capturado_em"]) * 1000.0
        validade = min(e["validade_ms"], validade_cond or e["validade_ms"])
        if idade > validade:
            return None, "expirado", e
        if e["atributo"]["estado"] != "definido":
            return None, e["atributo"].get("motivo", "indeterminado"), e
        return e["atributo"]["valor"], None, e

    def _avaliar_condicao(self, cache: dict[tuple[int, str, str], dict[str, Any]], cond: dict[str, Any], trilha: int, quadro_id: int,
                          ctx: dict[str, Any], agora: float) -> tuple[str, dict[str, Any]]:
        """Estado (sim/nao/indeterminado) de UMA condicao para UMA trilha + detalhe. Operadores: igual (v1 e v2), minimo, maximo, maior_que (v2)."""
        pid, nome = cond["especialista"], cond["atributo"]
        valor, motivo, e = self._ler(cache, pid, nome, trilha, cond.get("validade_ms"), ctx, agora)
        if motivo is not None:
            estado = INDETERMINADO
        elif "igual" in cond:
            estado = SIM if valor == cond["igual"] else NAO
        else:
            estado = self._comparar_numerica(cache, cond, valor, trilha, ctx, agora)
            if isinstance(estado, tuple):
                estado, motivo = estado
        reutilizado = e is not None and e["quadro_id"] != quadro_id
        falha = ctx["erros"].get(f"{pid}#{trilha}") or ctx["erros"].get(pid)
        det: dict[str, Any] = {"especialista": pid, "atributo": nome}
        for op in ("igual", "minimo", "maximo", "maior_que"):
            if op in cond:
                det[op] = cond[op]
        det.update({"resultado": estado, "motivo": motivo,
                    "idade_ms": None if e is None else round((agora - e["capturado_em"]) * 1000.0, 1),
                    "quadro_do_atributo": None if e is None else e["quadro_id"], "reutilizado": reutilizado,
                    "falha_recente": falha if (reutilizado or e is None) else None})
        return estado, det

    def _comparar_numerica(self, cache: dict[tuple[int, str, str], dict[str, Any]], cond: dict[str, Any], valor: Any, trilha: int,
                           ctx: dict[str, Any], agora: float) -> str | tuple[str, str]:
        if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
            return INDETERMINADO, "valor_nao_numerico"
        if "minimo" in cond:
            return SIM if valor >= cond["minimo"] else NAO
        if "maximo" in cond:
            return SIM if valor <= cond["maximo"] else NAO
        alvo = cond["maior_que"]
        v2, motivo2, _ = self._ler(cache, alvo["especialista"], alvo["atributo"], trilha, cond.get("validade_ms"), ctx, agora)
        if motivo2 is not None:
            return INDETERMINADO, f"comparado:{motivo2}"
        if isinstance(v2, bool) or not isinstance(v2, (int, float)) or not math.isfinite(v2):
            return INDETERMINADO, "comparado:valor_nao_numerico"
        return SIM if valor > v2 else NAO                       # empate = nao

    def _avaliar(self, camera_ref: str, geracao: int, receita: dict[str, Any], chave: tuple[str, str], o: Observacao, ctx: dict[str, Any], agora: float) -> dict[str, Any]:
        cache = self._cache[camera_ref]
        detalhes, estados = [], []
        for cond in receita["regra"]["condicoes"]:
            estado, det = self._avaliar_condicao(cache, cond, o.trilha_id, o.quadro_id, ctx, agora)
            estados.append(estado)
            detalhes.append(det)
        veredito = combinar(receita["regra"]["combinacao"], estados)
        self.contadores[f"veredito_{veredito}"] += 1
        sufixo = f"#{o.trilha_id}"
        erros = {k: v for k, v in ctx["erros"].items() if k.endswith(sufixo) or "#" not in k}
        return {"versao_evento": VERSAO_EVENTO, "modo": "sombra", "alerta_real": False, "camera_ref": camera_ref, "geracao": geracao,
                "quadro_id": o.quadro_id, "trilha_id": o.trilha_id, "capturado_em": o.capturado_em, "avaliado_em": agora,
                "receita": {"id": chave[0], "versao": chave[1], "config_hash": hash_config(receita, chave)},
                "especialistas": list(ctx["especialistas"].values()), "politica_cache": POLITICA_CACHE,
                "incompatibilidades": dict(ctx["incompatibilidades"]), "veredito": veredito, "condicoes": detalhes, "erros": erros,
                "descartes": dict(ctx["descartes"])}

    # ------------------------------------------------------------------ cena (contrato 1 identico ao v1; contrato 2 = papeis/relacoes/eventos)

    def processar_cena(self, cena: Cena) -> list[dict[str, Any]]:
        """Uma Cena. Receita contrato 1: DELEGA a `processar` (mesmos eventos, um por observacao). Receita contrato 2: avalia papeis por sujeito,
        roda as regras espaciais/temporais e emite UM evento `tipo:"cena"` (tambem para cena vazia). Entradas invalidas nao derrubam o laco."""
        if not self.habilitado:
            return []
        if not isinstance(cena, Cena):
            self.contadores["cena_rejeitada"] += 1
            return []
        with self._lock:
            chave = self._camera.get(cena.camera_ref)
            receita = self._receitas.get(chave) if chave else None
            if receita is None:
                return []
            contrato2 = contrato_da_receita(receita) == 2
        if not contrato2:
            try:
                # 05f: sujeito fora da ROI nao gera evento nem recorte no contrato 1 (nao ha "indeterminado" por sujeito no formato v1:
                # um evento por observacao). A Cena inteira fora da ROI ainda avanca geracao/relogio via `processar`.
                dentro = tuple(d for d in cena.deteccoes if d.na_roi)
                if len(dentro) != len(cena.deteccoes):
                    self.contadores["sujeito_fora_da_roi"] += len(cena.deteccoes) - len(dentro)
                    cena = dataclasses.replace(cena, deteccoes=dentro)
                observacoes = observacoes_de_cena(cena)
            except (AttributeError, TypeError):
                self.contadores["cena_rejeitada"] += 1
                return []
            return self.processar(cena.camera_ref, cena.geracao, cena.quadro, observacoes)
        with self._lock:
            evento = self._processar_cena2(cena, receita, chave)
        if evento is None:
            return []
        if self._destino is not None:
            try:
                self._destino(evento)
            except Exception:  # noqa: BLE001 -- falha do rastro nao derruba o laco da camera
                self.contadores["falha_ao_gravar_evento"] += 1
        return [evento]

    def encerrar_camera(self, camera_ref: str, motivo: str = M_SESSAO) -> list[dict[str, Any]]:
        """Descarta o estado das regras da camera (ex.: sessao encerrada). Devolve as linhas dos eventos que estavam ativos."""
        with self._lock:
            return self._descartar_regras(camera_ref, motivo)

    def _descartar_regras(self, camera_ref: str, motivo: str) -> list[dict[str, Any]]:
        av = self._regras.pop(camera_ref, None)
        self._regras_hash.pop(camera_ref, None)
        linhas = av.descartar(motivo) if av is not None else []
        if linhas:
            self._encerrados.setdefault(camera_ref, []).extend(linhas)
            for linha in linhas:
                self.contadores[f"evento_encerrado_{motivo}"] += 1
        return linhas

    def _processar_cena2(self, cena: Cena, receita: dict[str, Any], chave: tuple[str, str]) -> dict[str, Any] | None:
        cam, geracao = cena.camera_ref, cena.geracao
        atual = self._geracao.get(cam)
        if atual is not None and geracao < atual:
            self.contadores["descartado_sessao_anterior"] += 1
            return None
        agora = self._relogio()
        if not isinstance(cena.recebido_em, (int, float)) or not math.isfinite(cena.recebido_em) or cena.recebido_em > agora + 1e-6:
            self.contadores["cena_rejeitada"] += 1
            return None
        if atual is None or geracao > atual:
            self._geracao[cam] = geracao
            self._cache[cam] = {}
            self._descartar_regras(cam, M_GERACAO)
        self._cache.setdefault(cam, {})
        av = self._regras.get(cam)
        if av is None:
            av = self._regras[cam] = AvaliadorRegras(receita)
            self._regras_hash[cam] = hash_config(receita, chave)
        motivo = av.motivo_recusa(cena)
        if motivo:
            self.contadores[motivo if motivo == "cena_fora_de_ordem" else f"cena_rejeitada_{motivo}"] += 1
            return None
        try:
            observacoes = observacoes_de_cena(cena)
        except (AttributeError, TypeError):
            self.contadores["cena_rejeitada"] += 1
            return None
        validas = self._filtrar_observacoes(cam, geracao, observacoes, agora, por_classe=True)
        fora = {(d.trilha_id, d.classe) for d in cena.deteccoes if not d.na_roi}      # 05f: sem recorte nem especialista; papel indeterminado
        if fora:
            validas = [o for o in validas if (o.trilha_id, o.classe) not in fora]
            self.contadores["sujeito_fora_da_roi"] += len(fora)
        ctx = self._despachar(cam, geracao, receita, cena.quadro, validas, agora)
        if fora:
            ctx["descartes"]["fora_da_roi"] += len(fora)
        agora = self._relogio()
        cache = self._cache[cam]
        estados: dict[str, dict[int, str]] = {}
        detalhes: dict[tuple[str, int], dict[str, list[dict[str, Any]]]] = {}
        for papel_id, papel in receita["papeis"].items():
            estados[papel_id] = {}
            for d in cena.deteccoes:
                if d.classe != papel["classe"]:
                    continue
                if not d.na_roi:
                    estados[papel_id][d.trilha_id] = INDETERMINADO
                    detalhes[(papel_id, d.trilha_id)] = [{"especialista": None, "atributo": None, "resultado": INDETERMINADO, "motivo": "fora_da_roi",
                                                          "idade_ms": None, "quadro_do_atributo": None, "reutilizado": False, "falha_recente": None}]
                    continue
                ests, dets = [], []
                for cond in papel["condicoes"]:
                    e, det = self._avaliar_condicao(cache, cond, d.trilha_id, cena.quadro_id, ctx, agora)
                    ests.append(e)
                    dets.append(det)
                estados[papel_id][d.trilha_id] = combinar(papel["combinacao"], ests)
                detalhes[(papel_id, d.trilha_id)] = dets
        try:
            res = av.avancar(cena, estados)
        except Exception:  # noqa: BLE001 -- defeito nas regras nao derruba o laco; o estado so avanca se `avancar` terminou
            self.contadores["erro_regras"] += 1
            return None
        if res.rejeitada:
            self.contadores[f"cena_rejeitada_{res.rejeitada}"] += 1
            return None
        for s in res.sujeitos:
            s["detalhes"] = {p: detalhes.get((p, s["trilha_id"]), []) for p in s["papeis"]}
        for linha in res.eventos:
            if linha["transicao"]:
                self.contadores[f"evento_{linha['transicao']}" + (f"_{linha['motivo']}" if linha["motivo"] else "")] += 1
        self.contadores["cena_avaliada"] += 1
        evento = {"versao_evento": VERSAO_EVENTO, "modo": "sombra", "alerta_real": False, "tipo": "cena", "camera_ref": cam, "geracao": geracao,
                  "quadro_id": cena.quadro_id, "capturado_em": cena.recebido_em, "avaliado_em": agora,
                  "receita": {"id": chave[0], "versao": chave[1], "config_hash": hash_config(receita, chave)},
                  "sujeitos": res.sujeitos, "eventos": res.eventos, "especialistas": list(ctx["especialistas"].values()),
                  "politica_cache": POLITICA_CACHE, "incompatibilidades": dict(ctx["incompatibilidades"]), "erros": dict(ctx["erros"]),
                  "descartes": dict(ctx["descartes"]), "encerramentos_por_descarte": self._encerrados.pop(cam, [])}
        self._podar(cam, self._relogio())
        return evento
