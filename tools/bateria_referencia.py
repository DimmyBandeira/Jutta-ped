"""Bateria de referencia do CAMINHO ATUAL da analise (etapa 02 do plano do motor).

Mede, de forma reproduzivel, custo e comportamento de 1, 2 e 4 cameras simuladas (videos reais reproduzidos em tempo real,
em laco), a hipotese do GIL (perfil + comparacao threads x processos) e produz a base para contar alertas perdidos, falsos e
repetidos por troca de trilha nos episodios REVISADOS por uma pessoa. Diagnostico: nao e teste automatico.

Subcomandos (saida padrao: docs/referencia/<data>/):
    ambiente   versoes, hardware, hashes de modelos e videos, configuracao
    ensaio     um ensaio (N cameras)            -> JSON
    matriz     todos os ensaios, cada um em processo limpo
    perfil     cProfile de uma camera           -> JSON   (evidencia para a hipotese do GIL)
    escala     N cameras em threads x N processos        (idem)
    episodios  analisa os videos de episodios e prepara o pacote de revisao
    relatorio  monta RELATORIO.md a partir dos JSON

Procedimento completo: docs/referencia/README.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VIDEOS_PADRAO = [
    "videos/DVR-1-Ch.7-8 5-- PAV-Hall-ELEV SERVI.dav",
    "videos/DVR-3-Ch.0-1 ACESSO-BLINDEX.avi",
    "videos/Corredor pediatria.mp4",
    "videos/Corredor pediatria 2.mp4",
]


def _videos(args) -> list[Path]:
    return [ROOT / v if not Path(v).is_absolute() else Path(v) for v in (args.videos or VIDEOS_PADRAO)]


def _saida_dir(args) -> Path:
    d = Path(args.saida) if args.saida else ROOT / "docs" / "referencia" / date.today().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _gravar(caminho: Path, dados) -> None:
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"gravado: {caminho}")


def _cmd_ambiente(args) -> int:
    from tools.bateria.ambiente import coletar

    _gravar(_saida_dir(args) / "ambiente.json", coletar(_videos(args)))
    return 0


def _cmd_ensaio(args) -> int:
    from tools.bateria.ensaio import executar_ensaio

    r = executar_ensaio(n_cameras=args.cameras, analysis_fps=args.analysis_fps, dispositivo=args.dispositivo, videos=_videos(args),
                        aquecimento_s=args.aquecimento, estavel_s=args.estavel, inicio_janela_epoch=args.inicio_janela_epoch,
                        deslocamento_video=args.deslocamento_video)
    if args.arquivo:
        Path(args.arquivo).parent.mkdir(parents=True, exist_ok=True)
        Path(args.arquivo).write_text(json.dumps(r, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    else:
        print(json.dumps(r, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _cmd_matriz(args) -> int:
    from tools.bateria import matriz

    pasta = _saida_dir(args) / "ensaios"
    itens = matriz.plano(args.cameras, args.analysis_fps, args.extra_cuda, args.repeticoes, args.repeticao_inicial)
    indice = matriz.executar_plano(itens, pasta, aquecimento=args.aquecimento, estavel=args.estavel, videos=args.videos)
    return 0 if all(i["status"] == "ok" for i in indice) else 1


def _cmd_perfil(args) -> int:
    from tools.bateria.perfil import perfilar

    _gravar(_saida_dir(args) / "perfil.json", perfilar(_videos(args)[0], quadros=args.quadros, dispositivo=args.dispositivo))
    return 0


def _cmd_escala(args) -> int:
    from tools.bateria.escala import comparar

    _gravar(_saida_dir(args) / f"escala_{args.dispositivo}.json", comparar(args.cameras, analysis_fps=args.analysis_fps, videos=[str(v) for v in _videos(args)],
                                                       aquecimento=args.aquecimento, estavel=args.estavel, dispositivo=args.dispositivo))
    return 0


def _cmd_episodios(args) -> int:
    import shutil

    from tools.bateria.episodios import analisar_video, avaliar, carregar_anotacoes, leitura_provisoria, preparar_revisao

    saida = _saida_dir(args) / "episodios_v2"      # v2: a pasta `episodios/` (avaliador v1) fica como historico
    saida.mkdir(parents=True, exist_ok=True)
    confirmadas = carregar_anotacoes(Path(args.episodios) if args.episodios else ROOT / "docs" / "referencia" / "episodios")
    propostas = carregar_anotacoes(Path(args.propostas) if args.propostas else _saida_dir(args) / "propostas_ia")
    videos = _videos(args)[:2]
    if args.negativo and (ROOT / args.negativo).exists():
        videos.append(ROOT / args.negativo)               # candidato a negativo: analisado, mas SEM rotulo ate confirmacao humana
    for video in videos:
        analise = analisar_video(video, dispositivo=args.dispositivo)
        evid = analise.pop("evidencias_tmp")
        _gravar(saida / f"analise_{video.stem}.json", analise)
        cob = analise["cobertura"]
        print(f"{video.name}: cobertura integral={cob['integral']} ({cob['quadros_analisados']}/{cob['quadros_do_arquivo']})")
        pacote = preparar_revisao(analise, saida / "revisao" / video.stem, video)
        print("pacote de revisao:", pacote["pasta"])
        conf = confirmadas.get(analise["sha256"])
        _gravar(saida / f"avaliacao_{video.stem}.json", avaliar(analise, conf[1] if conf else None, tolerancia_s=args.tolerancia))
        prop = propostas.get(analise["sha256"])
        if prop is not None:
            _gravar(saida / f"provisorio_{video.stem}.json", leitura_provisoria(analise, prop[1], tolerancia_s=args.tolerancia))
        shutil.rmtree(evid, ignore_errors=True)
    return 0


def _cmd_confirmar(args) -> int:
    """Registra a DECLARACAO de uma pessoa que revisou o video. Nao revisa nada; cada declaracao e uma flag explicita."""
    from datetime import date

    from tools.bateria.episodios import confirmar_anotacao

    origem = Path(args.proposta)
    proposta = json.loads(origem.read_text(encoding="utf-8"))
    criterios = {}
    for item in args.criterio_inicio or []:
        chave, _, texto = item.partition("=")
        criterios[chave.strip()] = texto.strip()
    try:
        novo = confirmar_anotacao(proposta, revisor=args.revisor, data=args.data or date.today().isoformat(), revisao_completa=args.vi_o_video_inteiro,
                                  episodios_conferidos=args.episodios_conferidos, procedencia_confirmada=args.procedencia_confirmada,
                                  procedencia_fonte=args.procedencia_fonte, procedencia_tipo=args.procedencia_tipo, criterios_inicio=criterios,
                                  revisao_cega=args.revisao_cega)
    except ValueError as exc:
        print(f"recusado: {exc}", file=sys.stderr)
        return 2
    destino = Path(args.destino) if args.destino else ROOT / "docs" / "referencia" / "episodios"
    destino.mkdir(parents=True, exist_ok=True)
    arq = destino / (Path(novo["video"]).stem + ".json")
    if arq.exists() and not args.sobrescrever:
        print(f"recusado: {arq} ja existe (use --sobrescrever)", file=sys.stderr)
        return 2
    _gravar(arq, novo)
    print("Confirmacao registrada como DECLARACAO de", novo["revisor"], "- o avaliador nao verifica identidade. A proposta original NAO foi alterada.")
    print("Reavalie sem refazer a inferencia:  python tools/bateria_referencia.py reavaliar --saida", args.saida or "docs/referencia/<data>")
    return 0


def _cmd_reavaliar(args) -> int:
    """Recalcula avaliacao/leitura provisoria a partir das analises JA salvas (sem inferencia), p.ex. depois de confirmar uma anotacao."""
    from tools.bateria.episodios import avaliar, carregar_anotacoes, leitura_provisoria

    pasta = _saida_dir(args) / "episodios_v2"
    confirmadas = carregar_anotacoes(Path(args.episodios) if args.episodios else ROOT / "docs" / "referencia" / "episodios")
    propostas = carregar_anotacoes(Path(args.propostas) if args.propostas else _saida_dir(args) / "propostas_ia")
    for arq in sorted(pasta.glob("analise_*.json")):
        analise = json.loads(arq.read_text(encoding="utf-8"))
        stem = arq.stem.removeprefix("analise_")
        conf = confirmadas.get(analise["sha256"])
        _gravar(pasta / f"avaliacao_{stem}.json", avaliar(analise, conf[1] if conf else None, tolerancia_s=args.tolerancia))
        prop = propostas.get(analise["sha256"])
        if prop is not None:
            _gravar(pasta / f"provisorio_{stem}.json", leitura_provisoria(analise, prop[1], tolerancia_s=args.tolerancia))
    return 0


def _cmd_relatorio(args) -> int:
    from tools.bateria.relatorio import montar

    pasta = _saida_dir(args)
    (pasta / "RELATORIO.md").write_text(montar(pasta), encoding="utf-8")
    print(f"gravado: {pasta / 'RELATORIO.md'}")
    return 0


def _comuns(p: argparse.ArgumentParser) -> None:
    p.add_argument("--videos", nargs="*", help="videos das cameras, em ordem (padrao: 2 DVR de 19/06 + 2 corredores)")
    p.add_argument("--saida", help="pasta de saida (padrao docs/referencia/<hoje>)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tools/bateria_referencia.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="comando", required=True)
    a = sub.add_parser("ambiente")
    _comuns(a)
    a.set_defaults(func=_cmd_ambiente)
    e = sub.add_parser("ensaio")
    _comuns(e)
    e.add_argument("--cameras", type=int, required=True)
    e.add_argument("--analysis-fps", type=float, default=5.0, help="0 = sem limite (comportamento anterior)")
    e.add_argument("--dispositivo", default="auto", help="auto | cuda")
    e.add_argument("--aquecimento", type=float, default=15.0)
    e.add_argument("--estavel", type=float, default=45.0)
    e.add_argument("--inicio-janela-epoch", type=float, default=None, help="instante (time.time) em que a janela estavel comeca")
    e.add_argument("--deslocamento-video", type=int, default=0)
    e.add_argument("--arquivo", help="grava o JSON neste arquivo em vez de imprimir")
    e.set_defaults(func=_cmd_ensaio)
    m = sub.add_parser("matriz")
    _comuns(m)
    m.add_argument("--cameras", type=int, nargs="*", default=[1, 2, 4])
    m.add_argument("--analysis-fps", type=float, nargs="*", default=[0.0, 5.0])
    m.add_argument("--extra-cuda", type=int, nargs="*", default=[1, 4], help="cameras para ensaios extras com dispositivo cuda (analysis_fps 5)")
    m.add_argument("--repeticoes", type=int, default=1)
    m.add_argument("--repeticao-inicial", type=int, default=1, help="numero da 1a repeticao desta chamada (as anteriores ficam no indice)")
    m.add_argument("--aquecimento", type=float, default=15.0)
    m.add_argument("--estavel", type=float, default=45.0)
    m.set_defaults(func=_cmd_matriz)
    pf = sub.add_parser("perfil")
    _comuns(pf)
    pf.add_argument("--quadros", type=int, default=150)
    pf.add_argument("--dispositivo", default="auto")
    pf.set_defaults(func=_cmd_perfil)
    es = sub.add_parser("escala")
    _comuns(es)
    es.add_argument("--cameras", type=int, default=4)
    es.add_argument("--analysis-fps", type=float, default=0.0)
    es.add_argument("--dispositivo", default="auto")
    es.add_argument("--aquecimento", type=float, default=15.0)
    es.add_argument("--estavel", type=float, default=45.0)
    es.set_defaults(func=_cmd_escala)
    ep = sub.add_parser("episodios")
    _comuns(ep)
    ep.add_argument("--episodios", help="pasta com as anotacoes CONFIRMADAS por humano (padrao docs/referencia/episodios)")
    ep.add_argument("--propostas", help="pasta com propostas (ex.: de IA), so para a leitura provisoria (padrao <saida>/propostas_ia)")
    ep.add_argument("--negativo", default="videos/stream-normal.mp4", help="candidato a negativo a analisar (vazio = nenhum)")
    ep.add_argument("--tolerancia", type=float, default=2.0)
    ep.add_argument("--dispositivo", default="auto")
    ep.set_defaults(func=_cmd_episodios)
    cf = sub.add_parser("confirmar", help="registra a declaracao de revisao humana sobre uma proposta (nao revisa nada)")
    cf.add_argument("proposta", help="arquivo de proposta (ex.: propostas_ia/dvr1.json)")
    cf.add_argument("--revisor", required=True, help="nome da PESSOA que revisou o video inteiro")
    cf.add_argument("--data", help="AAAA-MM-DD (padrao: hoje)")
    cf.add_argument("--vi-o-video-inteiro", action="store_true", help="declaro ter revisado o video por inteiro")
    cf.add_argument("--episodios-conferidos", action="store_true", help="declaro ter conferido (ou corrigido) cada episodio e intervalo da proposta")
    cf.add_argument("--procedencia-confirmada", action="store_true", help="declaro ter confirmado a origem do arquivo de video")
    cf.add_argument("--procedencia-fonte", required=True, help="de onde veio o arquivo de video (ex.: DVR do 5o andar, exportado em 19/06)")
    cf.add_argument("--procedencia-tipo", required=True, choices=("real", "sintetico"), help="o video e gravacao real ou sintetico?")
    cf.add_argument("--criterio-inicio", action="append", help="ID=texto: o que marca o inicio do episodio (repetivel, um por episodio)")
    cf.add_argument("--revisao-cega", action="store_true", help="declaro que NAO consultei os alertas do sistema ao revisar")
    cf.add_argument("--saida", help="pasta da rodada (so para a mensagem de proximo passo)")
    cf.add_argument("--destino", help="pasta de saida (padrao docs/referencia/episodios)")
    cf.add_argument("--sobrescrever", action="store_true")
    cf.set_defaults(func=_cmd_confirmar)
    ra = sub.add_parser("reavaliar", help="recalcula avaliacao/leitura provisoria das analises ja salvas (sem inferencia)")
    _comuns(ra)
    ra.add_argument("--episodios")
    ra.add_argument("--propostas")
    ra.add_argument("--tolerancia", type=float, default=2.0)
    ra.set_defaults(func=_cmd_reavaliar)
    rl = sub.add_parser("relatorio")
    _comuns(rl)
    rl.set_defaults(func=_cmd_relatorio)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
