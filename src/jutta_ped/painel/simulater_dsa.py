"""Importa somente as fontes RTSP de um cenário SimulaterDSA; não abre os vídeos locais."""
import re

import yaml


def ler_cenario(conteudo: bytes, host: str) -> list[tuple[str, str]]:
    host = host.strip()
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host):
        raise ValueError("Informe o IP ou nome do servidor SimulaterDSA, sem porta ou protocolo.")
    if len(conteudo) > 1_000_000:
        raise ValueError("O YAML excede 1 MB.")
    try:
        dados = yaml.safe_load(conteudo)
    except (yaml.YAMLError, UnicodeError, RecursionError) as exc:
        raise ValueError("YAML inválido.") from exc
    if not isinstance(dados, dict) or type(dados.get("version")) is not int or dados["version"] != 1:
        raise ValueError("Esperado cenário SimulaterDSA version: 1.")
    server, sources = dados.get("server"), dados.get("sources")
    if not isinstance(server, dict) or not isinstance(sources, list) or len(sources) > 200:
        raise ValueError("Cenário deve conter server e sources (até 200 fontes).")
    porta = server.get("rtsp_port", 8554)
    if type(porta) is not int or not 1 <= porta <= 65535:
        raise ValueError("Porta RTSP inválida.")
    saida, nomes = [], set()
    for fonte in sources:
        if not isinstance(fonte, dict) or type(fonte.get("enabled", True)) is not bool:
            raise ValueError("Fonte inválida no cenário.")
        if not fonte.get("enabled", True):
            continue
        cameras = fonte.get("cameras")
        if not isinstance(cameras, list):
            raise ValueError("Cada fonte habilitada deve declarar cameras.")
        for nome in cameras:
            if not isinstance(nome, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", nome) or nome in {".", ".."}:
                raise ValueError("Nome de câmera inválido no cenário.")
            if nome.lower() in nomes:
                raise ValueError("Câmera repetida no cenário.")
            nomes.add(nome.lower())
            saida.append((nome, f"rtsp://{host}:{porta}/{nome}"))
            if len(saida) > 200:
                raise ValueError("Importe no máximo 200 câmeras por cenário.")
    if not saida:
        raise ValueError("Nenhuma câmera habilitada no cenário.")
    return saida
