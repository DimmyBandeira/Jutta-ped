[CmdletBinding()]
param([switch]$CpuOnly)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python Launcher nao encontrado. Instale Python 3.12 pelo instalador oficial."
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    & py -3.12 -m venv (Join-Path $Root ".venv")
}

& $VenvPython -m pip install --upgrade pip
if ($CpuOnly) {
    & $VenvPython -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu
} else {
    & $VenvPython -m pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cu121
}
& $VenvPython -m pip install -r (Join-Path $Root "requirements-mvp.txt")

& $VenvPython -c "import cv2, torch, ultralytics, PyQt6; print('Ambiente OK'); print('CUDA:', torch.cuda.is_available()); print('GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')"