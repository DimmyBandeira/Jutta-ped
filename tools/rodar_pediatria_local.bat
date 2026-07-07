@echo off
setlocal

set "ROOT=%~dp0.."
cd /d "%ROOT%"

if defined WEBGUARDIAO_PYTHON set "PYTHON_EXE=%WEBGUARDIAO_PYTHON%"
if not defined PYTHON_EXE set "PYTHON_EXE=%ROOT%\webguardiao\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%ROOT%\webguardiao3.12.10\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

if not defined PEDIATRIA_MODEL set "PEDIATRIA_MODEL=%ROOT%\src\models\pediatria_child_detector_v6_jutta_openvino_model"
if not exist "%PEDIATRIA_MODEL%" set "PEDIATRIA_MODEL=%ROOT%\src\models\pediatria_child_detector_v6_jutta.pt"
if not exist "%PEDIATRIA_MODEL%" set "PEDIATRIA_MODEL=%ROOT%\src\models\pediatria_child_detector_v5.pt"

if not defined PEDIATRIA_DEVICE set "PEDIATRIA_DEVICE=cpu"
if not defined PEDIATRIA_REPORT set "PEDIATRIA_REPORT=%ROOT%\pediatria_results\v6_jutta_openvino_local\report.json"
if not defined PEDIATRIA_CONF set "PEDIATRIA_CONF=0.25"

echo [Pediatria Local] Iniciando analise visual...
echo [Pediatria Local] Modelo: %PEDIATRIA_MODEL%
echo [Pediatria Local] Dispositivo: %PEDIATRIA_DEVICE%
echo [Pediatria Local] Report: %PEDIATRIA_REPORT%
echo [Pediatria Local] Python: %PYTHON_EXE%

"%PYTHON_EXE%" "%ROOT%\tools\run_pediatria_popup_mvp.py" ^
  --device "%PEDIATRIA_DEVICE%" ^
  --model "%PEDIATRIA_MODEL%" ^
  --report "%PEDIATRIA_REPORT%" ^
  --conf "%PEDIATRIA_CONF%"

echo.
echo [Pediatria Local] Encerrado.
pause
