"""Templates do dashboard (etapa 05e): renderiza com Jinja e contextos de exemplo, sem backend.

Confere estrutura, estados vazios, escape de HTML, ausencia de dados fixos e a presenca dos hooks
do contrato de integracao (docs/etapas_motor/05e_dashboard_sistema_mae.md).
"""
from __future__ import annotations

import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from jinja2 import Environment, FileSystemLoader

RAIZ = Path(__file__).resolve().parents[1]
TEMPLATES = RAIZ / "web" / "templates"
STATIC = RAIZ / "web" / "static"
VOID = {"meta", "link", "input", "br", "img", "hr", "source", "col", "area", "base", "wbr", "path", "rect", "circle", "line"}
XSS = '<script>alert("x")</script>'

env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)


def renderiza(nome: str, **ctx) -> str:
    base = {"usuario_logado": NS(login="ana"), "eh_admin_logado": False, "csrf_token": "tok123", "ativo": None}
    base.update(ctx)
    return env.get_template(nome).render(**base)


class _Verificador(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.pilha: list[str] = []
        self.ids: list[str] = []
        self.erros: list[str] = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if "id" in d:
            self.ids.append(d["id"])
        if tag not in VOID:
            self.pilha.append(tag)

    def handle_startendtag(self, tag, attrs):
        d = dict(attrs)
        if "id" in d:
            self.ids.append(d["id"])

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if not self.pilha or self.pilha[-1] != tag:
            self.erros.append(f"</{tag}> inesperado (aberto: {self.pilha[-3:]})")
            if tag in self.pilha:
                while self.pilha and self.pilha.pop() != tag:
                    pass
            return
        self.pilha.pop()


def valida_html(html: str) -> None:
    v = _Verificador()
    v.feed(html)
    assert not v.erros, v.erros
    assert not v.pilha, f"tags sem fechar: {v.pilha}"
    duplicados = {i for i in v.ids if v.ids.count(i) > 1}
    assert not duplicados, f"ids duplicados: {duplicados}"


def camera(i=1, nome="Sala 1", **extra):
    d = dict(id=i, nome=nome, player_url=f"http://go2rtc/stream.html?src=c{i}", ia_ativa=True, ia_estado="CRIANCA_ACOMPANHADA",
             ia_erro=None, sync_texto="stream sincronizado", receita_label=None, sombra_ativa=False, sombra_estado="desligada", sombra_erro=None)
    d.update(extra)
    return NS(**d)


def alerta(i="ev1", **extra):
    d = dict(event_id=i, estado="CHILD_ALONE", camera_nome="Sala 1", quando_fmt="10:00:00", criado_em=datetime(2026, 10, 4, 10, 0, 0),
             confianca=0.87, motivo=None, suprimido=False, reconhecido_em=None, reconhecido_por=None, entregas=[],
             crop_url="/e/crop.jpg", annotated_url="/e/ann.jpg")
    d.update(extra)
    return NS(**d)


# ---- layout base ---------------------------------------------------------------------------------

def test_layout_admin_tem_grupo_administracao_e_sem_destinos():
    html = renderiza("monitoramento.html", eh_admin_logado=True, ativo="monitoramento", cameras=[], cameras_opcoes=[])
    valida_html(html)
    for href in ("/painel/monitoramento", "/painel/cameras", "/painel/relatorio", "/painel/especialistas", "/painel/receitas",
                 "/painel/processamento", "/painel/configuracoes", "/painel/usuarios"):
        assert f'href="{href}"' in html, href
    assert "Administração" in html
    assert "/painel/destinos" not in html
    assert 'aria-current="page"' in html
    assert re.search(r'<form method="post" action="/logout"><input type="hidden" name="csrf_token" value="tok123">', html)
    assert "Administrador" in html and "ana" in html


def test_layout_usuario_comum_nao_ve_itens_de_admin():
    html = renderiza("monitoramento.html", eh_admin_logado=False, ativo="monitoramento", cameras=[], cameras_opcoes=[])
    for href in ("/painel/especialistas", "/painel/receitas", "/painel/processamento", "/painel/configuracoes", "/painel/usuarios"):
        assert f'href="{href}"' not in html, href
    assert "Administração" not in html
    assert 'href="/painel/relatorio"' in html and ">Alertas<" in html
    assert "Gerenciar" not in html


def test_menu_acessivel_e_uma_unica_conexao_sse():
    html = renderiza("monitoramento.html", cameras=[], cameras_opcoes=[])
    assert html.count("sse-connect=") == 1
    assert 'id="popup-stack"' in html and 'id="popup-fechar-todos"' in html
    assert 'aria-expanded="false"' in html and 'aria-controls="menu-lateral"' in html and 'id="menu-lateral"' in html
    assert '/static/js/popup.js' in html and '/static/js/nav.js' in html
    assert html.count("popup.js") == 1
    assert '<a class="pular" href="#conteudo">' in html and 'id="conteudo"' in html


def test_login_sem_lateral_e_sem_sse():
    html = renderiza("login.html", usuario_logado=None, erro="Credenciais " + XSS)
    valida_html(html)
    assert "<aside" not in html and "sse-connect" not in html and "menu-lateral" not in html
    assert XSS not in html and "&lt;script&gt;" in html
    assert 'name="csrf_token"' in html


def test_erro_e_em_construcao_dentro_do_layout():
    for nome in ("erro.html", "em_construcao.html"):
        html = renderiza(nome, titulo="Titulo " + XSS, mensagem="msg", ativo="x")
        valida_html(html)
        assert XSS not in html and "<h1>Titulo" in html


# ---- monitoramento -------------------------------------------------------------------------------

def test_monitoramento_hooks_do_contrato():
    cams = [camera(1, "Sala <b>1</b>", receita_label="ped v2", sombra_ativa=True, sombra_estado="ativa"), camera(2, "Corredor", player_url=None)]
    html = renderiza("monitoramento.html", eh_admin_logado=True, cameras=cams, cameras_opcoes=[NS(id=1, nome="Sala <b>1</b>"), NS(id=2, nome="Corredor")])
    valida_html(html)
    assert 'hx-get="/painel/indicadores"' in html and "every 10s" in html
    for rot in ("Câmeras cadastradas", "Streams sincronizados", "Análise em execução", "Alertas pendentes"):
        assert rot in html, rot
    assert 'id="grafico-periodo"' in html and 'value="24h"' in html and 'value="7d"' in html and 'value="30d"' in html
    assert 'id="grafico-camera"' in html and 'id="grafico-alertas"' in html and "Alertas por período" in html
    assert "/static/js/dashboard.js" in html and "/static/js/live.js" in html
    assert 'hx-get="/painel/eventos/recentes"' in html and 'id="lista-eventos-recentes"' in html
    assert "alertas-mudaram from:body" in html and "every 10s" in html and 'hx-include="#filtros-alertas"' in html
    assert 'name="pendentes"' in html and 'name="camera"' in html
    assert 'hx-get="/painel/receitas-resumo"' in html
    assert "Sombra (diagnóstico — não são alertas)" in html and 'id="sombra-area"' in html and "<details" in html
    assert 'hx-get="/painel/sombra/feed"' in html and "every 5s [sombraAberta()]" in html
    assert "liveTodas(true)" in html and "liveTodas(false)" in html and "Tocar todas" in html and "Pausar todas" in html
    # cartao de camera: ids de JS e selos separados (video != analise != receita != sombra)
    for i in (1, 2):
        for pre in ("player", "overlay", "btn-play", "ia-estado", "cam"):
            assert f'id="{pre}-{i}"' in html, (pre, i)
    assert "Vídeo:" in html and "Análise:" in html and "Receita:" in html and "Sombra:" in html
    assert "ped v2" in html and "sem receita" in html and ">ativa<" in html and ">desligada<" in html
    assert "Sala <b>1</b>" not in html and "Sala &lt;b&gt;1&lt;/b&gt;" in html
    assert "sem stream" in html and "stream configurado" in html


def test_monitoramento_sem_cameras_estado_vazio():
    html = renderiza("monitoramento.html", cameras=[], cameras_opcoes=[])
    valida_html(html)
    assert "Nenhuma câmera cadastrada" in html
    assert "Tocar todas" not in html
    assert "cam-bloco" not in html


def test_monitoramento_sem_numeros_fixos_nos_indicadores():
    fonte = (TEMPLATES / "monitoramento.html").read_text(encoding="utf-8")
    assert not re.search(r'class="valor[^"]*">\s*\d', fonte)
    assert "random" not in fonte.lower()


def test_indicadores_valores_reais_e_indisponivel():
    ind = NS(cameras_total=7, streams_sincronizados=5, analise_em_execucao=3, alertas_pendentes=2, ia_disponivel=True)
    html = renderiza("partials/indicadores.html", ind=ind)
    valida_html(html)
    vals = re.findall(r'class="valor[^"]*">\s*([^<]+?)\s*<', html)
    assert vals == ["7", "5", "3", "2"]
    sem_ia = NS(cameras_total=7, streams_sincronizados=5, analise_em_execucao=0, alertas_pendentes=0, ia_disponivel=False)
    html = renderiza("partials/indicadores.html", ind=sem_ia)
    vals = re.findall(r'class="valor[^"]*">\s*([^<]+?)\s*<', html)
    assert vals[2] == "indisponível" and vals[:2] == ["7", "5"]
    assert "Vídeo disponível" not in html and "não comprova vídeo" in html   # configuracao != video chegando, e != analise
    ind_fora = NS(cameras_total=7, streams_sincronizados=5, analise_em_execucao=1, alertas_pendentes=0, ia_disponivel=True, go2rtc="inacessivel", go2rtc_verificado_ha_s=4)
    assert "go2rtc inacessível" in renderiza("partials/indicadores.html", ind=ind_fora)


def test_ia_estado_continua_igual():
    html = renderiza("partials/ia_estado.html", c=NS(ia_ativa=True, ia_estado="CRIANCA_DESACOMPANHADA", ia_erro=None))
    assert 'class="badge alerta"' in html
    assert "IA desligada" in renderiza("partials/ia_estado.html", c=NS(ia_ativa=False, ia_estado=None, ia_erro=None))


# ---- alertas -------------------------------------------------------------------------------------

def test_eventos_recentes_cartoes_com_ciencia_e_escape():
    a1 = alerta("ev1", camera_nome="Sala " + XSS, entregas=[NS(sucesso=True, detalhe="ok", destino_nome="GPS")])
    a2 = alerta("ev2", reconhecido_em=datetime(2026, 10, 4, 10, 5), reconhecido_por="bia")
    a3 = alerta("ev3", suprimido=True, estado="SEPARATED")
    html = renderiza("partials/eventos_recentes.html", alertas=[a1, a2, a3], filtro_camera=None, so_pendentes=False)
    valida_html(html)
    assert XSS not in html and "&lt;script&gt;" in html
    assert html.count('hx-post="/painel/popups/') == 1 and 'hx-post="/painel/popups/ev1/ack"' in html
    assert ">Ciente<" in html and "pendente" in html and "bia" in html and "suprimido" in html
    assert 'href="/e/crop.jpg"' in html and 'href="/e/ann.jpg"' in html and "GPS" in html and "87%" in html
    assert "Desacompanhada" in html


def test_eventos_recentes_vazios():
    assert "Nenhum alerta recente" in renderiza("partials/eventos_recentes.html", alertas=[], filtro_camera=None, so_pendentes=False)
    h = renderiza("partials/eventos_recentes.html", alertas=[], filtro_camera=3, so_pendentes=True)
    assert "Nenhum alerta pendente" in h and "câmera selecionada" in h


def test_relatorio_filtros_e_sem_secoes_legadas():
    html = renderiza("relatorio.html", eh_admin_logado=True, ativo="relatorio", cameras=[NS(id=1, nome="Sala 1")], destinos_status=[])
    valida_html(html)
    assert 'hx-get="/painel/relatorio/alertas"' in html and 'hx-target="#tabela-alertas"' in html and 'id="tabela-alertas"' in html
    for nome in ("camera_id", "situacao", "de", "ate"):
        assert f'name="{nome}"' in html
    assert "Status das câmeras" not in html and "Status dos destinos" not in html


def test_tabela_de_alertas_estado_vazio_e_ciencia():
    assert "Nenhum alerta no período" in renderiza("partials/alertas_tabela.html", alertas=[])
    html = renderiza("partials/alertas_tabela.html", alertas=[alerta("ev9", camera_nome=XSS)])
    valida_html(html)
    assert XSS not in html and 'hx-post="/painel/popups/ev9/ack"' in html


# ---- receitas, sombra, processamento ---------------------------------------------------------------

def test_receitas_resumo_estados():
    vazio_admin = renderiza("partials/receitas_resumo.html", receitas=[], eh_admin_logado=True)
    assert "Nenhuma receita" in vazio_admin and 'href="/painel/receitas"' in vazio_admin
    vazio_comum = renderiza("partials/receitas_resumo.html", receitas=[], eh_admin_logado=False)
    assert 'href="/painel/receitas"' not in vazio_comum
    r = NS(id=1, nome="Ped " + XSS, versao=2, descricao="d", especialistas=[{"id": "idade", "versao_ativa": None}], cameras=["Sala 1"],
           valida=False, motivos=["atributo inexistente"])
    html = renderiza("partials/receitas_resumo.html", receitas=[r], eh_admin_logado=False)
    valida_html(html)
    assert XSS not in html and "inválida" in html and "atributo inexistente" in html and "sem versão ativa" in html and "Sala 1" in html


def test_sombra_feed_identifica_diagnostico_e_escapa():
    ev = {"camera_nome": "Sala " + XSS, "trilha_id": 3, "quadro_id": 10, "veredito": "atende", "idade_max_ms": 120, "receita": "ped v2",
          "config_hash": "abcdef0123456789", "versoes": [{"id": "idade", "versao": "1.0"}], "quando": "10:00:01",
          "condicoes": [{"especialista": "idade", "atributo": "faixa", "igual": "crianca", "resultado": True, "motivo": None,
                         "idade_ms": 80, "reutilizado": True, "falha_recente": True}],
          "incompatibilidades": {"idade": ["versao antiga"]}}
    sombra = {"eventos": [ev], "cameras": [{"id": 1, "nome": "Sala 1", "estado": "ativa", "erro": None, "enfileirados": 1, "descartados": 2, "processados": 3}]}
    html = renderiza("partials/sombra_feed.html", sombra=sombra)
    valida_html(html)
    assert XSS not in html
    for t in ("atende", "reutilizado", "falha recente", "incompatível", "versao antiga", "abcdef0123", "idade máx. 120 ms", "descartados 2"):
        assert t in html, t
    assert "Nenhum evento de sombra" in renderiza("partials/sombra_feed.html", sombra={"eventos": [], "cameras": []})


def receita_ctx():
    esp = [{"id": "idade", "nome": "Idade", "versao": "1.0", "atributos": [{"nome": "faixa", "tipo": "categorico", "categorias": ["crianca", "adulto"]}]},
           {"id": "pose", "nome": "Pose <b>", "versao": "0.3", "atributos": [{"nome": "sentado", "tipo": "booleano", "categorias": []}]}]
    return esp


def test_receitas_admin_pagina_completa():
    cams = [
        {"id": 1, "nome": "Sala 1", "receita_versao_id": 5, "receita_label": "ped v1", "valida": True, "motivos": [], "sombra_ativa": False, "sombra_estado": "desligada", "sombra_erro": None},
        {"id": 2, "nome": "Sala 2", "receita_versao_id": None, "receita_label": None, "valida": False, "motivos": [], "sombra_ativa": False, "sombra_estado": "desligada", "sombra_erro": None},
        {"id": 3, "nome": "Sala 3", "receita_versao_id": 5, "receita_label": "ped v1", "valida": True, "motivos": [], "sombra_ativa": True, "sombra_estado": "erro", "sombra_erro": "falha " + XSS},
        {"id": 4, "nome": "Sala 4", "receita_versao_id": 6, "receita_label": "x v1", "valida": False, "motivos": ["especialista removido"], "sombra_ativa": False, "sombra_estado": "desligada", "sombra_erro": None},
    ]
    rec = [{"id": 1, "nome": "Ped", "descricao": "desc", "versoes": [{"id": 5, "versao": 1, "config_hash": "0123456789abcdef", "criado_em": "04/10 10:00", "criado_por": "ana", "resumo": "1 condição"}]}]
    html = renderiza("receitas.html", eh_admin_logado=True, ativo="receitas", receitas=rec, especialistas_ativos=receita_ctx(), cameras_receita=cams,
                     opcoes_versao=[{"id": 5, "label": "Ped v1"}, {"id": 6, "label": "X v1"}], flash={"tipo": "ok", "texto": "Salvo"}, erro="Falha <i>")
    valida_html(html)
    assert "não gera alertas" in html and "não substitui" in html and "desligada por padrão" in html
    assert 'href="/painel/receitas/nova"' in html and 'href="/painel/receitas/1/editar"' in html and "Nova versão" in html
    assert "0123456789" in html and "ana" in html
    for i in (1, 2, 3, 4):
        assert f'action="/painel/cameras/{i}/receita"' in html and f'action="/painel/cameras/{i}/sombra"' in html
    assert html.count('name="receita_versao_id"') == 4 and html.count('name="csrf_token"') == 10  # 8 da tabela + Sair + importacao
    assert 'value="">sem receita<' in html.replace("\n", "")
    assert re.search(r'<option value="5" selected>Ped v1</option>', html)
    assert 'name="acao" value="ligar"' in html and 'name="acao" value="desligar"' in html
    # sem receita valida o botao de ligar fica desabilitado, com explicacao
    ligar = re.findall(r'<button class="btn pequeno" type="submit"([^>]*)>Ligar sombra</button>', html)
    assert len(ligar) == 3 and sum("disabled" in x for x in ligar) == 2
    assert "Associe uma receita válida" in html and "inválida; corrija" in html and "especialista removido" in html
    assert XSS not in html and "Falha &lt;i&gt;" in html and "Salvo" in html


def test_receitas_sem_dados_estados_vazios():
    html = renderiza("receitas.html", eh_admin_logado=True, receitas=[], especialistas_ativos=[], cameras_receita=[], opcoes_versao=[])
    valida_html(html)
    assert "Nenhuma receita cadastrada" in html and "Nenhuma câmera cadastrada" in html


def test_receitas_form_nova_e_edicao():
    esp = receita_ctx()
    form = {"rid": None, "nome": "", "descricao": "", "combinacao": "todas", "vinculos": {}, "condicoes": [{"especialista": "", "atributo": "", "igual": "", "validade_ms": None}] * 4}
    html = renderiza("receitas_form.html", eh_admin_logado=True, form=form, especialistas_ativos=esp, erro=None, problemas=[], editando=False)
    valida_html(html)
    assert 'action="/painel/receitas"' in html and 'name="csrf_token"' in html
    assert 'name="nome"' in html and "readonly" not in html.split('name="nome"')[1].split(">")[0]
    for campo in ("descricao", "combinacao", "vinc_usar", "vinc_orcamento__idade", "vinc_margem__idade", "vinc_orcamento__pose", "vinc_margem__pose"):
        assert f'name="{campo}"' in html, campo
    for campo in ("cond_esp", "cond_attr", "cond_igual", "cond_validade"):
        assert html.count(f'name="{campo}"') == 4, campo
    assert "<optgroup" in html and "faixa (categorico)" in html and 'value="crianca"' in html
    assert "uma trilha por vez" in html and "Relações entre trilhas" in html and "persistência" in html and "contrato 2" in html
    assert "Pose &lt;b&gt;" in html and "Pose <b>" not in html
    # edicao: nome somente leitura, valores preservados, envia para /versoes e mostra problemas
    form2 = {"rid": 9, "nome": "Ped", "descricao": "d", "combinacao": "alguma",
             "vinculos": {"idade": {"usar": True, "orcamento_ms": 40, "margem_recorte": 0.2}},
             "condicoes": [{"especialista": "idade", "atributo": "faixa", "igual": "crianca", "validade_ms": 500}] + [{"especialista": "", "atributo": "", "igual": "", "validade_ms": None}] * 3}
    html2 = renderiza("receitas_form.html", eh_admin_logado=True, form=form2, especialistas_ativos=esp, erro="Receita inválida", problemas=["cond 1: " + XSS], editando=True)
    valida_html(html2)
    assert 'action="/painel/receitas/9/versoes"' in html2 and "readonly" in html2
    assert 'value="40"' in html2 and 'value="0.2"' in html2 and "checked" in html2 and 'value="500"' in html2
    assert '<option value="alguma" selected>' in html2 and '<option value="idade" selected>' in html2
    assert "Receita inválida" in html2 and XSS not in html2 and "&lt;script&gt;" in html2
    assert re.search(r'value="faixa"[^>]*selected', html2)


def test_receitas_form_sem_especialistas():
    html = renderiza("receitas_form.html", eh_admin_logado=True, form=None, especialistas_ativos=[], erro=None, problemas=[], editando=False)
    valida_html(html)
    assert "Nenhum especialista ativo" in html


def test_processamento_blocos_e_nos_remotos_nao_implementados():
    proc = {"servico": {"iniciado_em": "04/10 09:00", "ia_disponivel": True, "max_cameras_ia": 4},
            "go2rtc": {"api_ok": True, "streams": 3, "sincronizadas": 2},
            "sessoes": [{"camera": "Sala " + XSS, "estado": "ok", "frames": 120, "popups": 1, "dispositivo": "GPU", "ultimo_erro": None}],
            "especialistas": [{"id": "idade", "versao": "1.0", "identidade_curta": "abc123"}],
            "sombra": {"ativas": 1, "cameras": [{"id": 1, "nome": "Sala 1", "estado": "ativa", "erro": None, "enfileirados": 0, "descartados": 0, "processados": 9}]},
            "nos_remotos": {"implementado": False, "texto": "Nós remotos não existem."}}
    html = renderiza("processamento.html", eh_admin_logado=True, ativo="processamento", proc=proc)
    valida_html(html)
    for t in ("Serviço", "go2rtc", "Sessões de IA", "Executor local", "Sombra (diagnóstico", "Nós remotos", "Não implementado", "abc123", "GPU"):
        assert t in html, t
    assert XSS not in html
    assert "operacional" not in html.split("Nós remotos")[-1].lower().replace("nada aqui está operacional", "")
    vazio = renderiza("processamento.html", eh_admin_logado=True, proc={"servico": {"ia_disponivel": False}, "go2rtc": {"api_ok": False}, "sessoes": [], "especialistas": [], "sombra": {"ativas": 0, "cameras": []}, "nos_remotos": {"implementado": False, "texto": ""}})
    valida_html(vazio)
    assert "indisponível" in vazio and "Nenhuma sessão" in vazio and "Não implementado" in vazio


# ---- paginas existentes continuam renderizando -------------------------------------------------------

def test_paginas_existentes_renderizam_no_novo_layout():
    cfg = NS(go2rtc_api_url="http://x", go2rtc_public_url="http://y", go2rtc_rtsp_url="rtsp://z", cooldown_seconds=30, evidence_retention_days=7, public_base_url="")
    casos = [
        ("cameras/list.html", dict(cameras=[], destinos=[])),
        ("usuarios/list.html", dict(usuarios=[])),
        ("destinos/list.html", dict(destinos=[])),
        ("configuracoes.html", dict(config=cfg, sync_status="ok", salvo=True, erro=None)),
        ("especialistas.html", dict(itens=[], operacoes=[], flash={"tipo": "ok", "texto": "ok"}, registro_erro=None, erro=None, problemas=[], upload_max_mib=50)),
    ]
    for nome, ctx in casos:
        html = renderiza(nome, eh_admin_logado=True, **ctx)
        valida_html(html)
        assert "<h1>" in html and "sse-connect" in html, nome
    assert "Configurações salvas" in renderiza("configuracoes.html", eh_admin_logado=True, config=cfg, sync_status="ok", salvo=True, erro=None)


# ---- higiene dos arquivos ----------------------------------------------------------------------------

def _arquivos(globs):
    for g in globs:
        yield from sorted(p for p in RAIZ.glob(g) if p.is_file() and "vendor" not in p.parts and "_referencia" not in p.parts)


def test_sem_safe_sem_cdn_sem_js_inline_com_dados():
    for p in _arquivos(["web/templates/**/*.html"]):
        t = p.read_text(encoding="utf-8")
        assert "|safe" not in t and "| safe" not in t, p.name
        assert "autoescape false" not in t, p.name
        assert not re.search(r'(?:src|href)="https?://', t), p.name
        for bloco in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", t, flags=re.S):
            assert "{{" not in bloco, p.name
    for p in _arquivos(["web/static/css/*.css", "web/static/js/*.js"]):
        t = p.read_text(encoding="utf-8")
        assert not re.search(r"https?://(?!www\.w3\.org)", t), p.name
        assert "@import" not in t, p.name


def test_css_cobre_reducao_de_movimento_e_foco():
    css = (STATIC / "css" / "app.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css and ":focus-visible" in css and "max-width:899px" in css
    assert "overflow-x:hidden" in css


def test_apenas_um_modulo_cria_conexao_sse():
    # a unica conexao SSE e a de base.html; nenhum script novo abre EventSource
    for p in _arquivos(["web/static/js/*.js"]):
        assert "EventSource" not in p.read_text(encoding="utf-8"), p.name
    fonte = "".join(p.read_text(encoding="utf-8") for p in _arquivos(["web/templates/**/*.html"]))
    assert fonte.count("sse-connect=") == 1


@pytest.mark.parametrize("nome", ["partials/camera_card.html", "partials/eventos_recentes.html"])
def test_templates_nao_quebram_com_contexto_minimo(nome):
    renderiza(nome, c=NS(id=1, nome="x", player_url=None, ia_ativa=False, ia_estado=None, ia_erro=None, sync_texto=""), alertas=[])
