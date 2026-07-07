@echo off
setlocal
if "%~1"=="" (
  echo Uso: rodar_video_headless.bat "C:\caminho\video.avi"
  exit /b 2
)
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON%" exit /b 1
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
if "%PEDIATRIA_DEVICE%"=="" set "PEDIATRIA_DEVICE=cpu"
cd /d "%ROOT%"
"%PYTHON%" tools\run_pediatria_detector_mvp.py --source "%~1" --model "%PEDIATRIA_MODEL%" --output-video pediatria_results\headless_v6\video_anotado.mp4 --report pediatria_results\headless_v6\report.json --device "%PEDIATRIA_DEVICE%"