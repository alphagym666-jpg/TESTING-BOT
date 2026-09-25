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
rem Nombre de bougies par marche et timeframe
set BARS=30000
rem =====================================================================================

:menu
cls
echo ==============================================
echo    Labo de strategies MT5 - 10 agents, 2 chefs
echo ==============================================
echo   Marches : %SYMS%
echo   Timeframes : %TFS%
echo   Capital fictif : %CAPITAL%    Perte max par trade : %RISK%%%
echo   Commission/lot : %COMMISSION%
echo.
echo   1. Tester la connexion a MT5
echo   2. Changer marches / timeframes
echo   3. RECHERCHE COMPLETE : tous les marches x tous les timeframes
echo   4. Recherche longue (encore plus de tests, plusieurs heures)
echo   5. Ouvrir la COMPARAISON (quelle strategie rapporte le plus)
echo   6. PAPER TRADING : les 30 meilleures strategies (tous marches et timeframes)
echo   7. PAPER TRADING : seulement les strategies validees
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
if "%CHOIX%"=="5" goto compare
if "%CHOIX%"=="6" goto paper
if "%CHOIX%"=="7" goto paperok
if "%CHOIX%"=="8" goto dash
if "%CHOIX%"=="9" exit /b 0
goto menu

:check
python run.py check --symbols %SYMS%
pause & goto menu

:params
set /p SYMS=Marches separes par des espaces (ex: NASDAQ XAUUSD EURUSD) : 
set /p TFS=Timeframes (ex: M15 H1 H4, ou ALL pour tous) : 
goto menu

:lab
echo Recherche en cours : environ 2 a 3 minutes par marche et par timeframe. Laissez MT5 ouvert.
python run.py lab --symbols %SYMS% --timeframes %TFS% --bars %BARS% --risk %RISK% --capital %CAPITAL% --commission %COMMISSION%
if exist "results\comparaison.html" start "" "results\comparaison.html"
pause & goto menu

:labxl
python run.py lab --symbols %SYMS% --timeframes %TFS% --bars 60000 --rounds 5 --budget 3000 --risk %RISK% --capital %CAPITAL% --commission %COMMISSION%
if exist "results\comparaison.html" start "" "results\comparaison.html"
pause & goto menu

:compare
python run.py compare --capital %CAPITAL%
if exist "results\comparaison.html" (start "" "results\comparaison.html") else (echo Lancez d'abord une recherche.)
pause & goto menu

:paper
echo Paper trading en cours. Ouvrez le tableau de bord avec l'option 8 dans une autre fenetre. Ctrl+C pour arreter.
python run.py paper --symbols %SYMS% --source meilleures --top 30 --capital %CAPITAL% --risk %RISK% --commission %COMMISSION%
pause & goto menu

:paperok
python run.py paper --symbols %SYMS% --timeframes %TFS% --source approuvees --capital %CAPITAL% --risk %RISK% --commission %COMMISSION%
pause & goto menu

:dash
if exist "results\paper\tableau_de_bord.html" (start "" "results\paper\tableau_de_bord.html") else (echo Lancez d'abord le paper trading.)
pause & goto menu
