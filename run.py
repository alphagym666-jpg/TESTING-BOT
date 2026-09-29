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

import pandas as pd
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


def load_news_arg(a):
    """Calendrier des nouvelles (mql5/ExportNews.mq5). Absent -> pas de filtre, avec un message."""
    if getattr(a, "sans_nouvelles", False):
        return None
    from mt5lab.data import load_news
    news = load_news(getattr(a, "nouvelles", None))
    if news is None:
        print("[nouvelles] pas de calendrier (news.csv) : filtre des nouvelles désactivé. Pour l'activer, lancez le "
              "script mql5/ExportNews.mq5 dans MT5 (voir README).")
    return news


def news_args(p):
    p.add_argument("--sans-nouvelles", action="store_true",
                   help="ne pas bloquer les entrées autour des annonces économiques importantes")
    p.add_argument("--fenetre-nouvelles", type=int, default=30, help="minutes bloquées avant/après une annonce")
    p.add_argument("--nouvelles", default=None, help="chemin du calendrier news.csv (sinon trouvé automatiquement)")


def period_args(p):
    p.add_argument("--sans-genies", action="store_true", help="sans les 2 génies (Einstein, Hawking)")
    p.add_argument("--generations-genies", type=int, default=12, help="générations d'évolution des formules des génies")
    p.add_argument("--sans-inter-marches", action="store_true",
                   help="ne pas donner aux inventeurs les prix des autres marchés")
    p.add_argument("--depuis", default=None,
                   help="ne garder que les données depuis cette date (ex. 2025-01-01) : recherche ET validation "
                        "sur la période récente")
    p.add_argument("--battre-buy-hold", action="store_true",
                   help="une stratégie n'est validée que si elle bat le buy & hold sur la période de validation")


def with_ext(df, sym, tf, symbols, raw, a):
    """Inter-marchés : ajoute les clôtures des autres marchés choisis (les inventeurs s'en servent)."""
    if getattr(a, "sans_inter_marches", False):
        return df
    from mt5lab.data import add_ext
    others = {}
    for o in symbols:
        if o == sym:
            continue
        try:
            others[o] = raw(o, tf)["close"]
        except Exception as exc:
            print(f"[inter-marchés] {o} {tf} indisponible : {exc}")
    return add_ext(df, others) if others else df


def since(df, a):
    """Mode « période récente » : coupe l'historique à --depuis."""
    if getattr(a, "depuis", None):
        df = df[df.index >= pd.Timestamp(a.depuis)]
    return df


def ftmo_rules(a):
    from mt5lab.ftmo import FtmoRules
    return FtmoRules(target1=a.ftmo_target, target2=a.ftmo_phase2, max_daily=a.ftmo_daily, max_total=a.ftmo_total,
                     min_days=a.ftmo_min_days, best_day_pct=a.ftmo_meilleur_jour)


def add_ftmo_args(p):
    g = p.add_argument_group("règles du challenge FTMO")
    g.add_argument("--ftmo-target", type=float, default=10.0, help="objectif de profit en %% (défaut 10)")
    g.add_argument("--ftmo-daily", type=float, default=3.0, help="perte max par jour en %% (défaut 3)")
    g.add_argument("--ftmo-total", type=float, default=10.0, help="perte max totale en %% (défaut 10)")
    g.add_argument("--ftmo-phase2", type=float, default=0.0, help="objectif phase 2 en %% (0 = pas de phase 2)")
    g.add_argument("--ftmo-min-days", type=int, default=4, help="jours de trading minimum (défaut 4)")
    g.add_argument("--ftmo-meilleur-jour", type=float, default=50.0,
                   help="la meilleure journée ne peut pas dépasser X %% du profit total (défaut 50 ; 0 = pas de règle)")


def cmd_directeur(a):
    from mt5lab.manager import Director, DirectorConfig

    cfg = DirectorConfig(a.symbols, a.timeframes, out=Path(a.out), capital=a.capital, risk_pct=a.risk_max,
                         day_budget=a.perte_max_jour, lab_risk_pct=a.risk, ftmo=ftmo_rules(a), rounds=a.rounds,
                         budget=a.budget, invent_generations=a.invent_generations, reuse=not a.refaire,
                         second_pass=not a.sans_deuxieme_passe, years=a.annees, total_budget=a.perte_max_totale,
                         max_fail=a.echec_max, catalog=not a.sans_catalogue, bank_teams=not a.sans_equipes_banques,
                         server_offset=a.decalage_horaire, beat_bh=a.battre_buy_hold, genies=not a.sans_genies,
                         min_bars=400 if a.depuis else 1000)
    if a.depuis:  # la « durée exigée » devient la période récente (sinon le Directeur redemanderait 5 ans)
        cfg.years = max(0.2, (pd.Timestamp.now() - pd.Timestamp(a.depuis)).days / 365.25)
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
    news = load_news_arg(a)
    with MT5Connector() as conn:
        raw_cache: dict = {}

        def raw(sym, tf):
            if (sym, tf) not in raw_cache:
                raw_cache[(sym, tf)] = since(conn.rates(sym, tf, a.bars) if a.bars else
                                             conn.rates_years(sym, tf, a.annees), a)
            return raw_cache[(sym, tf)]

        def get_data(sym, tf):
            df = with_ext(raw(sym, tf), sym, tf, a.symbols, raw, a)
            c = commission_for(comm, sym)
            df = conn.enrich(df, sym, a.commission_points, c, news, a.fenetre_nouvelles)
            return df, conn.typical_cost(df, sym, a.commission_points, c)
        Director(cfg, get_data).run()


def cmd_compare(a):
    from mt5lab.compare import build_comparison
    build_comparison(Path(a.out), a.capital, ftmo_rules(a), a.risk)


def cmd_lab(a):
    cfg = LabConfig(rounds=a.rounds, budget=a.budget, oos_fraction=a.oos, risk_pct=a.risk,
                    workers=a.workers, seed=a.seed, ftmo=ftmo_rules(a), invent=not a.no_invent,
                    catalog=not a.sans_catalogue, bank_teams=not a.sans_equipes_banques,
                    invent_generations=a.invent_generations, beat_bh=a.battre_buy_hold, genies=not a.sans_genies,
                    genie_generations=a.generations_genies)
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
        news = load_news_arg(a)
        with MT5Connector() as conn:
            raw_cache: dict = {}

            def raw(s, t):
                if (s, t) not in raw_cache:
                    raw_cache[(s, t)] = since(conn.rates(s, t, a.bars) if a.bars else conn.rates_years(s, t, a.annees), a)
                return raw_cache[(s, t)]

            total, n = len(a.symbols) * len(a.timeframes), 0
            # timeframe par timeframe (les autres marchés du même timeframe servent aux inter-marchés), une case à
            # la fois : on ne garde jamais tout l'historique de tous les marchés en mémoire
            for tf in a.timeframes:
                raw_cache.clear()
                for sym in a.symbols:
                    n += 1
                    try:
                        df = with_ext(raw(sym, tf), sym, tf, a.symbols, raw, a)
                    except Exception as exc:
                        print(f"[lab] {sym} {tf} ignoré : {exc}")
                        continue
                    min_bars = 400 if a.depuis else 1000
                    if len(df) < min_bars:
                        print(f"[lab] {sym} {tf} ignoré : seulement {len(df)} bougies (minimum {min_bars})")
                        continue
                    df = conn.enrich(df, sym, a.commission_points, commission_for(comm, sym), news,
                                     a.fenetre_nouvelles)
                    if a.cost is not None:  # coût imposé à la main : il remplace le spread historique
                        df, cost = df.drop(columns=["cost"], errors="ignore"), a.cost
                    else:
                        cost = conn.typical_cost(df, sym, a.commission_points, commission_for(comm, sym))
                    run_cell(f"{sym}_{tf}", df, cost, cfg, out_root, n, total)
                    jobs.append(f"{sym}_{tf}")
                    del df
    for n, (label, df, cost) in enumerate(j for j in jobs if isinstance(j, tuple)):
        run_cell(label, df, cost, cfg, out_root, n + 1, len(jobs))
    if len(jobs) > 1 or not (a.demo or a.csv):
        from mt5lab.compare import build_comparison
        build_comparison(out_root, a.capital, ftmo_rules(a), a.risk)


def run_cell(label, df, cost, cfg, out_root, n, total):
    print(f"\n########## [{n}/{total}] {label} ##########")
    board = run_lab(df, cost, cfg, label, out_root / label)
    ok = board[board["verdict"] == "APPROUVÉ"] if len(board) else board
    print(f"\n===== {label} : {len(ok)} stratégies approuvées =====")
    if len(ok):
        print(ok[["strategie", "risque", "trades_oos", "wr_oos", "avgR_oos", "pf_oos", "ret_oos_pct",
                  "dd_oos_pct"]].head(15).to_string(index=False))
    elif len(board) and board["trades_oos"].notna().any():
        near = board[board["trades_oos"].notna()].sort_values("avgR_oos", ascending=False).head(5)
        print("Les plus proches (non retenues, et pourquoi) :")
        for _, r in near.iterrows():
            print(f"  {r['avgR_oos']:+.2f}R/trade OOS sur {int(r['trades_oos'])} trades | {r['strategie'][:70]} "
                  f"| {r['verdict']}")
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
        slots, groups = load_combined_slots(Path(a.results), a.capital, a.horaire, a.combinee_fichier)
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
        bridge = None
        if groups and not a.sans_bot:  # stratégie combinée : les décisions sont aussi envoyées au bot LaboBot
            from mt5lab.pont import SIGNAL_FILE, SignalBridge, common_files_dir
            bridge = SignalBridge(common_files_dir(conn.mt5) / (a.signaux or SIGNAL_FILE))
            print(f"[bot] signaux pour LaboBot écrits dans {bridge.path} (le bot n'agit que s'il est posé sur un graphique)")
        eng = PaperEngine(conn, slots, Path(a.out), a.risk, {s.symbol: commission_for(comm, s.symbol) for s in slots},
                          ftmo=ftmo_rules(a), groups=groups, news=load_news_arg(a), news_window=a.fenetre_nouvelles,
                          bridge=bridge)
        write_dashboard(eng)
        eng.run(a.poll, server_port=a.port or None, open_browser=not a.no_browser)


def _install(mq5, name):
    """Installe et compile le bot directement dans MT5 (si MT5 est ouvert)."""
    try:
        from mt5lab.data import MT5Connector
        from mt5lab.pont import install_in_mt5
        with MT5Connector() as conn:
            ok, msg = install_in_mt5(mq5, conn.mt5, name)
        print(("OK : " if ok else "ATTENTION : ") + msg)
    except Exception as exc:
        print(f"Installation automatique impossible ({exc}) : copiez {mq5} dans MQL5\\Experts et compilez (F7).")


def cmd_bot(a):
    from mt5lab.pont import generate_bot, generate_strategy_bot, single_strategy
    if a.fiche:  # n'importe quelle stratégie : fiche du classement (results/fiches/ID.json ou juste ID)
        import json
        p = Path(a.fiche)
        if not p.exists():
            p = Path(a.results) / "fiches" / (a.fiche if a.fiche.endswith(".json") else a.fiche + ".json")
        if not p.exists():
            raise SystemExit(f"Fiche introuvable : {a.fiche} (l'identifiant est écrit en haut de chaque fiche)")
        card = json.loads(p.read_text(encoding="utf-8"))
        comb = single_strategy(card["candidate"], card["marche"], card["timeframe"], a.risk or card.get("risk_pct") or 1.0,
                               card.get("nom"))
        out = generate_strategy_bot(comb, Path(a.results), a.capital, ftmo_rules(a), comb["composants"][0]["risk_pct"])
        print(f"Bot prêt : {out}")
        _install(out / "LaboBot.mq5", f"LaboBot_{out.name}.mq5")
        print(f"1) Double-cliquez {out / 'LANCER_BOT.bat'}   2) posez {out / 'LaboBot.mq5'} sur un graphique MT5 "
              f"(mode d'emploi : {out / 'LISEZMOI_BOT.txt'})")
        return
    out = generate_bot(Path(a.results), a.capital, ftmo_rules(a), horaire=a.horaire, forcer=a.forcer)
    _install(out / "LaboBot.mq5", "LaboBot_combinee.mq5")
    print(f"Bot prêt : {out / 'LaboBot.mq5'}")
    print(f"Mode d'emploi : {out / 'LISEZMOI_BOT.txt'}")


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
    lab.add_argument("--bars", type=int, default=None, help="nombre de bougies (sinon : durée en années)")
    lab.add_argument("--annees", type=float, default=None,
                     help="années d'historique (défaut : 2 ans en M1/M5, 5 ans de M15 à D1)")
    lab.add_argument("--cost", type=float, default=None, help="coût aller-retour en prix (sinon spread MT5)")
    lab.add_argument("--commission-points", type=float, default=0.0)
    lab.add_argument("--commission", nargs="+", default=None,
                     help="commission aller-retour par lot : '5' pour tous, ou 'EURUSD=5 XAUUSD=5 NASDAQ=0'")
    lab.add_argument("--rounds", type=int, default=3)
    lab.add_argument("--budget", type=int, default=1000, help="tests max par agent et par round")
    lab.add_argument("--oos", type=float, default=0.35, help="part des données réservée à la validation")
    lab.add_argument("--risk", type=float, default=1.0, help="risque par trade en %% pour le calcul du rendement")
    lab.add_argument("--workers", type=int, default=None)
    lab.add_argument("--seed", type=int, default=int(time.time()) % 10000)
    lab.add_argument("--out", default="results")
    lab.add_argument("--no-invent", action="store_true", help="pas de round d'invention de stratégies")
    lab.add_argument("--sans-catalogue", action="store_true", help="ne pas optimiser les 99 stratégies du catalogue")
    lab.add_argument("--sans-equipes-banques", action="store_true", help="sans les équipes C et D")
    lab.add_argument("--invent-generations", type=int, default=8, help="générations d'évolution par agent inventeur")
    add_ftmo_args(lab)
    news_args(lab)
    period_args(lab)
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
    paper.add_argument("--source", choices=["exploration", "combinee", "meilleures", "portefeuille", "tous", "approuvees",
                                            "essai"], default="tous",
                       help="exploration = TOUTES les stratégies + inventions × TOUS les R:R ; "
                            "combinee = la stratégie combinée du Directeur (un seul compte) ; "
                            "portefeuille = les stratégies choisies ensemble par le Chef FTMO ; "
                            "meilleures = top N global de la comparaison (tous marchés et timeframes) ; "
                            "tous = top N finalistes par marché/timeframe ; approuvees = seulement les validées ; "
                            "essai = validées + « à l'essai » (non validées mais gagnantes hors-échantillon)")
    paper.add_argument("--port", type=int, default=8765, help="port de la plateforme web locale (0 = désactivée)")
    paper.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur automatiquement")
    paper.add_argument("--top", type=int, default=20, help="nb max de stratégies suivies par symbole/timeframe")
    paper.add_argument("--capital", type=float, default=100_000, help="capital virtuel de chaque stratégie")
    paper.add_argument("--risk", type=float, default=1.0,
                       help="perte max par trade en %% du capital (1 %% de 100 000 = 1 000 max au stop)")
    paper.add_argument("--commission", nargs="+", default=None,
                       help="commission aller-retour par lot : '5' pour tous, ou 'EURUSD=5 XAUUSD=5 NASDAQ=0'")
    paper.add_argument("--poll", type=int, default=5, help="secondes entre deux vérifications")
    paper.add_argument("--results", default="results")
    paper.add_argument("--out", default="results/paper")
    add_ftmo_args(paper)
    news_args(paper)
    paper.add_argument("--horaire", default=None, choices=["24h24", "8h-17h", "8h-13h"],
                       help="stratégie combinée : prendre la meilleure de cet horaire (défaut : la meilleure de toutes)")
    paper.add_argument("--combinee-fichier", default=None,
                       help="suivre cette stratégie (fichier strategie.json d'un bot) au lieu de la combinée du Directeur")
    paper.add_argument("--signaux", default=None, help="nom du fichier de signaux du bot (défaut labo_signaux.csv)")
    paper.add_argument("--sans-bot", action="store_true",
                       help="ne pas écrire les signaux pour le bot MT5 LaboBot (stratégie combinée)")
    paper.set_defaults(func=cmd_paper)

    di = sub.add_parser("directeur", help="le Directeur : pousse chefs et agents et construit la stratégie combinée")
    di.add_argument("--symbols", nargs="+", default=["NASDAQ", "XAUUSD", "EURUSD", "GER40", "US30", "GBPUSD", "USDJPY"])
    di.add_argument("--timeframes", nargs="+", default=["ALL"])
    di.add_argument("--bars", type=int, default=None, help="nombre de bougies (sinon : durée en années)")
    di.add_argument("--annees", type=float, default=None,
                    help="années d'historique (défaut : 2 ans en M1/M5, 5 ans de M15 à D1)")
    di.add_argument("--capital", type=float, default=100_000)
    di.add_argument("--risk-max", type=float, default=1.0, help="risque MAX par trade essayé par le Directeur (%%)")
    di.add_argument("--perte-max-jour", type=float, default=2.5,
                    help="perte possible max par jour : le Directeur teste tous les scénarios jusqu'à ce plafond (%%)")
    di.add_argument("--perte-max-totale", type=float, default=10.0, help="perte totale max, jamais dépassée (%%)")
    di.add_argument("--echec-max", type=float, default=2.0,
                    help="%% maximum de challenges ratés toléré pour retenir un scénario")
    di.add_argument("--sans-catalogue", action="store_true", help="ne pas optimiser les 99 stratégies du catalogue")
    di.add_argument("--sans-equipes-banques", action="store_true", help="sans les équipes C et D")
    di.add_argument("--decalage-horaire", type=float, default=7.0,
                    help="heure du serveur MT5 moins votre heure locale (FTMO vs Québec : 7)")
    di.add_argument("--risk", type=float, default=1.0, help="risque utilisé par les chefs pour noter les stratégies (%%)")
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
    news_args(di)
    period_args(di)
    di.set_defaults(func=cmd_directeur)

    bot = sub.add_parser("bot", help="générer le bot MT5 (LaboBot.mq5) de la stratégie combinée")
    bot.add_argument("--results", default="results")
    bot.add_argument("--capital", type=float, default=100_000)
    bot.add_argument("--fiche", default=None,
                     help="bot d'UNE stratégie : identifiant ou fichier de sa fiche (results/fiches/ID.json)")
    bot.add_argument("--risk", type=float, default=None, help="risque par trade du bot (défaut : celui de la fiche)")
    bot.add_argument("--forcer", action="store_true",
                     help="accepter une stratégie combinée « à l'essai » (non validée) : déconseillé")
    bot.add_argument("--horaire", default=None, choices=["24h24", "8h-17h", "8h-13h"],
                     help="prendre la stratégie combinée de cet horaire (défaut : la meilleure)")
    add_ftmo_args(bot)
    bot.set_defaults(func=cmd_bot)

    comp = sub.add_parser("compare", help="comparer tous les marchés × timeframes déjà testés")
    comp.add_argument("--out", default="results")
    comp.add_argument("--capital", type=float, default=100_000)
    comp.add_argument("--risk", type=float, default=1.0, help="risque par trade en %% (pour le portefeuille FTMO)")
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
