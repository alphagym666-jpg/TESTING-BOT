# Labo de stratégies MT5 — 10 agents + 2 chefs d'équipe

Plateforme Python reliée à MetaTrader 5. Elle teste automatiquement des milliers de combinaisons
de **stratégies × paramètres × filtres × stop loss × R:R × gestion de position**. Le travail est
réparti entre **10 agents chercheurs** supervisés par **2 chefs d'équipe**.

## L'équipe

| Chef d'équipe | Agent | Ce qu'il fait |
|---|---|---|
| **Chef A — Exploration** | 1. Chasseur de tendance | MA cross, EMA, Hull, MACD, Supertrend, SAR, ADX, Ichimoku… |
| | 2. Contrarien | RSI, RSI(2) Connors, Bollinger, Keltner, Stochastique, Williams %R, CCI, Z-score |
| | 3. Spécialiste cassures | Donchian/Turtle, Bollinger/Keltner breakout, Squeeze, inside bar, range, session (Asie→Londres) |
| | 4. Momentum | ROC, RSI 50, histogramme MACD, VWAP |
| | 5. Price action | Avalement, pin bar, retournement 3 bougies, fractales |
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
2. **Contre-expertise croisée** : chaque chef vérifie les trouvailles de l'autre avec des coûts
   (spread + commission) doublés et exige un résultat positif sur chaque moitié de la période de validation.

> Test de contrôle : lancée sur des marchés **100 % aléatoires** (où aucune stratégie ne peut gagner),
> la plateforme n'approuve **aucune** stratégie. Sans ces garde-fous, elle en approuvait plusieurs par pur hasard.

## Installation

```bash
pip install -r requirements.txt
```

Le package `MetaTrader5` ne fonctionne que sous **Windows**, avec le terminal MT5 installé,
ouvert et connecté à votre courtier. Identifiants (optionnels si le terminal est déjà connecté) :

```bat
set MT5_LOGIN=12345678
set MT5_PASSWORD=motdepasse
set MT5_SERVER=NomDuServeur-Demo
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

## Exécution sur MT5

```bash
# Simulation : affiche les ordres qu'il passerait, n'envoie rien
python run.py live --symbol EURUSD --timeframe H1 --strategies results/EURUSD_H1/meilleures_strategies.json

# Vrais ordres sur compte DÉMO, 0.5 % de risque par trade
python run.py live --symbol EURUSD --timeframe H1 --strategies results/EURUSD_H1/meilleures_strategies.json --execute --risk 0.5
```

Les comptes réels sont refusés sauf avec `--allow-real`. La taille de lot est calculée pour que le
stop loss corresponde exactement au % de risque choisi. Le SL et le TP sont placés dans l'ordre ;
le break-even et le trailing sont gérés à chaque clôture de bougie.

## Hypothèses du backtest

- Entrée à l'ouverture de la bougie qui suit le signal (aucun regard dans le futur, vérifié par les tests).
- Si SL et TP sont touchés dans la même bougie, le **SL** est retenu (pire cas).
- Gaps au-delà du stop : sortie au prix d'ouverture.
- Une seule position à la fois ; les résultats sont exprimés en **R** (multiples du risque).

## Limites

- « Toutes les stratégies du monde » n'est pas testable : le catalogue en contient 34, plus 8 filtres,
  les combinaisons de 2, 9 niveaux de R:R, 10 types de stop et 3 gestions de position. Cela fait des
  centaines de milliers de possibilités, que les agents échantillonnent intelligemment.
  Pour ajouter une stratégie, il suffit d'écrire une fonction avec `@strategy(...)` dans `mt5lab/strategies.py`.
- Un backtest reste une estimation, même après validation. **Testez toute stratégie plusieurs semaines en démo avant d'engager de l'argent réel.**

## Tests

```bash
python -m pytest -q
```
