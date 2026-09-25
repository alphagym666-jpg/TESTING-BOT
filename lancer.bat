@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Lancez d'abord install.bat
  pause & exit /b 1
)
call .venv\Scripts\activate.bat
set SYMS=EURUSD
set TF=H1

:menu
cls
echo ==============================================
echo    Labo de strategies MT5 - 10 agents, 2 chefs
echo ==============================================
echo   Symboles : %SYMS%    Timeframe : %TF%
echo.
echo   1. Tester la connexion a MT5
echo   2. Changer symboles / timeframe
echo   3. Lancer la recherche (10 agents)
echo   4. Lancer une recherche longue (plus de tests)
echo   5. Ouvrir les rapports
echo   6. PAPER TRADING : trades fictifs sur prix reels (top 20 par symbole)
echo   7. PAPER TRADING : seulement les strategies approuvees
echo   8. Ouvrir le tableau de bord du paper trading
echo   9. Quitter
echo.
echo   Aucune option n'envoie d'ordre a MetaTrader.
echo.
set CHOIX=
set /p CHOIX=Votre choix : 
if "%CHOIX%"=="1" goto check
if "%CHOIX%"=="2" goto params
if "%CHOIX%"=="3" goto lab
if "%CHOIX%"=="4" goto labxl
if "%CHOIX%"=="5" goto rapports
if "%CHOIX%"=="6" goto paper
if "%CHOIX%"=="7" goto paperok
if "%CHOIX%"=="8" goto dash
if "%CHOIX%"=="9" exit /b 0
goto menu

:check
python run.py check --symbols %SYMS% --timeframe %TF%
pause & goto menu

:params
set /p SYMS=Symboles separes par des espaces (ex: EURUSD XAUUSD US30) : 
set /p TF=Timeframe (M5, M15, M30, H1, H4, D1) : 
goto menu

:lab
python run.py lab --symbols %SYMS% --timeframe %TF% --bars 30000
pause & goto menu

:labxl
python run.py lab --symbols %SYMS% --timeframe %TF% --bars 60000 --rounds 5 --budget 3000
pause & goto menu

:rapports
for %%S in (%SYMS%) do if exist "results\%%S_%TF%\rapport.html" start "" "results\%%S_%TF%\rapport.html"
if not exist results echo Aucun rapport : lancez d'abord une recherche.
pause & goto menu

:paper
echo Paper trading en cours. Ouvrez le tableau de bord (option 8) dans une autre fenetre. Ctrl+C pour arreter.
python run.py paper --symbols %SYMS% --timeframes %TF% --source tous --top 20
pause & goto menu

:paperok
python run.py paper --symbols %SYMS% --timeframes %TF% --source approuvees
pause & goto menu

:dash
if exist "results\paper\tableau_de_bord.html" (start "" "results\paper\tableau_de_bord.html") else (echo Lancez d'abord le paper trading.)
pause & goto menu
