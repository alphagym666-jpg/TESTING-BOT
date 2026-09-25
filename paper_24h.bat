@echo off
rem Paper trading en continu : redemarre tout seul en cas d'arret (MT5 ferme, coupure internet...).
rem Fermez cette fenetre pour l'arreter. Tout est sauvegarde et reprend au prochain lancement.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
title Paper trading 24h/24 - reduisez cette fenetre, ne la fermez pas
set PREMIER=1
:boucle
if "%PREMIER%"=="1" (
  python run.py paper %*
) else (
  python run.py paper %* --no-browser
)
if errorlevel 3 goto deja
set PREMIER=0
echo.
echo [%date% %time%] Le paper trading s'est arrete. Redemarrage automatique dans 30 secondes.
echo Pour l'arreter pour de bon : fermez cette fenetre.
timeout /t 30 /nobreak >nul
goto boucle
:deja
echo.
echo Ce paper trading tourne deja dans une autre fenetre. Rien a faire : fermez celle-ci.
timeout /t 15 >nul
exit /b 0
