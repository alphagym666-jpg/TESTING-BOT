"""Point d'entrée de la plateforme.

Exemples :
    # Vérifier la connexion à votre MT5 (Windows)
    python run.py check --symbols EURUSD XAUUSD

    # Démo hors-ligne (données synthétiques, marche partout)
    python run.py lab --demo

    # Recherche sur MT5 (Windows + terminal MT5 ouvert)
    python run.py lab --symbols EURUSD XAUUSD --timeframe H1 --bars 20000

    # Recherche sur un CSV exporté de MT5
    python run.py lab --csv data/EURUSD_H1.csv --cost 0.00012

    # Paper trading : trades FICTIFS sur les prix réels de MT5 (rien n'est envoyé à MT5)
    python run.py paper --symbols EURUSD XAUUSD --timeframes H1 --top 20

    # (Optionnel) exécution réelle d'une stratégie, simulation par défaut
    python run.py live --symbol EURUSD --timeframe H1 --strategies results/EURUSD_H1/meilleures_strategies.json
    python run.py live ... --execute
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from mt5lab.lab import LabConfig, run_lab
from mt5lab.strategies import REGISTRY


def cmd_lab(a):
    cfg = LabConfig(rounds=a.rounds, budget=a.budget, oos_fraction=a.oos, risk_pct=a.risk,
                    workers=a.workers, seed=a.seed)
    out_root = Path(a.out)
    jobs = []
    if a.demo:
        from mt5lab.data import synthetic
        jobs.append(("DEMO_SYNTH_H1", synthetic(a.bars or 8000, seed=a.seed), a.cost if a.cost is not None else 0.00012))
    elif a.csv:
        from mt5lab.data import load_csv
        for p in a.csv:
            df = load_csv(p)
            if a.bars:
                df = df.iloc[-a.bars:]
            jobs.append((Path(p).stem, df, a.cost or 0.0))
    else:
        from mt5lab.data import MT5Connector
        with MT5Connector() as conn:
            for sym in a.symbols:
                df = conn.rates(sym, a.timeframe, a.bars or 20000)
                cost = a.cost if a.cost is not None else conn.cost_in_price(sym, a.commission_points, a.commission)
                jobs.append((f"{sym}_{a.timeframe}", df, cost))
    for label, df, cost in jobs:
        board = run_lab(df, cost, cfg, label, out_root / label)
        ok = board[board["verdict"] == "APPROUVÉ"] if len(board) else board
        print(f"\n===== {label} : {len(ok)} stratégies approuvées =====")
        if len(ok):
            print(ok[["strategie", "risque", "trades_oos", "wr_oos", "avgR_oos", "pf_oos", "ret_oos_pct",
                      "dd_oos_pct"]].head(15).to_string(index=False))
        print(f"Rapport : {out_root / label / 'rapport.html'}")


def cmd_live(a):
    from mt5lab.data import MT5Connector
    from mt5lab.live import LiveTrader, load_candidate

    cand = load_candidate(a.strategies, a.index)
    with MT5Connector() as conn:
        LiveTrader(conn, a.symbol, a.timeframe, cand, risk_pct=a.risk, execute=a.execute,
                   allow_real=a.allow_real).run(a.poll)


def cmd_paper(a):
    from mt5lab.data import MT5Connector
    from mt5lab.paper import PaperEngine, load_slots, write_dashboard

    slots = []
    for tf in a.timeframes:
        slots += load_slots(Path(a.results), a.symbols, tf, a.source, a.top, a.capital)
    if not slots:
        raise SystemExit("Aucune stratégie à suivre : lancez d'abord la recherche (python run.py lab ...).")
    with MT5Connector() as conn:
        eng = PaperEngine(conn, slots, Path(a.out), a.risk, a.commission)
        write_dashboard(eng)
        eng.run(a.poll)


def cmd_check(a):
    from mt5lab.data import MT5Connector
    with MT5Connector() as conn:
        ok = conn.diagnose(a.symbols, a.timeframe)
    raise SystemExit(0 if ok else 1)


def cmd_list(_a):
    for s in sorted(REGISTRY.values(), key=lambda s: (s.family, s.name)):
        print(f"{s.family:<15} {s.name:<20} {s.description}")
    print(f"\n{len(REGISTRY)} stratégies")


def main():
    p = argparse.ArgumentParser(description="Labo de stratégies MT5 : 10 agents + 2 chefs d'équipe")
    sub = p.add_subparsers(dest="cmd", required=True)

    lab = sub.add_parser("lab", help="lancer la recherche de stratégies")
    lab.add_argument("--demo", action="store_true", help="données synthétiques (sans MT5)")
    lab.add_argument("--csv", nargs="+", help="fichier(s) CSV OHLC")
    lab.add_argument("--symbols", nargs="+", default=["EURUSD"])
    lab.add_argument("--timeframe", default="H1")
    lab.add_argument("--bars", type=int, default=None)
    lab.add_argument("--cost", type=float, default=None, help="coût aller-retour en prix (sinon spread MT5)")
    lab.add_argument("--commission-points", type=float, default=0.0)
    lab.add_argument("--commission", type=float, default=0.0,
                     help="commission aller-retour par lot en devise du compte (ex. 5 chez FTMO sur le forex)")
    lab.add_argument("--rounds", type=int, default=3)
    lab.add_argument("--budget", type=int, default=1000, help="tests max par agent et par round")
    lab.add_argument("--oos", type=float, default=0.35, help="part des données réservée à la validation")
    lab.add_argument("--risk", type=float, default=0.5, help="risque par trade en %% pour le calcul du rendement")
    lab.add_argument("--workers", type=int, default=None)
    lab.add_argument("--seed", type=int, default=int(time.time()) % 10000)
    lab.add_argument("--out", default="results")
    lab.set_defaults(func=cmd_lab)

    live = sub.add_parser("live", help="exécuter une stratégie validée sur MT5")
    live.add_argument("--symbol", required=True)
    live.add_argument("--timeframe", default="H1")
    live.add_argument("--strategies", required=True, help="meilleures_strategies.json produit par 'lab'")
    live.add_argument("--index", type=int, default=0)
    live.add_argument("--risk", type=float, default=0.5, help="risque par trade en %% du solde")
    live.add_argument("--poll", type=int, default=10)
    live.add_argument("--execute", action="store_true", help="envoyer de VRAIS ordres (sinon simulation)")
    live.add_argument("--allow-real", action="store_true", help="autoriser un compte réel")
    live.set_defaults(func=cmd_live)

    paper = sub.add_parser("paper", help="trades FICTIFS sur les prix réels de MT5 (aucun ordre envoyé)")
    paper.add_argument("--symbols", nargs="+", default=["EURUSD"])
    paper.add_argument("--timeframes", nargs="+", default=["H1"])
    paper.add_argument("--source", choices=["tous", "approuvees"], default="tous",
                       help="tous = les meilleurs finalistes de la recherche ; approuvees = seulement les validées")
    paper.add_argument("--top", type=int, default=20, help="nb max de stratégies suivies par symbole/timeframe")
    paper.add_argument("--capital", type=float, default=100_000, help="capital virtuel de chaque stratégie")
    paper.add_argument("--risk", type=float, default=0.5,
                       help="perte max par trade en %% du capital (0.5 %% de 100 000 = 500 max au stop)")
    paper.add_argument("--commission", type=float, default=0.0, help="commission aller-retour par lot (devise du compte)")
    paper.add_argument("--poll", type=int, default=5, help="secondes entre deux vérifications")
    paper.add_argument("--results", default="results")
    paper.add_argument("--out", default="results/paper")
    paper.set_defaults(func=cmd_paper)

    check = sub.add_parser("check", help="tester la connexion à MT5")
    check.add_argument("--symbols", nargs="+", default=["EURUSD"])
    check.add_argument("--timeframe", default="H1")
    check.set_defaults(func=cmd_check)

    sub.add_parser("strategies", help="lister le catalogue").set_defaults(func=cmd_list)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
