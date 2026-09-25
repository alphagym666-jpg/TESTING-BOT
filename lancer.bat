@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Lancez d'abord install.bat
  pause & exit /b 1
)
call .venv\Scripts\activate.bat

rem ===================== REGLAGES (modifiables avec le Bloc-notes) =====================
rem Marches : NASDAQ et GOLD sont reconnus automatiquement (US100.cash, XAUUSD...)
set SYMS=NASDAQ XAUUSD EURUSD
rem Timeframes : ALL = M1 M5 M15 M30 H1 H4 D1
set TFS=ALL
rem Capital fictif de chaque strategie et perte max par trade (en %% du capital)
set CAPITAL=100000
set RISK=0.5
rem Commission aller-retour par lot, par symbole (verifiez les montants de votre compte FTMO)
set COMMISSION=EURUSD=5 XAUUSD=5 NASDAQ=0
rem Regles du challenge FTMO : objectif, perte max par jour, perte max totale (en %%)
set FTMO=--ftmo-target 10 --ftmo-daily 3 --ftmo-total 10
rem Nombre de bougies par marche et timeframe
set BARS=30000
rem =====================================================================================

:menu
cls
echo ==================================================
echo    Labo de strategies MT5 - 10 agents, 2 chefs
echo ==================================================
echo   Marches : %SYMS%    Timeframes : %TFS%
echo   Capital fictif : %CAPITAL%    Perte max par trade : %RISK%%%
echo   Commission/lot : %COMMISSION%
echo   FTMO : %FTMO%
echo.
echo   1. Tester la connexion a MT5
echo   2. Changer marches / timeframes
echo.
echo   --- RECHERCHE (les agents testent et inventent des strategies) ---
echo   3. Recherche complete : tous les marches x tous les timeframes
echo   4. Recherche FTMO INTENSIVE (beaucoup plus de tests, plusieurs heures)
echo   5. Ouvrir la COMPARAISON (quelle strategie rapporte le plus / passe FTMO)
echo.
echo   --- PAPER TRADING EN DIRECT (trades fictifs sur prix reels) ---
echo   6. EXPLORATION : toutes les strategies x tous les R:R
echo   7. Les 30 meilleures strategies de la recherche
echo   8. Le portefeuille du Chef FTMO / strategies validees
echo   9. Ouvrir la PLATEFORME (voir les trades en direct)
echo.
echo   0. Quitter
echo.
echo   Aucune option n'envoie d'ordre a MetaTrader.
echo.
set CHOIX=
set /p CHOIX=Votre choix : 
if "%CHOIX%"=="1" goto check
if "%CHOIX%"=="2" goto params
if "%CHOIX%"=="3" goto lab
if "%CHOIX%"=="4" goto labxl
if "%CHOIX%"=="5" goto compare
if "%CHOIX%"=="6" goto explore
if "%CHOIX%"=="7" goto paper
if "%CHOIX%"=="8" goto paperok
if "%CHOIX%"=="9" goto platform
if "%CHOIX%"=="0" exit /b 0
goto menu

:check
python run.py check --symbols %SYMS%
pause & goto menu

:params
set /p SYMS=Marches separes par des espaces (ex: NASDAQ XAUUSD EURUSD) : 
set /p TFS=Timeframes (ex: M15 H1 H4, ou ALL pour tous) : 
goto menu

:lab
echo Recherche en cours : environ 2 a 4 minutes par marche et par timeframe. Laissez MT5 ouvert.
python run.py lab --symbols %SYMS% --timeframes %TFS% --bars %BARS% --risk %RISK% --capital %CAPITAL% --commission %COMMISSION% %FTMO%
if exist "results\comparaison.html" start "" "results\comparaison.html"
pause & goto menu

:labxl
echo Recherche intensive : 6 rounds, 3000 tests par agent et par round, 15 generations d'inventions.
python run.py lab --symbols %SYMS% --timeframes %TFS% --bars 60000 --rounds 6 --budget 3000 --invent-generations 15 --risk %RISK% --capital %CAPITAL% --commission %COMMISSION% %FTMO%
if exist "results\comparaison.html" start "" "results\comparaison.html"
pause & goto menu

:compare
python run.py compare --capital %CAPITAL% --risk %RISK% %FTMO%
if exist "results\comparaison.html" (start "" "results\comparaison.html") else (echo Lancez d'abord une recherche.)
pause & goto menu

:explore
echo Toutes les strategies x tous les R:R en paper trading. La plateforme s'ouvre dans le navigateur.
echo Laissez cette fenetre et MT5 ouverts. Ctrl+C pour arreter (tout est sauvegarde).
python run.py paper --symbols %SYMS% --timeframes %TFS% --source exploration --capital %CAPITAL% --risk %RISK% --commission %COMMISSION% %FTMO%
pause & goto menu

:paper
python run.py paper --symbols %SYMS% --source meilleures --top 30 --capital %CAPITAL% --risk %RISK% --commission %COMMISSION% %FTMO% --out results\paper_meilleures --port 8766
pause & goto menu

:paperok
python run.py paper --symbols %SYMS% --timeframes %TFS% --source portefeuille --capital %CAPITAL% --risk %RISK% --commission %COMMISSION% %FTMO% --out results\paper_validees --port 8767
pause & goto menu

:platform
start "" "http://localhost:8765"
echo Si la page ne s'affiche pas : lancez d'abord le paper trading (option 6).
echo Options 7 et 8 : http://localhost:8766 et http://localhost:8767
pause & goto menu
