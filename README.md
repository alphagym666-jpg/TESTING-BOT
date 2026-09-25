# Labo de stratégies MT5 — 10 agents + 2 chefs d'équipe

Plateforme Python reliée à MetaTrader 5. Elle teste automatiquement des milliers de combinaisons
de **stratégies × paramètres × filtres × stop loss × R:R × gestion de position**. Le travail est
réparti entre **10 agents chercheurs** supervisés par **2 chefs d'équipe**.

## L'équipe

| Chef d'équipe | Agent | Ce qu'il fait |
|---|---|---|
| **Chef A — Exploration** | 1. Chasseur de tendance | MA/EMA/DEMA/TEMA/KAMA cross, Hull, MACD, Supertrend, SAR, ADX, Ichimoku, Aroon, Vortex, Alligator, Chandelier, pullback EMA20… |
| | 2. Contrarien | RSI, RSI(2) Connors, Bollinger, %b, Keltner, enveloppes, Williams %R, CCI, CMO, MFI, Ultimate, Z-score, **7 variantes Stochastique**, **divergences** RSI/MACD/Stoch/CCI/OBV (classiques et cachées) |
| | 3. Spécialiste cassures | Donchian/Turtle, Bollinger/Keltner, Squeeze, inside bar, NR7, range, **sessions ICT** (Silver Bullet, Judas swing asiatique, ORB Londres/NY), PDH/PDL, pivots |
| | 4. Momentum | ROC, RSI 50, MACD, VWAP, TRIX, Awesome Oscillator, Fisher, OBV, Coppock, Elder Impulse / Ray |
| | 5. Price action & SMC | **Order blocks, breaker blocks, FVG, inverse FVG, BOS, CHoCH, liquidity sweeps, equal highs/lows, OTE, premium/discount**, **zones offre/demande, break & retest, supports/résistances, Fibonacci**, double top/bottom, chandeliers (avalement, pin bar, étoiles, marteau, harami, tweezer, 3 soldats, marubozu, doji, Heikin Ashi…) |
| **Chef B — Optimisation** | 6. Spécialiste filtres | Ajoute des filtres (tendance EMA200/50, ADX fort/faible, volatilité, session Londres-NY), long seul / short seul |
| | 7. Architecte de combos | Combine 2 stratégies (confirmation sur N bougies, ET, OU) |
| | 8. Optimiseur R:R | Tous les SL (ATR, swing, %) × tous les R:R (1:0.5 → 1:5, ou sortie sur signal) × break-even / trailing |
| | 9. Explorateur aléatoire | Tire au hasard dans tout l'espace des possibilités |
| | 10. Généticien | Mute et croise les meilleurs candidats |

À chaque round, le Chef A fait cartographier toutes les familles puis transmet ses champions au
Chef B, qui les filtre, les combine et optimise leur R:R. À la fin :

1. **Validation hors-échantillon** : chaque chef reteste ses candidats sur les derniers 35 % de
   l'historique, que personne n'a vus pendant la recherche. Rejet si trop peu de trades, espérance
   négative, profit factor < 1.1, edge non significatif statistiquement, ou trop de dégradation.
   Le seuil de significativité est **corrigé pour les tests multiples** (Šidák) : plus on présente de
   candidats, plus la barre monte, pour qu'une stratégie « chanceuse » ne passe pas.
2. **Contre-expertise croisée** : chaque chef vérifie les trouvailles de l'autre avec des coûts
   (spread + commission) doublés et exige un résultat positif sur chaque moitié de la période de validation.

> Tests de contrôle : sur des marchés **100 % aléatoires** (où aucune stratégie ne peut gagner), la plateforme
> n'approuve **aucune** stratégie. Sans ces garde-fous, elle en approuvait plusieurs par pur hasard. Sur un marché
> avec un vrai edge caché (momentum), elle le retrouve.

### Retours sur zone

Toutes les stratégies de zone (order block, FVG, breaker, offre/demande, break & retest, S/R, OTE) se testent
avec plusieurs **modes d'entrée**. Les agents les essaient tous :

| Mode | Entrée |
|---|---|
| `touche` | dès que le prix revient dans la zone |
| `rejet` | retour dans la zone + bougie de rejet (mèche ≥ corps, clôture du bon côté) |
| `sans_rejet` | retour dans la zone **sans** mèche de rejet : on entre quand même |
| `cassure` | le prix traverse la zone et clôture au-delà sans rejet : on joue la cassure |

Une zone n'est jouée qu'au premier retour (zone « fraîche »). Les swings ne sont reconnus qu'une fois confirmés,
donc les stratégies ne voient jamais le futur (vérifié par les tests sur plusieurs réglages de chaque stratégie).

Les heures (sessions, killzones, Silver Bullet, ORB) sont celles du **serveur MT5** de votre courtier.

## Brancher la plateforme sur votre MT5 (Windows)

La connexion Python ↔ MetaTrader 5 fonctionne **uniquement sous Windows**, sur le PC (ou le VPS Windows)
où le terminal MT5 est installé.

1. **Téléchargez le projet** : sur GitHub, bouton *Code → Download ZIP* (branche
   `claude/mt5-trading-agents-platform-4grro9`), puis dézippez-le, par exemple dans `C:\LaboMT5`.
2. **Installez Python 3.10+ 64 bits** depuis python.org en cochant *Add python.exe to PATH*.
3. **Double-cliquez sur `install.bat`**. Il crée l'environnement et installe `MetaTrader5`, `numpy` et `pandas`.
4. **Ouvrez MetaTrader 5 et connectez-vous** à votre compte, idéalement un compte **démo**.
   - Pour avoir beaucoup d'historique : *Outils → Options → Graphiques → Barres max. dans le graphique = Unlimited*.
5. *(Optionnel)* Remplissez le fichier `.env` (login, mot de passe, serveur) si vous voulez que la plateforme
   se connecte toute seule. Si MT5 est déjà connecté, laissez-le vide. Ce fichier reste sur votre PC.
6. **Double-cliquez sur `lancer.bat`** pour ouvrir le menu :

```
  1. Tester la connexion a MT5          <- commencez par ici
  2. Changer symboles / timeframe
  3. Lancer la recherche (10 agents)
  4. Lancer une recherche longue (plus de tests)
  5. Ouvrir les rapports
  6. PAPER TRADING : trades fictifs sur prix reels (top 20 par symbole)
  7. PAPER TRADING : seulement les strategies approuvees
  8. Ouvrir le tableau de bord du paper trading
```

Le test de connexion (`python run.py check --symbols EURUSD XAUUSD`) affiche ceci :

```
Diagnostic MT5
  [OK] Terminal connecté au serveur (Votre courtier)
  [OK] Compte 12345678 sur Courtier-Demo | DÉMO | 10000.0 USD | levier 1:100
  [--] Algo Trading désactivé (inutile pour la recherche et le paper trading : aucun ordre n'est envoyé)
  [OK] EURUSD.m : 5000 bougies H1 du ... au ... | spread 12 pts | lot min 0.01 | heure serveur ...
Tout est prêt.
```

La plateforme gère automatiquement :
- les **suffixes de symboles** des courtiers (`EURUSD` → `EURUSD.m`, `EURUSDm`, `EURUSD.raw`…) ;
- le **mode de remplissage** des ordres accepté par le courtier (évite l'erreur 10030) ;
- la **limite d'historique** du terminal (elle récupère ce qui est disponible) ;
- le **spread réel** du symbole comme coût dans les backtests.

### Sans Windows (Mac, Linux)

Le package Python `MetaTrader5` n'existe pas hors Windows. Vous pouvez quand même lancer la **recherche** :
copiez `mql5/ExportHistory.mq5` dans le dossier *MQL5/Scripts* de MT5 (*Fichier → Ouvrir le dossier des données*),
compilez-le, glissez-le sur un graphique, puis :

```bash
python run.py lab --csv EURUSD_H1.csv --cost 0.00012
```

Pour exécuter les stratégies en direct, il faut un PC ou un VPS Windows.

### Installation manuelle

```bash
pip install -r requirements.txt
```

## Utilisation

```bash
# 1) Essai sans MT5 (données synthétiques)
python run.py lab --demo

# 2) Recherche sur vos symboles MT5
python run.py lab --symbols EURUSD GBPUSD XAUUSD --timeframe H1 --bars 30000
python run.py lab --symbols US30 --timeframe M15 --bars 50000 --rounds 5 --budget 1500

# 3) Ou sur un CSV exporté de MT5
python run.py lab --csv data/EURUSD_H1.csv --cost 0.00012

# Liste des stratégies
python run.py strategies
```

Résultats dans `results/<SYMBOLE>_<TF>/` :

- `rapport.html` — tableau de bord : équipe, courbes, classement, journal des agents
- `classement.csv` — tous les finalistes avec stats in-sample et hors-échantillon
- `meilleures_strategies.json` — les stratégies approuvées, prêtes pour l'exécution
- `journal_agents.txt` — ce que chaque agent et chef a fait

Plus de rounds (`--rounds`) et de budget (`--budget`) = recherche plus large (et plus longue).

## Tous les marchés × tous les timeframes

```bash
# NASDAQ, or et EURUSD sur M1, M5, M15, M30, H1, H4 et D1, avec les commissions par symbole
python run.py lab --symbols NASDAQ XAUUSD EURUSD --timeframes ALL --commission EURUSD=5 XAUUSD=5 NASDAQ=0
```

- `NASDAQ` et `GOLD` sont reconnus automatiquement sous le nom de votre courtier (FTMO : `US100.cash`, `XAUUSD`…).
- Chaque couple marché × timeframe passe par les 10 agents et les 2 chefs. Comptez 2 à 3 minutes par couple
  sur un PC 4 cœurs, soit environ 1 h pour 3 marchés × 7 timeframes.
- À la fin, `results/comparaison.html` répond à « quelle stratégie rapporte le plus ? » :
  - une **carte marché × timeframe** avec la meilleure stratégie validée de chaque case ;
  - le **classement par gain mensuel** (% et $ sur le capital), calculé sur la période hors-échantillon et ramené
    par mois, pour comparer équitablement M1 et D1 ;
  - les stratégies qui marchent sur **plusieurs marchés / timeframes** (les plus robustes) ;
  - les stratégies prometteuses mais non validées.
- `python run.py compare` régénère la comparaison à partir des résultats déjà calculés.
- `python run.py paper --source meilleures --top 30` suit en paper trading les 30 meilleures, tous marchés et timeframes confondus.

## Paper trading : trades fictifs sur les prix réels

La plateforme **ne passe aucun ordre dans MetaTrader**. Elle prend les trades **fictivement**, en suivant
les vrais prix de votre MT5 en direct :

```bash
# les 20 meilleures stratégies de la recherche, par symbole
python run.py paper --symbols EURUSD XAUUSD --timeframes H1 --top 20
# seulement les stratégies approuvées, avec la commission de votre courtier (ex. 7 $ par lot)
python run.py paper --symbols EURUSD --timeframes H1 M15 --source approuvees --commission 7
```

Ce qui rend les chiffres réalistes :
- **entrée au vrai prix** : ask pour un achat, bid pour une vente, donc avec le spread réel du moment ;
- **SL et TP vérifiés tick par tick** avec l'historique des ticks MT5. Si le prix saute par-dessus le stop,
  la sortie se fait au prix réel du tick, glissement compris. Le TP est pris à son niveau ;
- **taille de lot, valeur du pip, lot minimum et pas de lot** : ceux de votre courtier ;
- break-even, trailing stop et sortie sur signal gérés comme dans le backtest ;
- **chaque stratégie a son compte virtuel** (100 000 par défaut, `--capital`) et son propre suivi ;
- **perte max par trade** : 0,5 % par défaut (`--risk`), soit **500 sur 100 000**. Le lot est arrondi vers le bas
  pour que la perte au stop, commission comprise, ne dépasse jamais ce montant. Le plafond est calculé sur le
  capital de départ (ou sur le solde s'il a baissé), donc il ne grossit pas avec les gains. Si même le lot minimum
  du courtier dépasse ce risque, le trade est ignoré. Seul un gap par-dessus le stop peut faire perdre un peu plus,
  exactement comme en réel.

Résultats dans `results/paper/` :
- `tableau_de_bord.html` : classement des stratégies en direct, positions ouvertes avec P&L latent,
  derniers trades, et comparaison avec les résultats attendus d'après la recherche (se rafraîchit toutes les 30 s) ;
- `trades.csv` : tous les trades fictifs, à ouvrir dans Excel ;
- `etat.json` : sauvegarde. Si vous arrêtez puis relancez, les positions ouvertes et les comptes virtuels reprennent.

Le terminal MT5 doit rester ouvert et connecté (un compte démo suffit). Au démarrage, la plateforme attend la
prochaine clôture de bougie avant de prendre un trade.

> Le module `live` (envoi de vrais ordres) existe toujours dans le code pour plus tard. Il n'est **pas** dans le
> menu et ne fait rien sans l'option `--execute`.

## Hypothèses du backtest

- Entrée à l'ouverture de la bougie qui suit le signal (aucun regard dans le futur, vérifié par les tests).
- Si SL et TP sont touchés dans la même bougie, le **SL** est retenu (pire cas).
- Gaps au-delà du stop : sortie au prix d'ouverture.
- Une seule position à la fois ; les résultats sont exprimés en **R** (multiples du risque).

## Limites

- « Toutes les stratégies du monde » n'est pas testable : le catalogue en contient 99 (et des centaines de
  réglages), plus 11 filtres, les combinaisons de 2, 9 niveaux de R:R, 10 types de stop et 3 gestions de
  position. Cela fait des millions de possibilités, que les agents échantillonnent intelligemment.
  Montez `--budget` et `--rounds` pour explorer plus.
  Pour ajouter une stratégie, il suffit d'écrire une fonction avec `@strategy(...)` dans `mt5lab/strategies.py`, `strategies_plus.py` ou `strategies_smc.py`.
- Un backtest reste une estimation, même après validation. **Testez toute stratégie plusieurs semaines en démo avant d'engager de l'argent réel.**

## Tests

```bash
python -m pytest -q
```
