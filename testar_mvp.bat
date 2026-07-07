@echo off
setlocal
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%PYTHON%" exit /b 1
set "QT_QPA_PLATFORM=offscreen"
set "YOLO_CONFIG_DIR=%ROOT%runtime\ultralytics"
cd /d "%ROOT%"
"%PYTHON%" -m pytest tests -q