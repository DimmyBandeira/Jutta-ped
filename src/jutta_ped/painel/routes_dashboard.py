"""Dashboard do sistema-mae: indicadores, historico de alertas, processamento e relatorio. Somente leitura (consultar nunca
reinicia nem reconfigura a analise). Os numeros vem de fontes reais: banco, gerente de IA, go2rtc e instalador de especialistas."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.jutta_ped.painel import receitas_service as rs
from src.jutta_ped.painel.config_service import ler_config
from src.jutta_ped.painel.deps import DbDep, exige_admin, render, usuario_atual, verifica_csrf
from src.jutta_ped.painel.eventos import para_visao
from src.jutta_ped.painel.go2rtc import PREFIXO_STREAM, nome_stream
from src.jutta_ped.painel.models import Camera, EventoPediatria, Usuario
from src.jutta_ped.painel.sombra_service import visao_feed

router = APIRouter(prefix="/painel", dependencies=[Depends(verifica_csrf)])

PERIODOS = {"24h": ("hora", 24), "7d": ("dia", 7), "30d": ("dia", 30)}
SITUACOES = {"", "pendente", "reconhecido", "suprimido"}
LIMITE_RELATORIO = 500
TEXTO_NOS_REMOTOS = "Nós remotos de processamento ainda não foram implementados: todo o processamento roda neste servidor."


def _servicos(request: Request):
    return request.app.state.painel


# ---------------------------------------------------------------------------------------------- indicadores

def _stream_configurado(camera: Camera) -> bool:
    return bool(nome_stream(camera)) and (not camera.gerenciada_pelo_sync or camera.go2rtc_sync == "ok")


def _analise_rodando(camera: Camera, servicos) -> bool:
    """IA rodando DE VERDADE (sessao ativa e sem erro operacional) -- nao confundir com video disponivel."""
    ia = servicos.ia
    if ia is None or not camera.ia_pediatria or ia.estado(camera.id).get("estado") != "rodando":
        return False
    try:
        status = ia.gerente.camera_status(f"{PREFIXO_STREAM}{camera.id}")
    except Exception:  # noqa: BLE001
        return False
    return bool(status.get("active")) and status.get("operational_status") != "ERROR"


def indicadores(db: Session, servicos) -> dict[str, Any]:
    """`streams_sincronizados` = configuracao do stream sincronizada no go2rtc. NAO comprova video chegando; `go2rtc` diz se a
    API respondeu na ultima sondagem de fundo (cache; a renderizacao nao sonda)."""
    cameras = list(db.scalars(select(Camera)).unique().all())
    estado = servicos.go2rtc_estado()
    pendentes = db.scalar(select(func.count()).select_from(EventoPediatria).where(
        EventoPediatria.suprimido.is_(False), EventoPediatria.reconhecido_em.is_(None))) or 0
    return {"cameras_total": len(cameras), "streams_sincronizados": sum(1 for c in cameras if _stream_configurado(c)),
            "analise_em_execucao": sum(1 for c in cameras if _analise_rodando(c, servicos)), "alertas_pendentes": int(pendentes),
            "ia_disponivel": servicos.ia is not None, "go2rtc": estado[0],
            "go2rtc_verificado_ha_s": None if estado[1] is None else int(estado[1])}


@router.get("/indicadores")
def indicadores_fragmento(request: Request, usuario: Usuario = Depends(usuario_atual), db: Session = DbDep):
    return render(request, "partials/indicadores.html", usuario, ind=indicadores(db, _servicos(request)))


# ---------------------------------------------------------------------------------------------- historico de alertas

def serie_alertas(db: Session, periodo: str, camera_id: int | None, agora: datetime) -> dict[str, Any]:
    unidade, n = PERIODOS[periodo]
    if unidade == "hora":
        fim, passo = agora.replace(minute=0, second=0, microsecond=0), timedelta(hours=1)
    else:
        fim, passo = agora.replace(hour=0, minute=0, second=0, microsecond=0), timedelta(days=1)
    inicio = fim - passo * (n - 1)
    consulta = select(EventoPediatria.criado_em).where(EventoPediatria.suprimido.is_(False), EventoPediatria.criado_em >= inicio)
    if camera_id is not None:
        consulta = consulta.where(EventoPediatria.camera_id == camera_id)
    contagem: Counter[datetime] = Counter()
    for (quando,) in db.execute(consulta):
        contagem[quando.replace(minute=0, second=0, microsecond=0) if unidade == "hora" else quando.replace(hour=0, minute=0, second=0, microsecond=0)] += 1
    pontos = []
    for k in range(n):
        b = inicio + passo * k
        pontos.append({"inicio": b.isoformat(timespec="seconds"), "rotulo": b.strftime("%Hh") if unidade == "hora" else b.strftime("%d/%m"), "total": contagem.get(b, 0)})
    total = sum(p["total"] for p in pontos)
    return {"periodo": periodo, "unidade": unidade, "camera": camera_id, "total": total, "vazio": total == 0, "pontos": pontos}


@router.get("/alertas/historico")
def historico(request: Request, periodo: str = "24h", camera: str | None = None, usuario: Usuario = Depends(usuario_atual), db: Session = DbDep):
    if periodo not in PERIODOS:
        return JSONResponse({"detail": f"periodo inválido: use {', '.join(PERIODOS)}."}, status_code=422)
    camera_id = None
    if camera is not None and camera.strip() != "":
        if not camera.strip().isdigit():
            return JSONResponse({"detail": "camera inválida: informe o id numérico da câmera."}, status_code=422)
        camera_id = int(camera.strip())
    eventos = _servicos(request).eventos
    agora = eventos.agora() if eventos is not None else datetime.now()
    return JSONResponse(serie_alertas(db, periodo, camera_id, agora))


# ---------------------------------------------------------------------------------------------- processamento (admin)

def _sessoes(servicos, nomes: dict[int, str]) -> list[dict[str, Any]]:
    if servicos.ia is None:
        return []
    try:
        itens = list(servicos.ia.gerente.cameras())
    except Exception:  # noqa: BLE001
        return []
    saida = []
    for item in itens:
        ref = str(item.get("camera_id") or "")
        cid = int(ref[len(PREFIXO_STREAM):]) if ref.startswith(PREFIXO_STREAM) and ref[len(PREFIXO_STREAM):].isdigit() else None
        if "operational_status" not in item and ref:
            try:
                item = {**item, **servicos.ia.gerente.camera_status(ref)}
            except Exception:  # noqa: BLE001
                pass
        saida.append({"camera": nomes.get(cid, ref) if cid is not None else ref,
                      "estado": item.get("operational_status") or item.get("session_status") or ("ativa" if item.get("active") else "parada"),
                      "frames": item.get("frames_processed", 0), "popups": item.get("popup_count", 0),
                      "dispositivo": item.get("resolved_device") or item.get("device"), "ultimo_erro": item.get("last_error")})
    return saida


def processamento(db: Session, servicos, inst: Any) -> dict[str, Any]:
    cfg = ler_config(db)
    cameras = list(db.scalars(select(Camera)).unique().all())
    api_ok, streams = servicos.sync.sondar()
    contratos = rs.contratos_ativos(inst)
    feed = visao_feed(db, servicos.sombra)
    return {
        "servico": {"iniciado_em": servicos.iniciado_em.strftime("%d/%m/%Y %H:%M:%S"), "ia_disponivel": servicos.ia is not None,
                    "max_cameras_ia": cfg["max_cameras_ia"]},
        "go2rtc": {"api_ok": api_ok, "streams": streams, "sincronizadas": sum(1 for c in cameras if c.go2rtc_sync == "ok")},
        "sessoes": _sessoes(servicos, {c.id: c.nome for c in cameras}),
        "especialistas": [{"id": pid, "versao": c["versao"], "identidade_curta": str(c["identidade"])[:12]} for pid, c in sorted(contratos.items())],
        "sombra": {"ativas": sum(1 for c in feed["cameras"] if c["estado"] == "ativa"), "cameras": feed["cameras"]},
        "nos_remotos": {"implementado": False, "texto": TEXTO_NOS_REMOTOS},
    }


@router.get("/processamento")
def pagina_processamento(request: Request, admin: Usuario = Depends(exige_admin), db: Session = DbDep):
    return render(request, "processamento.html", admin, ativo="processamento", proc=processamento(db, _servicos(request), request.app.state.especialistas))


# ---------------------------------------------------------------------------------------------- relatorio de alertas

class FiltroInvalido(ValueError):
    pass


def _data(texto: str, rotulo: str) -> datetime | None:
    texto = texto.strip()
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto) if "T" in texto or " " in texto else datetime.combine(date.fromisoformat(texto), datetime.min.time())
    except ValueError:
        raise FiltroInvalido(f"Data inválida em '{rotulo}': use AAAA-MM-DD.") from None


def consultar_alertas(db: Session, camera_id: str = "", situacao: str = "", de: str = "", ate: str = "") -> list[Any]:
    camera_txt, situacao = (camera_id or "").strip(), (situacao or "").strip().lower()
    if camera_txt and not camera_txt.isdigit():
        raise FiltroInvalido("Câmera inválida.")
    if situacao not in SITUACOES:
        raise FiltroInvalido("Situação inválida: use pendente, reconhecido ou suprimido.")
    inicio, fim = _data(de, "de"), _data(ate, "até")
    if fim is not None and not any(c in (ate or "") for c in "T "):
        fim = fim + timedelta(days=1)                      # 'ate' sem hora inclui o dia inteiro
    if inicio is not None and fim is not None and inicio >= fim:
        raise FiltroInvalido("Período inválido: a data inicial deve ser anterior à final.")
    consulta = select(EventoPediatria).order_by(EventoPediatria.id.desc()).limit(LIMITE_RELATORIO)
    if camera_txt:
        consulta = consulta.where(EventoPediatria.camera_id == int(camera_txt))
    if situacao == "suprimido":
        consulta = consulta.where(EventoPediatria.suprimido.is_(True))
    else:
        consulta = consulta.where(EventoPediatria.suprimido.is_(False))
        if situacao == "pendente":
            consulta = consulta.where(EventoPediatria.reconhecido_em.is_(None))
        elif situacao == "reconhecido":
            consulta = consulta.where(EventoPediatria.reconhecido_em.is_not(None))
    if inicio is not None:
        consulta = consulta.where(EventoPediatria.criado_em >= inicio)
    if fim is not None:
        consulta = consulta.where(EventoPediatria.criado_em < fim)
    return [para_visao(e) for e in db.scalars(consulta).unique().all()]


@router.get("/relatorio")
def pagina_relatorio(request: Request, usuario: Usuario = Depends(usuario_atual), db: Session = DbDep):
    cameras = [{"id": c.id, "nome": c.nome} for c in db.scalars(select(Camera).order_by(Camera.nome)).unique().all()]
    return render(request, "relatorio.html", usuario, ativo="relatorio", cameras=cameras, alertas=consultar_alertas(db),
                  filtros={"camera_id": "", "situacao": "", "de": "", "ate": ""})


@router.get("/relatorio/alertas")
def fragmento_relatorio(request: Request, camera_id: str = "", situacao: str = "", de: str = "", ate: str = "",
                        usuario: Usuario = Depends(usuario_atual), db: Session = DbDep):
    try:
        alertas = consultar_alertas(db, camera_id, situacao, de, ate)
    except FiltroInvalido as exc:
        from html import escape
        return HTMLResponse(f'<div class="badge offline" style="display:block">{escape(str(exc))}</div>', status_code=422)
    return render(request, "partials/alertas_tabela.html", usuario, alertas=alertas)
