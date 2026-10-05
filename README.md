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
   moins de 2 % d'échecs puis donne un **challenge réussi le plus vite** : le moins de jours attendus, reprises
   comprises (jours médians pour réussir / probabilité de réussite ; 90 % en 8 jours = ~8,9 jours).
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

10. **Horaires : 24h/24 ou seulement le jour ?** Tout est refait pour 3 horaires d'entrée, en heure du Québec :
    **24h/24**, **8h-17h** et **8h-13h** (le serveur FTMO a 7 h d'avance : `--decalage-horaire` pour changer).
    Le rapport compare les trois (réussite, jours, échecs, challenges réussis/ratés) et garde le meilleur. Chaque
    horaire a sa stratégie combinée (`strategie_combinee_24h24.json`, `_8h-17h.json`, `_8h-13h.json`) : réglez
    `HORAIRE` en haut de `lancer.bat` pour la suivre en paper trading et dans le bot. Les positions ouvertes gardent
    leur SL et TP chez le courtier après la fin de l'horaire.

Réglages en haut de `lancer.bat` : `DIR_RISQUE_MAX` (risque max par trade, 1 %), `DIR_PERTE_JOUR` (perte max
par jour, 2,5 %) et `DIR_PERTE_TOTALE` (perte totale max, 10 %).

## Le bot MT5 (LaboBot)

Option **E** du menu (`python run.py bot`) : prépare `results/bot/LaboBot.mq5`, déjà réglé pour votre stratégie
combinée, et `results/bot/LISEZMOI_BOT.txt` (installation pas à pas).

- **Le cerveau reste en Python** : le paper trading de la stratégie combinée (option C) décide des trades avec
  exactement le code qui a été testé, et écrit chaque décision (ouvrir, déplacer le stop, break-even, fermer) dans
  le dossier commun de MT5 (`labo_signaux.csv`). Le bot, posé sur UN graphique, les exécute chaque seconde.
  Le PC (ou un VPS Windows) doit donc rester allumé avec MT5 et l'option C.
- Le lot est calculé pour perdre au maximum le risque prévu au stop ; SL et TP sont posés chez le courtier dès
  l'ouverture (protégés même si le PC s'éteint).
- **Garde-fous du bot**, indépendants de Python : compte réel refusé sauf `InpAutoriserReel = true`, risque max par
  trade, tout fermé au-delà de 2,8 % de perte du jour (reprise le lendemain) et arrêt définitif à 9,5 % de perte
  totale, plus de nouveau trade une fois l'objectif atteint, signal de plus de 90 s ignoré.
- Testez-le plusieurs semaines sur un compte **démo** avant un challenge.

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
**Règle du meilleur jour** (`--ftmo-meilleur-jour 50`, 0 = aucune) : aucune journée ne peut faire plus de 50 % du
profit total. Une journée à +6 % oblige donc à atteindre plus de +12 %. La règle est appliquée partout : simulateur,
challenges enchaînés, paper trading (colonne « objectif 12 % ») et bot.
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
- **perte max par trade** : 1 % par défaut (`--risk`), soit **1 000 sur 100 000**. Le lot est arrondi vers le bas
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

## Les nouveaux employés

- **Analyse du direct** (menu **L**, onglet « Meilleurs setups du direct » de la plateforme, et dans le rapport du
  Directeur) : les trades réellement pris par le paper trading sont analysés — classement des stratégies qui
  tournent et meilleure combinaison (1 % max par trade, 2,5 % par jour, 10 % au total), avec un bouton pour en
  faire un bot. Tant qu'il y a moins de 20 jours de données, c'est clairement indiqué.
- **Auditeur anti-hasard** : chaque stratégie validée est attaquée : part du profit venant des 3 meilleurs trades,
  risque que le résultat soit du hasard (rééchantillonnage), entrée retardée d'une bougie. Rejet si c'est grave.
- **Météorologue** : type de marché du moment (tendance ou range, calme ou nerveux), sans regarder le futur. Les
  agents peuvent l'utiliser comme filtre (meteo_*), chaque fiche dit dans quelle météo la stratégie gagne, et la
  météo de chaque marché est affichée dans la plateforme.
- **Surveillant** (menu **T**) : alertes sur le téléphone par Telegram (trades de la combinée et des bots,
  challenge réussi/raté, MT5 déconnecté, bot silencieux, ordre non exécuté, glissement), rapport du soir dans
  `rapports/`. Le bot MT5 écrit un journal d'exécution que le surveillant compare au paper trading.
- **Ingénieur de vitesse** : backtests compilés avec numba (≈ 40 fois plus rapides, résultats identiques, installé
  automatiquement ; sans numba tout marche quand même), calculs SMC vectorisés et mis en cache : une recherche
  complète va environ 2,5 fois plus vite.

## Un bot MT5 pour N'IMPORTE QUELLE stratégie

- Dans la plateforme de paper trading, onglet « Classement des stratégies » : bouton **Bot MT5** sur chaque ligne ;
  onglet « Stratégie combinée » : bouton pour la combinée.
- Ou menu **E**, puis l'identifiant écrit dans la fiche de la stratégie (option F), ou
  `python run.py bot --fiche ID`.
- Résultat : un dossier `results/bots/<marché>_<tf>_<numéro>/` avec `LaboBot.mq5` (déjà réglé, avec son propre
  fichier de signaux et son propre numéro magique : plusieurs bots peuvent tourner sur le même compte),
  `LANCER_BOT.bat` (le paper trading de CETTE stratégie, qui envoie ses signaux au bot) et `LISEZMOI_BOT.txt`.


### Bouton « Bot MT5 » dans tous les rapports

Chaque stratégie affichée dans `comparaison.html`, `directeur.html`, `fiches_strategies.html`, `direct.html`,
`compte_perso.html` et `compte_finance.html` a son bouton **Bot MT5**, ainsi que chaque stratégie combinée : la combinée
du Directeur, chaque horaire, le portefeuille du Chef FTMO, la combinaison du direct et les 2 comptes.

Un rapport est un simple fichier : pour créer le bot, **une plateforme doit être ouverte** (option 6, 9 ou C).
Le bouton lui demande de créer le dossier `results/bots/<nom>/`, puis d'installer et compiler le bot dans MT5.
La demande est protégée par un jeton secret (fichier `.bot_token` du projet) : un site web ne peut pas le faire à
votre place.

Pour ajouter les boutons aux pages déjà faites : option **R** (directeur.html), **F** (fiches) ou **5** (comparaison).
Elles sont refaites en quelques secondes, sans relancer la recherche.

## Les 2 génies : Einstein et Hawking

Deux génies travaillent **seuls**, sans chef ni Directeur. Ils ne combinent pas des indicateurs : ils **inventent
des formules mathématiques** par programmation génétique (des milliers de formules naissent, sont testées, se
croisent et mutent ; une loi simple est préférée à une loi compliquée).
- **Einstein (physique)** : vitesse et accélération du prix, énergie cinétique (masse = volume), impulsion, force de
  rappel d'un ressort (loi de Hooke), frottement, **relativité** (ce marché vu depuis un autre marché).
- **Hawking (maths et cosmologie)** : exposant de Hurst (mémoire du marché), entropie de Shannon (désordre),
  asymétrie et queues épaisses (événements extrêmes), tendance filtrée de Holt, cycles, gravité autour du prix
  moyen pondéré par le volume.
- Ils peuvent aussi prendre comme ingrédients les **5 meilleures stratégies des agents** et les prix des autres
  marchés, par exemple « z[corps + stratégie⟨Agent 8⟩] ».
- Une loi devient une stratégie : ACHAT quand la formule normalisée franchit +k (VENTE au miroir sous -k).
- Chaque génie se contrôle lui-même sur une période qu'il n'a pas utilisée, puis présente ses 3 meilleures lois.
  La plateforme les passe au même test hors-échantillon que tout le monde (sans ça, impossible de distinguer une
  vraie loi d'un hasard) ; toutes leurs lois sont affichées dans « Les découvertes des génies » du rapport du
  Directeur, avec une fiche qui donne la formule et la signification de chaque symbole.
- `--sans-genies` pour les désactiver.

## Inventions inter-marchés et pilote de risque du challenge

**Inter-marchés** : chaque marché reçoit aussi les prix des autres marchés choisis (sans jamais regarder le futur).
Les inventeurs (surtout les agents 7, 9 et 20) cherchent des règles où un marché en annonce un autre, par exemple
« QUAND le NASDAQ a monté de 2 écarts-types en 5 bougies ET le GER40 est en retard -> acheter le GER40 », ou
« QUAND USDJPY chute fort -> vendre le NASDAQ ». Le paper trading va chercher tout seul les prix des marchés utilisés.
`--sans-inter-marches` désactive.

**Pilote de risque du challenge** : le Directeur essaie des règles qui changent le risque selon où en est le
challenge, sans jamais dépasser ton risque max : risque réduit quand le compte est à -1,5 / -3 / -5 %, le lendemain
d'une journée perdante, ou quand il reste moins de 2-4 % pour l'objectif. Moins d'échecs, donc il peut monter le
risque des composants, donc challenge réussi plus vite. Il ne garde le pilote que s'il aide. Le paper trading de
la stratégie combinée et le bot l'appliquent.

## Stratégies « À L'ESSAI »

Quand la validation est trop dure pour un marché (souvent en M1-M15 après les frais), rien n'est « APPROUVÉ ».
Les 10 meilleures stratégies rejetées de chaque case qui **gagnent quand même hors-échantillon** (au moins 20
trades, R moyen > 0, profit factor >= 1,05) passent « **À L'ESSAI (paper seulement)** », avec la raison du rejet.
- Option **S** du menu : paper trading des stratégies validées + à l'essai (plateforme http://localhost:8769).
- Si aucune stratégie n'est validée, le Directeur construit une **stratégie combinée à l'essai** (option C) pour le
  paper trading ; le bot (option E) la refuse tant qu'elle n'a pas fait ses preuves (`--forcer` pour passer outre).
- Les recherches déjà faites sont reprises telles quelles : rien à recalculer.

## Période récente (2025-2026) et buy & hold

Par défaut, les agents cherchent sur les années passées et le chef vérifie sur la période la plus récente, que
personne n'a vue (avec 5 ans d'historique : recherche 2021-2024, vérification 2025-2026). Ça évite de garder des
stratégies qui ont seulement « appris par cœur » le passé.

Option **P** du menu (ou `--depuis 2025-01-01`) : recherche ET vérification **seulement sur la période récente**
(recherche sur le début de 2025, vérification sur les derniers mois). Les stratégies sont adaptées au marché
d'aujourd'hui, mais avec moins de données le hasard pèse plus : le paper trading en direct sert de vraie
vérification. Les résultats vont dans un dossier à part (`results_depuis_2025-01-01`) ; le paper trading, le
Directeur et le bot du menu utilisent ce dossier tant que la période est réglée.

**Buy & hold** : chaque stratégie est comparée à « acheter et garder » le marché sur la même période (gain et pire
baisse), à 1 % de risque par trade. Avec `--battre-buy-hold` (question de l'option P), une stratégie qui ne fait
pas mieux n'est pas validée. Le rapport du Directeur montre aussi le gain de la stratégie combinée à côté du buy &
hold de ses marchés.

## Les autres comptes : compte perso et compte financé

Mêmes stratégies validées que pour le challenge, mais un autre objectif (module `mt5lab/comptes.py`) :

| | Compte perso (option K puis M) | Compte financé (option K puis V) |
|---|---|---|
| Capital | 5 000 $ (`CAPITAL_PERSO` dans lancer.bat) | 100 000 $ (`CAPITAL_FINANCE`) |
| But | meilleur rendement à long terme | meilleur rendement par mois |
| Risque | suit le SOLDE (intérêts composés), 2 %/trade max (`RISQUE_PERSO`) | sur le capital de départ, 1 %/trade max |
| Limites | perte possible max 5 %/jour (`PERTE_JOUR_PERSO`, le bot ferme tout à -5 % et reprend demain), trading arrêté à -30 % | jamais -3 % dans une journée ni -10 % au total |
| Sécurité exigée | au plus 5 % de chances de baisser de 25 % en un an | au plus 5 % de chances de toucher une limite en un an |
| Plateforme | http://localhost:8856 | http://localhost:8857 |

Le gestionnaire du compte choisit les composants, le risque de chacun et les règles (arrêt journalier, positions
max, marchés corrélés, frein de bonne journée) en simulant 2 000 années possibles à partir des journées
hors-échantillon. Résultats : `compte_perso.html`, `compte_finance.html` et `strategie_combinee_perso.json` /
`strategie_combinee_finance.json`. Le bouton « Bot MT5 » de chaque plateforme crée un bot réglé pour ce compte
(pas d'objectif ; `InpComposer = true` pour le compte perso).

Ligne de commande : `python run.py directeur --comptes-seulement` (rapide, avec la recherche déjà faite), puis
`python run.py paper --profil perso` ou `--profil finance`.

Attention :
- Les rendements affichés viennent du passé. Le paper trading en direct doit les confirmer avant d'y mettre de l'argent.
- Avec 5 000 $, 2 % = 100 $ par trade : sur certains marchés, le lot minimum (0,01) dépasse ce risque, et le trade est
  alors sauté (visible dans le journal).
- Sur un compte financé FTMO, vous touchez seulement votre part du profit (souvent 80 %).

## Équipe E : le desk quantitatif, et le dimensionnement par volatilité

Ce que les fonds quantitatifs regardent vraiment. On ne copie pas les banques : elles gagnent surtout grâce aux
ordres de leurs clients, à la vitesse et au market making. On cherche les empreintes que ces gros acteurs laissent
dans les prix. Trois nouveaux analystes, avec la même méthode que l'équipe C (mesuré sur une période, revérifié sur
une autre, puis validation finale commune). Leurs failles servent aussi de pistes aux inventeurs de l'équipe D.

- **Agent 21, profil de volume (Market Profile)** : POC (le prix le plus échangé) et zone de valeur (70 % du volume)
  de la veille et des 5 derniers jours, que les desks défendent et vers lesquels le prix revient souvent.
- **Agent 22, arbitragiste statistique** : un marché qui s'écarte anormalement de son marché lié (NASDAQ/US30,
  EURUSD/GBPUSD, GER40/indices US…), au-delà de ce que leur lien habituel explique. On parie qu'il revient.
- **Agent 23, positionnement des fonds (rapport COT)** : positions des gros spéculateurs publiées gratuitement chaque
  semaine par la CFTC (or, euro, livre, yen, NASDAQ, Dow ; pas le DAX). Téléchargé automatiquement dans `data/cot/`
  (une fois par semaine), et utilisé seulement à partir du samedi qui suit sa publication, jamais en avance.
  `--sans-cot` pour s'en passer.
- **Dimensionnement par volatilité** (comme les fonds de tendance) : quand les résultats journaliers de la stratégie
  combinée deviennent nerveux, le risque de chaque nouveau trade baisse (jamais au-dessus du risque choisi). Le
  Directeur le teste et le garde seulement s'il fait réussir le challenge plus vite ou plus sûrement. Le paper trading
  et le bot l'appliquent.

## L'avocat du diable

À la fin de la touche 1 (TOUT FAIRE), il prend les 2 cases (marché × timeframe) où la recherche a trouvé le plus de
stratégies. Il refait EXACTEMENT la même recherche sur les mêmes bougies, remises dans un **ordre au hasard** : même
volatilité, mêmes mèches, mêmes spreads et mêmes heures, mais plus aucune vraie tendance ni aucun vrai motif. Tout
ce qui « marche » là gagne par pure chance.
- **Bon signe** : (presque) rien trouvé sur les prix au hasard.
- **Prudence** : un peu trouvé, mais beaucoup moins que sur les vrais prix.
- **Danger** : presque autant trouvé que sur les vrais prix. Ne payez pas de challenge sans confirmation en paper trading.

Le verdict est en haut de `top10.html` et de `resultats.html`. Les recherches au hasard sont dans
`results/avocat_du_diable/`.

## Gestion des trades : break-even, paliers, sortie intelligente

5 gestions possibles pour chaque stratégie : elles sont testées par les agents, et le Directeur les compare sur
chaque stratégie validée.
- **Aucune** : le stop et le TP ne bougent pas.
- **Break-even** : à +1R (clôture), le stop passe au prix d'entrée.
- **Stop suiveur** : le stop suit le prix à distance d'ATR.
- **Paliers** : +1R → break-even, +2R → stop à +1R, +3R → stop à +2R… jusqu'au TP (ou au signal de sortie).
- **Sortie intelligente** : les paliers, et en plus le trade est fermé AVANT un retournement. C'est le cas si la
  stratégie donne le signal inverse, ou si, après avoir atteint +1R, le prix rend 1 ATR depuis son meilleur cours.

La page des résultats montre la meilleure gestion de chaque stratégie (R moyen par trade pour chacune). Les
meilleures versions entrent dans les stratégies combinées. Le paper trading et le bot appliquent exactement la
même gestion : le stop est déplacé chez le courtier, la fermeture passe au marché.

## Le menu simple (lancer.bat)

| Touche | Ce qu'elle fait |
|---|---|
| **1. TOUT FAIRE** | La nuit ou la journée : recherche au maximum par tous les employés, stratégies combinées, mélanges, compte perso, compte financé, classement. La page des résultats s'ouvre à la fin. Arrêtable et relançable : le travail fait est gardé. |
| **2. TOP 10** | `top10.html` : page dédiée aux 10 meilleures stratégies combinées. Pour chacune : toutes ses infos (jours attendus, réussite, échecs, trades par mois et par jour, plus grosse baisse, pire journée, durée des trades, week-end, période analysée) et ses stratégies. Bouton « Créer le bot MT5 » pour la combinée, et « Bot seule » pour chacune de ses stratégies. |
| **R** | `resultats.html` : tous les résultats (comptes perso et financé, choix du compte, gestion des trades, avocat du diable). |
| **3. LANCER** | La stratégie combinée choisie (★) en paper trading, avec la plateforme et les signaux pour le bot. |
| **4 / 5** | Plateforme du compte perso / du compte financé. |
| 6 / 7 / 8 | Connexion MT5, marchés et timeframes, alertes téléphone. |
| **9** | Toutes les anciennes options (avancé). |

**La page des résultats (`resultats.html`)** contient :
- **La n°1** pour réussir le challenge le plus vite, avec ses chiffres et son bouton Bot MT5 :
  - jours attendus, réussite, échec, trades par mois ;
  - plus grosse baisse, pire journée, durée moyenne d'un trade, % gardés le week-end ;
  - challenges enchaînés et période analysée (dates).
- **Le TOP 10 de toutes les stratégies combinées** construites : chaque horaire, chaque perte max par jour, chaque
  mélange, le portefeuille du Chef FTMO et la combinaison du direct. Même chiffres pour chacune ; on clique pour voir
  les stratégies qui la composent (marché, timeframe, risque, trades/mois).
- **Le compte financé et le compte perso** : rendement par an et par mois, mauvaise année, baisse typique, bot.
- **Quel compte FTMO acheter** (1 étape / 2 étapes, Standard / Swing).
- **Sur quoi c'est basé** : la période jamais vue pendant la recherche, et les **frais inclus**. Ce sont le spread de
  chaque bougie, la commission du courtier, le **glissement** (mesuré en direct, sinon estimé à la moitié du spread
  médian) et les **swaps** (le week-end compte 3 nuits). Les stratégies doivent aussi tenir avec des coûts doublés.

## Règles FTMO 2026 prises en compte : perte suiveuse, week-end, choix du compte

À vérifier sur ftmo.com, les règles changent ; voici celles intégrées.

- **FTMO 1 étape** : +10 %, perte max 3 %/jour, meilleur jour ≤ 50 % du profit, part des profits 90 %. La **perte max
  de 10 % est SUIVEUSE** : le plancher = plus haut solde de fin de journée − 10 % du capital, et il ne dépasse
  jamais le capital de départ. Exemple : à 104 000 $ en fin de journée, le plancher monte à 94 000 $. Le simulateur,
  le paper trading et le bot (`InpPerteSuiveuse`) l'appliquent. Option `--ftmo-perte-suiveuse 0` pour la règle fixe.
- **FTMO 2 étapes** : +10 % puis +5 %, 5 %/jour, 10 % au total fixe, 4 jours minimum par phase, part 80 %.
- **Week-end** : pendant le challenge, garder une position la nuit ou le week-end est permis (Standard comme Swing).
  Une fois financé, le compte **Standard** l'interdit le week-end (et autour des nouvelles) ; le **Swing** le permet,
  mais avec un levier de 1:30 au lieu de 1:100.
- **Frais de swap** : chaque nuit en position coûte (ou rapporte) le swap du courtier ; le week-end compte comme 3 nuits.
  Ils sont maintenant bien comptés dans tous les tests (un bug les mettait à zéro avec la version récente de
  pandas). La méthode de calcul passe en version 4 : l'option D refait les recherches.
- Chaque stratégie affiche le **% de ses trades gardés pendant un week-end** et sa **durée moyenne**.
- **Conseiller du compte** (section « Quel compte FTMO choisir ? » de directeur.html, options D et W) : avec la
  stratégie combinée, il donne les jours attendus pour être financé en 1 étape et en 2 étapes. Il montre aussi
  l'effet d'une fermeture obligatoire le vendredi soir, et dit si un compte Swing vaut le coup.
- **Compte financé** (options K et V) : il est construit et tradé SANS position pendant le week-end. Le paper trading
  arrête les entrées le vendredi dès 21 h et ferme tout dès 22 h (heure du serveur MT5) ; le bot aussi
  (`InpFermerVendredi = 22`), même si la plateforme Python est arrêtée.

## Le Conseil et le Chef des combinaisons (nouveaux employés)

- **Le Conseil** (dans chaque recherche, et sur les cases déjà cherchées sans tout refaire) : il prend les
  meilleures stratégies DIFFÉRENTES d'une case (choisies sur la période de recherche seulement) et les fait
  **voter**. Le signal part seulement quand 2 (ou 3) d'entre elles donnent le même sens dans une fenêtre de 1, 3 ou
  5 bougies. Moins de trades, mais souvent plus fiables. Les votes passent la même validation hors-échantillon
  que tout le monde. Ils apparaissent comme « CONSEIL 2/3 … » dans les classements, avec leur fiche et leur bot.
- **Le Chef des combinaisons** (après les scénarios du Directeur) : il mélange la stratégie combinée avec chaque
  autre combinaison : autres horaires, portefeuille du Chef FTMO, combinaison du direct, puis toutes ensemble. Il
  retire ensuite ce qui ne sert à rien et règle le risque. Si un mélange fait réussir le challenge plus vite, sans
  plus d'échecs, il devient LA stratégie combinée (paper trading C et bot). Le tableau « Le Chef des combinaisons »
  de directeur.html montre chaque essai : oui / non, jours attendus, trades par mois.
- **Option X du menu (Directeur MAXIMUM)** : refait toutes les cases avec le maximum d'effort. Chaque agent fait
  `MAX_TESTS` tests par round (5000, contre 1000 avec l'option D), sur `MAX_ROUNDS` rounds (6). Les inventeurs ont 20
  générations, les génies 30, et le Conseil travaille sur chaque case. Comptez une nuit ou plus ; le PC ne se met pas
  en veille pendant le calcul. Plus de tests trouvent plus de candidats, mais la validation hors-échantillon reste
  la même : seules les stratégies qui tiennent sur des données jamais vues passent.
- **Trades par mois** : affichés pour chaque stratégie combinée (carte « TRADES PAR MOIS » et tableau des horaires),
  chaque composant, le portefeuille du Chef FTMO, les comptes perso / financé et, sur la plateforme, le rythme en
  direct comparé au rythme attendu.

## TOP 10 des combinaisons du direct (bouton de la plateforme)

Onglet **« TOP 10 combinées du direct »** de la plateforme de paper trading, bouton **« Compiler toutes les
stratégies du direct → TOP 10 des combinaisons »** :

- prend TOUTES les stratégies qui tournent sur la plateforme (pas celles en pause), garde les gagnantes avec au
  moins 5 trades en direct ;
- le Chef des combinaisons part de chacune des 12 meilleures (et d'un départ libre), ajoute à chaque étape la
  stratégie qui fait réussir le challenge le plus vite (jusqu'à 6), avec 2 dosages du risque (0,5 % ou 1 % par
  trade) ;
- chaque combinaison tourne sur UN seul compte : 1 % max par trade, perte possible max 2,5 % par jour, règles du
  challenge FTMO (même sur la plateforme d'un compte perso) ;
- classement comme le TOP 10 du Directeur : d'abord celles qui ratent au plus 2 % des challenges, puis le moins de
  jours pour réussir, puis la réussite ;
- pour chacune : réussite et échecs du challenge, jours pour réussir, gain en direct, pire jour, DD max,
  trades par mois, challenges réussis / ratés sur les vrais jours, et un bouton **« Créer le bot MT5 »**.

**Bouton « Backtest »** sur chaque combinaison : elle est rejouée sur TOUT l'historique MT5 (chaque stratégie sur
son marché et son timeframe, mêmes coûts que la recherche : spread, commission, glissement, swaps, nouvelles),
sur un seul compte avec les mêmes règles de risque. La page montre la courbe du compte, le gain par année et par
mois, la pire journée, le drawdown max, les trades par mois, les challenges FTMO réussis / ratés en les
enchaînant sur les vrais jours et la réussite simulée, pour TOUT l'historique et pour la PÉRIODE RÉCENTE seule
(les 35 % les plus récents). Les stratégies ont été trouvées sur une partie de cet historique : « tout
l'historique » est optimiste, la période récente et le paper trading sont les chiffres honnêtes. Résultats
enregistrés dans `backtests/` (ils réapparaissent après un redémarrage, lien « refaire » pour recalculer).

Le calcul tourne en arrière-plan (1 à 5 minutes, barre d'avancement) et est enregistré dans `top10_direct.json`
(le dernier TOP 10 réapparaît après un redémarrage). Avec moins de 20 jours de bourse en direct, le classement
bouge encore : la page le dit.

## TOP 10 backtest 2 ans (stratégies du direct)

Onglet **« TOP 10 backtest 2 ans »** de la plateforme, bouton **« Backtester toutes les stratégies du direct sur les
2 dernières années → TOP 10 »** :

- chaque stratégie qui a pris au moins un trade sur la plateforme (et chaque composant d'une stratégie combinée
  qui tourne) est rejouée sur les **2 dernières années** de MT5 (spread, commission, glissement, swaps,
  nouvelles) ; même signal avec plusieurs R:R = calculé une seule fois ; jusqu'à 6 000 stratégies (les plus
  actives d'abord) ;
- **TOP 10 des stratégies SEULES** et **TOP 10 des stratégies COMBINÉES** (Chef des combinaisons sur les trades du
  backtest, un seul compte, 1 % max par trade, 2,5 % de perte possible max par jour), classées pour passer le
  challenge. La stratégie combinée qui tourne en paper et les combinaisons du TOP 10 du direct sont classées avec
  les autres et toujours montrées (« hors TOP 10 » si elles n'y sont pas) ;
- pour chacune, à côté du backtest : ce qu'elle a donné **en paper trading** jusqu'à maintenant (trades, gain,
  challenges enchaînés, depuis quand) ;
- **« Voir le backtest »** : courbe du compte, 2 ans contre période récente (8 derniers mois), années, mois ;
- **un bot pour chaque** : « Bot MT5 combinée » pour la combinaison, et un petit bouton **« Bot »** devant CHAQUE
  stratégie d'une combinaison pour la prendre seule (aussi dans l'onglet « TOP 10 combinées du direct »).

**Classement croisé backtest × paper trading** (en haut de l'onglet) : pour chaque stratégie avec au moins 5 trades
en paper et 10 dans le backtest, son RANG (100 % = la meilleure) d'après la solidité t, dans le backtest et en
paper, et le ratio « paper / backtest » (R moyen du paper ÷ R moyen du backtest ; 100 % = pareil). Trois listes :
**bonnes partout** (backtest ET paper : les plus solides), **bonnes en paper seulement** (à surveiller),
**bonnes en backtest seulement** (le backtest ne se confirme pas : prudence). Bot MT5 et « Voir le backtest » sur
chaque ligne ; colonne « Paper / backtest » aussi dans les TOP 10 seules et combinées.

Enregistré dans `top_backtest_2ans.json`. Le n°1 parmi des milliers de backtests est en partie chanceux : la
stratégie bonne sur 2 ans ET en paper trading est la plus solide.

## Meilleures heures de chaque stratégie (et combinées « chacune dans ses heures »)

Pour CHAQUE stratégie, le labo cherche quand elle trade le mieux :

- **heure par heure** (0 h à 23 h, heure du serveur MT5) : trades, gagnants et R moyen à chaque heure d'ouverture
  (bande de 24 cases vertes / rouges dans la plateforme) ;
- **sa meilleure plage** : toutes les plages sont essayées (début 0 h à 23 h, durée 2 h à 12 h : 8h-11h, 9h-14h,
  22h-2h...). La plage est CHOISIE sur les 60 % premiers trades puis CONTRÔLÉE sur les 40 % suivants (au moins 10
  trades, gagnante, au moins +0,1R par trade de mieux que 24 h/24, t >= 1,5). Sur des données au hasard, environ
  3 % des stratégies passent ce contrôle par chance ; une vraie plage gagnante est retrouvée environ une fois sur
  deux avec 400 trades ;
- chaque plage confirmée devient une stratégie **« horaire »** (🕘) qui n'entre QUE dans ses heures (ses positions
  ouvertes continuent après) ; le **Chef des combinaisons** mélange alors des stratégies qui tradent chacune à
  leur meilleur moment de la journée.

Partout :

- **agents / Directeur** (et donc le compte perso 5 000 $ et le compte financé) : variantes horaires dans le choix
  des stratégies combinées, section « Meilleures heures de chaque stratégie » dans `directeur.html`,
  `heures_strategies.json` ;
- **plateforme, onglet « TOP 10 backtest 2 ans »** : variantes horaires de chaque stratégie du direct, tableau
  « Meilleures heures » (heure par heure, plage, contrôle, paper dans / hors de la plage), TOP 10 seules et
  combinées avec les heures de chaque composant ;
- **plateforme, onglet « TOP 10 combinées du direct »** : une stratégie avec au moins 40 trades en paper et une plage
  confirmée est aussi essayée dans ses heures ;
- **paper trading et bots** : chaque composant a ses heures (`horaire` dans le fichier de la stratégie). Le paper
  trading n'ouvre jamais un trade d'un composant en dehors de ses heures ; le bot MT5 exécute les signaux du paper
  (LANCER_BOT.bat) et respecte donc exactement les heures de chaque stratégie. Les heures sont écrites dans
  LaboBot.mq5 et LISEZMOI_BOT.txt. Boutons « Bot » d'une stratégie dans ses heures (`id@8-11`).

## Utiliser la plateforme (ergonomie)

- **🏠 Accueil « Aujourd'hui »** (page de départ) : *Quel bot faire tourner ?* (le n°1 du classement général, ses
  stratégies et leurs heures, $ par jour, boutons bot FTMO / perso 5k), *Mon challenge* (objectif, aujourd'hui, baisse
  max, jours tradés, vrai compte MT5), *Alertes* (stratégie en pause, bot silencieux, événements), *Calculs* (dates
  des derniers calculs, bouton « Tout recalculer maintenant »).
- **5 sections** au lieu de 12 onglets : Accueil · En direct (combinée, positions, historique, challenges, journal)
  · Classements (général, backtest 2 ans, TOP 10 du direct, toutes les stratégies, meilleur par marché, setups)
  · Analyse (heures & planning, analyse des trades, R:R) · Mes bots.
- **Fiche complète** : un clic sur « Fiche complète » montre tout d'une stratégie ou d'une combinée : chaque
  stratégie heure par heure, courbe du backtest, $ par jour sur 1 an, paper trading, bots, et un lien vers
  l'analyse de ses trades.
- **Mode simple / mode expert** (bouton en haut à droite) : en mode simple, des pastilles 🟢 solide (bonne en backtest
  ET en paper) / 🟡 à surveiller / 🔴 prudence, et les colonnes techniques cachées ; en mode expert, tout. Bulles
  d'aide sur les en-têtes (soulignés en pointillé).
- **🤖 Mes bots** : chaque bot créé, ses stratégies et leurs heures, actif ou arrêté (son LANCER_BOT.bat), son résultat
  en paper, bouton « Ouvrir le dossier ». Créer un bot ouvre une fenêtre avec les étapes (au lieu d'une alerte).
- **Calcul automatique chaque nuit à 2 h** (heure du PC) : TOP 10 du direct puis backtest 2 ans (heures, planning,
  classement général), résumé sur Telegram si configuré. Autre heure : variable d'environnement `LABO_AUTO_HEURE`
  (ex. 3) ; `-1` pour désactiver.
- **Petit écran / téléphone** : sous 760 px de large, les tableaux deviennent des cartes. Par sécurité, la plateforme
  n'écoute que sur le PC lui-même (personne d'autre ne peut créer un bot) : pour la voir sur le téléphone, utilisez un
  bureau à distance (ex. Chrome Remote Desktop, ou le VPS) ; les alertes Telegram arrivent directement sur le téléphone.

## Classement général backtest × paper (seules ET combinées)

En haut de l'onglet « TOP 10 backtest 2 ans » :

- **CLASSEMENT GÉNÉRAL** : toutes les stratégies seules et combinées (24 h/24 ou dans leurs meilleures heures 🕘) qui
  ont déjà tradé en paper, dans UN seul classement. Rang backtest = position pour passer le challenge sur les 2 ans
  (échecs, jours pour réussir, réussite) ; rang paper = gain par jour en paper trading (mêmes risques). Le n°1 est
  celle dont le PLUS FAIBLE des deux rangs est le plus haut : bonne dans les deux, pas seulement dans un ;
- **TOP 10 des COMBINÉES bonnes en backtest ET en paper** : le Chef des combinaisons ne part que des stratégies
  « bonnes partout » (24 h/24 ou dans leurs heures), puis chaque combinée est classée sur ses deux rangs ;
- le classement croisé des stratégies seules (bonnes partout / en paper seulement / en backtest seulement) ;
- partout : backtest détaillé, $ par jour sur 1 an (financé 100k, perso 5k), paper trading, bots FTMO et perso 5k.

## Planning de la journée : la meilleure stratégie à chaque heure

En plus de la meilleure plage de CHAQUE stratégie, le labo regarde TOUTES les stratégies ensemble :

- **à chaque heure** (0 h à 23 h, heure du serveur MT5), la plus forte de toutes les stratégies parmi celles dont la
  plage horaire confirmée couvre cette heure (plus solide qu'un choix heure par heure : une heure seule n'a que
  quelques trades) ; les heures de suite d'une même stratégie forment une plage (ex. US30 1h-4h, GER40 8h-11h,
  NASDAQ 14h-18h) ;
- choisi sur les 60 % premiers trades, **contrôlé** sur les 40 % suivants : seules les plages confirmées entrent dans
  la combinée **« Planning de la journée »** (chaque stratégie ne trade que dans ses heures, un seul compte) ;
- **les meilleures heures en général** : toutes les stratégies réunies, heure par heure.

Partout : dans la recherche des **agents** (Directeur : `planning_journee.json`, section du rapport, et la combinée
« Planning de la journée » classée dans le TOP 10 des stratégies combinées) et dans la plateforme (onglet « TOP 10
backtest 2 ans » : frise des 24 heures, plages, bots de chaque plage, bot de la combinée pour le challenge FTMO ou le
compte perso 5k).

**Période du backtest** : par défaut les 2 dernières années jusqu'à aujourd'hui (la dernière bougie MT5 : environ
octobre 2024 → octobre 2026), ou à partir de la date choisie dans le champ « Début du backtest » (ex. 2024-01-01).

## Combien ça ferait sur 1 an : compte financé et compte perso 5 000 $

Pour chaque stratégie (seule ou combinée) du TOP 10 backtest 2 ans : 1 000 années possibles tirées des journées du
backtest, pour le **compte financé FTMO** (100 000 $, 1 %/trade, 2,5 %/jour, avant le partage des profits) et le
**compte perso** (5 000 $, 2 %/trade, 5 %/jour, intérêts composés) : gain médian sur 1 an, **$ par jour** (gain de
l'année ÷ 252 jours de bourse), $ du 1er mois, mauvaise année (1 sur 10), baisse typique, risque de problème.
Section **« Le meilleur pour le COMPTE PERSO 5 000 $ »** avec, pour chacune, un bot challenge FTMO et un bot compte
perso (risques mis à l'échelle : 2 % au lieu de 1 %, limites du compte perso). Ce sont des projections du passé :
à confirmer en paper trading.

## Analyse des trades (onglet de la plateforme)

Chaque trade du paper trading enregistre maintenant beaucoup plus que son résultat (colonnes ajoutées à
`trades.csv`, les anciens fichiers sont mis à jour tout seuls au démarrage) :

- **excursions** : `mae_r` (jusqu'où le prix est allé CONTRE le trade, en R), `mfe_r` (jusqu'où il est allé EN SA
  FAVEUR) et `min_apres_1r_r` (le pire point après avoir touché +1R), mesurés tick par tick ;
- **contexte** : `regime` (type de marché à l'ouverture), `nouvelle_avant_min` / `nouvelle_apres_min` (minutes
  depuis / jusqu'à l'annonce importante la plus proche, avec le calendrier `news.csv` de mql5/ExportNews.mq5).

Onglet **« Analyse des trades »** (filtres : marché, timeframe, une stratégie) :

1. **Stops & objectifs** : pour chaque stratégie, sur les prix réellement vus, ce qu'auraient donné un stop au
   point d'entrée à +1R, un objectif plus proche (0,5R à 4R) ou un stop plus serré (même risque en argent), le %
   de perdants qui étaient passés à +1R, le recul médian des gagnants et l'avance médiane des perdants, et un
   conseil. (Un objectif plus loin ou un stop plus large ne se déduisent pas : à tester dans le backtest.)
2. **Quand ça marche** : R par heure, jour, type de marché et proximité des annonces ; « moments à éviter »
   (au moins 15 trades et un écart net) et « points forts ». À vérifier dans le backtest avant d'en faire un filtre.
3. **Trades refusés (fantômes)** : chaque signal refusé par les règles de la stratégie combinée (perte possible
   max du jour, positions max, marchés corrélés, frein, horaire, week-end) est suivi jusqu'au bout sans argent
   (`fantomes.csv`, stop / objectif / signal opposé / durée max, sans gestion). Bilan par raison : la règle
   protège (fantômes perdants) ou coûte des gains (fantômes gagnants).

## Annonces et marchés macro comme ingrédients

- **Annonces** : avec le calendrier `news.csv`, la recherche ajoute à chaque bougie les minutes depuis la dernière
  annonce importante et jusqu'à la prochaine (`news_prev_min`, `news_next_min` ; l'heure des annonces est connue
  à l'avance, pas de regard vers le futur). Nouveaux ingrédients des inventeurs `news_after` / `news_before`
  (ex. « entrer 30 à 90 min après une annonce ») et nouvel analyste de l'équipe E : **Agent 24, Analyste des
  annonces**. Le paper trading calcule les mêmes colonnes en direct.
- **Marchés macro** : si le courtier les propose, le dollar (DXY), le VIX, le taux US 10 ans, le pétrole (WTI) et
  le S&P 500 sont ajoutés aux inter-marchés (noms trouvés automatiquement : USDX, US500.cash...). Ils ne sont pas
  tradés : ce sont des ingrédients (« QUAND le dollar monte de 2 écarts-types... »). `--sans-macro` pour les retirer.

## Meilleur bot par marché

Onglet **« Meilleur bot par marché »** de la plateforme : pour chaque marché, la stratégie qui marche le mieux EN
DIRECT (au moins 10 trades, gagnante, pas en pause, classée par solidité), avec son timeframe, un remplaçant et le
bouton **« Créer le bot MT5 »**. Dans MT5, posez chaque bot sur un graphique de SON marché et de SON timeframe.
Un avertissement s'affiche si une seule journée fait plus de 50 % du profit (règle FTMO du meilleur jour).

## Coûts réels mesurés en direct et frein de bonne journée

- **Coûts réels** : à chaque lancement de la recherche (lab) ou du Directeur, le programme lit tous les
  `trades.csv` du paper trading (spread réel à l'entrée) et le fichier `glissements.csv` du bot MT5 (écart entre le
  prix du signal et le prix obtenu). Dès qu'un marché a au moins 10 mesures, le spread réel (s'il est plus large que
  celui de l'historique) et le glissement (x2 : entrée + sortie) sont **ajoutés au coût de chaque trade testé**.
  Résultat dans `results/couts_reels.json`. Une stratégie qui ne gagne que sur papier est donc éliminée.
  Pour l'ignorer : `--sans-couts-reels`.
- **Frein de bonne journée** : le Directeur teste « après +X % gagnés dans la journée (X de 1,5 à 5 %), plus de
  nouveau trade » ou « risque divisé par 2 » jusqu'au lendemain. Il ne le garde que s'il fait passer le challenge
  plus vite ou plus sûrement (utile pour la règle du meilleur jour ≤ 50 % du profit). La règle choisie est affichée
  dans `directeur.html`, sur la plateforme (avec « ACTIF aujourd'hui ») et appliquée par le paper trading et le bot
  (avec le bot : le plus haut gain du jour entre le paper et le VRAI compte).

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
