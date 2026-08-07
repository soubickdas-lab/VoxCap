@echo off
setlocal
cd /d "%~dp0"
echo.
echo  ==== VoxCap Setup ====
echo.

rem -- find or install Python 3.12 --
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if exist "%PY%" goto havepython

python -c "import sys; assert sys.version_info >= (3,10)" >nul 2>&1
if %errorlevel%==0 ( set "PY=python" & goto havepython )

echo  Python nahi mila - winget se install ho raha hai...
winget install --id Python.Python.3.12 -e --silent --accept-package-agreements --accept-source-agreements
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" (
  echo.
  echo  Python install nahi ho paya. https://www.python.org/downloads/ se
  echo  Python 3.12 install karo, phir setup.bat dobara chalao.
  pause
  exit /b 1
)

:havepython
echo  Python: %PY%
echo.
if not exist .venv ( "%PY%" -m venv .venv )
echo  Dependencies install ho rahi hain (pehli baar ~1 GB download, time lagega)...
.venv\Scripts\python.exe -m pip install --upgrade pip -q
.venv\Scripts\python.exe -m pip install -r requirements.txt
if %errorlevel% neq 0 (
  echo  Install me error aaya - internet check karke dobara chalao.
  pause
  exit /b 1
)
echo.
echo  Setup complete! Ab start-local.bat double-click karo.
echo  (Pehli transcription par Whisper model download hoga - GPU wale PC par ~3 GB)
echo.
pause
