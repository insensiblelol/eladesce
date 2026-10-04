@echo off
title Studio Pro - Ultimate Desktop Video Suite v3.5
cd /d "%~dp0"
echo Starting Studio Pro v3.5 ...
python -m pip install -q -r requirements.txt 2>nul
python studio_pro_app.py %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Application exited with error code %ERRORLEVEL%.
    pause
)
