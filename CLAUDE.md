# Labo MT5 (TESTING-BOT) : contexte pour Claude

Ce fichier résume des mois de conversation avec l'utilisateur pour qu'une nouvelle session reparte au bon endroit.
Lis-le en entier avant de toucher au code.

## Qui est l'utilisateur, comment lui parler
- Francophone québécois, PC **Windows**, compte **FTMO-Demo** sur MT5. Il écrit vite, avec des fautes : comprends l'intention.
- **Réponds en français, ton québécois accessible, phrases courtes, sans jargon inutile.** Explique le « pourquoi ».
- Il veut **des chiffres réalistes auxquels il peut se fier** avant de mettre un bot sur un compte. Jamais de promesses,
  jamais de chiffres gonflés. Dis honnêtement quand rien n'est fiable ou quand tu ne peux pas vérifier (ex. MT5 Windows).
- Il veut que tout soit **simple à utiliser** (double-clics, boutons, pas de ligne de commande).
- Il n'est pas développeur : il copie / télécharge le dossier depuis GitHub (ZIP), il n'utilise pas git.

## Le but
Passer les challenges **FTMO** (1 étape : +10 %, perte max du jour 3 %, perte totale 10 % **suiveuse**, meilleur jour <= 50 %
du profit, 4 jours minimum) et trader un **compte perso 5 000 $**, avec des **bots MT5** fiables.
Ses limites perso : 1 %/trade et 2,5 %/jour (FTMO) ; 2 %/trade et 5 %/jour (compte perso 5k).

## Sécurité (toujours)
- **Ne jamais demander ni écrire de mots de passe, tokens ou clés API** dans le chat ni dans le code. Les secrets vont dans
  `.env` sur son PC seulement. Si tu en vois dans un dépôt (ex. `nasdaq-bot` : token Telegram, clés Finnhub / Twelve Data en clair),
  dis-lui de les **changer** et de les mettre dans les variables de Railway, sans recopier les valeurs.
- Le **paper trading n'envoie jamais d'ordre**. Le bot `LaboBot.mq5` refuse les comptes réels sauf `InpAutoriserReel=true`.
- La plateforme web n'écoute que sur `127.0.0.1:8765`. Les boutons des rapports exigent un jeton.
- **Pas de pull request sans demande.** Branche de travail : `claude/mt5-trading-agents-platform-4grro9`.
  Commits en français, avec la ligne `Co-Authored-By` et `Claude-Session` demandées par l'environnement.
- Confirme avant tout geste difficile à annuler ou visible à l'extérieur.

## Le projet
Python (`mt5lab/`), relié à MT5 par le module `MetaTrader5`. Point d'entrée : `run.py` (commandes `lab`, `directeur`,
`paper`, `bot`, `direct`, `railway`, `compare`, `check`, `strategies`). Menu pour l'utilisateur : `lancer.bat`
(touches 1 à 9, R, G). `paper_24h.bat` lance le paper trading. `METTRE_A_JOUR.bat` met son dossier à jour depuis GitHub
(résultats, `.env` et bots conservés).

Modules importants :
- `strategies*.py`, `inventions.py`, `genies.py`, `banques.py`, `conseil.py` : le catalogue de stratégies et les « employés » qui en inventent.
- `backtest.py` (moteur numba, gestions : none, breakeven, trailing, paliers, intelligente, **verrou**), `backtest_combinee.py`.
- `direct.py` (Chef des combinaisons, `top_combinations`, classement `rank_key`), `manager.py` (le Directeur), `comptes.py` (profils perso 5k / financé).
- `top_backtest.py` : le backtest des 2 dernières années + classements (seules, combinées, croisé backtest x paper, général, perso, **réalistes**).
- **`fiabilite.py`** : les 5 contrôles de fiabilité et le gain réaliste (voir plus bas).
- `horaires.py` : meilleures heures de chaque stratégie, planning de la journée. **Toutes les heures sont en heure du serveur MT5.**
- `paper.py` : le paper trading (aussi la source des signaux du bot). `pont.py` : fichier de signaux vers `mql5/LaboBot.mq5`,
  génération et installation des bots, diagnostic « pourquoi 0 trade ».
- `plateforme.py` : serveur + page web (gros fichier : HTML/JS dans la chaîne `PAGE`). Sections : Accueil, En direct, Classements
  (✅ Réalistes, Général, Backtest 2 ans, TOP 10 du direct...), Analyse, **Combinaisons**, **Mes bots**.
- `railway.py` : bots GAP FILL et STOCH de la plateforme Railway reproduits (voir plus bas).
- Tests : `python -m pytest -q --ignore=tests/test_manager.py` (~2-3 min, 252 tests) ; `tests/test_manager.py` seul ~5 min.

## Ce qu'on a appris (important)
1. **Le capital du bot était codé en dur à 100 000 $** : sur un autre compte, le bot croyait à une perte énorme (ou à l'objectif
   atteint) et faisait 0 trade. Corrigé : `InpCapital = 0` = auto (solde au 1er lancement) ; `InpReinitialiser` ; le paper s'aligne
   sur le vrai solde. La page « Mes bots » affiche un diagnostic (signaux, ordres, refus et raisons).
2. **Les « meilleures stratégies » étaient surtout de la chance** : on teste des milliers de stratégies et on garde les meilleures
   (99 % de réussite, +190 % en 2 mois, des milliards de $ sur 1 an = absurde). Causes : seulement ~2 mois d'historique M1,
   intérêts composés sans plafond, aucun contrôle de chance. Corrigé :
   - projections sur 1 an refusées sous 6 mois d'historique ; intérêts composés plafonnés à 10 x le capital ;
   - **onglet ✅ Réalistes** : une stratégie n'a des chiffres que si elle passe 5 contrôles (1 an d'historique, 100 trades,
     solidité t > racine(2 ln N), période récente gagnante, paper pas perdant) ; gain réaliste = période récente / 2, sans
     intérêts composés ; sinon « pas fiable : aucun chiffre ». Mesuré : la meilleure de 3 000 stratégies au hasard passe 3 fois sur 30.
3. **Il faut plus d'historique** : MT5 > Outils > Options > Graphiques > « Barres max. » = Unlimited, redémarrer MT5, puis surtout
   des marchés en **M15 / H1 / H4** (des années d'historique), pas en M1 (2 mois).
4. **Heures** : bougies et ticks MT5 sont en heure du serveur (FTMO : environ 7 h d'avance sur le Québec). Le code est cohérent ;
   la plateforme détecte le décalage et affiche « (x h-y h chez vous) ».
5. **GAP FILL / STOCH (plateforme Railway de l'utilisateur, `https://worker-production-2f53.up.railway.app/`, code dans le dépôt
   `nasdaq-bot`)** : règles copiées dans `railway.py` (`gapfill_ny`, `stoch_range`, gestion « verrou », jumeau exact de la grille
   Railway). La grille Railway vérifie stop et cible sur la bougie d'entrée avant l'entrée (biais). Test honnête sur 3 mois de
   NASDAQ 15 min : **GAP FILL (stop x1,3, cible 4R) +11R et (x1,7, 2R) +8R, à tester en paper** ; **STOCH 15 min perd** (-1R à -12R),
   ses +29R sur Railway venaient d'une bonne période. `python run.py railway` (touche G) refait ce test sur l'historique MT5 et crée les bots.

## À faire / idées en attente
- Avec plus d'historique MT5 (Unlimited, M15/H1/H4) : relancer « Backtester toutes les stratégies » et regarder ✅ Réalistes et « presque fiables ».
- Laisser tourner le paper trading plusieurs semaines : il départage vraiment. Un seul bot par compte (jamais deux combinaisons sur le même compte).
- Lancer `python run.py railway` sur son PC (MT5 ouvert) et regarder GAP FILL M15 sur US100.cash.
- Vérifier sur son vrai MT5 Windows : installation / compilation du bot (F7 dans MetaEditor), bouton « ▶ Démarrer » de Mes bots,
  `METTRE_A_JOUR.bat` : **rien de tout ça n'a pu être testé depuis le cloud.**
- Projet **séparé** : l'app de facturation Murco (dépôt `GROUPE-MURCO`, React + Capacitor). Il avait demandé : micro « clic pour commencer /
  clic pour arrêter », une dictée qui crée client + soumission + agenda, et la correction du bouton « Analyser » (« facture pour Nathalie
  Bouchard au 18-35 rue des » ne marche que pour un client déjà existant, et le micro Android coupe au premier silence).
  Il a ensuite dit d'**annuler** ça (« c'est mon autre système ») : rien n'a été modifié dans ce dépôt. Ne le touche que s'il le redemande.
