@echo off
setlocal
cd /d "%~dp0"
echo.
echo  ==== VoxCap Update ====
echo.
echo  Latest version download ho raha hai...

powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest 'https://github.com/soubickdas-lab/VoxCap/releases/latest/download/VoxCap-share.zip' -OutFile '%TEMP%\voxcap-update.zip'"
if %errorlevel% neq 0 (
  echo  Download fail - internet check karke dobara chalao.
  pause
  exit /b 1
)

powershell -NoProfile -Command "Expand-Archive '%TEMP%\voxcap-update.zip' -DestinationPath '%TEMP%\voxcap-update' -Force"
del /q "version *.txt" >nul 2>&1
xcopy /e /y /q "%TEMP%\voxcap-update\VoxCap\*" . >nul
for %%f in ("version *.txt") do echo  Ab aap %%~nf par ho.
del "%TEMP%\voxcap-update.zip" >nul 2>&1
rmdir /s /q "%TEMP%\voxcap-update" >nul 2>&1

if exist .venv\Scripts\python.exe (
  echo  Dependencies check ho rahi hain...
  .venv\Scripts\python.exe -m pip install -r requirements.txt -q
)

echo.
echo  Update complete! Ab start-local.bat se chalao.
echo.
pause
