"""Monta RELATORIO.md a partir dos JSON da pasta de resultados. So transcreve dados; a interpretacao vai em `leitura.md`."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _ler(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _f(v: Any, casas: int = 1) -> str:
    return "—" if v is None else (f"{v:.{casas}f}" if isinstance(v, float) else str(v))


def _faixa(valores: list[float], casas: int = 1) -> str:
    return "—" if not valores else (f"{min(valores):.{casas}f}" if min(valores) == max(valores) else f"{min(valores):.{casas}f}–{max(valores):.{casas}f}")


def _tabela(cabecalho: list[str], linhas: list[list[str]]) -> str:
    out = ["| " + " | ".join(cabecalho) + " |", "|" + "|".join("---" for _ in cabecalho) + "|"]
    out += ["| " + " | ".join(l) + " |" for l in linhas]
    return "\n".join(out)


def linha_ensaio(e: dict[str, Any], rotulo: str) -> list[str]:
    a, cams = e["agregado"], list(e["cameras"].values())
    pior = lambda chave, q: max((c[chave][q] or 0.0) for c in cams)  # noqa: E731
    vram = a["vram_delta_medio_mb"]
    return [rotulo, _f(a["fps_efetivo_total"]), _faixa([c["fps_efetivo"] for c in cams]), str(a["quadros_descartados_total"]),
            f"{_f(a['cpu_processo_pct_do_total']['media'])} / {_f(a['cpu_processo_pct_do_total']['p95'])}",
            _f(a["cpu_processo_nucleos_equivalentes"]["media"]), _f(a["cpu_sistema_pct"]["media"]),
            f"{_f(a['rss_mb']['media'], 0)} / {_f(a['rss_pico_mb'], 0)}", _f(vram, 0), _f(a["gpu_util_pct"]["media"]),
            _f(sum(c["carga_inicial_s"] for c in cams) / len(cams)), _f(max(c["primeiro_quadro_s"] for c in cams)),
            f"{_f(pior('idade_em_uso_ms', 'p50'), 0)} / {_f(pior('idade_em_uso_ms', 'p95'), 0)}",
            f"{_f(pior('idade_na_publicacao_ms', 'p50'), 0)} / {_f(pior('idade_na_publicacao_ms', 'p95'), 0)}",
            f"{_f(pior('latencia_processamento_ms', 'p50'), 0)} / {_f(pior('latencia_processamento_ms', 'p95'), 0)}"]


CAB_ENSAIO = ["Ensaio", "fps total", "fps/câmera", "descart.", "CPU proc. % do total (média / p95)", "núcleos eq.", "CPU sist. %", "RAM MB (média / pico)",
              "VRAM Δ MB", "GPU %", "carga inicial s", "1º quadro s", "idade em uso ms (p50 / p95)*", "idade na publicação ms (p50 / p95)*",
              "latência proc. ms (p50 / p95)*"]


def montar(pasta: Path) -> str:
    amb = _ler(pasta / "ambiente.json")
    indice = _ler(pasta / "ensaios" / "indice.json") or []
    perfil = _ler(pasta / "perfil.json")
    escalas = [_ler(f) for f in sorted(pasta.glob("escala_*.json"))]
    leitura = (pasta / "leitura.md").read_text(encoding="utf-8") if (pasta / "leitura.md").exists() else "_Sem leitura registrada ainda (`leitura.md`)._"
    p: list[str] = ["# Bateria de referência — caminho atual da análise", "",
                    "Gerado por `tools/bateria_referencia.py relatorio`. Dados brutos em JSON na mesma pasta. "
                    "Procedimento e critérios: [README](../README.md).", ""]
    if amb:
        s, c, m = amb["sistema"], amb["codigo"], amb["modelos"]
        p += ["## 1. Ambiente e procedimento", "",
              f"* **Coleta:** {amb['coletado_em']} · **commit:** `{c['commit'][:10]}` ({c['ramo']}), "
              f"{len(c['arquivos_rastreados_modificados'])} arquivo(s) rastreado(s) modificado(s) na coleta.",
              f"* **Máquina:** {s['so']} · {s['cpu']} · {s['threads_logicos']} threads lógicos ({s['nucleos_fisicos']} núcleos físicos) · {s['ram_total_mb']} MB de RAM.",
              "* **GPUs:** " + "; ".join(f"{g['nome']} ({g['api']}{', ' + str(g['vram_total_mb']) + ' MB' if 'vram_total_mb' in g else ''})" for g in s["gpus"]),
              "* **Bibliotecas:** " + ", ".join(f"{k} {v}" for k, v in amb["bibliotecas"].items() if v),
              "* **Modelos (hash de conteúdo):** " + "; ".join(f"{k}: `{Path(v['caminho']).name}` `{v['sha256'][:16]}…`" for k, v in m.items()), "",
              "**Fontes (reproduzidas em tempo real e em laço, como câmera ao vivo):**", "",
              _tabela(["arquivo", "resolução", "fps", "quadros", "duração s", "SHA-256"],
                      [[f["arquivo"], f"{f['largura']}×{f['altura']}", _f(f["fps"]), str(f["quadros"]), _f(f["duracao_s"]), f"`{f['sha256'][:16]}…`"] for f in amb["fontes"]]), ""]
    ok = [i for i in indice if i.get("status") == "ok"]
    if ok:
        p += ["## 2. Ensaios de 1, 2 e 4 câmeras", "",
              "Cada ensaio roda em processo limpo; as métricas são da **janela estável** (após carga inicial e aquecimento, que ficam fora). "
              "`*` = pior câmera do ensaio. CPU em % do total da máquina (threads lógicos). `analysis_fps=0` = sem limite (comportamento anterior à "
              "mudança de cadência); `5` = padrão atual para fontes ao vivo.", ""]
        linhas = []
        for i in ok:
            e = _ler(pasta / "ensaios" / i["arquivo"])
            cfg = e["config"]
            rotulo = f"{cfg['cameras']} cam · fps {int(cfg['analysis_fps'])} · {e['colocacao']['bat0']['dispositivo']} · r{i['repeticao']}"
            if not e.get("validade", {}).get("valido", True):
                rotulo = "⚠ INVÁLIDO · " + rotulo
            linhas.append(linha_ensaio(e, rotulo))
        p += [_tabela(CAB_ENSAIO, linhas), ""]
        invalidos = [(i["id"], _ler(pasta / "ensaios" / i["arquivo"])["validade"]["problemas"]) for i in ok
                     if not _ler(pasta / "ensaios" / i["arquivo"]).get("validade", {}).get("valido", True)]
        if invalidos:
            p += ["**⚠ Ensaios inválidos (câmera parou durante a janela; não entram nas comparações):** " + "; ".join(f"{k}: {'; '.join(v)}" for k, v in invalidos), ""]
        falhos = [i for i in indice if i.get("status") != "ok"]
        if falhos:
            p += ["**Ensaios que não completaram:** " + "; ".join(f"{i['id']} ({i['status']})" for i in falhos), ""]
        grupos: dict[str, list[dict[str, Any]]] = {}
        for i in ok:
            e_i = _ler(pasta / "ensaios" / i["arquivo"])
            if e_i.get("validade", {}).get("valido", True):          # ensaio invalido nao entra na variacao
                grupos.setdefault(i["id"].rsplit("_r", 1)[0], []).append(e_i)
        if any(len(g) > 1 for g in grupos.values()):
            p += ["### Variação entre repetições", "", "Cada repetição é um processo novo. Mostra mín–máx entre as repetições (n = número de execuções).", ""]
            linhas = []
            for chave, es in grupos.items():
                pior = lambda e, k, q: max((c[k][q] or 0.0) for c in e["cameras"].values())  # noqa: E731
                linhas.append([chave, str(len(es)), _faixa([e["agregado"]["fps_efetivo_total"] for e in es]),
                               _faixa([e["agregado"]["cpu_processo_pct_do_total"]["media"] or 0 for e in es]),
                               _faixa([e["agregado"]["cpu_sistema_pct"]["media"] or 0 for e in es]),
                               _faixa([e["agregado"]["rss_mb"]["media"] or 0 for e in es], 0),
                               _faixa([pior(e, "idade_em_uso_ms", "p95") for e in es], 0),
                               _faixa([pior(e, "latencia_processamento_ms", "p95") for e in es], 0)])
            p += [_tabela(["configuração", "n", "fps total", "CPU proc. % (média)", "CPU sist. % (média)", "RAM MB (média)", "idade em uso p95 ms*", "latência proc. p95 ms*"], linhas), ""]
        p += ["### Janela e fases por ensaio", ""]
        linhas = []
        for i in ok:
            e = _ler(pasta / "ensaios" / i["arquivo"])
            cfg = e["config"]
            linhas.append([i["id"], str(cfg["aquecimento_s"]), str(cfg["janela_real_s"]), _f(e["fases"]["importacao_s"], 2),
                           str(e["agregado"]["amostras_de_recursos_na_janela"]), f"{e['eventos']['popup_alert']} / {e['eventos']['popup_suppressed']}",
                           ", ".join(f"{k}={v['dispositivo']}/{v['backend']}" for k, v in list(e["colocacao"].items())[:1])])
        p += [_tabela(["ensaio", "aquecimento s", "janela estável s", "import. s", "amostras de recursos", "alertas / suprimidos", "colocação"], linhas), ""]
    if perfil or escalas:
        p += ["## 3. Hipótese do GIL (separada da medição de capacidade)", ""]
    if perfil:
        pf = perfil["perfil"]
        p += [f"### 3.1 Perfil de uma câmera (`{perfil['video']}`, {perfil['quadros_por_fase']} quadros; mesmos quadros e estado reiniciado nas duas passadas; "
              f"{perfil['placement']['dispositivo']}/{perfil['placement']['backend']})", "",
              f"* Sem perfilador: **{perfil['fps_sem_perfil']} fps** ({perfil['ms_por_quadro_sem_perfil']} ms/quadro); com cProfile: {perfil['fps_com_perfil']} fps "
              f"(overhead {perfil['overhead_do_cprofile_pct']} %).",
              f"* Do tempo próprio total: **Python puro (executa bytecode, segura o GIL): {pf['pct_python_puro']} %** · envoltório Python de inferência nativa "
              f"(OpenVINO): {pf['pct_envoltorio_de_inferencia_nativa']} % · builtins/C visíveis (OpenCV, NumPy, imencode…): {pf['pct_nativo_builtin']} %.",
              "* Python puro por pacote (% do total): " + (", ".join(f"{k} {v}" for k, v in list(pf["pct_python_por_pacote"].items())[:6]) or "—"), "",
              "Maiores consumidores de Python puro: " + "; ".join(f"`{x['funcao']}` {x['pct']} %" for x in pf["top_python"][:5]), "",
              "Envoltórios de inferência: " + "; ".join(f"`{x['funcao']}` {x['pct']} %" for x in pf["top_envoltorio"][:3]), "",
              "Maiores builtins/C: " + "; ".join(f"`{x['funcao']}` {x['pct']} %" for x in pf["top_nativo"][:5]), "", f"_{perfil['leitura']}_", ""]
    for escala in escalas:
        v = escala["veredito"]
        p += [f"### 3.2 Threads × processos — {escala['n']} câmeras, analysis_fps={int(escala['analysis_fps'])}, dispositivo `{escala['dispositivo']}`", "",
              _tabela(["cenário", "fps total", "CPU sistema % (média / p95)", "descartados", "RAM"],
                      [["A) threads em 1 processo", _f(escala["threads"]["fps_total"]), f"{_f(escala['threads']['cpu_sistema_pct']['media'])} / {_f(escala['threads']['cpu_sistema_pct']['p95'])}",
                        str(escala["threads"]["descartados"]), f"{_f(escala['threads']['rss_mb']['media'], 0)} MB"],
                       [f"B) {escala['n']} processos × 1 câmera", _f(escala["processos"]["fps_total"]), f"{_f(escala['processos']['cpu_sistema_pct']['media'])} / {_f(escala['processos']['cpu_sistema_pct']['p95'])}",
                        str(escala["processos"]["descartados"]), f"{_f(escala['processos']['rss_mb_soma'], 0)} MB (soma)"]]), "",
              f"* **Razão B/A:** {v['razao_processos_sobre_threads']} · **CPU média do sistema em A:** {_f(v['cpu_sistema_media_threads_pct'])} %.",
              f"* **Conclusão pela regra pré-registrada:** **{v['conclusao']}**. Regra: {v['regra']}.", ""]
    pasta_ep = pasta / "episodios_v2" if (pasta / "episodios_v2").exists() else pasta / "episodios"
    ep = sorted(pasta_ep.glob("avaliacao_*.json")) if pasta_ep.exists() else []
    if ep:
        p += ["## 4. Qualidade dos alertas (episódios)", "",
              f"Avaliador v{(_ler(ep[0]).get('versao_avaliador') or 1)} ({pasta_ep.name}). Só **anotação oficial** (humana, confirmada, revisão completa, procedência do arquivo "
              "confirmada) com **cobertura integral** da análise gera métricas; o restante aparece como pendência.", ""]
        linhas = []
        for f in ep:
            a = _ler(f)
            mt, cob, an = a["metricas"], a.get("cobertura"), a.get("anotacao", {})
            linhas.append([a["video"], str(a["alertas_emitidos"]), str(a["alertas_suprimidos"]), str(a["trilhas_distintas_nos_alertas"]),
                           "—" if cob is None else ("sim" if cob["integral"] else "**não**"), an.get("estado", "—"),
                           "—" if mt is None else f"{mt['detectados']}/{mt['episodios_positivos']}", "—" if mt is None else str(mt["n_alertas_falsos"]),
                           "—" if mt is None else str(mt["alertas_repetidos"]),
                           "—" if mt is None else f"{mt['trilhas_adicionais']} / {mt['transicoes_de_trilha']}", a.get("motivo", "")])
        p += [_tabela(["vídeo", "alertas", "suprimidos", "trilhas nos alertas", "cobertura integral", "anotação", "detectados", "falsos", "repetidos", "trilhas adic. / transições", "observação"], linhas), "",
              "`—` = indisponível (não estimado).", ""]
        prov = sorted(pasta_ep.glob("provisorio_*.json"))
        if prov:
            p += ["### 4.1 Leitura provisória das propostas de IA (NÃO são métricas de qualidade)", "",
                  "**Cálculo sobre anotações não confirmadas por pessoa**, só para mostrar o que sairia se elas fossem confirmadas. Não citar como qualidade do sistema.", ""]
            linhas = []
            for f in prov:
                a = _ler(f)
                lt = a.get("leitura")
                linhas.append([a["video"], a["anotacao"]["estado"], "—" if lt is None else f"{lt['detectados']}/{lt['episodios_positivos']}",
                               "—" if lt is None else (", ".join(lt["perdidos"]) or "nenhum"), "—" if lt is None else str(lt["n_alertas_fora_dos_episodios_propostos"]),
                               "—" if lt is None else str(lt["alertas_repetidos"]), "—" if lt is None else str(len(lt["alertas_ambiguos"])),
                               a.get("ressalva") or a.get("motivo") or "", "; ".join(a["anotacao"]["pendencias"]) + (" | AVISOS: " + "; ".join(a["anotacao"]["avisos"]) if a["anotacao"]["avisos"] else "")])
            p += [_tabela(["vídeo", "estado da proposta", "detectados", "perdidos", "alertas fora dos episódios propostos", "repetidos", "alertas ambíguos", "ressalvas", "pendências para confirmar"], linhas), ""]
    p += ["## 5. Leitura dos resultados", "", leitura, ""]
    p += ["## 6. Limitações", "",
          "* Fontes são **arquivos de vídeo reproduzidos em tempo real**, não câmeras RTSP: não medem rede, decodificação H.264/H.265 em tempo real do go2rtc nem a latência do navegador.",
          "* Poucos vídeos curtos (15–32 s) em laço: o conteúdo se repete e é limitado; resultados dependem da cena (número de pessoas) e não representam carga máxima de um hospital.",
          "* Uma repetição por configuração (salvo indicação): sem estimativa de variância entre execuções; a dispersão mostrada é entre amostras dentro de uma janela.",
          "* Máquina única (notebook com GPU integrada e NVIDIA de 4 GB); resultados não se transferem a outro hardware sem repetir.",
          "* A instrumentação (`_process_frame` envolvido por cronômetros) e o amostrador (20 Hz) têm custo próprio, pequeno e não medido isoladamente.",
          "* A idade \"em uso\" assume um consumidor que lê o resultado mais recente em instantes aleatórios; ela não inclui a captura física da câmera nem a rede.",
          "* Perfil do GIL: cProfile infla o tempo Python; \"tempo em bytecode\" não é \"tempo esperando o GIL\". A comparação threads × processos tem confundidores declarados em `escala.py`.",
          "* Qualidade (perdidos/falsos/repetidos) depende de episódios revisados por pessoa; sem revisão, só há contagem descritiva.", ""]
    return "\n".join(p)
