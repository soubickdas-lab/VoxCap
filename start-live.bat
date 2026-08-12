@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Pehli baar setup chahiye - setup.bat chal raha hai...
  call setup.bat
  if not exist .venv\Scripts\python.exe exit /b 1
)
title VoxCap - LIVE (srt.aipoint.online)
echo.
echo  Purane processes clean ho rahe hain...
taskkill /f /im cloudflared.exe >nul 2>&1
for /f "tokens=5" %%p in ('netstat -aon ^| findstr ":8765 " ^| findstr LISTENING') do taskkill /f /pid %%p >nul 2>&1
timeout /t 2 /nobreak >nul

echo  VoxCap server start ho raha hai...
start "VoxCap Server" cmd /k .venv\Scripts\python.exe server.py
timeout /t 5 /nobreak >nul

echo  Browser me live site khul rahi hai...
start "" https://srt.aipoint.online
echo.
echo  Cloudflare tunnel connect ho raha hai: https://srt.aipoint.online
echo  (Ye dono windows khuli rakhni hain - band karne se site offline ho jayegi)
echo.
"C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --config "%~dp0cloudflare-config.yml" run voxcap
