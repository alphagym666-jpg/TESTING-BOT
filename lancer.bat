@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\activate.bat (
  echo Lancez d'abord install.bat
  pause & exit /b 1
)
call .venv\Scripts\activate.bat
rem accelerateur de calcul (numba) : installe une seule fois si absent, sans bloquer si impossible
python -c "import numba" 2>nul || (echo Installation de l'accelerateur de calcul numba... & pip install -q numba >nul 2>&1)

rem ===================== REGLAGES (modifiables avec le Bloc-notes) =====================
rem Marches : NASDAQ, GOLD, GER40, US30 sont reconnus automatiquement (US100.cash, XAUUSD, GER40.cash, US30.cash...)
rem Plus de marches = recherche plus longue (7 marches x 7 timeframes = 49 cases)
set SYMS=NASDAQ XAUUSD EURUSD GER40 US30 GBPUSD USDJPY
rem Timeframes : ALL = M1 M5 M15 M30 H1 H4 D1
set TFS=ALL
rem Capital fictif de chaque strategie et perte max par trade (en %% du capital)
set CAPITAL=100000
set RISK=1
rem Commission aller-retour par lot, par symbole (verifiez les montants de votre compte FTMO)
set COMMISSION=EURUSD=5 GBPUSD=5 USDJPY=5 XAUUSD=5 NASDAQ=0 GER40=0 US30=0
rem Regles OFFICIELLES du challenge FTMO (objectif, perte max par jour, perte max totale, en %%).
rem --ftmo-meilleur-jour 50 : une journee ne peut pas faire plus de 50 %% du profit total (0 = pas de regle).
rem Vos propres limites, plus prudentes (2.5 %% par jour), sont DIR_PERTE_JOUR et DIR_PERTE_TOTALE plus bas.
rem --ftmo-perte-suiveuse 1 : FTMO 1 etape, la perte max de 10 %% suit le plus haut solde de fin de journee (0 = fixe, 2 etapes)
set FTMO=--ftmo-target 10 --ftmo-daily 3 --ftmo-total 10 --ftmo-meilleur-jour 50 --ftmo-perte-suiveuse 1
rem Le Directeur : risque MAX par trade, et perte possible max par jour (il teste tous les scenarios jusqu'a ce plafond)
set DIR_RISQUE_MAX=1.0
set DIR_PERTE_JOUR=2.5
set DIR_PERTE_TOTALE=10
rem Option X (MAXIMUM) : tests par agent et par round, rounds, generations des inventeurs et des genies
set MAX_TESTS=5000
set MAX_ROUNDS=6
set MAX_INVENTIONS=20
set MAX_GENIES=30
rem Horaire de la strategie combinee pour le paper trading (C) et le bot (E) :
rem   vide = la meilleure trouvee par le Directeur ; sinon 24h24, 8h-17h ou 8h-13h (heure du Quebec)
set HORAIRE=
rem Periode : vide = tout l'historique (2 ans M1/M5, 5 ans ailleurs) ; ou une date, ex. 2025-01-01,
rem pour chercher ET valider seulement sur la periode recente (resultats dans un dossier a part)
set DEPUIS=
rem Exiger que chaque strategie batte le buy ^& hold (1 = oui)
set BATTRE_BH=0
rem Les autres comptes (options K, M, V) : capital du compte perso et du compte finance apres le challenge
set CAPITAL_PERSO=5000
rem Compte perso : risque max par trade et perte possible max par jour (en %%)
set RISQUE_PERSO=2
set PERTE_JOUR_PERSO=5
set CAPITAL_FINANCE=100000
rem Historique teste : 2 ans en M1 et M5, 5 ans en M15, M30, H1, H4 et D1 (automatique)
rem =====================================================================================

:menu
set RES=results
set PER=
if not "%DEPUIS%"=="" (set RES=results_depuis_%DEPUIS%& set PER=--depuis %DEPUIS%)
if "%BATTRE_BH%"=="1" set PER=%PER% --battre-buy-hold
cls
echo ==================================================
echo    Labo de strategies MT5 - Directeur, 4 chefs, 20 agents, 2 genies, le Conseil
echo ==================================================
echo   Marches : %SYMS%    Timeframes : %TFS%
echo   Capital fictif : %CAPITAL%    Risque max par trade : %RISK%%%    Perte max par jour : %DIR_PERTE_JOUR%%%    Perte max totale : %DIR_PERTE_TOTALE%%%
echo   Commission/lot : %COMMISSION%
echo   FTMO : %FTMO%
if not "%DEPUIS%"=="" echo   PERIODE RECENTE : depuis %DEPUIS% (resultats dans %RES%)
echo   P. Changer la periode (tout l'historique / depuis 2025...) et le buy ^& hold
echo.
echo   1. Tester la connexion a MT5
echo   2. Changer marches / timeframes
echo   N. Activer le filtre des NOUVELLES economiques (explications)
echo.
echo   --- LE DIRECTEUR (4 chefs, 20 agents + les 2 GENIES Einstein et Hawking) ---
echo   D. PASSER LE CHALLENGE FTMO LE PLUS VITE : le Directeur fait tout (recherche, melange de
echo      tous les marches et timeframes, reglages du risque), puis lance le paper trading du resultat
echo   X. DIRECTEUR MAXIMUM : refait TOUTES les cases avec le maximum de tests par agent (%MAX_TESTS% par round,
echo      %MAX_ROUNDS% rounds) + genies et Conseil au maximum. TRES long (une nuit ou plus), laissez MT5 ouvert
echo   W. MELANGER LES STRATEGIES COMBINEES entre elles (Chef des combinaisons) : la combinee + les autres
echo      horaires + le portefeuille du Chef FTMO + le direct, gardee seulement si ca passe plus vite
echo      + QUEL COMPTE CHOISIR (1 etape ou 2 etapes, Standard ou Swing, effet du week-end)
echo   R. Ouvrir le CLASSEMENT GENERAL (meilleures strategies, catalogue, failles, combinee)
echo   T. Alertes sur le TELEPHONE (Telegram) : explications + message de test
echo   L. MEILLEURS SETUPS DU DIRECT : analyse des trades du paper trading + meilleure combinaison
echo   G. Voir les DECOUVERTES DES GENIES (Einstein et Hawking : leurs lois mathematiques)
echo   F. Ouvrir les FICHES detaillees des strategies (pour le paper trading et le bot)
echo   C. PAPER TRADING de la strategie combinee (un seul compte, 24h/24) + signaux pour le bot
echo   E. Generer le BOT MT5 (LaboBot.mq5) : strategie combinee OU n'importe quelle strategie (fiche)
echo.
echo   --- LES AUTRES COMPTES (memes strategies, autre objectif) ---
echo   K. Construire la strategie du COMPTE PERSO (%CAPITAL_PERSO% $, %RISQUE_PERSO%%%/trade, %PERTE_JOUR_PERSO%%%/jour max) et du COMPTE FINANCE (apres le challenge)
echo   M. PLATEFORME + paper trading du COMPTE PERSO (http://localhost:8856)
echo   V. PLATEFORME + paper trading du COMPTE FINANCE (3 %%/jour, 10 %% au total ; http://localhost:8857)
echo.
echo   --- RECHERCHE (les agents testent et inventent des strategies) ---
echo   3. Recherche complete : tous les marches x tous les timeframes (agents + genies)
echo   4. Recherche FTMO INTENSIVE (beaucoup plus de tests, plusieurs heures)
echo   5. Ouvrir la COMPARAISON (quelle strategie rapporte le plus / passe FTMO)
echo.
echo   --- PAPER TRADING 24h/24 (s'ouvre dans sa propre fenetre, le menu reste libre) ---
echo   6. EXPLORATION : toutes les strategies x tous les R:R
echo   7. Les 30 meilleures strategies de la recherche
echo   8. Le portefeuille du Chef FTMO / strategies validees
echo   S. Strategies A L'ESSAI (non validees mais gagnantes sur la periode de test) + validees
echo   9. Ouvrir la PLATEFORME (voir les trades en direct)
echo   A. Lancer l'exploration automatiquement au demarrage de Windows
echo   B. Ne plus lancer au demarrage de Windows
echo.
echo   0. Quitter le menu (le paper trading continue dans sa fenetre)
echo.
echo   Aucune option n'envoie d'ordre a MetaTrader.
echo.
set CHOIX=
set /p CHOIX=Votre choix : 
if "%CHOIX%"=="1" goto check
if "%CHOIX%"=="2" goto params
if /i "%CHOIX%"=="P" goto periode
if "%CHOIX%"=="3" goto lab
if "%CHOIX%"=="4" goto labxl
if "%CHOIX%"=="5" goto compare
if "%CHOIX%"=="6" goto explore
if "%CHOIX%"=="7" goto paper
if "%CHOIX%"=="8" goto paperok
if /i "%CHOIX%"=="S" goto paperessai
if "%CHOIX%"=="9" goto platform
if /i "%CHOIX%"=="D" goto directeur
if /i "%CHOIX%"=="X" goto directeurmax
if /i "%CHOIX%"=="W" goto melanges
if /i "%CHOIX%"=="N" goto nouvelles
if /i "%CHOIX%"=="R" goto rapportdir
if /i "%CHOIX%"=="G" goto genies
if /i "%CHOIX%"=="L" goto direct
if /i "%CHOIX%"=="T" goto telegram
if /i "%CHOIX%"=="F" goto fiches
if /i "%CHOIX%"=="C" goto papercomb
if /i "%CHOIX%"=="E" goto bot
if /i "%CHOIX%"=="K" goto comptes
if /i "%CHOIX%"=="M" goto paperperso
if /i "%CHOIX%"=="V" goto paperfinance
if /i "%CHOIX%"=="A" goto autostart
if /i "%CHOIX%"=="B" goto noautostart
if "%CHOIX%"=="0" exit /b 0
goto menu

:periode
echo.
echo Periode actuelle : %DEPUIS% (vide = tout l'historique)
echo  - Tout l'historique : les strategies sont cherchees sur les annees passees et verifiees sur les plus recentes.
echo  - Depuis 2025-01-01 : cherchees ET verifiees seulement sur 2025-2026 (le marche d'aujourd'hui), moins de
echo    donnees donc plus de risque de hasard : le paper trading en direct sert de vraie verification.
set DEPUIS=
set /p DEPUIS=Date de debut (ex: 2025-01-01, ou Entree seul pour tout l'historique) : 
set BATTRE_BH=0
set /p BATTRE_BH=Exiger de battre le buy and hold ? (1 = oui, 0 = non) : 
goto menu

:nouvelles
cls
echo Filtre des nouvelles : aucune entree 30 min avant / apres une annonce a fort impact (NFP, CPI, FOMC, BCE...)
echo.
echo  1. Dans MT5 : Fichier ^> Ouvrir le dossier des donnees ^> MQL5 ^> Scripts
echo  2. Copiez-y le fichier mql5\ExportNews.mq5 de ce dossier
echo  3. Dans MT5 : clic droit sur "Scripts" dans le Navigateur ^> Actualiser
echo  4. Double-cliquez ExportNews.mq5 (MetaEditor s'ouvre) et appuyez sur F7 pour compiler
echo  5. Glissez le script ExportNews sur n'importe quel graphique ^> OK
echo.
echo  Le calendrier est ecrit dans le dossier commun de MT5 et la plateforme le trouve toute seule.
echo  Refaites l'etape 5 une fois par mois pour garder les annonces a venir a jour.
echo.
pause
goto menu

:check
python run.py check --symbols %SYMS%
pause & goto menu

:params
set /p SYMS=Marches separes par des espaces (ex: NASDAQ XAUUSD EURUSD GER40 US30 GBPUSD USDJPY) : 
set /p TFS=Timeframes (ex: M15 H1 H4, ou ALL pour tous) : 
goto menu

:lab
echo Recherche sur 2 ans (M1, M5) et 5 ans (M15 a D1) : comptez plusieurs heures pour tout. Laissez MT5 ouvert.
python run.py lab --out %RES% %PER% --symbols %SYMS% --timeframes %TFS% --risk %RISK% --capital %CAPITAL% --commission %COMMISSION% %FTMO%
if exist "%RES%\comparaison.html" start "" "%RES%\comparaison.html"
pause & goto menu

:labxl
echo Recherche intensive : 6 rounds, 3000 tests par agent et par round, 15 generations d'inventions.
python run.py lab --out %RES% %PER% --symbols %SYMS% --timeframes %TFS% --rounds 6 --budget 3000 --invent-generations 15 --generations-genies 30 --risk %RISK% --capital %CAPITAL% --commission %COMMISSION% %FTMO%
if exist "%RES%\comparaison.html" start "" "%RES%\comparaison.html"
pause & goto menu

:compare
python run.py compare --out %RES% --capital %CAPITAL% --risk %RISK% %FTMO%
if exist "%RES%\comparaison.html" (start "" "%RES%\comparaison.html") else (echo Lancez d'abord une recherche.)
pause & goto menu

:melanges
echo Le Chef des combinaisons melange la strategie combinee avec les autres combinaisons (quelques minutes).
python run.py directeur --melanges-seulement --out %RES% %PER% --symbols %SYMS% --timeframes %TFS% --capital %CAPITAL% --risk %RISK% --risk-max %DIR_RISQUE_MAX% --perte-max-jour %DIR_PERTE_JOUR% --perte-max-totale %DIR_PERTE_TOTALE% --commission %COMMISSION% %FTMO%
if exist "%RES%\directeur.html" start "" "%RES%\directeur.html"
pause & goto menu

:directeurmax
echo Directeur MAXIMUM : chaque agent fait %MAX_TESTS% tests par round, %MAX_ROUNDS% rounds, %MAX_INVENTIONS% generations
echo d'inventions, %MAX_GENIES% generations pour les genies, et le Conseil sur chaque case. TOUT est refait.
echo Comptez une nuit ou plus. Le PC doit rester allume (mise en veille bloquee pendant le calcul).
python run.py directeur --refaire --out %RES% %PER% --symbols %SYMS% --timeframes %TFS% --capital %CAPITAL% --risk %RISK% --risk-max %DIR_RISQUE_MAX% --perte-max-jour %DIR_PERTE_JOUR% --perte-max-totale %DIR_PERTE_TOTALE% --commission %COMMISSION% --budget %MAX_TESTS% --rounds %MAX_ROUNDS% --invent-generations %MAX_INVENTIONS% --generations-genies %MAX_GENIES% %FTMO%
if exist "%RES%\directeur.html" start "" "%RES%\directeur.html"
if exist "%RES%\strategie_combinee.json" goto papercomb
pause & goto menu

:directeur
echo Le Directeur reprend le travail deja fait, relance les cases faibles en mode intensif,
echo puis construit la strategie combinee. Comptez de quelques minutes a plusieurs heures. Laissez MT5 ouvert.
python run.py directeur --out %RES% %PER% --symbols %SYMS% --timeframes %TFS% --capital %CAPITAL% --risk %RISK% --risk-max %DIR_RISQUE_MAX% --perte-max-jour %DIR_PERTE_JOUR% --perte-max-totale %DIR_PERTE_TOTALE% --commission %COMMISSION% %FTMO%
if exist "%RES%\directeur.html" start "" "%RES%\directeur.html"
if exist "%RES%\strategie_combinee.json" goto papercomb
pause & goto menu

:telegram
cls
echo Le SURVEILLANT peut envoyer sur votre telephone : trades de la strategie combinee et des bots, challenge
echo reussi ou rate, MT5 deconnecte, bot MT5 silencieux ou ordre non execute, et le rapport du soir.
echo.
echo  1. Installez Telegram sur le telephone.
echo  2. Cherchez @BotFather, envoyez /newbot, donnez un nom : il vous donne un TOKEN (ex. 123456:ABC-xyz).
echo  3. Ouvrez la conversation avec VOTRE nouveau bot et envoyez-lui "bonjour".
echo  4. Cherchez @userinfobot sur Telegram : il vous donne votre ID (un nombre).
echo  5. Ouvrez le fichier .env de ce dossier avec le Bloc-notes et ajoutez 2 lignes :
echo        TELEGRAM_TOKEN=le token de l'etape 2
echo        TELEGRAM_CHAT_ID=le nombre de l'etape 4
echo  6. Revenez ici : un message de test va partir. Relancez ensuite le paper trading.
echo.
pause
python run.py telegram
pause & goto menu

:direct
python run.py direct --results %RES% --risk %RISK% --perte-max-jour %DIR_PERTE_JOUR% --perte-max-totale %DIR_PERTE_TOTALE% %FTMO%
if exist "%RES%\direct.html" start "" "%RES%\direct.html"
echo (Aussi en direct dans la plateforme : onglet "Meilleurs setups du direct")
pause & goto menu

:genies
echo.
echo Les 2 genies travaillent seuls pendant chaque recherche (option D ou 3) : ils inventent des formules
echo mathematiques (physique pour Einstein, maths et cosmologie pour Hawking). Dans le rapport qui s ouvre :
echo section "Les decouvertes des genies".
if exist "%RES%\directeur.html" (start "" "%RES%\directeur.html") else (echo Lancez d'abord le Directeur : option D.)
pause & goto menu

:rapportdir
if exist "%RES%\strategie_combinee.json" python run.py directeur --rapport-seulement --out %RES% %PER% --symbols %SYMS% --timeframes %TFS% --risk-max %DIR_RISQUE_MAX% %FTMO%
if exist "%RES%\directeur.html" (start "" "%RES%\directeur.html") else (echo Lancez d'abord le Directeur : option D.)
pause & goto menu

:fiches
if exist "%RES%\strategie_combinee.json" python run.py directeur --rapport-seulement --out %RES% %PER% --symbols %SYMS% --timeframes %TFS% --risk-max %DIR_RISQUE_MAX% %FTMO%
if exist "%RES%\fiches_strategies.html" (start "" "%RES%\fiches_strategies.html") else (echo Lancez d'abord le Directeur : option D.)
pause & goto menu

:bot
echo.
echo Bot MT5 : Entree seule = la strategie combinee du Directeur.
echo Pour une AUTRE strategie : collez l'identifiant ecrit dans sa fiche (option F), ex. XAUUSD-H1-ema-cross-...
echo (ou utilisez le bouton "Bot MT5" dans la plateforme de paper trading)
set FICHE=
set /p FICHE=Identifiant de la fiche (ou Entree) : 
if not "%FICHE%"=="" goto botfiche
set HOR=
if not "%HORAIRE%"=="" set HOR=--horaire %HORAIRE%
python run.py bot --results %RES% --capital %CAPITAL% %FTMO% %HOR%
if exist "%RES%\bot\LaboBot.mq5" (start "" "%RES%\bot" & start "" notepad "%RES%\bot\LISEZMOI_BOT.txt")
pause & goto menu

:botfiche
python run.py bot --results %RES% --fiche %FICHE% --capital %CAPITAL% %FTMO%
if exist "%RES%\bots" start "" "%RES%\bots"
pause & goto menu

:comptes
echo Le Directeur reprend les strategies deja trouvees et construit :
echo  - le COMPTE PERSO : meilleur rendement a long terme (interets composes), au plus 5 %% de chances de baisser de 25 %%
echo  - le COMPTE FINANCE : meilleur rendement par mois sans jamais perdre 3 %% dans une journee ni 10 %% au total
python run.py directeur --comptes-seulement --out %RES% %PER% --symbols %SYMS% --timeframes %TFS% --capital-perso %CAPITAL_PERSO% --risque-perso %RISQUE_PERSO% --perte-jour-perso %PERTE_JOUR_PERSO% --capital-finance %CAPITAL_FINANCE% --risk %RISK% --commission %COMMISSION% %FTMO%
if exist "%RES%\compte_perso.html" start "" "%RES%\compte_perso.html"
if exist "%RES%\compte_finance.html" start "" "%RES%\compte_finance.html"
pause & goto menu

:paperperso
if not exist "%RES%\strategie_combinee_perso.json" (echo Lancez d'abord l'option K. & pause & goto menu)
start "Paper trading - compte perso" "%~dp0paper_24h.bat" --results %RES% --profil perso --capital %CAPITAL_PERSO% --commission %COMMISSION% --out %RES%\paper_perso --port 8856
echo Plateforme du compte perso : http://localhost:8856 (bouton Bot MT5 sur la plateforme pour son bot)
pause & goto menu

:paperfinance
if not exist "%RES%\strategie_combinee_finance.json" (echo Lancez d'abord l'option K. & pause & goto menu)
start "Paper trading - compte finance" "%~dp0paper_24h.bat" --results %RES% --profil finance --capital %CAPITAL_FINANCE% --commission %COMMISSION% --out %RES%\paper_finance --port 8857
echo Plateforme du compte finance : http://localhost:8857 (bouton Bot MT5 sur la plateforme pour son bot)
pause & goto menu

:papercomb
set HOR=
if not "%HORAIRE%"=="" set HOR=--horaire %HORAIRE%
start "Paper trading - strategie combinee" "%~dp0paper_24h.bat" --results %RES% --source combinee %HOR% --capital %CAPITAL% --risk %RISK% --commission %COMMISSION% %FTMO% --out %RES%\paper_combinee --port 8768
echo.
echo La strategie combinee demarre dans une NOUVELLE fenetre (reduisez-la, ne la fermez pas).
echo Plateforme : http://localhost:8768 - onglet "Strategie combinee".
pause & goto menu

:explore
start "Paper trading - exploration" "%~dp0paper_24h.bat" --results %RES% --symbols %SYMS% --timeframes %TFS% --source exploration --capital %CAPITAL% --risk %RISK% --commission %COMMISSION% %FTMO% --out %RES%\paper
goto lance

:paper
start "Paper trading - 30 meilleures" "%~dp0paper_24h.bat" --results %RES% --symbols %SYMS% --source meilleures --top 30 --capital %CAPITAL% --risk %RISK% --commission %COMMISSION% %FTMO% --out %RES%\paper_meilleures --port 8766
goto lance

:paperok
start "Paper trading - portefeuille FTMO" "%~dp0paper_24h.bat" --results %RES% --symbols %SYMS% --timeframes %TFS% --source portefeuille --capital %CAPITAL% --risk %RISK% --commission %COMMISSION% %FTMO% --out %RES%\paper_validees --port 8767
goto lance

:paperessai
start "Paper trading - strategies a l'essai" "%~dp0paper_24h.bat" --results %RES% --symbols %SYMS% --timeframes %TFS% --source essai --capital %CAPITAL% --risk %RISK% --commission %COMMISSION% %FTMO% --out %RES%\paper_essai --port 8769
goto lance

:lance
echo.
echo Le paper trading demarre dans une NOUVELLE fenetre : reduisez-la, ne la fermez pas.
echo Il tourne en continu et redemarre tout seul s'il s'arrete. La plateforme s'ouvre dans le navigateur.
echo Vous pouvez fermer le navigateur quand vous voulez et le rouvrir avec l'option 9.
echo Ce menu reste libre : vous pouvez lancer une recherche en meme temps.
pause & goto menu

:autostart
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
> "%STARTUP%\LaboMT5_paper_trading.bat" echo @echo off
>> "%STARTUP%\LaboMT5_paper_trading.bat" echo start "Paper trading - exploration" /min "%~dp0paper_24h.bat" --results %RES% --symbols %SYMS% --timeframes %TFS% --source exploration --capital %CAPITAL% --risk %RISK% --commission %COMMISSION% %FTMO% --out %RES%\paper
echo.
echo C'est fait : a chaque demarrage de Windows, l'exploration se lance toute seule (fenetre reduite).
echo MetaTrader 5 est ouvert automatiquement par la plateforme s'il est installe.
echo Pensez a garder la session Windows ouverte et le PC allume.
pause & goto menu

:noautostart
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\LaboMT5_paper_trading.bat" 2>nul
echo Le lancement automatique au demarrage de Windows est desactive.
pause & goto menu

:platform
start "" "http://localhost:8765"
echo Si la page ne s'affiche pas : lancez d'abord le paper trading (option 6) et attendez 30 secondes.
echo Options 7 et 8 : http://localhost:8766 et http://localhost:8767 - Strategie combinee (C) : http://localhost:8768
pause & goto menu
