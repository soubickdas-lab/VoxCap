@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Pehli baar setup chahiye - setup.bat chal raha hai...
  call setup.bat
  if not exist .venv\Scripts\python.exe exit /b 1
)
title VoxCap - LIVE (srt.aipoint.online)
echo.
echo  Starting VoxCap server...
start "VoxCap Server" cmd /k .venv\Scripts\python.exe server.py
timeout /t 4 /nobreak >nul
echo.
echo  Connecting Cloudflare tunnel: https://srt.aipoint.online
echo  (Is window ko band karne se site offline ho jayegi)
echo.
"C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --config "%~dp0cloudflare-config.yml" run voxcap
