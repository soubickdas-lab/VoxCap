@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo.
echo  ==== VoxCap Publish (GitHub update) ====
echo.

rem -- current version file dhundo --
set "CURVER="
for %%f in ("version *.txt") do set "CURFILE=%%~nf"
if defined CURFILE set "CURVER=%CURFILE:version =%"
echo  Abhi ka version: %CURVER%
set /p NEWVER=Naya version number likho (blank = %CURVER% hi rakho):
if "%NEWVER%"=="" set "NEWVER=%CURVER%"

if not "%NEWVER%"=="%CURVER%" (
  set /p CHANGES=Is update me kya badla (ek line likho):
  rem -- nayi version file banao: naya changelog entry upar, purana content neeche --
  echo ==========================================> "version %NEWVER%.txt.new"
  echo   VoxCap — Version %NEWVER%>> "version %NEWVER%.txt.new"
  echo ==========================================>> "version %NEWVER%.txt.new"
  echo.>> "version %NEWVER%.txt.new"
  echo v%NEWVER% (%date%)>> "version %NEWVER%.txt.new"
  echo - !CHANGES!>> "version %NEWVER%.txt.new"
  echo.>> "version %NEWVER%.txt.new"
  type "version %CURVER%.txt">> "version %NEWVER%.txt.new"
  del "version %CURVER%.txt"
  ren "version %NEWVER%.txt.new" "version %NEWVER%.txt"
  echo  version %NEWVER%.txt ban gayi. Detail me edit karna ho to Notepad me kholo.
)

rem -- fresh share zip banao --
set "STAGE=%TEMP%\voxcap-share\VoxCap"
if exist "%TEMP%\voxcap-share" rmdir /s /q "%TEMP%\voxcap-share"
mkdir "%STAGE%\static"
copy /y server.py "%STAGE%" >nul
copy /y ai33.py "%STAGE%" >nul
copy /y requirements.txt "%STAGE%" >nul
copy /y setup.bat "%STAGE%" >nul
copy /y start-local.bat "%STAGE%" >nul
copy /y update.bat "%STAGE%" >nul
copy /y README.md "%STAGE%" >nul
copy /y "version %NEWVER%.txt" "%STAGE%" >nul
copy /y static\index.html "%STAGE%\static" >nul
powershell -NoProfile -Command "Compress-Archive -Path '%TEMP%\voxcap-share\VoxCap' -DestinationPath 'VoxCap-share.zip' -Force"
echo  VoxCap-share.zip fresh ban gayi (version %NEWVER%).

rem -- commit + push --
git add -A
set /p MSG=Commit message likho (blank = "update v%NEWVER%"):
if "%MSG%"=="" set "MSG=update v%NEWVER%"
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
echo  Done! Version %NEWVER% live ho gaya.
echo  Download link: https://github.com/soubickdas-lab/VoxCap/releases/latest/download/VoxCap-share.zip
echo.
pause
