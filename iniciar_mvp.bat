@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Ambiente ausente. Execute primeiro: powershell -ExecutionPolicy Bypass -File setup_mvp.ps1
  pause
  exit /b 1
)
if not exist "%ROOT%runtime\ultralytics" mkdir "%ROOT%runtime\ultralytics"
set "YOLO_CONFIG_DIR=%ROOT%runtime\ultralytics"
if "%PEDIATRIA_MODEL%"=="" (
  if exist "%ROOT%src\models\pediatria_child_detector_v6_jutta_openvino_model" (
    set "PEDIATRIA_MODEL=src\models\pediatria_child_detector_v6_jutta_openvino_model"
  ) else if exist "%ROOT%src\models\pediatria_child_detector_v6_jutta.pt" (
    set "PEDIATRIA_MODEL=src\models\pediatria_child_detector_v6_jutta.pt"
  ) else (
    set "PEDIATRIA_MODEL=src\models\pediatria_child_detector_v5.pt"
  )
)
if "%PEDIATRIA_DEVICE%"=="" set "PEDIATRIA_DEVICE=auto"
if "%PEDIATRIA_CONF%"=="" set "PEDIATRIA_CONF=0.25"
if "%PEDIATRIA_REPORT%"=="" set "PEDIATRIA_REPORT=pediatria_results\v6_jutta_openvino_local\report.json"
cd /d "%ROOT%"
"%PYTHON%" tools\run_pediatria_popup_mvp.py --device "%PEDIATRIA_DEVICE%" --model "%PEDIATRIA_MODEL%" --report "%PEDIATRIA_REPORT%" --conf "%PEDIATRIA_CONF%" %*