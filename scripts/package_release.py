"""Monta a pasta de release da IA Pediatria (Demo Viewer) e gera o ZIP final.

Roda DEPOIS de scripts/build_nuitka_windows.ps1 (ou e chamado por ele com
-Package). Nao compila nada: so copia o dist do Nuitka e gera os metadados
de entrega (README_CLIENTE.txt, BUILD_INFO.json, CHECKSUMS.sha256), depois
zipa tudo.

Modelos NAO entram no release por padrao (ver modulo/pediatria/
model_registry_service.py e Ctrl+Shift+M no Demo Viewer): o pacote so cria
o esqueleto vazio (runtime/models/, runtime/model_registry.example.json).
O tecnico importa o modelo real depois, pela tela escondida.

Uso:
    .venv\\Scripts\\python.exe scripts\\package_release.py --version 20260708
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = ROOT / "build" / "nuitka" / "run_pediatria_viewer.dist"
EXE_NAME = "IA_Pediatria_DemoViewer.exe"
RELEASE_PARENT = ROOT / "release"
APP_NAME = "IA_Pediatria"

# Nunca deve aparecer dentro da pasta de release final.
FORBIDDEN_PATTERNS = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx",
    ".git", ".github", "__pycache__", "*.pyc", ".pytest_cache", ".mypy_cache",
    "*.ipynb", "*.log", "*.dav", "*.mp4", "*.avi", "*.mov",
]

# Falsos positivos conhecidos: arquivos publicos, sem segredo nenhum, que
# batem em algum padrao acima so pela extensao. certifi/cacert.pem e o
# bundle publico de CAs raiz (Mozilla) que a lib `certifi` embute em toda
# instalacao Python -- necessario para verificacao HTTPS, nao e credencial.
# Caminho relativo exato (nao so o nome) para nao abrir excecao demais.
ALLOWED_FORBIDDEN_OVERRIDES = {
    "certifi/cacert.pem",
}

# Modelos sao externos ao build (ver modulo/pediatria/model_registry_service.py):
# nenhum peso de modelo pode vazar para dentro do release. Extensoes de
# arquivo de modelo, checadas a parte do FORBIDDEN_PATTERNS generico porque
# aqui a violacao e especifica (peso de modelo, nao segredo/artefato de dev).
MODEL_WEIGHT_PATTERNS = ["*.pt", "*.onnx", "*.xml", "*.bin"]

# Mesma ideia do ALLOWED_FORBIDDEN_OVERRIDES acima: caminho relativo exato
# de arquivo que bate em MODEL_WEIGHT_PATTERNS mas nao e peso de modelo (ex.:
# alguma dependencia que legitimamente ship .xml/.bin sem ser modelo).
# Vazio ate aparecer um caso real -- nao adivinhar de antemao.
ALLOWED_MODEL_WEIGHT_OVERRIDES: set[str] = set()


def run_git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()
    except Exception as exc:  # git ausente, nao e repo, etc.
        return f"<indisponivel: {exc}>"


@dataclass
class PackageReport:
    included: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024.0:
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{value:.1f}TB"


def copy_dist(release_dir: Path, report: PackageReport) -> None:
    if not DIST_DIR.is_dir():
        raise SystemExit(
            f"Dist do Nuitka nao encontrado em {DIST_DIR}. "
            "Rode scripts\\build_nuitka_windows.ps1 antes de empacotar."
        )
    for item in DIST_DIR.iterdir():
        target = release_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)
        report.included.append(str(target.relative_to(release_dir)))

    exe_path = release_dir / EXE_NAME
    if not exe_path.is_file():
        raise SystemExit(
            f"{EXE_NAME} nao apareceu em {release_dir} depois de copiar o dist "
            "(o --output-filename do Nuitka mudou?)."
        )


_REGISTRY_EXAMPLE = {
    "version": 1,
    "updated_at": "2026-01-01T00:00:00+00:00",
    "active_by_role": {
        "specialist": "exemplo-specialist-0001",
        "detector": "exemplo-detector-0001",
    },
    "entries": [
        {
            "id": "exemplo-specialist-0001",
            "name": "pediatria_child_detector_v6_jutta",
            "version": "v6",
            "role": "specialist",
            "backend": "openvino",
            "path": "runtime/models/specialist_pediatria_child_detector_v6_jutta_v6_exemplo",
            "created_at": "2026-01-01T00:00:00+00:00",
            "sha256": "0" * 64,
            "enabled": True,
        },
        {
            "id": "exemplo-detector-0001",
            "name": "yolo11n",
            "version": "v1",
            "role": "detector",
            "backend": "pt",
            "path": "runtime/models/detector_yolo11n_v1_exemplo.pt",
            "created_at": "2026-01-01T00:00:00+00:00",
            "sha256": "0" * 64,
            "enabled": True,
        },
    ],
}


def create_model_registry_scaffolding(release_dir: Path, report: PackageReport) -> None:
    """Cria so o esqueleto do registry de modelos -- nunca copia peso de
    modelo nenhum para o release (ver modulo/pediatria/model_registry_service.py).
    O app abre normalmente sem nada aqui dentro; o tecnico importa o modelo
    de verdade depois, por Ctrl+Shift+M (Gerenciador de Modelos)."""
    runtime_dir = release_dir / "runtime"
    models_dir = runtime_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    readme = (
        "MODELOS -- IA Pediatria\n"
        "========================\n\n"
        "Esta pasta comeca vazia de proposito: modelos NAO sao embutidos no\n"
        "executavel nem no ZIP de entrega (reduz tamanho/build e evita travar\n"
        "a versao do modelo dentro do binario).\n\n"
        "Para configurar um modelo, abra o Demo Viewer e pressione\n"
        "Ctrl+Shift+M (Gerenciador de Modelos -- tela tecnica). La e possivel:\n"
        "  - Importar um modelo .pt;\n"
        "  - Importar um modelo OpenVINO (.xml + .bin correspondente);\n"
        "  - Definir qual versao esta ativa;\n"
        "  - Testar se o modelo carrega antes de usar em producao;\n"
        "  - Voltar (rollback) para uma versao importada anteriormente --\n"
        "    nenhum modelo importado e apagado ao importar um novo.\n\n"
        "Importar copia o arquivo/pasta para dentro desta pasta automaticamente\n"
        "e atualiza runtime\\model_registry.json (nao mexa nesse arquivo na mao\n"
        "a menos que saiba o formato -- veja model_registry.example.json ao\n"
        "lado para referencia do schema).\n\n"
        "Sem nenhum modelo configurado aqui, o app abre normalmente; so ao\n"
        "clicar em 'Iniciar' sem modelo valido aparece um aviso explicando o\n"
        "que falta.\n"
    )
    (models_dir / "README_MODELS.txt").write_text(readme, encoding="utf-8")
    report.included.append("runtime/models/README_MODELS.txt")

    example_text = json.dumps(_REGISTRY_EXAMPLE, indent=2, ensure_ascii=False)
    (runtime_dir / "model_registry.example.json").write_text(example_text, encoding="utf-8")
    report.included.append("runtime/model_registry.example.json")


def create_output_dirs(release_dir: Path, report: PackageReport) -> None:
    results_dir = release_dir / "pediatria_results"
    results_dir.mkdir(parents=True, exist_ok=True)
    readme = (
        "SAIDA DA ANALISE -- IA Pediatria\n"
        "=================================\n\n"
        "Esta pasta comeca vazia. Quando o Demo Viewer roda uma analise, ele\n"
        "grava aqui (por sessao): evidencias (frames/crops/anotados), eventos\n"
        "(events.jsonl) e o resumo da sessao (session_summary.json/csv). Nao\n"
        "existe pasta separada de 'logs' nesta versao -- este e o unico lugar\n"
        "onde a aplicacao escreve saida em disco.\n"
    )
    (results_dir / "README_RESULTADOS.txt").write_text(readme, encoding="utf-8")
    report.included.append("pediatria_results/README_RESULTADOS.txt")


def write_launcher(release_dir: Path, report: PackageReport) -> None:
    # Sem --model/--person-model explicitos de proposito: o app resolve o
    # caminho sozinho (registry ativo em runtime/model_registry.json: se
    # nao houver entrada, o app abre do mesmo jeito e avisa so quando
    # "Iniciar" for clicado sem modelo configurado -- ver Ctrl+Shift+M).
    launcher = (
        "@echo off\r\n"
        "setlocal\r\n"
        "cd /d \"%~dp0\"\r\n"
        f"\"%~dp0{EXE_NAME}\" --report \"%~dp0pediatria_results\\report.json\"\r\n"
        "endlocal\r\n"
    )
    launcher_path = release_dir / "Iniciar_Demo.bat"
    launcher_path.write_text(launcher, encoding="utf-8")
    report.included.append(launcher_path.name)


def write_readme_cliente(release_dir: Path, version: str, report: PackageReport) -> None:
    text = f"""IA Pediatria -- Demo Viewer
===========================
Versao/build: {version}

O QUE E ISSO
------------
Demonstracao visual da deteccao de crianca desacompanhada por camera
(YOLO + OpenVINO/CPU). Este pacote e uma demo/PoC: nao inclui integracao
com nenhum sistema de terceiros.

COMO INICIAR
------------
1. Extraia este ZIP para uma pasta local (evite caminhos com acentuacao
   pesada ou permissao restrita, ex.: Downloads/Documentos do usuario).
2. De dois cliques em "Iniciar_Demo.bat".
3. Na janela, escolha a fonte de video (arquivo, URL RTSP/HTTP ou camera
   USB) e clique em "Iniciar".

ONDE CONFIGURAR CAMERAS/MODELOS
--------------------------------
- Camera/fonte de video: escolhida na propria janela do Demo Viewer a cada
  sessao (nao ha arquivo de cadastro de cameras nesta versao).
- Modelos: configurados pela equipe tecnica (nao ficam embutidos no .exe
  nem sao carregados automaticamente). Consulte o suporte tecnico para
  configurar ou trocar a versao do modelo usada nesta instalacao.
- Modelos e configuracoes sao carregados dinamicamente a cada inicio -- nao
  estao embutidos dentro do .exe.

ONDE FICAM OS RESULTADOS
-------------------------
Pasta "pediatria_results\\" ao lado do executavel: evidencias (imagens),
eventos e resumo de cada sessao de analise. Veja
"pediatria_results\\README_RESULTADOS.txt".

REQUISITOS
----------
- Windows 10/11 64-bit.
- CPU com suporte a OpenVINO (a maioria dos CPUs Intel/AMD modernos).
- GPU NVIDIA e opcional; sem ela a analise roda em CPU normalmente.

SUPORTE
-------
Entre em contato com a equipe responsavel por este pacote para duvidas,
bugs ou solicitacao de nova build.
"""
    path = release_dir / "README_CLIENTE.txt"
    path.write_text(text, encoding="utf-8")
    report.included.append(path.name)


def collect_build_info(version: str) -> dict:
    nuitka_version = "<indisponivel>"
    try:
        result = subprocess.run(
            [sys.executable, "-m", "nuitka", "--version"],
            capture_output=True, text=True, check=True,
        )
        nuitka_version = result.stdout.strip().splitlines()[0]
    except Exception:
        pass

    return {
        "app_name": "IA Pediatria - Demo Viewer",
        "build_date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_branch": run_git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_commit": run_git("rev-parse", "HEAD"),
        "git_commit_short": run_git("rev-parse", "--short", "HEAD"),
        "python_version": sys.version.split()[0],
        "nuitka_version": nuitka_version,
        "platform": platform.platform(),
        "entrypoint": "tools/run_pediatria_viewer.py",
        "qt_binding_detected": "PyQt6",
        "models_bundled": False,
        "model_registry_path": "runtime/model_registry.json",
        "version_label": version,
        "notes": (
            "Build standalone (--standalone, one-dir). Modelos ficam fora do "
            "build (ver modulo/pediatria/model_registry_service.py); o "
            "tecnico importa/ativa via Ctrl+Shift+M no Demo Viewer, gravado "
            "em runtime/model_registry.json ao lado do executavel. torch e "
            "dependencia transitiva obrigatoria do ultralytics (import "
            "direto no pacote), por isso nao pode ser excluido do build sem "
            "quebrar o carregamento do modelo -- e a maior parte do tamanho "
            "final. Para reduzir drasticamente o tamanho, reinstale o venv "
            "com torch CPU-only (setup_mvp.ps1 -CpuOnly) antes de buildar; a "
            "deteccao automatica de GPU CUDA fica indisponivel nesse caso, "
            "mas o pipeline padrao (OpenVINO/CPU) nao e afetado."
        ),
    }


def write_build_info(release_dir: Path, version: str, report: PackageReport) -> None:
    info = collect_build_info(version)
    path = release_dir / "BUILD_INFO.json"
    path.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
    report.included.append(path.name)


def write_checksums(release_dir: Path, report: PackageReport) -> None:
    lines = []
    for path in sorted(release_dir.rglob("*")):
        if path.is_dir() or path.name == "CHECKSUMS.sha256":
            continue
        rel = path.relative_to(release_dir).as_posix()
        lines.append(f"{sha256_of(path)}  {rel}")
    checksum_path = release_dir / "CHECKSUMS.sha256"
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report.included.append(checksum_path.name)


def find_forbidden_files(release_dir: Path) -> list[str]:
    hits = []
    for path in release_dir.rglob("*"):
        name = path.name
        rel_posix = path.relative_to(release_dir).as_posix()
        if rel_posix in ALLOWED_FORBIDDEN_OVERRIDES:
            continue
        for pattern in FORBIDDEN_PATTERNS:
            if fnmatch.fnmatch(name, pattern):
                hits.append(rel_posix)
                break
    return hits


def find_model_weight_leaks(release_dir: Path) -> list[str]:
    """Confirma que nenhum peso de modelo vazou para dentro do release
    (criterio obrigatorio desta rodada: modelos sao 100% externos)."""
    hits = []
    for path in release_dir.rglob("*"):
        if path.is_dir():
            continue
        rel_posix = path.relative_to(release_dir).as_posix()
        if rel_posix in ALLOWED_MODEL_WEIGHT_OVERRIDES:
            continue
        for pattern in MODEL_WEIGHT_PATTERNS:
            if fnmatch.fnmatch(path.name, pattern):
                hits.append(rel_posix)
                break
    return hits


def zip_release(release_dir: Path, version: str) -> Path:
    zip_path = RELEASE_PARENT / f"{APP_NAME}_{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in release_dir.rglob("*"):
            if path.is_file():
                zf.write(path, arcname=Path(release_dir.name) / path.relative_to(release_dir))
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument(
        "--force", action="store_true",
        help="Remove a pasta de release existente com o mesmo nome antes de recriar.",
    )
    args = parser.parse_args()

    release_dir = RELEASE_PARENT / f"{APP_NAME}_{args.version}"
    RELEASE_PARENT.mkdir(parents=True, exist_ok=True)

    if release_dir.exists():
        if not args.force:
            raise SystemExit(
                f"{release_dir} ja existe. Rode com --force para sobrescrever."
            )
        shutil.rmtree(release_dir)
    release_dir.mkdir(parents=True)

    report = PackageReport()

    print(f"[1/6] Copiando dist do Nuitka de {DIST_DIR}...")
    copy_dist(release_dir, report)

    print("[2/6] Criando esqueleto do registry de modelos (sem pesos)...")
    create_model_registry_scaffolding(release_dir, report)

    print("[3/6] Criando pasta de saida (pediatria_results/) e launcher...")
    create_output_dirs(release_dir, report)
    write_launcher(release_dir, report)

    print("[4/6] Escrevendo README_CLIENTE.txt e BUILD_INFO.json...")
    write_readme_cliente(release_dir, args.version, report)
    write_build_info(release_dir, args.version, report)

    print("[5/6] Validando arquivos proibidos/pesos de modelo e gerando CHECKSUMS.sha256...")
    forbidden = find_forbidden_files(release_dir)
    model_leaks = find_model_weight_leaks(release_dir)
    for item in forbidden:
        report.warnings.append(f"ARQUIVO PROIBIDO ENCONTRADO: {item}")
    for item in model_leaks:
        report.warnings.append(f"PESO DE MODELO VAZOU PARA O RELEASE: {item}")
    write_checksums(release_dir, report)

    print("[6/6] Gerando ZIP...")
    zip_path = zip_release(release_dir, args.version)

    total_bytes = sum(p.stat().st_size for p in release_dir.rglob("*") if p.is_file())

    print()
    print("=== Empacotamento concluido ===")
    print(f"Pasta release: {release_dir}")
    print(f"Tamanho pasta: {human_size(total_bytes)}")
    print(f"ZIP:           {zip_path} ({human_size(zip_path.stat().st_size)})")
    print("Modelos incluidos: False (externos, ver runtime/models/README_MODELS.txt)")
    if report.warnings:
        print()
        print("AVISOS:")
        for warning in report.warnings:
            print(f"  - {warning}")
    if forbidden or model_leaks:
        raise SystemExit(
            f"Encontrados {len(forbidden)} arquivo(s) proibido(s) e {len(model_leaks)} "
            "peso(s) de modelo vazado(s) na pasta de release. Corrija antes de "
            "entregar ao cliente (veja avisos acima)."
        )


if __name__ == "__main__":
    main()
