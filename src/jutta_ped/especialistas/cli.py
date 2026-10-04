"""CLI dos especialistas.

    python -m src.jutta_ped.especialistas validar <pasta-ou-zip> [--formato texto|json] [--host-protocolo 1]
    python -m src.jutta_ped.especialistas hash <arquivo-ou-pasta>
    python -m src.jutta_ped.especialistas instalar <pasta-ou-zip> [--raiz runtime/especialistas]
    python -m src.jutta_ped.especialistas ativar <id> <versao> | reverter <id> | remover <id> <versao>
    python -m src.jutta_ped.especialistas listar | recuperar
    python -m src.jutta_ped.especialistas executar <id> <imagem> [--parametro nome=valor ...] [--altura PX]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.jutta_ped.especialistas.fontes import ErroPacote, Limites, PastaFonte
from src.jutta_ped.especialistas.hashing import hash_pasta, sha256_blocos
from src.jutta_ped.especialistas.instalador import RAIZ_PADRAO, ErroInstalacao, Instalador
from src.jutta_ped.especialistas.validador import NOMES_CATEGORIA, Relatorio, validar

RODAPE_ESCOPO = (
    "CONFORMIDADE ESTATICA: o pacote respeita o contrato (estrutura, referencias, arquivos e hashes).\n"
    "Isto NAO aprova o modelo para execucao: pesos nao foram carregados nem executados."
)


def formatar_texto(rel: Relatorio) -> str:
    linhas = [f"Pacote: {rel.origem}"]
    if rel.pacote:
        linhas.append(f"Especialista: {rel.pacote['id']} {rel.pacote['versao']} ({rel.pacote['nome']})")
    if rel.problemas:
        linhas.append(f"\nPROBLEMAS ({len(rel.problemas)}):")
        for p in rel.ordenado():
            onde = f" [{p.caminho}]" if p.caminho else ""
            linhas.append(f"  - ({NOMES_CATEGORIA[p.categoria]}) {p.codigo}{onde}: {p.mensagem}")
    if rel.avisos:
        linhas.append(f"\nAVISOS ({len(rel.avisos)}):")
        linhas.extend(f"  - {a}" for a in sorted(rel.avisos))
    if rel.hashes:
        linhas.append("\nHASHES CALCULADOS:")
        linhas.extend(f"  {k}: {v}" for k, v in sorted(rel.hashes.items()))
    linhas.append("\nNAO VERIFICADO:")
    linhas.extend(f"  - {n}" for n in sorted(set(rel.nao_verificado)))
    linhas.append("")
    if rel.conformidade_estatica:
        linhas.append("RESULTADO: " + RODAPE_ESCOPO)
    else:
        linhas.append(f"RESULTADO: NAO CONFORME (codigo de saida {rel.codigo_saida}: {NOMES_CATEGORIA[rel.codigo_saida]}).")
    return "\n".join(linhas)


def _cmd_validar(args: argparse.Namespace) -> int:
    try:
        rel = validar(args.caminho, host_protocolo=args.host_protocolo, limites=Limites())
    except ErroPacote as exc:
        rel = Relatorio(origem=str(args.caminho), host_protocolo=args.host_protocolo, limites=Limites().como_dict())
        rel.adicionar(exc.categoria, exc.codigo, exc.caminho, exc.mensagem)
    except (FileNotFoundError, OSError) as exc:
        print(f"erro: nao foi possivel abrir '{args.caminho}': {exc}", file=sys.stderr)
        return 1
    if args.formato == "json":
        print(json.dumps(rel.como_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(formatar_texto(rel))
    return rel.codigo_saida


def _cmd_hash(args: argparse.Namespace) -> int:
    alvo = Path(args.caminho)
    try:
        if alvo.is_dir():
            fonte = PastaFonte(alvo)
            lst = fonte.listar(Limites())
            if lst.problemas:
                for p in lst.problemas:
                    print(f"erro: {p.codigo} [{p.caminho}]: {p.mensagem}", file=sys.stderr)
                return 3
            print(hash_pasta((e.caminho, e.tamanho, (lambda c=e.caminho: fonte.blocos(c))) for e in lst.entradas))
        elif alvo.is_file():
            with open(alvo, "rb") as f:
                print(sha256_blocos(iter(lambda: f.read(1 << 20), b"")))
        else:
            print(f"erro: '{alvo}' nao existe", file=sys.stderr)
            return 1
    except OSError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 1
    return 0


def _instalacao(args: argparse.Namespace) -> int:
    inst = Instalador(args.raiz)
    try:
        if args.comando == "instalar":
            r = inst.instalar(args.caminho)
            print(f"{'ja instalado' if r.ja_instalado else 'instalado'}: {r.id} {r.versao} (inativo; use 'ativar') em {r.caminho}")
        elif args.comando == "ativar":
            print(f"ativa: {args.id} {inst.ativar(args.id, args.versao)}")
        elif args.comando == "reverter":
            print(f"revertido para: {args.id} {inst.reverter(args.id)}")
        elif args.comando == "remover":
            inst.remover(args.id, args.versao)
            print(f"removido: {args.id} {args.versao}")
        elif args.comando == "listar":
            print(json.dumps(inst.listar(), ensure_ascii=False, indent=2, sort_keys=True))
        elif args.comando == "recuperar":
            acoes = inst.recuperar()
            print(chr(10).join(acoes) if acoes else "nada a recuperar")
    except ErroInstalacao as exc:
        print(f"erro ({exc.slug}): {exc.mensagem}", file=sys.stderr)
        if exc.relatorio is not None:
            print(formatar_texto(exc.relatorio), file=sys.stderr)
        return exc.codigo
    except OSError as exc:
        print(f"erro de E/S: {exc}", file=sys.stderr)
        return 13
    return 0


def _executar(args: argparse.Namespace) -> int:
    import cv2

    from src.jutta_ped.especialistas.executor import Executor, Pedido

    imagem = cv2.imread(args.imagem)
    if imagem is None:
        print(f"erro: nao foi possivel ler a imagem '{args.imagem}'", file=sys.stderr)
        return 1
    params: dict[str, float] = {}
    for item in args.parametro or []:
        nome, _, valor = item.partition("=")
        try:
            params[nome] = float(valor)
        except ValueError:
            print(f"erro: parametro invalido '{item}' (use nome=valor numerico)", file=sys.stderr)
            return 1
    ex = Executor(Instalador(args.raiz))
    resultado = ex.executar(args.id, [Pedido(pedido_id="cli-1", recorte=imagem, fonte=args.fonte, altura_pessoa_px=args.altura, parametros=params)])[0]
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    return 0 if resultado["estado"] == "ok" else 17


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.jutta_ped.especialistas", description="Ferramentas de pacotes de especialista (protocolo 1).")
    sub = ap.add_subparsers(dest="comando", required=True)
    v = sub.add_parser("validar", help="validacao estatica de pasta ou .zip")
    v.add_argument("caminho")
    v.add_argument("--formato", choices=("texto", "json"), default="texto")
    v.add_argument("--host-protocolo", type=int, default=1, help="protocolo suportado pelo host (padrao 1)")
    v.set_defaults(func=_cmd_validar)
    h = sub.add_parser("hash", help="SHA-256 de um arquivo, ou hash canonico (jutta-dir-v1) de uma pasta OpenVINO")
    h.add_argument("caminho")
    h.set_defaults(func=_cmd_hash)
    for nome, ajuda, posicionais in (
        ("instalar", "valida, extrai de forma isolada e registra (fica inativo)", ["caminho"]),
        ("ativar", "torna uma versao instalada a ativa (confere integridade)", ["id", "versao"]),
        ("reverter", "volta para a versao anterior valida", ["id"]),
        ("remover", "remove uma versao inativa", ["id", "versao"]),
        ("listar", "mostra o registro de instalacao", []),
        ("recuperar", "conclui ou desfaz operacoes interrompidas", []),
    ):
        sp = sub.add_parser(nome, help=ajuda)
        for p in posicionais:
            sp.add_argument(p)
        sp.add_argument("--raiz", default=str(RAIZ_PADRAO), help="armazenamento (padrao runtime/especialistas)")
        sp.set_defaults(func=_instalacao)
    ex = sub.add_parser("executar", help="roda o especialista ATIVO sobre uma imagem (diagnostico; sem alertas)")
    ex.add_argument("id")
    ex.add_argument("imagem")
    ex.add_argument("--parametro", action="append", help="nome=valor (repetivel)")
    ex.add_argument("--fonte", default="visivel", choices=("visivel", "termica"))
    ex.add_argument("--altura", type=int, default=None, help="altura da pessoa no quadro original, em px")
    ex.add_argument("--raiz", default=str(RAIZ_PADRAO))
    ex.set_defaults(func=_executar)
    args = ap.parse_args(argv)
    return args.func(args)
