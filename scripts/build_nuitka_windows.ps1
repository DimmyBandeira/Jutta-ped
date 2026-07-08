<#
.SYNOPSIS
    Compila o Demo Viewer da IA Pediatria (PyQt6) com Nuitka em modo
    --standalone (one-dir), gerando build/nuitka/run_pediatria_viewer.dist/.

.DESCRIPTION
    So compila codigo. Nao copia modelos, nao monta a pasta de release e nao
    gera ZIP -- isso e responsabilidade de scripts/package_release.py, que
    roda depois deste script (ou e chamado por ele com -Package).

    Nao hardcoda nenhum caminho absoluto de maquina de desenvolvedor: tudo e
    resolvido a partir de $PSScriptRoot (raiz do repo = pai de scripts/).

.PARAMETER Version
    Rotulo de versao usado no relatorio final e em BUILD_INFO.json (nao
    renomeia o output do Nuitka). Default: data de hoje (yyyyMMdd).

.PARAMETER Clean
    Remove build/nuitka antes de compilar (equivalente a --remove-output,
    mas tambem limpa builds antigos de versoes anteriores).

.PARAMETER Package
    Depois de compilar com sucesso, chama scripts/package_release.py
    automaticamente.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\build_nuitka_windows.ps1 -Package
#>

[CmdletBinding()]
param(
    [string]$Version = (Get-Date -Format "yyyyMMdd"),
    [switch]$Clean,
    [switch]$Package
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $RepoRoot

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Nao encontrei $Python. Rode setup_mvp.ps1 primeiro para criar o venv do projeto."
}

$Entrypoint = "tools/run_pediatria_viewer.py"
if (-not (Test-Path (Join-Path $RepoRoot $Entrypoint))) {
    throw "Entrypoint '$Entrypoint' nao existe. Build cancelado (nao vou chutar outro arquivo)."
}

# Confere o binding Qt de verdade em vez de assumir -- se o venv nao tiver
# PyQt6 instalado, o --enable-plugin=pyqt6 abaixo faria o Nuitka falhar
# tarde, com um erro confuso.
& $Python -c "from PyQt6 import QtCore" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "PyQt6 nao importa no venv ($Python). Instale as dependencias (requirements-mvp.txt) antes de buildar."
}

$OutputDir = Join-Path $RepoRoot "build\nuitka"
$LogDir = Join-Path $RepoRoot "build\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

if ($Clean -and (Test-Path $OutputDir)) {
    Write-Host "Limpando build antigo em $OutputDir..."
    Remove-Item -Recurse -Force $OutputDir
}

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogDir "nuitka_build_$Timestamp.log"

Write-Host "=== WebGuardiao IA Pediatria -- build Nuitka (standalone) ==="
Write-Host "Repo:       $RepoRoot"
Write-Host "Entrypoint: $Entrypoint"
Write-Host "Output dir: $OutputDir"
Write-Host "Log:        $LogFile"
Write-Host ""

# --standalone (one-dir), nunca --onefile nesta etapa: PyQt6 + OpenCV +
# Ultralytics/OpenVINO + torch (dependencia transitiva do ultralytics) sao
# sensiveis a extracao onefile (COM do pyttsx3/comtypes em especial).
#
# Nao inclui modelos (--include-data-dir de src/models) de proposito: os
# modelos ficam fora do binario, copiados pelo script de empacotamento para
# release/<pasta>/models/, e o Demo Viewer recebe o caminho explicito via
# --model/--person-model no atalho .bat gerado. Isso preserva o carregamento
# dinamico por configuracao (nao hardcoda .pt/.xml no build).

# --file-version/--product-version do Nuitka exigem N.N.N.N numerico; Version
# por default e uma data (yyyyMMdd), que nao serve como major version isolado.
# Normaliza para "<ano>.<mes>.<dia>.0" quando Version bater com esse formato;
# caso contrario usa "0.0.0.0" e guarda o rotulo real so em BUILD_INFO.json.
if ($Version -match "^(\d{4})(\d{2})(\d{2})$") {
    $FileVersion = "$($Matches[1]).$($Matches[2]).$($Matches[3]).0"
} else {
    $FileVersion = "0.0.0.0"
}

# openvino/__init__.py resolve suas DLLs nativas (openvino.dll, plugin de
# CPU, TBB, etc.) via os.path.dirname(__file__) + "libs" -- nao sao "data
# files" no sentido do Nuitka (extensao .dll) nem sao descobertas por
# analise estatica de import (sao carregadas por nome via plugins.xml em
# runtime). Sem isso a inferencia falha silenciosamente so dentro do build
# congelado. Empacota explicitamente no mesmo caminho relativo que o pacote
# openvino espera dentro do dist.
$OpenvinoLibsDir = Join-Path $RepoRoot ".venv\Lib\site-packages\openvino\libs"
if (-not (Test-Path $OpenvinoLibsDir)) {
    throw "Nao encontrei $OpenvinoLibsDir. O venv tem openvino instalado? (pip show openvino)"
}

$NuitkaArgs = @(
    "-m", "nuitka"
    $Entrypoint
    "--standalone"
    "--enable-plugins=pyqt6"
    "--output-dir=$OutputDir"
    "--output-filename=IA_Pediatria_DemoViewer.exe"
    "--remove-output"
    "--assume-yes-for-downloads"
    "--windows-console-mode=disable"
    "--include-package=src"
    "--include-package=modulo"
    "--include-package=ultralytics"
    "--include-package-data=ultralytics"
    "--include-package=lap"
    "--include-package=pyttsx3"
    "--include-package=comtypes"
    "--include-package-data=openvino"
    "--include-data-dir=$OpenvinoLibsDir=openvino/libs"
    "--company-name=WebGuardiao"
    "--product-name=IA Pediatria - Demo Viewer"
    "--file-version=$FileVersion"
    "--product-version=$FileVersion"
)

Write-Host "Comando: $Python $($NuitkaArgs -join ' ')"
Write-Host ""

# Nuitka escreve progresso/info em stderr o tempo todo (nao so erros). Com
# $ErrorActionPreference = "Stop" (setado no topo deste script), 2>&1 num
# processo nativo faz o PowerShell 5.1 embrulhar CADA linha de stderr como
# NativeCommandError terminante -- o script morria no meio da compilacao
# mesmo com o Nuitka rodando normalmente. Relaxa localmente para Continue
# so em volta desta chamada e usa o exit code real (nao a presenca de
# stderr) para decidir sucesso/falha.
$PreviousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $Python @NuitkaArgs 2>&1 | Tee-Object -FilePath $LogFile
$NuitkaExitCode = $LASTEXITCODE
$ErrorActionPreference = $PreviousErrorActionPreference

if ($NuitkaExitCode -ne 0) {
    Write-Error "Nuitka terminou com codigo $NuitkaExitCode. Veja o log completo em $LogFile."
    exit $NuitkaExitCode
}

$DistDir = Join-Path $OutputDir "run_pediatria_viewer.dist"
if (-not (Test-Path $DistDir)) {
    throw "Build reportou sucesso mas $DistDir nao existe. Algo esta errado."
}

Write-Host ""
Write-Host "=== Build concluido ==="
Write-Host "Dist: $DistDir"

if ($Package) {
    Write-Host ""
    Write-Host "=== Empacotando release (scripts/package_release.py) ==="
    & $Python (Join-Path $RepoRoot "scripts\package_release.py") --version $Version
    if ($LASTEXITCODE -ne 0) {
        Write-Error "package_release.py terminou com codigo $LASTEXITCODE."
        exit $LASTEXITCODE
    }
}
