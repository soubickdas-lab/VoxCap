@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Pehli baar setup chahiye - setup.bat chal raha hai...
  call setup.bat
  if not exist .venv\Scripts\python.exe exit /b 1
)
title VoxCap - Local
echo.
echo  VoxCap local server: http://127.0.0.1:8765
echo  (Ye window band karoge to server band ho jayega)
echo.
start "" http://127.0.0.1:8765
.venv\Scripts\python.exe server.py
