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


def parse_commission(values) -> dict:
    """"5" -> 5 pour tous ; "EURUSD=5 XAUUSD=5 NASDAQ=0" -> par symbole ("*" = défaut)."""
    out = {"*": 0.0}
    for v in values or []:
        if "=" in v:
            k, x = v.split("=", 1)
            out[k.strip().upper()] = float(x)
        else:
            out["*"] = float(v)
    return out


def commission_for(table: dict, symbol: str) -> float:
    return table.get(symbol.upper(), table["*"])


def ftmo_rules(a):
    from mt5lab.ftmo import FtmoRules
    return FtmoRules(target1=a.ftmo_target, target2=a.ftmo_phase2, max_daily=a.ftmo_daily, max_total=a.ftmo_total,
                     min_days=a.ftmo_min_days)


def add_ftmo_args(p):
    g = p.add_argument_group("règles du challenge FTMO")
    g.add_argument("--ftmo-target", type=float, default=10.0, help="objectif de profit en %% (défaut 10)")
    g.add_argument("--ftmo-daily", type=float, default=3.0, help="perte max par jour en %% (défaut 3)")
    g.add_argument("--ftmo-total", type=float, default=10.0, help="perte max totale en %% (défaut 10)")
    g.add_argument("--ftmo-phase2", type=float, default=0.0, help="objectif phase 2 en %% (0 = pas de phase 2)")
    g.add_argument("--ftmo-min-days", type=int, default=4, help="jours de trading minimum (défaut 4)")


def cmd_directeur(a):
    from mt5lab.manager import Director, DirectorConfig

    cfg = DirectorConfig(a.symbols, a.timeframes, out=Path(a.out), capital=a.capital, risk_pct=a.risk_max,
                         day_budget=a.perte_max_jour, lab_risk_pct=a.risk, ftmo=ftmo_rules(a), rounds=a.rounds,
                         budget=a.budget, invent_generations=a.invent_generations, reuse=not a.refaire,
                         second_pass=not a.sans_deuxieme_passe)
    if a.demo:
        from mt5lab.data import synthetic
        freq = {"M1": "min", "M5": "5min", "M15": "15min", "M30": "30min", "H1": "h", "H4": "4h", "D1": "D"}

        def get_data(sym, tf):
            import zlib
            return synthetic(a.bars or 6000, seed=zlib.crc32(f"{sym}{tf}".encode()) % 1000, freq=freq.get(tf, "h")), 0.00012
        Director(cfg, get_data).run()
        return
    from mt5lab.data import MT5Connector
    comm = parse_commission(a.commission)
    with MT5Connector() as conn:
        def get_data(sym, tf):
            df = conn.rates(sym, tf, a.bars or 30000)
            return df, conn.cost_in_price(sym, a.commission_points, commission_for(comm, sym))
        Director(cfg, get_data).run()


def cmd_compare(a):
    from mt5lab.compare import build_comparison
    build_comparison(Path(a.out), a.capital, ftmo_rules(a), a.risk)


def cmd_lab(a):
    cfg = LabConfig(rounds=a.rounds, budget=a.budget, oos_fraction=a.oos, risk_pct=a.risk,
                    workers=a.workers, seed=a.seed, ftmo=ftmo_rules(a), invent=not a.no_invent,
                    invent_generations=a.invent_generations)
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
        comm = parse_commission(a.commission)
        with MT5Connector() as conn:
            for sym in a.symbols:
                for tf in a.timeframes:
                    try:
                        df = conn.rates(sym, tf, a.bars or 30000)
                    except Exception as exc:
                        print(f"[lab] {sym} {tf} ignoré : {exc}")
                        continue
                    if len(df) < 1000:
                        print(f"[lab] {sym} {tf} ignoré : seulement {len(df)} bougies (minimum 1000)")
                        continue
                    cost = a.cost if a.cost is not None else \
                        conn.cost_in_price(sym, a.commission_points, commission_for(comm, sym))
                    jobs.append((f"{sym}_{tf}", df, cost))
    for n, (label, df, cost) in enumerate(jobs, 1):
        print(f"\n########## [{n}/{len(jobs)}] {label} ##########")
        board = run_lab(df, cost, cfg, label, out_root / label)
        ok = board[board["verdict"] == "APPROUVÉ"] if len(board) else board
        print(f"\n===== {label} : {len(ok)} stratégies approuvées =====")
        if len(ok):
            print(ok[["strategie", "risque", "trades_oos", "wr_oos", "avgR_oos", "pf_oos", "ret_oos_pct",
                      "dd_oos_pct"]].head(15).to_string(index=False))
        print(f"Rapport : {out_root / label / 'rapport.html'}")
    if len(jobs) > 1 or not (a.demo or a.csv):
        from mt5lab.compare import build_comparison
        build_comparison(out_root, a.capital, ftmo_rules(a), a.risk)


def cmd_live(a):
    from mt5lab.data import MT5Connector
    from mt5lab.live import LiveTrader, load_candidate

    cand = load_candidate(a.strategies, a.index)
    with MT5Connector() as conn:
        LiveTrader(conn, a.symbol, a.timeframe, cand, risk_pct=a.risk, execute=a.execute,
                   allow_real=a.allow_real).run(a.poll)


def cmd_paper(a):
    from mt5lab.data import MT5Connector
    from mt5lab.paper import (PaperEngine, load_best_slots, load_combined_slots, load_exploration_slots,
                              load_portfolio_slots, load_slots, write_dashboard)

    if a.port:  # déjà en marche ? (deux copies écriraient dans les mêmes fichiers)
        import socket
        with socket.socket() as sock:
            sock.settimeout(1)
            if sock.connect_ex(("127.0.0.1", a.port)) == 0:
                print(f"[paper] Ce paper trading tourne déjà : http://localhost:{a.port}")
                raise SystemExit(3)
    slots, groups = [], {}
    if a.source == "combinee":
        slots, groups = load_combined_slots(Path(a.results), a.capital)
        if not slots:
            raise SystemExit("Pas encore de stratégie combinée : lancez d'abord le Directeur (python run.py directeur).")
    if a.source == "portefeuille":
        slots = load_portfolio_slots(Path(a.results), a.capital)
        if not slots:
            print("[paper] pas encore de portefeuille FTMO : je prends les stratégies validées")
            a.source = "approuvees"
    if not slots:
        if a.source == "exploration":
            slots = load_exploration_slots(Path(a.results), a.symbols, a.timeframes, a.capital)
        elif a.source == "meilleures":
            slots = load_best_slots(Path(a.results), a.symbols, a.top, a.capital)
        else:
            for tf in a.timeframes:
                slots += load_slots(Path(a.results), a.symbols, tf, a.source, a.top, a.capital)
    if not slots:
        raise SystemExit("Aucune stratégie à suivre : lancez d'abord la recherche (python run.py lab ...).")
    with MT5Connector() as conn:
        comm = parse_commission(a.commission)
        eng = PaperEngine(conn, slots, Path(a.out), a.risk, {s.symbol: commission_for(comm, s.symbol) for s in slots},
                          ftmo=ftmo_rules(a), groups=groups)
        write_dashboard(eng)
        eng.run(a.poll, server_port=a.port or None, open_browser=not a.no_browser)


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
    lab.add_argument("--timeframes", "--timeframe", nargs="+", default=["H1"],
                     help="un ou plusieurs : M1 M5 M15 M30 H1 H4 D1 (ou ALL)")
    lab.add_argument("--capital", type=float, default=100_000, help="capital pour exprimer les gains en $/mois")
    lab.add_argument("--bars", type=int, default=None)
    lab.add_argument("--cost", type=float, default=None, help="coût aller-retour en prix (sinon spread MT5)")
    lab.add_argument("--commission-points", type=float, default=0.0)
    lab.add_argument("--commission", nargs="+", default=None,
                     help="commission aller-retour par lot : '5' pour tous, ou 'EURUSD=5 XAUUSD=5 NASDAQ=0'")
    lab.add_argument("--rounds", type=int, default=3)
    lab.add_argument("--budget", type=int, default=1000, help="tests max par agent et par round")
    lab.add_argument("--oos", type=float, default=0.35, help="part des données réservée à la validation")
    lab.add_argument("--risk", type=float, default=0.5, help="risque par trade en %% pour le calcul du rendement")
    lab.add_argument("--workers", type=int, default=None)
    lab.add_argument("--seed", type=int, default=int(time.time()) % 10000)
    lab.add_argument("--out", default="results")
    lab.add_argument("--no-invent", action="store_true", help="pas de round d'invention de stratégies")
    lab.add_argument("--invent-generations", type=int, default=8, help="générations d'évolution par agent inventeur")
    add_ftmo_args(lab)
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
    paper.add_argument("--timeframes", nargs="+", default=["H1"], help="un ou plusieurs timeframes (ou ALL)")
    paper.add_argument("--source", choices=["exploration", "combinee", "meilleures", "portefeuille", "tous", "approuvees"], default="tous",
                       help="exploration = TOUTES les stratégies + inventions × TOUS les R:R ; "
                            "combinee = la stratégie combinée du Directeur (un seul compte) ; "
                            "portefeuille = les stratégies choisies ensemble par le Chef FTMO ; "
                            "meilleures = top N global de la comparaison (tous marchés et timeframes) ; "
                            "tous = top N finalistes par marché/timeframe ; approuvees = seulement les validées")
    paper.add_argument("--port", type=int, default=8765, help="port de la plateforme web locale (0 = désactivée)")
    paper.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur automatiquement")
    paper.add_argument("--top", type=int, default=20, help="nb max de stratégies suivies par symbole/timeframe")
    paper.add_argument("--capital", type=float, default=100_000, help="capital virtuel de chaque stratégie")
    paper.add_argument("--risk", type=float, default=0.5,
                       help="perte max par trade en %% du capital (0.5 %% de 100 000 = 500 max au stop)")
    paper.add_argument("--commission", nargs="+", default=None,
                       help="commission aller-retour par lot : '5' pour tous, ou 'EURUSD=5 XAUUSD=5 NASDAQ=0'")
    paper.add_argument("--poll", type=int, default=5, help="secondes entre deux vérifications")
    paper.add_argument("--results", default="results")
    paper.add_argument("--out", default="results/paper")
    add_ftmo_args(paper)
    paper.set_defaults(func=cmd_paper)

    di = sub.add_parser("directeur", help="le Directeur : pousse chefs et agents et construit la stratégie combinée")
    di.add_argument("--symbols", nargs="+", default=["NASDAQ", "XAUUSD", "EURUSD"])
    di.add_argument("--timeframes", nargs="+", default=["ALL"])
    di.add_argument("--bars", type=int, default=None)
    di.add_argument("--capital", type=float, default=100_000)
    di.add_argument("--risk-max", type=float, default=1.0, help="risque MAX par trade essayé par le Directeur (%%)")
    di.add_argument("--perte-max-jour", type=float, default=1.7,
                    help="perte possible max par jour : le Directeur teste tous les scénarios jusqu'à ce plafond (%%)")
    di.add_argument("--risk", type=float, default=0.5, help="risque utilisé par les chefs pour noter les stratégies (%%)")
    di.add_argument("--commission", nargs="+", default=None)
    di.add_argument("--commission-points", type=float, default=0.0)
    di.add_argument("--rounds", type=int, default=3)
    di.add_argument("--budget", type=int, default=1000)
    di.add_argument("--invent-generations", type=int, default=8)
    di.add_argument("--refaire", action="store_true", help="refaire aussi les cases déjà recherchées")
    di.add_argument("--sans-deuxieme-passe", action="store_true")
    di.add_argument("--demo", action="store_true", help="données synthétiques (sans MT5)")
    di.add_argument("--out", default="results")
    add_ftmo_args(di)
    di.set_defaults(func=cmd_directeur)

    comp = sub.add_parser("compare", help="comparer tous les marchés × timeframes déjà testés")
    comp.add_argument("--out", default="results")
    comp.add_argument("--capital", type=float, default=100_000)
    comp.add_argument("--risk", type=float, default=0.5, help="risque par trade en %% (pour le portefeuille FTMO)")
    add_ftmo_args(comp)
    comp.set_defaults(func=cmd_compare)

    check = sub.add_parser("check", help="tester la connexion à MT5")
    check.add_argument("--symbols", nargs="+", default=["EURUSD"])
    check.add_argument("--timeframe", default="H1")
    check.set_defaults(func=cmd_check)

    sub.add_parser("strategies", help="lister le catalogue").set_defaults(func=cmd_list)
    a = p.parse_args()
    from mt5lab.data import TIMEFRAMES
    for attr in ("timeframes",):
        if getattr(a, attr, None) and [t.upper() for t in getattr(a, attr)] == ["ALL"]:
            setattr(a, attr, [t for t in TIMEFRAMES if t != "W1"])
    a.func(a)


if __name__ == "__main__":
    main()
