@echo off
setlocal
cd /d "%~dp0"
echo.
echo  ==== VoxCap Publish (GitHub update) ====
echo.

rem -- fresh share zip banao --
set "STAGE=%TEMP%\voxcap-share\VoxCap"
if exist "%TEMP%\voxcap-share" rmdir /s /q "%TEMP%\voxcap-share"
mkdir "%STAGE%\static"
copy /y server.py "%STAGE%" >nul
copy /y requirements.txt "%STAGE%" >nul
copy /y setup.bat "%STAGE%" >nul
copy /y start-local.bat "%STAGE%" >nul
copy /y README.md "%STAGE%" >nul
copy /y static\index.html "%STAGE%\static" >nul
powershell -NoProfile -Command "Compress-Archive -Path '%TEMP%\voxcap-share\VoxCap' -DestinationPath 'VoxCap-share.zip' -Force"
echo  VoxCap-share.zip fresh ban gayi.

rem -- commit + push --
git add -A
set /p MSG=Commit message likho (blank = "update"):
if "%MSG%"=="" set MSG=update
git commit -m "%MSG%"
git push
if %errorlevel% neq 0 (
  echo  Push me error - internet ya login check karo.
  pause
  exit /b 1
)

rem -- release asset update --
"%ProgramFiles%\GitHub CLI\gh.exe" release upload v1.0 VoxCap-share.zip --clobber
echo.
echo  Done! Code push ho gaya aur download zip bhi update ho gayi.
echo  Download link: https://github.com/soubickdas-lab/VoxCap/releases/latest/download/VoxCap-share.zip
echo.
pause
