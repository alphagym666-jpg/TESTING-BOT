@echo off
rem MISE A JOUR EN UN DOUBLE-CLIC : telecharge la derniere version du labo sur GitHub et la copie dans CE dossier.
rem Vos resultats (results*), votre .env (cles, mots de passe) et vos bots ne sont JAMAIS touches.
setlocal
cd /d "%~dp0"
set "URL=https://github.com/alphagym666-jpg/TESTING-BOT/archive/refs/heads/claude/mt5-trading-agents-platform-4grro9.zip"
set "TMPD=%TEMP%\labo_maj_%RANDOM%"
echo.
echo  Mise a jour du labo MT5...
echo  (fermez d'abord les fenetres du paper trading et de LANCER_BOT.bat)
echo.
pause
mkdir "%TMPD%" >nul 2>&1
echo Telechargement de la derniere version...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest -UseBasicParsing -Uri '%URL%' -OutFile '%TMPD%\maj.zip'; Expand-Archive -Force '%TMPD%\maj.zip' '%TMPD%\x'; exit 0 } catch { Write-Host $_; exit 1 }"
if errorlevel 1 (
  echo.
  echo ECHEC du telechargement : verifiez Internet et reessayez.
  rmdir /s /q "%TMPD%" >nul 2>&1
  pause
  exit /b 1
)
for /d %%D in ("%TMPD%\x\*") do set "SRC=%%D"
echo Copie des nouveaux fichiers (resultats, .env et bots conserves)...
robocopy "%SRC%" "%CD%" /E /NFL /NDL /NJH /NJS /NP /XD results* __pycache__ .git /XF .env *.log >nul
if errorlevel 8 (
  echo.
  echo ECHEC de la copie (un fichier est peut-etre ouvert : fermez le paper trading et MetaEditor, puis reessayez).
  rmdir /s /q "%TMPD%" >nul 2>&1
  pause
  exit /b 1
)
rmdir /s /q "%TMPD%" >nul 2>&1
echo Installation des nouveaux modules Python s'il y en a...
python -m pip install -q -r requirements.txt >nul 2>&1
echo.
echo  OK : le labo est a jour. Relancez lancer.bat.
echo  Les bots deja installes dans MT5 : recreez-les depuis la plateforme pour avoir leur derniere version.
echo.
pause
