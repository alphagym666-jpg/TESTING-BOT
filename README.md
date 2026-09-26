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
| **Chef C — Algorithmes des banques** | 11. Chasseur de liquidité | Chasses aux stops : balayage des plus hauts/bas puis retour, range de la veille |
| | 12. Horloge institutionnelle | Ouvertures de sessions, fixing, heures, minutes, jours de la semaine |
| | 13. Niveaux ronds et ordres | Niveaux psychologiques, plus hauts/bas de la veille |
| | 14. Exécution VWAP/TWAP | Écart au VWAP du jour, pics de volume |
| | 15. Flux calendaires | Fin de mois, jour de la semaine, range asiatique |
| **Chef D — Inventions institutionnelles** | 16 à 20. Inventeurs | Liquidité, sessions, niveaux, VWAP, synthèse : inventent des stratégies à partir des failles de l'équipe C |

Au-dessus des 4 chefs, **le Directeur** mène la campagne (voir plus bas).

À chaque round, le Chef A fait cartographier toutes les familles puis transmet ses champions au
Chef B, qui les filtre, les combine et optimise leur R:R. Ensuite :
- les agents 1 à 10 **inventent** leurs propres stratégies ;
- l'**équipe C** cherche les failles des algorithmes des banques : leurs empreintes statistiques dans les prix,
  mesurées sur une 1re partie de l'historique et confirmées par le Chef C sur une autre. Chaque faille confirmée
  devient une stratégie jouable (« FAILLE A12-1 »…) ;
- l'**équipe D** invente des stratégies complètes à partir de ces failles, que le Chef D confirme ;
- l'**optimiseur du catalogue** travaille chacune des 99 stratégies du catalogue (réglages, R:R, stop, gestion,
  filtre, sens) pour en sortir la meilleure version.

Personne n'a accès aux vrais algorithmes des banques : l'équipe C cherche uniquement ce qui est mesurable dans
l'historique de prix, et chaque faille passe la même validation que le reste.

À la fin :

1. **Validation hors-échantillon** : chaque chef reteste ses candidats sur les derniers 35 % de
   l'historique, que personne n'a vus pendant la recherche. Rejet si trop peu de trades, espérance
   négative, profit factor < 1.1, edge non significatif statistiquement, ou trop de dégradation.
   Le seuil de significativité est **corrigé pour les tests multiples** (Šidák) : plus on présente de
   candidats, plus la barre monte, pour qu'une stratégie « chanceuse » ne passe pas.
2. **Contre-expertise croisée** : chaque chef vérifie les trouvailles d'un autre avec des coûts
   (spread + commission) doublés et exige un résultat positif sur chaque moitié de la période de validation.
3. Les meilleures versions des 99 stratégies du catalogue sont validées de la même façon, dans leur propre
   famille de tests (avec sa propre correction statistique).

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
  D. Lancer le DIRECTEUR : strategie combinee pour passer FTMO le plus vite possible
  R. Ouvrir le CLASSEMENT GENERAL (meilleures strategies, catalogue, failles, combinee)
  F. Ouvrir les FICHES detaillees des strategies (pour le paper trading et le bot)
  C. PAPER TRADING de la strategie combinee (un seul compte, 24h/24)
  A. Lancer l'exploration automatiquement au demarrage de Windows
  B. Ne plus lancer au demarrage de Windows
  0. Quitter le menu (le paper trading continue dans sa fenetre)
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

## Historique testé

Les agents testent sur une **durée** d'historique MT5, et plus sur un nombre fixe de bougies :

| Timeframes | Historique testé |
|---|---|
| M1, M5 | 2 ans |
| M15, M30, H1, H4, D1 | 5 ans |

`--annees N` impose une autre durée pour tous les timeframes. La période réellement couverte est affichée pour
chaque marché et timeframe, dans la console et dans `comparaison.html` (« testé sur X ans »). Si le serveur de votre
courtier fournit moins d'historique, un avertissement le dit. Dans MT5, réglez Outils > Options > Graphiques >
« Barres max. dans l'historique » et « dans le graphique » sur **Unlimited**, puis ouvrez un graphique du timeframe
concerné et faites défiler vers le passé (touche Début) pour télécharger plus d'historique.

Le Directeur fait **refaire automatiquement** les recherches qui ne couvraient pas assez d'années.

Plus d'historique rend les tests plus fiables, mais aussi plus longs : 2 ans en M1 représentent environ 750 000
bougies. Comptez plusieurs heures pour 3 marchés × 7 timeframes.

## Le Directeur : la stratégie combinée pour passer FTMO le plus vite

Au-dessus des 2 chefs et des 10 agents, **le Directeur** (menu, option **D**, ou `python run.py directeur`) mène
toute la campagne :
1. **Il confie chaque marché × timeframe aux chefs**, ou reprend le travail déjà fait.
2. **Revue** : il note chaque case (stratégies validées, réussite FTMO), ce qui marche le mieux, et quels agents
   inventeurs livrent de la qualité.
3. **Directives** : là où rien n'est validé, il relance une recherche **intensive** (budget ×2, +2 rounds,
   générations d'inventions ×2) et apporte ses idées : les stratégies qui marchent ailleurs, transmises au Chef B,
   et les indicateurs des inventions validées, imposés aux inventeurs. La barre de validation ne baisse jamais.
4. **Stratégie combinée** : il assemble les meilleures stratégies validées de tous les marchés et timeframes, y
   compris leurs **variantes de R:R** qui restent gagnantes hors-échantillon. Il règle ensuite le **risque de chaque
   composant** (0,25 à 1 % par trade), le nombre max de positions et un arrêt journalier.
5. **Scénarios de perte max par jour** : il refait tout pour 0,5 / 0,75 / 1 / 1,25 / 1,5 / 1,75 / 2 / 2,25 /
   2,5 % par jour. Un trade n'est pris que si *perte déjà réalisée aujourd'hui + risque des positions ouvertes +
   risque du nouveau trade* (frais compris) reste sous ce plafond. La **perte totale de 10 %** est protégée de la
   même façon : un nouveau trade n'est jamais pris s'il pouvait la faire dépasser. Il garde le scénario qui a
   moins de 2 % d'échecs puis **passe le challenge le plus souvent, puis le plus vite**.
6. **Test sur tous les timeframes** : chaque composant est rejoué, sans réoptimisation, sur les autres timeframes
   de son marché.

Résultats :
- `results/directeur.html` : **le classement général** (option R du menu) avec la stratégie combinée, les
  scénarios, le classement des meilleures stratégies validées (tous marchés, timeframes et équipes), la meilleure
  version de chacune des stratégies du catalogue, les failles des banques, le test multi-timeframes, la revue,
  les directives et le journal ;
- `results/fiches_strategies.html` (option F) : **une fiche détaillée par stratégie**, pour la trader, la suivre
  en paper trading ou la coder en bot MetaTrader : règles d'entrée en français, filtre, sens, stop, objectif,
  gestion, durée max, taille de position, règles du compte, statistiques, pseudo-code et code Python exact de la
  logique ;
- `results/fiches/*.json` : la même fiche en JSON, lisible par le bot Python de la plateforme
  (`python run.py live --symbol XAUUSD --timeframe H4 --strategies results/fiches/ID.json`, simulation par défaut) ;
- `results/strategie_combinee.json`.

L'option **C** fait tourner la stratégie combinée en paper trading 24h/24 sur **un seul compte fictif**, avec les
mêmes règles de risque. Elle est visible dans l'onglet « Stratégie combinée » de la plateforme
(http://localhost:8768) : équité, progression vers l'objectif, perte du jour par rapport au plafond, risque ouvert,
pire journée, drawdown et statut du challenge.

7. **Marchés corrélés** : NASDAQ, US30 et GER40 bougent ensemble ; EURUSD, GBPUSD et USDJPY (inversé) aussi,
   à travers le dollar. Le Directeur teste une limite de **positions ouvertes dans le même sens sur des marchés
   corrélés** (aucune, 1 ou 2) : acheter NASDAQ + US30 + GER40 en même temps, c'est trois fois le même pari.
8. **Challenges enchaînés sur l'historique réel** : en plus du Monte Carlo, le Directeur rejoue la stratégie
   combinée jour après jour sur tout l'historique commun de ses composants, comme si vous achetiez un challenge,
   puis un autre dès qu'il est réussi ou raté : « **X réussis / Y ratés** ». Le même chiffre est donné pour chaque
   scénario sur la période hors-échantillon (le plus fiable : la partie d'avant a servi à choisir les stratégies).
9. **Contrôleur de qualité** : il lit `controle_qualite.json` du paper trading et écarte de la stratégie combinée
   les stratégies mises en pause parce qu'elles font nettement moins bien en direct que prévu.

Réglages en haut de `lancer.bat` : `DIR_RISQUE_MAX` (risque max par trade, 1 %), `DIR_PERTE_JOUR` (perte max
par jour, 2,5 %) et `DIR_PERTE_TOTALE` (perte totale max, 10 %).

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
- **Challenges réussis / ratés** : chaque stratégie validée est aussi rejouée sur **tout** l'historique, jour après
  jour, en enchaînant les challenges (un nouveau dès que le précédent est réussi ou raté). Le classement, la
  comparaison et les fiches affichent « X réussis / Y ratés » sur tout l'historique et sur la seule période
  hors-échantillon.
- Le **Chef FTMO** combine ensuite les stratégies validées de tous les marchés et timeframes pour trouver le
  **portefeuille** qui passe le challenge le plus souvent et le plus vite (`results/portefeuille_ftmo.csv`).
- `comparaison.html` affiche le portefeuille, le classement FTMO et le classement par gain mensuel.

## Tous les marchés × tous les timeframes

```bash
# 7 marchés sur M1, M5, M15, M30, H1, H4 et D1, avec les commissions par symbole
python run.py lab --symbols NASDAQ XAUUSD EURUSD GER40 US30 GBPUSD USDJPY --timeframes ALL \
    --commission EURUSD=5 GBPUSD=5 USDJPY=5 XAUUSD=5 NASDAQ=0 GER40=0 US30=0
```

- `NASDAQ`, `GOLD`, `GER40` (ou `DAX`) et `US30` (ou `DOW`) sont reconnus automatiquement sous le nom de votre
  courtier (FTMO : `US100.cash`, `XAUUSD`, `GER40.cash`, `US30.cash`…).
- Chaque couple marché × timeframe passe par toutes les équipes. Avec 7 marchés × 7 timeframes (49 cases), la
  recherche complète prend plusieurs heures : lancez-la le soir, ou réduisez `SYMS` dans `lancer.bat`.
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
utilisés. Après une recherche, ce sont les meilleurs réglages trouvés, plus les inventions des agents, les failles
des banques, la meilleure version exacte de chaque stratégie du catalogue et les stratégies validées (étiquetées
« portefeuille FTMO n°… » quand elles en font partie).
3 marchés × 7 timeframes × 99 stratégies × 9 R:R, cela fait environ 19 000 comptes fictifs suivis en même temps.

### Faire tourner le paper trading en continu

Les options 6, 7 et 8 du menu ouvrent le paper trading **dans sa propre fenêtre** (`paper_24h.bat`). Le menu reste
libre pour lancer une recherche en même temps.
- **Réduisez la fenêtre sans la fermer.** Fermer le navigateur ne l'arrête pas : la plateforme se rouvre avec l'option 9.
- Si le paper trading s'arrête (MT5 fermé, coupure internet, erreur), il **redémarre tout seul** au bout de 30 s et
  reprend ses positions et ses comptes fictifs là où il en était.
- Le PC ne se met pas en veille tant qu'il tourne. La session Windows doit rester ouverte et le PC allumé.
- **Option A** : l'exploration se lance automatiquement à chaque démarrage de Windows, fenêtre réduite. MetaTrader 5
  est ouvert automatiquement s'il est installé. **Option B** désactive ce lancement automatique.
- Un deuxième lancement du même paper trading est refusé, pour éviter que deux copies écrivent dans les mêmes fichiers.

Pour que ça tourne vraiment 24h/24 sans laisser votre PC allumé, installez le dossier sur un **VPS Windows**
(ordinateur Windows loué en ligne, environ 10 à 20 $ par mois) avec MT5, puis activez l'option A.

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

## Filtres et validations supplémentaires

- **Tendance du timeframe supérieur** : les agents peuvent exiger que le trade aille dans le sens de la tendance
  H1, H4 ou D1 (EMA 50/200, structure SMC, Supertrend), calculée uniquement sur les bougies supérieures déjà
  terminées. Les inventeurs peuvent aussi l'utiliser dans leurs règles.
- **Walk-forward** : l'historique est coupé en 5 périodes ; une stratégie doit gagner sur au moins 4 d'entre
  elles, sinon elle est rejetée (« instable dans le temps »). Le détail par période est dans chaque fiche.
- **Coûts réels** : le spread de **chaque bougie** (historique MT5) + la commission, et les **swaps** pour chaque
  nuit passée en position (réglages du symbole chez votre courtier), en recherche comme en paper trading.
- **Filtre des nouvelles** : aucune entrée 30 minutes avant / après une annonce à fort impact (NFP, CPI, FOMC,
  BCE…) sur une devise du marché (jusqu'au H1). Le calendrier vient de MT5 : copiez `mql5/ExportNews.mq5` dans
  `MQL5\Scripts`, compilez-le (F7) et glissez-le sur un graphique (option **N** du menu). Refaites-le une fois
  par mois. Sans ce fichier, le filtre est simplement désactivé (message au démarrage). `--sans-nouvelles` le
  coupe, `--fenetre-nouvelles 15` change la fenêtre.
- **Contrôleur de qualité (paper trading)** : après 20 trades, une stratégie dont le R moyen en direct est négatif
  et nettement sous celui attendu (écart statistique z < -2) est **mise en pause** : dans la stratégie combinée,
  elle continue à être suivie « à blanc » mais ne touche plus le compte. Elle est réactivée si ses 20 derniers
  trades redeviennent bons. Colonne « Contrôle » dans la plateforme ; le Directeur en tient compte.

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
