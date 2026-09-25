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
  2. Changer marches / timeframes
  3. Recherche complete : tous les marches x tous les timeframes
  4. Recherche FTMO INTENSIVE (beaucoup plus de tests, plusieurs heures)
  5. Ouvrir la COMPARAISON (quelle strategie rapporte le plus / passe FTMO)
  6. EXPLORATION : toutes les strategies x tous les R:R
  7. Les 30 meilleures strategies de la recherche
  8. Le portefeuille du Chef FTMO / strategies validees
  9. Ouvrir la PLATEFORME (voir les trades en direct)
  0. Quitter
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

## Les agents inventent leurs propres stratégies

Après les rounds de recherche, chaque recherche se termine par un **round d'invention** :
- chacun des 10 agents crée des stratégies nouvelles : une **règle** faite d'un déclencheur et de filtres, par exemple
  « QUAND RSI(2)-50 > 14,6 ET ADX(14) < 24 », avec le miroir exact en vente. Les seuils sont tirés du comportement
  réel du marché (quantiles des indicateurs), puis la population de règles évolue sur plusieurs générations
  (mutation et croisement). Chaque agent reste dans sa spécialité : tendance, retour à la moyenne, cassures,
  momentum, price action/SMC… Le Généticien fait évoluer les inventions confirmées des autres ;
- l'agent invente sur une 1re partie de l'historique, et **son chef d'équipe confirme** sur une 2e partie que
  l'agent n'a pas vue. En cas de refus, l'agent recommence (jusqu'à 3 essais) ;
- chaque invention passe ensuite **la même validation finale** que les 99 stratégies du catalogue, sur la période
  hors-échantillon que personne n'a vue. Les inventions validées se retrouvent dans le classement, face aux autres.

La confirmation du chef n'est qu'un premier filtre : sur un marché 100 % aléatoire, les chefs confirment parfois
des inventions « chanceuses », mais la validation finale les rejette toutes.

## Objectif FTMO

Toute la recherche est notée selon votre challenge. Par défaut (phase 1) : **+10 %**, perte max **3 % par jour** et
**10 % au total**, 4 jours de trading minimum. Tout est réglable : `--ftmo-target`, `--ftmo-daily`, `--ftmo-total`,
`--ftmo-min-days`, et `--ftmo-phase2 5` pour simuler aussi la phase 2.
- Pour chaque stratégie, un **simulateur Monte Carlo** rejoue des milliers de challenges à partir de ses journées
  réelles hors-échantillon. Les positions ouvertes sont comptées à leur stop dans la perte du jour. Il en sort la
  **probabilité de réussite**, le **nombre de jours** pour atteindre +10 % et le **taux d'échec**.
- Le **Chef FTMO** combine ensuite les stratégies validées de tous les marchés et timeframes pour trouver le
  **portefeuille** qui passe le challenge le plus souvent et le plus vite (`results/portefeuille_ftmo.csv`).
- `comparaison.html` affiche le portefeuille, le classement FTMO et le classement par gain mensuel.

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
les vrais prix de votre MT5 en direct. Quatre modes :

```bash
# EXPLORATION : toutes les stratégies (+ inventions) × tous les R:R, sur tous les marchés et timeframes
python run.py paper --symbols NASDAQ XAUUSD EURUSD --timeframes ALL --source exploration
# les 30 meilleures de la recherche / le portefeuille du Chef FTMO / les validées
python run.py paper --symbols NASDAQ XAUUSD EURUSD --source meilleures --top 30
python run.py paper --source portefeuille
python run.py paper --symbols EURUSD --timeframes H1 M15 --source approuvees --commission 7
```

L'exploration fonctionne même sans recherche préalable : les réglages par défaut de chaque stratégie sont alors
utilisés. Après une recherche, ce sont les meilleurs réglages trouvés, plus les inventions des agents.
3 marchés × 7 timeframes × 99 stratégies × 9 R:R, cela fait environ 19 000 comptes fictifs suivis en même temps.

### La plateforme en direct

Pendant le paper trading, la page **http://localhost:8765** s'ouvre dans votre navigateur et se met à jour
toutes les 3 secondes. Elle n'est accessible que depuis votre PC. Onglets :
- **Positions ouvertes** : marché, sens, lots, heure d'ouverture, prix d'entrée, SL initial, SL actuel, TP,
  prix actuel, distance en pips jusqu'au SL et au TP, gain ou perte latent en $ et en R ;
- **Historique des trades** : ouverture, fermeture, durée, entrée, SL, TP, sortie, raison (TP, stop loss,
  break-even, stop suiveur, signal opposé), pips, R, P&L, solde, spread à l'entrée ;
- **Classement des stratégies** : un compte fictif par stratégie × marché × timeframe × R:R, avec progression
  vers l'objectif FTMO ;
- **Meilleur R:R** : R total par niveau de R:R, et le meilleur R:R de chaque stratégie ;
- **Challenges FTMO** : chaque compte fictif est suivi comme un vrai challenge (réussi / échoué / en cours) ;
- **Journal en direct** : chaque ouverture, fermeture et événement FTMO.

Filtres par marché, timeframe et texte, tri en cliquant sur les colonnes, et téléchargement de tous les trades
pour Excel.

Ce qui rend les chiffres réalistes :
- **entrée au vrai prix** : ask pour un achat, bid pour une vente, donc avec le spread réel du moment ;
- **SL et TP vérifiés tick par tick** avec l'historique des ticks MT5. Si le prix saute par-dessus le stop,
  la sortie se fait au prix réel du tick, glissement compris. Le TP est pris à son niveau ;
- **taille de lot, valeur du pip, lot minimum et pas de lot** : ceux de votre courtier ;
- break-even, trailing stop et sortie sur signal gérés comme dans le backtest ;
- **chaque stratégie a son compte virtuel** (100 000 par défaut, `--capital`), suivi comme un challenge FTMO ;
- **perte max par trade** : 0,5 % par défaut (`--risk`), soit **500 sur 100 000**. Le lot est arrondi vers le bas
  pour que la perte au stop, commission comprise, ne dépasse jamais ce montant. Le plafond est calculé sur le
  capital de départ (ou sur le solde s'il a baissé), donc il ne grossit pas avec les gains. Si même le lot minimum
  du courtier dépasse ce risque, le trade est ignoré. Seul un gap par-dessus le stop peut faire perdre un peu plus,
  exactement comme en réel.

Résultats dans `results/paper/` :
- `tableau_de_bord.html` : version de secours de la plateforme, sans serveur (se rafraîchit toutes les 30 s) ;
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
