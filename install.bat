@echo off
cd /d "%~dp0"
echo === Installation du Labo de strategies MT5 ===
set PY=python
where py >nul 2>nul && set PY=py -3
%PY% --version >nul 2>nul || (
  echo Python est introuvable. Installez Python 3.10+ 64 bits depuis python.org
  echo en cochant "Add python.exe to PATH", puis relancez install.bat.
  pause & exit /b 1
)
%PY% -c "import struct,sys; sys.exit(0 if struct.calcsize('P')==8 else 1)" || (
  echo Python 64 bits requis par MetaTrader5. Installez la version 64 bits depuis python.org.
  pause & exit /b 1
)
if not exist .venv %PY% -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt || (echo Echec de l'installation des paquets. & pause & exit /b 1)
if not exist .env copy .env.example .env >nul
echo.
echo Installation terminee.
echo 1) Ouvrez MetaTrader 5 et connectez-vous a votre compte (DEMO conseille).
echo 2) Optionnel : remplissez le fichier .env avec vos identifiants.
echo 3) Lancez lancer.bat
pause
