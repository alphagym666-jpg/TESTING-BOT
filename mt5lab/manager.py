"""Le Directeur : le cerveau au-dessus des 2 chefs d'équipe et des 10 agents.

Sa mission : une stratégie COMBINÉE qui passe le challenge FTMO le plus vite possible sans toucher les limites
de perte, testée sur tous les timeframes.

Campagne :
 1. Passe 1 — chaque marché × timeframe est confié aux 2 chefs et à leurs 10 agents (ou les résultats déjà
    obtenus sont repris).
 2. Revue — le Directeur note chaque case (stratégies validées, réussite FTMO), chaque famille de stratégies
    et chaque agent inventeur.
 3. Directives — là où rien n'est validé, il relance une recherche INTENSIVE (budget, rounds et générations
    d'inventions augmentés), en apportant ses idées : les stratégies qui marchent ailleurs (transférées aux chefs)
    et les indicateurs des inventions validées (imposés aux inventeurs). La barre de validation ne baisse jamais.
 4. Stratégie combinée — il assemble les meilleures stratégies validées de tous les marchés et timeframes, puis
    règle le risque de chaque composant (jamais plus que le risque max par trade), un arrêt journalier et un nombre
    maximum de positions ouvertes, pour maximiser la réussite du challenge puis la vitesse.
 5. Test multi-timeframes — chaque composant est rejoué, sans réoptimisation, sur tous les autres timeframes
    de son marché.
"""
from __future__ import annotations

import copy
import html
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .backtest import MANAGEMENT, RR_LEVELS, RiskConfig, run_backtest
from .compare import build_comparison
from .data import DEFAULT_YEARS
from .horaires import filter_trades
from .evaluator import candidate_key, compute_signal, describe
from .ftmo import (FtmoRules, holding_stats, vol_text, apply_risk_rules, count_challenges, daily_table, lock_text, pilot_text, simulate,
                   to_dt)
from .lab import LAB_VERSION, LabConfig, run_lab
from .strategies import REGISTRY, apply_filter


@dataclass
class DirectorConfig:
    symbols: list
    timeframes: list
    out: Path = Path("results")
    capital: float = 100_000.0
    risk_pct: float = 1.0              # risque MAX par trade : le Directeur ne le dépasse jamais
    risk_levels: tuple = (0.25, 0.5, 0.75, 0.8, 0.9, 1.0)  # niveaux de risque par trade essayés (<= risk_pct)
    day_budget: float = 2.5            # perte max possible par jour (réalisé + positions ouvertes + nouveau trade)
    day_budgets: tuple = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5)  # scénarios (jamais au-dessus de day_budget)
    total_budget: float = 10.0         # perte totale max : aucun nouveau trade ne peut la faire dépasser
    max_fail: float = 2.0              # % d'échecs toléré au challenge (limite de perte touchée) pour un scénario
    catalog: bool = True               # optimisation des stratégies du catalogue dans chaque recherche
    bank_teams: bool = True            # équipes C et D dans chaque recherche
    rr_variants: bool = True           # essayer aussi chaque stratégie validée avec tous les R:R
    horaires: bool = True              # chercher les MEILLEURES HEURES de chaque stratégie (variantes horaires)
    lab_risk_pct: float = 0.5          # risque utilisé pendant la recherche des chefs (pour noter les stratégies)
    ftmo: FtmoRules = field(default_factory=FtmoRules)
    rounds: int = 3
    budget: int = 1000
    invent_generations: int = 8
    reuse: bool = True                 # reprendre les résultats déjà calculés (s'ils couvrent assez d'années)
    years: float | None = None         # années d'historique exigées (défaut : 2 ans en M1/M5, 5 ans de M15 à D1)
    second_pass: bool = True           # relancer les cases faibles en mode intensif
    max_components: int = 8
    min_window_days: int = 45
    # horaires testés (heure LOCALE de l'utilisateur, entrées seulement) : (nom, début, fin) ; None = 24h/24
    sessions: tuple = (("24h/24", None, None), ("8h-17h", 8, 17), ("8h-13h", 8, 13))
    genies: bool = True                # les 2 génies (Einstein, Hawking) dans chaque recherche
    beat_bh: bool = False              # exiger que chaque stratégie batte le buy & hold sur la période de validation
    min_bars: int = 1000               # bougies minimum par case (400 en mode « période récente »)
    server_offset: float = 7.0         # heure du serveur MT5 - heure locale (FTMO vs Québec/New York : 7 h)
    seed: int = 7
    comptes: bool = True               # construire aussi le compte perso et le compte financé (comptes.py)
    conseil: bool = True               # le Conseil : les meilleures stratégies de chaque case votent ensemble
    genie_generations: int = 12        # générations d'évolution des formules des génies
    avocat: bool = True                # l'avocat du diable : la même recherche sur des prix mélangés au hasard
    avocat_cases: int = 2              # nombre de cases (marché × timeframe) rejouées par l'avocat du diable


def _fmt(v, f="{:.1f}"):
    try:
        return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f.format(v)
    except (TypeError, ValueError):
        return str(v)


def _txt(v) -> str:
    return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v)


def same_rule_key(sym, tf, cand: dict) -> str:
    """Clé qui ignore le nom : une invention recopiée par un autre agent reste la même stratégie."""
    sig = {k: v for k, v in cand["signal"].items() if k != "name"}
    return json.dumps([sym, tf, sig, cand["filter"], cand["risk"]], sort_keys=True)


def kind(r) -> str:
    """Type de stratégie, pour le classement."""
    equipe = _txt(r.get("equipe", ""))
    name = _txt(r.get("strategie", ""))
    if equipe == "Génies" or name.startswith("LOI "):
        return "Loi d'un génie (Einstein / Hawking)"
    if equipe == "Conseil" or name.startswith("CONSEIL"):
        return "Vote du Conseil"
    if equipe == "E":
        return "Desk quantitatif (équipe E)"
    if name.startswith("FAILLE") or equipe == "C":
        return "Faille des banques (équipe C)"
    if equipe == "D":
        return "Invention institutionnelle (équipe D)"
    if _txt(r.get("invention", "")) or name.startswith("INVENTION"):
        return "Invention (équipes A/B)"
    if equipe == "Optimiseur du catalogue":
        return "Catalogue optimisé"
    if equipe == "Directeur":
        return "Idée du Directeur"
    return "Catalogue"


class Director:
    def __init__(self, cfg: DirectorConfig, get_data: Callable[[str, str], tuple], log: Callable = print):
        self.cfg = cfg
        self.get_data = get_data  # (symbole, timeframe) -> (DataFrame OHLC, coût aller-retour en prix)
        self._data: dict = {}
        self.journal: list[str] = []
        self._print = log
        self.review_rows: list[dict] = []
        self.directives: list[str] = []
        self.combined: dict = {}
        self.scenario_rows: list[dict] = []
        self.session_rows: list[dict] = []
        self.allr = pd.DataFrame()
        self.card_ids: dict = {}
        self.multi_tf: list[dict] = []
        self.all_combos: list[dict] = []   # TOUTES les stratégies combinées construites (pour le TOP 10)

    # --------------------------------------------------------------------------- utilitaires
    def say(self, msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] DIRECTEUR | {msg}"
        self.journal.append(line)
        self._print(line)

    def data(self, sym, tf):
        if (sym, tf) not in self._data:
            self._data[(sym, tf)] = self.get_data(sym, tf)
        return self._data[(sym, tf)]

    def lab_cfg(self, intensive=False, seeds=None, bias=None, seed=0) -> LabConfig:
        c = self.cfg
        k = 2 if intensive else 1
        return LabConfig(rounds=c.rounds + (2 if intensive else 0), budget=c.budget * k, risk_pct=c.lab_risk_pct,
                         seed=c.seed + seed, ftmo=c.ftmo, invent_generations=c.invent_generations * k,
                         invent_attempts=3 + (2 if intensive else 0), seeds=seeds or [], invent_bias=bias or [],
                         catalog=c.catalog, bank_teams=c.bank_teams, beat_bh=c.beat_bh, bh_risk_pct=c.risk_pct,
                         genies=c.genies, genie_generations=c.genie_generations + (8 if intensive else 0), conseil=c.conseil)

    def run_cell(self, sym, tf, lab_cfg: LabConfig, why: str):
        try:
            df, cost = self.data(sym, tf)
        except Exception as exc:
            self.say(f"{sym} {tf} : pas de données ({exc}), case ignorée")
            return None
        if len(df) < self.cfg.min_bars:
            self.say(f"{sym} {tf} : seulement {len(df)} bougies, case ignorée (il en faut {self.cfg.min_bars})")
            return None
        self.say(f"{sym} {tf} : {why}")
        return run_lab(df, cost, lab_cfg, f"{sym}_{tf}", self.cfg.out / f"{sym}_{tf}")

    def live_analysis(self):
        """Les trades du paper trading qui tourne (le vrai test, sur des prix jamais vus) : meilleurs setups du
        direct et meilleure combinaison des stratégies qui tournent."""
        try:
            from .direct import run_direct
            self.direct = run_direct(self.cfg.out, self.cfg.ftmo, self.cfg.risk_pct, self.cfg.day_budget,
                                     self.cfg.total_budget, log=self.say)
        except Exception as exc:
            self.say(f"Analyse du direct impossible : {exc}")
            self.direct = {}

    def run_genies_cell(self, sym, tf, genies=True, conseil=False):
        """Case déjà recherchée : seuls les nouveaux employés y travaillent (Einstein et Hawking, le Conseil) ;
        leurs stratégies s'ajoutent au classement."""
        from .lab import run_newcomers
        try:
            df, cost = self.data(sym, tf)
        except Exception as exc:
            self.say(f"{sym} {tf} : pas de données pour les génies ({exc})")
            return
        if len(df) < self.cfg.min_bars:
            return
        who = " et ".join(x for x, on in (("Einstein et Hawking", genies), ("le Conseil", conseil)) if on)
        self.say(f"{sym} {tf} : nouveaux employés sur cette case : {who} s'y mettent (le travail des agents est gardé)")
        g = run_newcomers(df, cost, self.lab_cfg(), f"{sym}_{tf}", self.cfg.out / f"{sym}_{tf}", genies, conseil)
        n_ok = int(g["verdict"].eq("APPROUVÉ").sum()) if len(g) else 0
        self.say(f"{sym} {tf} : {len(g)} stratégies des nouveaux employés, {n_ok} validées hors-échantillon")

    @staticmethod
    def _version(path: Path) -> int:
        try:
            b = pd.read_csv(path, usecols=["version_calcul"], nrows=1)
            return int(b["version_calcul"].iloc[0])
        except Exception:
            return 0

    @staticmethod
    def _covered_years(path: Path) -> float | None:
        try:
            b = pd.read_csv(path, usecols=["donnees_debut", "donnees_fin"], nrows=1)
            a, z = pd.Timestamp(b["donnees_debut"].iloc[0]), pd.Timestamp(b["donnees_fin"].iloc[0])
            return (z - a).days / 365.25
        except Exception:
            return None

    # --------------------------------------------------------------------------- 1. passe 1
    def pass1(self):
        lv = self.levels()
        self.say(f"Mission : {self.cfg.ftmo.label()} ; risque par trade essayé : {', '.join(f'{x:g}' for x in lv)} % ; "
                 f"perte possible max {self.cfg.day_budget:g} % par jour ; "
                 f"{len(self.cfg.symbols)} marchés × {len(self.cfg.timeframes)} timeframes")
        for sym in self.cfg.symbols:
            for tf in self.cfg.timeframes:
                path = self.cfg.out / f"{sym}_{tf}" / "classement.csv"
                if path.exists() and self.cfg.reuse:
                    need = self.cfg.years or DEFAULT_YEARS.get(tf, 5)
                    got = self._covered_years(path)
                    if self._version(path) < LAB_VERSION:
                        self.say(f"{sym} {tf} : ancienne recherche faite avec l'ancienne méthode de calcul des coûts, "
                                 "je la fais refaire")
                    elif got is not None and got >= need * 0.9:
                        self.say(f"{sym} {tf} : je reprends le travail déjà fait par les chefs ({got:.1f} ans testés)")
                        g_new = self.cfg.genies and not (path.parent / "genies_fait.txt").exists()
                        k_new = self.cfg.conseil and not (path.parent / "conseil_fait.txt").exists()
                        if g_new or k_new:
                            self.run_genies_cell(sym, tf, g_new, k_new)
                        continue
                    else:
                        self.say(f"{sym} {tf} : l'ancienne recherche ne couvrait que "
                                 f"{'une période inconnue' if got is None else f'{got:.1f} ans'} ; "
                                 f"j'exige {need:g} ans, je la fais refaire")
                self.run_cell(sym, tf, self.lab_cfg(), "je confie la case aux 2 chefs et à leurs 10 agents")

    # --------------------------------------------------------------------------- 2. revue
    def review(self) -> pd.DataFrame:
        allr = build_comparison(self.cfg.out, self.cfg.capital, self.cfg.ftmo, self.cfg.lab_risk_pct)
        if not len(allr):
            self.say("Aucun résultat à examiner.")
            return allr
        allr = allr[allr["symbole"].isin(self.cfg.symbols) & allr["timeframe"].isin(self.cfg.timeframes)]
        self.allr = allr
        self.review_rows = []
        for sym in self.cfg.symbols:
            for tf in self.cfg.timeframes:
                cell = allr[(allr["symbole"] == sym) & (allr["timeframe"] == tf)]
                ok = cell[cell["_ok"]]
                inv = cell[cell.get("invention", pd.Series("", index=cell.index)).fillna("") != ""] \
                    if "invention" in cell else cell.iloc[0:0]
                row = {"symbole": sym, "timeframe": tf, "testee": bool(len(cell)), "validees": int(len(ok)),
                       "meilleure_ftmo": float(ok["ftmo_pass"].max()) if len(ok) else float("nan"),
                       "meilleur_gain_mois": float(ok["gain_mois_pct"].max()) if len(ok) else float("nan"),
                       "inventions": int(len(inv)), "inventions_validees": int((inv["verdict"] == "APPROUVÉ").sum())}
                if not len(cell):
                    row["note"] = "non testée"
                elif not len(ok):
                    row["note"] = "À REFAIRE : rien de validé"
                elif row["meilleure_ftmo"] < 50:
                    row["note"] = "faible pour FTMO"
                else:
                    row["note"] = "bon"
                self.review_rows.append(row)
        ok = allr[allr["_ok"]]
        self.say(f"Revue : {len(ok)} stratégies validées au total ; "
                 f"{sum(r['validees'] > 0 for r in self.review_rows)}/{len(self.review_rows)} cases ont au moins "
                 f"une stratégie validée")
        if len(ok):
            fam = ok["strategie"].str.split("(").str[0].str.split(" :").str[0].value_counts().head(5)
            self.say("Ce qui marche le mieux : " + ", ".join(f"{k} ({v})" for k, v in fam.items()))
        inv_all = allr[allr.get("invention", pd.Series("", index=allr.index)).fillna("") != ""] \
            if "invention" in allr else allr.iloc[0:0]
        if len(inv_all):
            by_agent = inv_all.groupby("invention")["verdict"].apply(lambda v: (v == "APPROUVÉ").sum())
            stars = [a for a, n in by_agent.items() if n > 0]
            lazy = [a for a, n in by_agent.items() if n == 0]
            if stars:
                self.say("Bravo aux inventeurs : " + ", ".join(f"{a.strip()} ({by_agent[a]} validée(s))" for a in stars))
            if lazy:
                self.say("Je veux mieux de : " + ", ".join(a.strip() for a in lazy)
                         + " -> plus de générations et mes pistes d'indicateurs au prochain passage")
        return allr

    # --------------------------------------------------------------------------- 3. directives
    def ideas(self, allr: pd.DataFrame, exclude=None, n=12) -> tuple[list, list]:
        """Idées du Directeur : stratégies validées ailleurs + indicateurs des inventions validées."""
        ok = allr[allr["_ok"]].sort_values(["ftmo_pass", "gain_mois_pct"], ascending=False)
        seeds, seen = [], set()
        for r in ok.itertuples():
            if exclude and (r.symbole, r.timeframe) == exclude:
                continue
            c = json.loads(r.candidate)
            k = candidate_key(c)
            if k not in seen:
                seen.add(k)
                seeds.append(c)
            if len(seeds) >= n:
                break
        bias = []
        for r in ok.itertuples():
            sig = json.loads(r.candidate)["signal"]
            if sig.get("type") == "rule":
                for c in [sig["trigger"]] + sig.get("filters", []):
                    if c["f"] not in bias:
                        bias.append(c["f"])
        return seeds, bias

    def pass2(self, allr: pd.DataFrame):
        weak = [r for r in self.review_rows if r["testee"] and r["validees"] == 0]
        if not weak:
            self.say("Toutes les cases testées ont des stratégies validées : pas de deuxième passe nécessaire.")
            return
        for r in weak:
            seeds, bias = self.ideas(allr, exclude=(r["symbole"], r["timeframe"]))
            msg = (f"rien de validé. Deuxième passe INTENSIVE : budget ×2, "
                   f"+2 rounds, générations d'inventions ×2, {len(seeds)} idées transférées")
            if bias:
                msg += f", pistes d'indicateurs pour les inventeurs : {', '.join(bias[:6])}"
            self.directives.append(f"{r['symbole']} {r['timeframe']} : {msg}")
            self.run_cell(r["symbole"], r["timeframe"], self.lab_cfg(True, seeds, bias, seed=101), msg)

    # --------------------------------------------------------------------------- 4. stratégie combinée
    def paused_by_controller(self) -> list[dict]:
        """Stratégies mises en pause par le contrôleur de qualité du paper trading (results/paper*/controle_qualite.json)."""
        out = []
        for path in sorted(self.cfg.out.glob("paper*/controle_qualite.json")):
            try:
                rows = json.loads(path.read_text(encoding="utf-8")).get("strategies", [])
            except (OSError, ValueError):
                continue
            out += [r for r in rows if r.get("en_pause")]
        return out

    @staticmethod
    def _same_symbol(ours: str, paper: str) -> bool:
        from .data import SYMBOL_ALIASES
        names = [ours.upper()] + [a.upper() for a in SYMBOL_ALIASES.get(ours.upper(), [])]
        return any(paper.upper().startswith(n) for n in names)

    def _pool(self, allr: pd.DataFrame):
        """Stratégies validées (sans doublons) + leurs variantes de R:R qui restent gagnantes hors-échantillon."""
        ok = allr[allr["_ok"] & allr["oos_debut"].notna()].copy()
        self.trial_mode = False
        if not len(ok):  # rien de validé : on construit quand même une combinée « à l'essai », pour le paper trading
            ok = allr[allr["verdict"].astype(str).str.startswith("À L'ESSAI") & allr["oos_debut"].notna()].copy()
            if len(ok):
                self.trial_mode = True
                self.say(f"Aucune stratégie validée : je construis une stratégie combinée « À L'ESSAI » avec les "
                         f"{len(ok)} stratégies non validées mais gagnantes hors-échantillon. PAPER TRADING SEULEMENT, "
                         "pas de bot tant qu'elle n'a pas fait ses preuves en direct.")
        trades, windows, info = {}, {}, {}
        for label in (ok["symbole"] + "_" + ok["timeframe"]).unique():
            path = self.cfg.out / label / "trades_oos.csv"
            if not path.exists():
                continue
            t = pd.read_csv(path)
            t["entry_time"], t["exit_time"] = to_dt(t["entry_time"]), to_dt(t["exit_time"])
            for k, g in t.groupby("key"):
                trades[f"{label}|{k}"] = g[[c for c in ("entry_time", "exit_time", "r", "side") if c in g.columns]
                                           ].reset_index(drop=True)
        seen_rules = set()
        for r in ok.itertuples():
            cand = json.loads(r.candidate)
            key = f"{r.symbole}_{r.timeframe}|{candidate_key(cand)}"
            sig = {k: v for k, v in cand["signal"].items() if k != "name"}  # une invention recopiée = même règle
            same = (r.symbole, r.timeframe, json.dumps(sig, sort_keys=True), cand["filter"],
                    json.dumps(cand["risk"], sort_keys=True))
            if key in trades and key not in info and same not in seen_rules:
                seen_rules.add(same)
                windows[key] = (pd.Timestamp(r.oos_debut), pd.Timestamp(r.oos_fin))
                info[key] = {"symbole": r.symbole, "timeframe": r.timeframe, "candidate": cand,
                             "strategie": r.strategie, "risque": r.risque, "seule_ftmo": r.ftmo_pass, "variante": False,
                             "attendu_r": None if pd.isna(r.avgR_oos) else float(r.avgR_oos),
                             "attendu_wr": None if pd.isna(r.wr_oos) else float(r.wr_oos)}
        paused = self.paused_by_controller()
        if paused:
            for key in list(info):
                x = info[key]
                ck = candidate_key(x["candidate"])
                hit = next((p for p in paused if p.get("timeframe") == x["timeframe"]
                            and self._same_symbol(x["symbole"], str(p.get("symbole", "")))
                            and candidate_key(p["candidate"]) == ck), None)
                if hit:
                    del info[key]
                    self.say(f"Contrôleur de qualité : {x['symbole']} {x['timeframe']} « {x['strategie']} » est en pause "
                             f"en paper trading ({hit.get('raison', '')}) -> écartée de la stratégie combinée")
        if self.cfg.rr_variants:
            n_var = 0
            self.mgmt_rows = []
            for key in list(info):
                base = info[key]
                c = base["candidate"]
                try:
                    df, cost = self.data(base["symbole"], base["timeframe"])
                except Exception:
                    continue
                lo, hi = windows[key]
                sig = apply_filter(df, compute_signal(df, c["signal"]), c["filter"])
                mask = (df.index >= lo) & (df.index <= hi)
                if mask.sum() < 50:
                    continue
                # variantes : chaque R:R (même gestion), puis chaque GESTION des trades (même R:R)
                tries = [("rr", rr) for rr in RR_LEVELS
                         if rr != c["risk"]["rr"] and not (rr is None and c["risk"]["management"] == "breakeven")]
                tries += [("management", m) for m in MANAGEMENT
                          if m != c["risk"]["management"] and not (c["risk"]["rr"] is None and m == "breakeven")]
                row = {"symbole": base["symbole"], "timeframe": base["timeframe"], "strategie": base["strategie"],
                       "rr": RiskConfig(**c["risk"]).label().split("|")[1].strip(), "modes": {}}
                if base.get("attendu_r") is not None:
                    row["modes"][c["risk"]["management"]] = {"r": base["attendu_r"], "wr": base.get("attendu_wr")}
                for what, val in tries:
                    v = copy.deepcopy(c)
                    v["risk"][what] = val
                    res, tr = run_backtest(df[mask], sig[mask], RiskConfig(**v["risk"]), cost=cost,
                                           risk_pct=self.cfg.lab_risk_pct, return_trades=True)
                    if what == "management":
                        row["modes"][val] = {"r": float(res.avg_r), "wr": float(res.win_rate), "n": int(res.trades)}
                    if res.trades < 20 or res.avg_r <= 0 or res.profit_factor < 1.1:
                        continue
                    k2 = f"{base['symbole']}_{base['timeframe']}|{candidate_key(v)}"
                    if k2 in info:
                        continue
                    trades[k2] = tr[["entry_time", "exit_time", "r", "side"]].reset_index(drop=True)
                    windows[k2] = (lo, hi)
                    solo = simulate(daily_table(trades[k2], self.cfg.lab_risk_pct, lo, hi), self.cfg.ftmo, 1500, seed=0)
                    info[k2] = {**base, "candidate": v, "risque": RiskConfig(**v["risk"]).label(),
                                "seule_ftmo": solo["ftmo_pass"], "variante": True,
                                "attendu_r": float(res.avg_r), "attendu_wr": float(res.win_rate)}
                    n_var += 1
                if row["modes"]:
                    row["meilleure"] = max(row["modes"], key=lambda m: row["modes"][m]["r"])
                    row["actuelle"] = c["risk"]["management"]
                    self.mgmt_rows.append(row)
            self.say(f"Variantes de R:R et de gestion des trades : {n_var} variantes restent gagnantes "
                     "hors-échantillon et rejoignent le choix")
            if self.mgmt_rows:
                from collections import Counter
                wins = Counter(r["meilleure"] for r in self.mgmt_rows)
                self.say("Gestion des trades qui marche le mieux : " + ", ".join(
                    f"{MGMT_FR.get(m, m)} ({n} stratégies)" for m, n in wins.most_common()))
                (self.cfg.out / "gestion_trades.json").write_text(
                    json.dumps(self.mgmt_rows, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        if self.cfg.horaires:
            self._hour_variants(trades, windows, info)
        return trades, windows, info

    def _hour_variants(self, trades, windows, info):
        """MEILLEURES HEURES : pour chaque stratégie, la plage horaire où elle trade le mieux (choisie sur 60 % de
        ses trades, contrôlée sur les 40 % suivants). Chaque plage confirmée devient une variante « horaire » que
        le Chef des combinaisons peut mélanger avec des stratégies qui tradent à d'autres heures."""
        from .horaires import best_window, horaire, hours_of, in_window
        self.hour_rows, n = [], 0
        for key in [k for k in list(info) if not info[k].get("horaire")]:
            tr = trades.get(key)
            if tr is None or not len(tr):
                continue
            try:
                bw = best_window(tr["entry_time"], tr["r"])
            except Exception:
                continue
            if not bw:
                continue
            base = info[key]
            self.hour_rows.append({"symbole": base["symbole"], "timeframe": base["timeframe"],
                                   "strategie": base["strategie"], "risque": base["risque"], **bw})
            if not bw["ok"]:
                continue
            a, b = bw["debut"], bw["fin"]
            k2 = f"{key}|h{a:g}-{b:g}"
            sub = tr[in_window(hours_of(tr["entry_time"]), a, b)].reset_index(drop=True)
            lo, hi = windows[key]
            trades[k2], windows[k2] = sub, (lo, hi)
            solo = simulate(daily_table(sub, self.cfg.lab_risk_pct, lo, hi), self.cfg.ftmo, 1500, seed=0)
            info[k2] = {**base, "horaire": horaire(a, b), "seule_ftmo": solo["ftmo_pass"],
                        "strategie": f"{base['strategie']} | heures {bw['nom']}",
                        "attendu_r": bw["r_moyen_plage"], "variante": True}
            n += 1
        self.hour_rows.sort(key=lambda r: -(r["r_moyen_plage"] - r["r_moyen_24h"]))
        (self.cfg.out / "heures_strategies.json").write_text(
            json.dumps(self.hour_rows, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        self.say(f"Meilleures heures : {len(self.hour_rows)} stratégies ont une plage horaire nettement meilleure, "
                 f"{n} confirmées sur une période que le choix n'a pas vue -> variantes « horaires » ajoutées au choix "
                 "(chacune ne trade que dans SES heures)")

    def levels(self) -> list[float]:
        """Niveaux de risque autorisés : <= risque max, et un seul stop (+10 % de frais) doit tenir dans le budget du jour."""
        return sorted(l for l in self.cfg.risk_levels if l <= self.cfg.risk_pct and l * 1.1 <= self.cfg.day_budget + 1e-9)

    def _daily(self, keys, weights, day_stop, max_open, trades, windows, max_corr=None, day_lock=None, vol=None):
        """Journées de la combinaison (P&L % et pire moment) sur la période commune, règles de risque appliquées."""
        lo = max(windows[k][0] for k in keys)
        hi = min(windows[k][1] for k in keys)
        if (hi - lo).days < self.cfg.min_window_days:
            return None
        parts = []
        for k in keys:
            t = _with_corr(k.split("|")[0].rsplit("_", 1)[0], trades[k].assign(w=weights[k]))
            e = pd.to_datetime(t["entry_time"].astype(str), format="mixed")
            x = pd.to_datetime(t["exit_time"].astype(str), format="mixed")
            parts.append(t[(e >= lo) & (x <= hi)])
        merged = apply_risk_rules(pd.concat(parts, ignore_index=True), day_stop, max_open, self.cfg.risk_pct,
                                  day_budget=self.cfg.day_budget, max_corr=max_corr, day_lock=day_lock,
                                  vol_target=vol)
        return daily_table(merged, self.cfg.risk_pct, lo, hi), merged, lo, hi

    def _summary(self, res) -> str:
        return (f"réussite {res['ftmo_pass']:.1f} %, +{self.cfg.ftmo.target1:g} % en "
                f"~{_fmt(res['ftmo_jours_p1'], '{:.0f}')} jours, échec {_fmt(res['ftmo_echec_p1'])} %")

    def _eval(self, keys, weights, day_stop, max_open, trades, windows, n=1500, max_corr=None, pilot=None,
              day_lock=None, vol=None):
        got = self._daily(keys, weights, day_stop, max_open, trades, windows, max_corr, day_lock, vol)
        if got is None:
            return None
        daily, merged, lo, hi = got
        res = simulate(daily, self.cfg.ftmo, n, seed=0, pilot=pilot)
        res["fenetre"] = (str(lo.date()), str(hi.date()))
        res["trades"] = int(len(merged))
        res["trades_mois"] = _per_month(len(merged), lo, hi)
        res.update(holding_stats(merged))
        res.update(_drawdown(daily), jours_periode=int((hi - lo).days))
        res["pire_jour"] = float(daily["worst"].min()) if len(daily) else 0.0
        res["rendement_pct"] = float(daily["pnl"].sum()) if len(daily) else 0.0
        res["jours_attendus"] = expected_days(res)
        c = count_challenges(daily, self.cfg.ftmo, pilot)
        res["challenges_oos"] = {k: c[k] for k in ("reussis", "rates", "jours_moyens")}
        return res

    def history_challenges(self, comb: dict) -> dict:
        """Combien de challenges la stratégie combinée aurait réussis / ratés en les enchaînant sur TOUT l'historique
        commun à ses composants (mêmes règles de risque que le paper trading)."""
        parts, lo, hi = [], None, None
        for c in comb["composants"]:
            try:
                df, cost = self.data(c["symbole"], c["timeframe"])
            except Exception:
                continue
            cand = c["candidate"]
            sig = apply_filter(df, compute_signal(df, cand["signal"]), cand["filter"])
            _, tr = run_backtest(df, sig, RiskConfig(**cand["risk"]), cost=cost, risk_pct=c["risk_pct"],
                                 return_trades=True)
            tr = filter_trades(tr, c.get("horaire"))   # composant « horaire » : seulement dans SES heures
            lo = df.index[0] if lo is None else max(lo, df.index[0])
            hi = df.index[-1] if hi is None else min(hi, df.index[-1])
            if len(tr):
                parts.append(_with_corr(c["symbole"], tr[["entry_time", "exit_time", "r", "side"]].assign(w=c["risk_pct"])))
        if not parts or lo is None or hi <= lo:
            return {}
        t = pd.concat(parts, ignore_index=True)
        t = t[(to_dt(t["entry_time"]) >= lo) & (to_dt(t["exit_time"]) <= hi)]
        sess = comb.get("horaire") or {}
        if sess.get("debut") is not None:
            t = t[self.in_session(t["entry_time"], sess["debut"], sess["fin"])]
        rules = comb["regles"]
        t = apply_risk_rules(t, rules.get("day_stop"), rules.get("max_open"), self.cfg.risk_pct,
                             day_budget=rules.get("day_budget"), max_corr=rules.get("max_correles"),
                             day_lock=rules.get("frein"), vol_target=rules.get("volatilite"))
        c = count_challenges(daily_table(t, self.cfg.risk_pct, lo, hi), self.cfg.ftmo, rules.get("pilote"))
        c["periode"] = f"{lo:%Y-%m-%d} → {hi:%Y-%m-%d}"
        return c

    def _better(self, a, b) -> bool:
        """LE BUT : avoir un challenge RÉUSSI le plus vite possible.
        1. échecs sous le seuil toléré (limite de perte touchée) ;
        2. le moins de jours attendus pour réussir, reprises comprises (jours médians / probabilité de réussite) ;
        3. à vitesse égale (±5 %), plus de réussite, puis moins d'échecs."""
        if a is None or math.isnan(a.get("ftmo_pass", float("nan"))):
            return False
        if b is None:
            return True
        a_ok = a.get("ftmo_echec_p1", 100) <= self.cfg.max_fail
        b_ok = b.get("ftmo_echec_p1", 100) <= self.cfg.max_fail
        if a_ok != b_ok:
            return a_ok
        ea, eb = expected_days(a), expected_days(b)
        if ea < eb * 0.95:
            return True
        if ea > eb * 1.05:
            return False
        if a["ftmo_pass"] > b["ftmo_pass"] + 0.5:
            return True
        if a["ftmo_pass"] < b["ftmo_pass"] - 0.5:
            return False
        return a.get("ftmo_echec_p1", 100) < b.get("ftmo_echec_p1", 100) - 0.5

    PILOTS = ([{"type": "dd", "seuil": s, "facteur": f} for s in (1.5, 3.0, 5.0) for f in (0.5, 0.75)]
              + [{"type": "jour", "facteur": f} for f in (0.5, 0.75)]
              + [{"type": "cible", "seuil": s, "facteur": f} for s in (2.0, 4.0) for f in (0.5,)])

    def _tune_pilot(self, keys, weights, rules, best, trades, windows, info, say):
        """Pilote de risque du challenge : baisser le risque quand ça va mal (ou près du but). Moins d'échecs ->
        on peut remonter le risque des composants -> challenge réussi plus vite. Gardé seulement s'il aide."""
        found = None
        for pl in self.PILOTS:
            res = self._eval(keys, weights, rules["day_stop"], rules["max_open"], trades, windows,
                             max_corr=rules.get("max_correles"), pilot=pl)
            if self._better(res, best):
                best, found = res, pl
        if found is None:
            return best, rules, weights
        rules = {**rules, "pilote": found}
        for k in list(keys):  # avec le pilote, chaque composant peut-il prendre plus de risque ?
            for w in sorted(self.levels(), reverse=True):
                if w <= weights[k]:
                    break
                trial = {**weights, k: round(w, 4)}
                res = self._eval(keys, trial, rules["day_stop"], rules["max_open"], trades, windows,
                                 max_corr=rules.get("max_correles"), pilot=found)
                if self._better(res, best):
                    best, weights = res, trial
                    break
        say(f"Pilote de risque du challenge : {pilot_text(found)} -> challenge réussi en "
            f"~{_fmt(expected_days(best), '{:.0f}')} jours attendus, échec {_fmt(best['ftmo_echec_p1'])} %")
        return best, rules, weights

    LOCKS = [{"seuil": s, "facteur": f} for s in (1.5, 2.0, 3.0, 4.0, 5.0) for f in (0.0, 0.5)]

    def _tune_lock(self, keys, weights, rules, best, trades, windows, say):
        """Frein de bonne journée : après +X % dans la journée, plus de trade (ou risque réduit) jusqu'au lendemain.
        Protège la règle du meilleur jour (50 %) et évite de redonner les gains. Gardé seulement s'il aide."""
        found = None
        for lk in self.LOCKS:
            res = self._eval(keys, weights, rules["day_stop"], rules["max_open"], trades, windows,
                             max_corr=rules.get("max_correles"), pilot=rules.get("pilote"), day_lock=lk,
                             vol=rules.get("volatilite"))
            if self._better(res, best):
                best, found = res, lk
        if found is None:
            return best, rules
        say(f"Frein de bonne journée : {lock_text(found)} -> {self._summary(best)}")
        return best, {**rules, "frein": found}

    VOLS = [{"cible": c, "jours": 20} for c in (0.5, 0.75, 1.0, 1.5)]

    def _tune_vol(self, keys, weights, rules, best, trades, windows, say):
        """DIMENSIONNEMENT PAR VOLATILITÉ (comme les fonds de tendance) : quand les résultats journaliers deviennent
        trop nerveux, le risque de chaque trade baisse. Gardé seulement s'il aide."""
        found = None
        for v in self.VOLS:
            res = self._eval(keys, weights, rules["day_stop"], rules["max_open"], trades, windows,
                             max_corr=rules.get("max_correles"), pilot=rules.get("pilote"), day_lock=rules.get("frein"),
                             vol=v)
            if self._better(res, best):
                best, found = res, v
        if found is None:
            return best, rules
        say(f"Dimensionnement par volatilité : {vol_text(found)} -> {self._summary(best)}")
        return best, {**rules, "volatilite": found}

    def build_combined(self, allr: pd.DataFrame, pool=None, quiet=False, start=None) -> dict:
        """start = (composants, risques, règles) : on part d'une combinaison existante au lieu de partir de zéro."""
        trades, windows, info = pool or self._pool(allr)
        say = (lambda m: None) if quiet else self.say
        if not trades:
            say("Pas encore de stratégie validée avec des trades : impossible de construire la stratégie combinée.")
            return {}
        def solo(k):
            v = info[k]["seule_ftmo"]
            return -1.0 if v is None or v != v else float(v)
        ranked = sorted(info, key=solo, reverse=True)
        cand = ranked[:40]
        # + les 20 meilleures variantes « horaires » : seules elles tradent moins (donc moins bien classées), mais
        # chacune dans SES heures, elles se complètent dans une combinée
        cand += [k for k in ranked if info[k].get("horaire") and k not in cand][:20]
        R = self.cfg.risk_pct
        say(f"Je construis la stratégie combinée à partir de {len(cand)} stratégies validées "
                 f"(tous marchés et timeframes).")
        keys: list[str] = []
        weights: dict = {}
        rules = {"day_stop": None, "max_open": None, "max_correles": None}
        best = None
        if start:
            keys = [k for k in start[0] if k in info]
            weights = {k: float(start[1][k]) for k in keys}
            rules = {"day_stop": None, "max_open": None, "max_correles": None,
                     **{k: v for k, v in (start[2] or {}).items() if k in ("day_stop", "max_open", "max_correles")}}
            best = self._eval(keys, weights, rules["day_stop"], rules["max_open"], trades, windows,
                              max_corr=rules.get("max_correles")) if keys else None
        day_stops = [None] + [x for x in (0.5, 0.75) if x < self.cfg.day_budget]
        for rnd in range(3):
            # a) ajouter les composants qui améliorent le tout
            improved = True
            while improved and len(keys) < self.cfg.max_components:
                improved = False
                pick, pick_res = None, None
                pick_w = None
                for k in cand:
                    if k in keys:
                        continue
                    top = self.levels()[-1] if self.levels() else R
                    for lvl in sorted({min(0.5, top), top}):  # prudent, puis au risque max autorisé
                        w = {**weights, k: lvl}
                        res = self._eval(keys + [k], w, rules["day_stop"], rules["max_open"], trades, windows,
                                     max_corr=rules.get("max_correles"), pilot=rules.get("pilote"), day_lock=rules.get("frein"), vol=rules.get("volatilite"))
                        if self._better(res, best) and (pick_res is None or self._better(res, pick_res)):
                            pick, pick_res, pick_w = k, res, lvl
                if pick:
                    keys.append(pick)
                    weights[pick] = pick_w
                    best = pick_res
                    improved = True
                    say(f"+ composant {len(keys)} : {info[pick]['symbole']} {info[pick]['timeframe']} | "
                             f"{info[pick]['strategie']} à {pick_w:g} %/trade -> {self._summary(best)}")
            if not keys:
                break
            # b) régler l'arrêt journalier et le nombre max de positions
            before = best
            for ds in day_stops:
                for mo in (None, 2, 3, 4, 6):
                    if mo is not None and mo >= len(keys) + 1:
                        continue
                    # marchés corrélés (NASDAQ/US30/GER40, EURUSD/GBPUSD/USDJPY) : combien de positions dans le même sens ?
                    for mc in (None, 1, 2):
                        res = self._eval(keys, weights, ds, mo, trades, windows, max_corr=mc)
                        if self._better(res, best):
                            best, rules = res, {"day_stop": ds, "max_open": mo, "max_correles": mc}
            # c) régler le risque de chaque composant (jamais au-dessus du risque max)
            for k in list(keys):
                for w in sorted(self.levels(), reverse=True):
                    trial = {**weights, k: round(w, 4)}
                    res = self._eval(keys, trial, rules["day_stop"], rules["max_open"], trades, windows,
                                     max_corr=rules.get("max_correles"), pilot=rules.get("pilote"), day_lock=rules.get("frein"), vol=rules.get("volatilite"))
                    if self._better(res, best):
                        best, weights = res, trial
            # d) retirer ce qui ne sert plus
            for k in list(keys):
                if len(keys) <= 1:
                    break
                rest = [x for x in keys if x != k]
                res = self._eval(rest, weights, rules["day_stop"], rules["max_open"], trades, windows,
                                     max_corr=rules.get("max_correles"), pilot=rules.get("pilote"), day_lock=rules.get("frein"), vol=rules.get("volatilite"))
                if res and not self._better(best, res):
                    keys.remove(k)
                    weights.pop(k, None)
                    best = res
                    say(f"- je retire {info[k]['strategie']} ({info[k]['symbole']} {info[k]['timeframe']}) : "
                             f"elle n'apportait plus rien")
            if best is before:
                break
            ds_txt = "aucun" if rules["day_stop"] is None else f"-{rules['day_stop']:g} %"
            say(f"Réglages après le tour {rnd + 1} : arrêt journalier {ds_txt}, "
                     f"max positions {rules['max_open'] or 'illimité'}, max marchés corrélés dans le même sens "
                     f"{rules.get('max_correles') or 'illimité'} -> {self._summary(best)}")
        if keys:
            best, rules, weights = self._tune_pilot(keys, weights, rules, best, trades, windows, info, say)
            best, rules = self._tune_lock(keys, weights, rules, best, trades, windows, say)
            best, rules = self._tune_vol(keys, weights, rules, best, trades, windows, say)
        if not keys:
            return {}
        final = self._eval(keys, weights, rules["day_stop"], rules["max_open"], trades, windows, n=5000,
                           max_corr=rules.get("max_correles"), pilot=rules.get("pilote"), day_lock=rules.get("frein"), vol=rules.get("volatilite"))
        comps = [{"symbole": info[k]["symbole"], "timeframe": info[k]["timeframe"], "candidate": info[k]["candidate"],
                  "strategie": info[k]["strategie"], "risque_config": info[k]["risque"], "risk_pct": weights[k],
                  "horaire": info[k].get("horaire"),
                  "reussite_seule": info[k]["seule_ftmo"], "variante_rr": info[k].get("variante", False),
                  "r_moyen_attendu": info[k].get("attendu_r"), "wr_attendu": info[k].get("attendu_wr"),
                  "trades_mois": _comp_tpm(trades, windows, k)}
                 for k in keys]
        rules = {**rules, "day_budget": self.cfg.day_budget, "total_budget": self.cfg.total_budget}
        self._last_setup = (list(keys), dict(weights), dict(rules))
        combined = {"nom": "Stratégie combinée du Directeur", "ftmo_regles": self.cfg.ftmo.label(),
                    "risque_max_par_trade": R, "regles": rules, "resultat": final, "composants": comps,
                    "cree_le": time.strftime("%Y-%m-%d %H:%M")}
        say(f"STRATÉGIE COMBINÉE : {len(keys)} composants, {self._summary(final)}, "
            f"pire journée {final['pire_jour']:.2f} %")
        return combined

    def in_session(self, times, start, end) -> np.ndarray:
        """Vrai pour les heures (serveur MT5) qui tombent dans l'horaire [début, fin[ en heure LOCALE."""
        t = to_dt(times) - pd.Timedelta(hours=self.cfg.server_offset)
        if start is None:
            return np.ones(len(t), dtype=bool)
        h = t.hour + t.minute / 60.0
        return np.asarray((h >= start) & (h < end))

    def _session_pool(self, pool, start, end):
        trades, windows, info = pool
        if start is None:
            return pool
        kept = {k: t[self.in_session(t["entry_time"], start, end)].reset_index(drop=True) for k, t in trades.items()}
        kept = {k: t for k, t in kept.items() if len(t) >= 5}
        return kept, windows, {k: v for k, v in info.items() if k in kept}

    def scenarios(self, allr: pd.DataFrame) -> dict:
        """Pour chaque horaire (24h/24, 8h-17h, 8h-13h...) : une stratégie combinée par scénario de perte max par
        jour. On garde, pour chaque horaire, celle qui passe le plus vite, puis la meilleure de tous les horaires."""
        pool0 = self._pool(allr)
        self._pool0 = pool0
        if not pool0[0]:
            self.say("Pas encore de stratégie validée avec des trades : impossible de construire la stratégie combinée.")
            return {}
        self.session_rows, all_rows, overall = [], [], None
        for name, start, end in self.cfg.sessions:
            pool = self._session_pool(pool0, start, end)
            if not pool[0]:
                self.say(f"Horaire {name} : aucune stratégie validée n'a assez de trades dans cet horaire.")
                continue
            self.say(f"===== Horaire {name} (entrées seulement {'24h/24' if start is None else f'de {start}h à {end}h'}, "
                     "heure locale) =====")
            self._cur_horaire = {"nom": name, "debut": start, "fin": end, "decalage_serveur": self.cfg.server_offset}
            best, best_b, rows = self._budget_scenarios(allr, pool)
            for r in rows:
                r["horaire"] = name
            all_rows += rows
            if not best:
                continue
            best["horaire"] = {"nom": name, "debut": start, "fin": end, "decalage_serveur": self.cfg.server_offset}
            if self.trial_mode:
                best["essai"] = True
            best["scenario_choisi"] = best_b
            best["scenarios"] = rows
            hist = self.history_challenges(best)
            if hist:
                best["challenges_historique"] = hist
            res = best["resultat"]
            self.session_rows.append({
                "horaire": name, "budget": best_b, "reussite": res["ftmo_pass"], "jours": res["ftmo_jours_p1"],
                "attendus": expected_days(res),
                "echec": res["ftmo_echec_p1"], "trades": res.get("trades"), "trades_mois": res.get("trades_mois"),
                "pire_jour": res["pire_jour"],
                "reussis_oos": res.get("challenges_oos", {}).get("reussis"),
                "rates_oos": res.get("challenges_oos", {}).get("rates"),
                "reussis_hist": hist.get("reussis") if hist else None, "rates_hist": hist.get("rates") if hist else None,
                "composants": len(best["composants"]), "fichier": f"strategie_combinee_{_slug(name)}.json"})
            (self.cfg.out / f"strategie_combinee_{_slug(name)}.json").write_text(
                json.dumps(best, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            self.say(f"Horaire {name} : challenge réussi en ~{_fmt(expected_days(res), '{:.0f}')} jours attendus, "
                     f"réussite {res['ftmo_pass']:.1f} %, +{self.cfg.ftmo.target1:g} % en "
                     f"~{_fmt(res['ftmo_jours_p1'], '{:.0f}')} jours, échec {_fmt(res['ftmo_echec_p1'])} %"
                     + (f", sur tout l'historique {hist['reussis']} challenges réussis / {hist['rates']} ratés" if hist else ""))
            if self._better(res, overall["resultat"] if overall else None):
                overall = best
        self.scenario_rows = all_rows
        if not overall:
            return {}
        overall["scenarios"] = all_rows
        overall["horaires"] = self.session_rows
        if getattr(self, "trial_mode", False):
            overall["essai"] = True
            overall["nom"] = "Stratégie combinée À L'ESSAI (non validée : paper trading seulement)"
        self.combined = overall
        (self.cfg.out / "strategie_combinee.json").write_text(
            json.dumps(overall, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        res = overall["resultat"]
        self.say(f"OBJECTIF : challenge réussi en ~{_fmt(expected_days(res), '{:.0f}')} jours de bourse attendus "
                 "(reprises comprises)")
        self.say(f"MEILLEUR CHOIX : horaire {overall['horaire']['nom']}, perte max {overall['scenario_choisi']:g} %/jour "
                 f"-> réussite {res['ftmo_pass']:.1f} %, +{self.cfg.ftmo.target1:g} % en "
                 f"~{_fmt(res['ftmo_jours_p1'], '{:.0f}')} jours de bourse")
        if len(self.session_rows) > 1:
            self.say("Comparaison des horaires : " + " | ".join(
                f"{r['horaire']} : réussi en ~{_fmt(r['attendus'], '{:.0f}')} j attendus ({_fmt(r['reussite'])} %)"
                for r in self.session_rows))
        for i, c in enumerate(overall["composants"], 1):
            self.say(f"  {i}. {c['symbole']} {c['timeframe']} | {c['strategie']} | {c['risque_config']} | "
                     f"{c['risk_pct']:g} %/trade" + (" (variante R:R)" if c.get("variante_rr") else ""))
        return overall

    def _budget_scenarios(self, allr: pd.DataFrame, pool) -> tuple:
        """Une stratégie combinée par scénario de perte max par jour ; renvoie (meilleure, budget, lignes)."""
        cap = self.cfg.day_budget
        budgets = sorted({b for b in self.cfg.day_budgets if b <= cap} | {cap})
        rows = []
        best, best_b = None, None
        setups = []  # meilleures combinaisons trouvées pour chaque budget : réessayées dans tous les scénarios
        for b in budgets:
            self.cfg.day_budget = b
            comb = self.build_combined(allr, pool, quiet=True)
            if comb:
                setups.append(self._last_setup)
        for b in budgets:
            self.cfg.day_budget = b
            comb, res = None, None
            for keys, weights, rules in setups:
                lv = self.levels()
                if not lv:
                    continue
                w = {k: min(v, lv[-1]) for k, v in weights.items()}  # chaque risque doit tenir dans le budget
                r = self._eval(keys, w, rules.get("day_stop"), rules.get("max_open"), *pool[:2], n=5000,
                               max_corr=rules.get("max_correles"), pilot=rules.get("pilote"), day_lock=rules.get("frein"), vol=rules.get("volatilite"))
                if r is not None and self._better(r, res):
                    res, comb = r, (keys, w, rules)
            if comb is None:
                continue
            keys, w, rules = comb
            trades, windows, info = pool
            comb = {"nom": "Stratégie combinée du Directeur", "ftmo_regles": self.cfg.ftmo.label(),
                    "risque_max_par_trade": self.cfg.risk_pct,
                    "regles": {**rules, "day_budget": b, "total_budget": self.cfg.total_budget}, "resultat": res,
                    "composants": [{"symbole": info[k]["symbole"], "timeframe": info[k]["timeframe"],
                                    "candidate": info[k]["candidate"], "strategie": info[k]["strategie"],
                                    "risque_config": info[k]["risque"], "risk_pct": w[k],
                                    "horaire": info[k].get("horaire"),
                                    "reussite_seule": info[k]["seule_ftmo"],
                                    "variante_rr": info[k].get("variante", False),
                                    "r_moyen_attendu": info[k].get("attendu_r"),
                                    "wr_attendu": info[k].get("attendu_wr"),
                                    "trades_mois": _comp_tpm(trades, windows, k)} for k in keys],
                    "cree_le": time.strftime("%Y-%m-%d %H:%M")}
            h = getattr(self, "_cur_horaire", None) or {"nom": "24h/24", "debut": None, "fin": None}
            self._register(f"Challenge FTMO · horaire {h['nom']} · perte max {b:g} %/jour", "passer le challenge FTMO",
                           {**comb, "horaire": h})
            rows.append({"budget": b, "reussite": res["ftmo_pass"], "jours": res["ftmo_jours_p1"],
                                       "reussis_oos": res.get("challenges_oos", {}).get("reussis"),
                                       "rates_oos": res.get("challenges_oos", {}).get("rates"),
                                       "echec": res["ftmo_echec_p1"], "pire_jour": res["pire_jour"],
                                       "composants": len(comb["composants"]), "trades_mois": res.get("trades_mois"),
                                       "risques": ", ".join(f"{c['risk_pct']:g}" for c in comb["composants"]),
                                       "rr": ", ".join(RiskConfig(**c["candidate"]["risk"]).label().split("|")[1].strip()
                                                       for c in comb["composants"])})
            self.say(f"Scénario perte max {b:g} %/jour : réussite {res['ftmo_pass']:.1f} %, +{self.cfg.ftmo.target1:g} % "
                     f"en ~{_fmt(res['ftmo_jours_p1'], '{:.0f}')} jours, échec {_fmt(res['ftmo_echec_p1'])} %, "
                     f"pire journée {res['pire_jour']:.2f} %, {len(comb['composants'])} composants")
            if self._better(res, best["resultat"] if best else None):
                best, best_b = comb, b
        self.cfg.day_budget = cap
        return best, best_b, rows

    # --------------------------------------------------------------------------- 4 bis. le Chef des combinaisons
    def _keys_of(self, comb: dict | None, info: dict) -> dict:
        out = {}
        for c in (comb or {}).get("composants", []):
            cand = c["candidate"] if isinstance(c["candidate"], dict) else json.loads(c["candidate"])
            k = f"{c['symbole']}_{c['timeframe']}|{candidate_key(cand)}"
            h = c.get("horaire") or {}
            if h.get("debut") is not None and h.get("decalage_serveur", 0) == 0:
                k += f"|h{float(h['debut']):g}-{float(h['fin']):g}"
            if k in info:
                out[k] = float(c.get("risk_pct") or min(0.5, self.cfg.risk_pct))
        return out

    def _mix_note(self, why: str):
        self.say(f"Chef des combinaisons : {why}.")
        self.combined["melanges"], self.combined["melanges_note"] = [], why
        (self.cfg.out / "strategie_combinee.json").write_text(
            json.dumps(self.combined, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    def _trades_weekend_closed(self, keys, info, windows, horaire=None) -> dict:
        """Trades des composants rejoués SANS position pendant le week-end (fermeture le vendredi soir)."""
        out = {}
        for k in keys:
            x = info[k]
            try:
                df, cost = self.data(x["symbole"], x["timeframe"])
            except Exception:
                continue
            lo, hi = windows[k]
            c = x["candidate"]
            sig = apply_filter(df, compute_signal(df, c["signal"]), c["filter"])
            mask = (df.index >= lo) & (df.index <= hi)
            _, tr = run_backtest(df[mask], sig[mask], RiskConfig(**c["risk"]), cost=cost,
                                 risk_pct=self.cfg.lab_risk_pct, return_trades=True, weekend_exit=True)
            tr = tr[["entry_time", "exit_time", "r", "side"]].reset_index(drop=True) if len(tr) else tr
            tr = filter_trades(tr, x.get("horaire")).reset_index(drop=True) if len(tr) else tr
            if horaire and horaire.get("debut") is not None and len(tr):
                tr = tr[self.in_session(tr["entry_time"], horaire["debut"], horaire["fin"])].reset_index(drop=True)
            out[k] = tr
        return out

    def account_advice(self, allr: pd.DataFrame) -> dict:
        """LE CONSEILLER DU COMPTE : FTMO 1 étape ou 2 étapes ? Standard ou Swing ? Avec la stratégie combinée
        finale : jours attendus pour être financé, réussite, échecs, et l'effet d'une fermeture obligatoire avant
        le week-end (compte FTMO Standard une fois financé)."""
        c = self.combined
        if not c or not len(allr):
            return {}
        pool0 = getattr(self, "_pool0", None) or self._pool(allr)
        h = c.get("horaire") or {}
        trades, windows, info = self._session_pool(pool0, h.get("debut"), h.get("fin"))
        w = self._keys_of(c, info)
        if not w:
            return {}
        keys, rg = list(w), c.get("regles", {})
        cap = self.cfg.day_budget
        self.cfg.day_budget = float(rg.get("day_budget") or cap)
        one = FtmoRules(target1=10.0, max_daily=3.0, max_total=10.0, min_days=self.cfg.ftmo.min_days,
                        best_day_pct=50.0, trailing=True)
        two = FtmoRules(target1=10.0, target2=5.0, max_daily=5.0, max_total=10.0, min_days=4, best_day_pct=0.0,
                        trailing=False)

        def evaluate(tr, rules, name):
            got = self._daily(keys, w, rg.get("day_stop"), rg.get("max_open"), tr, windows, rg.get("max_correles"),
                              rg.get("frein"), rg.get("volatilite"))
            if got is None:
                return None
            daily = got[0]
            res = simulate(daily, rules, 3000, seed=0, pilot=rg.get("pilote"))
            j = (res["ftmo_jours_p1"] or 0) + (res["ftmo_jours_p2"] if rules.target2 > 0 and
                                                 res["ftmo_jours_p2"] == res["ftmo_jours_p2"] else 0)
            p = res["ftmo_pass"]
            hist = count_challenges(daily, rules, rg.get("pilote"))
            return {"compte": name, "reussite": p, "echec": res["ftmo_echec_p1"],
                    "jours_median": j, "jours_attendus": j / (p / 100) if p and p == p and p > 0 else float("nan"),
                    "reussis_oos": hist["reussis"], "rates_oos": hist["rates"],
                    **holding_stats(got[1])}
        try:
            rows = [r for r in (evaluate(trades, one, "FTMO 1 étape (Standard)"),
                                evaluate(trades, two, "FTMO 2 étapes (Standard ou Swing)")) if r]
            closed = self._trades_weekend_closed(keys, info, windows, h)
            if len(closed) == len(keys):
                r = evaluate({**trades, **closed}, one, "FTMO 1 étape, en fermant tout avant le week-end")
                if r:
                    rows.append(r)
        finally:
            self.cfg.day_budget = cap
        if not rows:
            return {}
        best = min(rows[:2], key=lambda r: r["jours_attendus"] if r["jours_attendus"] == r["jours_attendus"] else 1e9)
        wk = rows[0].get("week_end_pct") or 0.0
        txt = [f"Le plus rapide pour être financé avec cette stratégie combinée : {best['compte']} "
               f"(~{_fmt(best['jours_attendus'], '{:.0f}')} jours de bourse attendus, réussite {_fmt(best['reussite'])} %)."]
        if wk >= 5:
            txt.append(f"{wk:.0f} % de ses trades restent ouverts pendant un week-end. Pendant le challenge c'est permis "
                       "(Standard comme Swing), mais une fois financé en Standard il faut tout fermer avant le week-end.")
            if len(rows) > 2:
                cl = rows[2]
                worse = (cl["jours_attendus"] != cl["jours_attendus"]) or cl["jours_attendus"] > rows[0]["jours_attendus"] * 1.2
                txt.append("En fermant tout le vendredi soir : " + (
                    f"~{_fmt(cl['jours_attendus'], '{:.0f}')} jours attendus, réussite {_fmt(cl['reussite'])} %. " if
                    cl["jours_attendus"] == cl["jours_attendus"] else "plus de résultat exploitable. ")
                    + ("Nettement moins bon : un compte SWING (garde le week-end, levier 1:30) vaut le coup pour la phase "
                       "financée, ou demandez au Directeur des stratégies plus courtes." if worse else
                       "Presque pareil : un compte STANDARD suffit (le bot fermera avant le week-end une fois financé)."))
        else:
            txt.append(f"Seulement {wk:.0f} % des trades passent le week-end : un compte STANDARD (levier 1:100, moins "
                       "cher) est le bon choix ; le Swing n'apporte rien ici.")
        txt.append("Rappel : le Swing a un levier de 1:30 (contre 1:100) : avec des stops serrés sur les indices ou l'or, "
                   "les gros lots peuvent manquer de marge.")
        self.advice = {"lignes": rows, "texte": txt}
        self.combined["conseil_compte"] = self.advice
        (self.cfg.out / "strategie_combinee.json").write_text(
            json.dumps(self.combined, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        for t in txt:
            self.say("Conseiller du compte : " + t)
        return self.advice

    # --------------------------------------------------------------------------- l'avocat du diable
    def devils_advocate(self, allr: pd.DataFrame) -> dict:
        """L'AVOCAT DU DIABLE : refait EXACTEMENT la même recherche sur les mêmes bougies remises dans un ordre au
        hasard (aucune vraie tendance, aucun vrai motif). S'il y trouve presque autant de stratégies « validées »
        que sur les vrais prix, nos trouvailles sont surtout de la chance."""
        from .data import shuffle_prices
        if not len(allr):
            return {}
        good = allr["verdict"].astype(str)
        allr = allr.assign(_n_ok=good.eq("APPROUVÉ").astype(int),
                           _n_essai=good.str.startswith("À L'ESSAI").astype(int))
        cells = (allr.groupby(["symbole", "timeframe"])[["_n_ok", "_n_essai"]].sum()
                 .sort_values(["_n_ok", "_n_essai"], ascending=False).head(self.cfg.avocat_cases))
        rows = []
        for (sym, tf), r in cells.iterrows():
            try:
                df, cost = self.data(sym, tf)
            except Exception as exc:
                self.say(f"Avocat du diable : {sym} {tf} impossible ({exc})")
                continue
            self.say(f"Avocat du diable : je refais la recherche de {sym} {tf} sur les mêmes bougies MÉLANGÉES au "
                     "hasard (plus aucune vraie tendance). Si je trouve autant de gagnantes, c'était de la chance.")
            fake = run_lab(shuffle_prices(df, seed=self.cfg.seed), cost, self.lab_cfg(), f"{sym}_{tf}_HASARD",
                           self.cfg.out / "avocat_du_diable" / f"{sym}_{tf}")
            v = fake["verdict"].astype(str) if len(fake) else pd.Series(dtype=str)
            row = {"symbole": sym, "timeframe": tf, "vrais_valides": int(r["_n_ok"]),
                   "vrais_essai": int(r["_n_essai"]), "hasard_valides": int(v.eq("APPROUVÉ").sum()),
                   "hasard_essai": int(v.str.startswith("À L'ESSAI").sum())}
            rows.append(row)
            self.say(f"Avocat du diable {sym} {tf} : vrais prix {row['vrais_valides']} validées / "
                     f"{row['vrais_essai']} à l'essai ; prix au hasard {row['hasard_valides']} validées / "
                     f"{row['hasard_essai']} à l'essai")
        if not rows:
            return {}
        real = sum(r["vrais_valides"] + r["vrais_essai"] for r in rows)
        fake = sum(r["hasard_valides"] + r["hasard_essai"] for r in rows)
        real_ok = sum(r["vrais_valides"] for r in rows)
        fake_ok = sum(r["hasard_valides"] for r in rows)
        if real == 0:
            verdict, level = ("Rien trouvé sur les vrais prix de ces cases : pas de conclusion possible.", "neutre")
        elif fake_ok == 0 and fake <= real * 0.25:
            verdict, level = ("BON SIGNE : sur des prix au hasard, la recherche ne trouve (presque) rien. Ce qu'elle "
                              "trouve sur les vrais prix n'est probablement pas que de la chance.", "bon")
        elif fake < real * 0.6:
            verdict, level = ("PRUDENCE : la recherche trouve aussi des « gagnantes » sur des prix au hasard, mais "
                              "beaucoup moins que sur les vrais. Une partie de nos stratégies est sûrement de la chance : "
                              "fiez-vous surtout à celles confirmées en paper trading.", "moyen")
        else:
            verdict, level = ("DANGER : la recherche trouve presque autant de « gagnantes » sur des prix au hasard que "
                              "sur les vrais. La plupart de nos stratégies sont probablement de la chance. Ne payez pas de "
                              "challenge avant une confirmation solide en paper trading.", "danger")
        self.avocat = {"cases": rows, "verdict": verdict, "niveau": level, "fait_le": time.strftime("%Y-%m-%d %H:%M"),
                       "vrais": real, "hasard": fake, "vrais_valides": real_ok, "hasard_valides": fake_ok}
        (self.cfg.out / "avocat_du_diable.json").write_text(
            json.dumps(self.avocat, indent=1, ensure_ascii=False), encoding="utf-8")
        self.say("AVOCAT DU DIABLE : " + verdict)
        return self.avocat

    # --------------------------------------------------------------------------- TOP 10 des stratégies combinées
    def _register(self, nom: str, pour: str, comb: dict | None, star: bool = False):
        """Garde chaque stratégie combinée construite (une seule fois) avec ses chiffres, pour le TOP 10."""
        from .boutons import slim
        if not comb or not comb.get("resultat") or not comb.get("composants"):
            return
        sig = json.dumps([[c["symbole"], c["timeframe"], candidate_key(c["candidate"]), c.get("risk_pct")]
                          for c in comb["composants"]] + [comb.get("regles"), (comb.get("horaire") or {}).get("nom")],
                         sort_keys=True, default=str)
        for e in self.all_combos:
            if e["_sig"] == sig:
                e["choisie"] = e.get("choisie") or star
                return
        r = comb["resultat"]
        keep = ("ftmo_pass", "ftmo_jours_p1", "ftmo_echec_p1", "jours_attendus", "trades", "trades_mois",
                "pire_jour", "dd_max", "rendement_pct", "fenetre", "jours_periode", "week_end_pct", "duree_moy_h",
                "challenges_oos")
        res = {k: r.get(k) for k in keep}
        if res.get("jours_attendus") is None:
            res["jours_attendus"] = expected_days(r)
        sc = slim(comb)
        for c, full in zip(sc["composants"], comb["composants"]):
            c["trades_mois"] = full.get("trades_mois")
        self.all_combos.append({"_sig": sig, "nom": nom, "pour": pour, "choisie": star, "comb": sc, "res": res})

    def _eval_external(self, comb: dict | None, info, trades, windows, default_w: float):
        """Chiffres d'une combinaison venue d'ailleurs (Chef FTMO, direct) avec la même méthode que le Directeur."""
        w = {k: (v if v else default_w) for k, v in self._keys_of(comb, info).items()}
        if not w:
            return None
        res = self._eval(list(w), w, None, None, trades, windows, n=3000)
        if res is None:
            return None
        comps = [{"symbole": info[k]["symbole"], "timeframe": info[k]["timeframe"], "candidate": info[k]["candidate"],
                  "strategie": info[k]["strategie"], "risque_config": info[k]["risque"], "risk_pct": w[k],
                  "horaire": info[k].get("horaire"), "trades_mois": _comp_tpm(trades, windows, k)} for k in w]
        return {"resultat": res, "composants": comps,
                "regles": {"day_budget": self.cfg.day_budget, "total_budget": self.cfg.total_budget},
                "horaire": {"nom": "24h/24", "debut": None, "fin": None}}

    def rank_combos(self, allr: pd.DataFrame | None = None) -> list[dict]:
        """Ajoute le portefeuille du Chef FTMO et la combinaison du direct, classe tout et enregistre le TOP."""
        if allr is not None and len(allr):
            try:
                pool0 = getattr(self, "_pool0", None) or self._pool(allr)
                trades, windows, info = pool0
                pf = self.cfg.out / "portefeuille_ftmo.csv"
                if pf.exists():
                    p = pd.read_csv(pf)
                    comb = {"composants": [{"symbole": r.symbole, "timeframe": r.timeframe,
                                            "candidate": json.loads(r.candidate)}
                                           for r in p.itertuples() if isinstance(getattr(r, "candidate", None), str)]}
                    self._register("Portefeuille du Chef FTMO", "passer le challenge FTMO",
                                   self._eval_external(comb, info, trades, windows, min(0.5, self.cfg.risk_pct)))
                d = _load_json(self.cfg.out / "strategie_combinee_direct.json")
                if d:
                    self._register("Combinaison du direct (paper trading)", "passer le challenge FTMO",
                                   self._eval_external(d, info, trades, windows, min(0.5, self.cfg.risk_pct)))
            except Exception as exc:
                self.say(f"TOP 10 : portefeuille / direct non évalués ({exc})")
        if self.combined:
            self._register("LA stratégie combinée du Directeur", "passer le challenge FTMO", self.combined, star=True)
        lim = self.cfg.max_fail

        def key(e):  # 1. échecs sous la limite 2. jours attendus 3. réussite 4. échecs
            r = e["res"]
            ech = r.get("ftmo_echec_p1")
            j = r.get("jours_attendus")
            p = r.get("ftmo_pass")
            bad = ech is None or ech != ech or ech > lim
            return (bad, round(j) if j is not None and j == j else 10**9, -(p if p == p and p is not None else 0),
                    ech if ech == ech and ech is not None else 100)
        self.all_combos.sort(key=key)
        for i, e in enumerate(self.all_combos, 1):
            e["rang"] = i
        (self.cfg.out / "toutes_les_combinees.json").write_text(
            json.dumps(self.all_combos, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        if self.all_combos:  # LA n°1 : c'est elle que lance la touche 3 du menu
            top = self.all_combos[0]
            n1 = {**top["comb"], "nom": f"N°1 du TOP 10 : {top['nom']}", "resultat": top["res"]}
            if self.combined.get("essai") or top["comb"].get("essai"):
                n1["essai"] = True
            (self.cfg.out / "strategie_n1.json").write_text(
                json.dumps(n1, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        if self.all_combos:
            b = self.all_combos[0]
            self.say(f"TOP 10 des stratégies combinées : n°1 = {b['nom']} -> challenge réussi en "
                     f"~{_fmt(b['res'].get('jours_attendus'), '{:.0f}')} jours attendus "
                     f"({len(self.all_combos)} stratégies combinées classées)")
        return self.all_combos

    def _load_combos(self):
        self.all_combos = _load_json(self.cfg.out / "toutes_les_combinees.json") or []
        if not isinstance(self.all_combos, list):
            self.all_combos = []

    def mix_only(self):
        """Option W : seulement le Chef des combinaisons, avec les résultats déjà calculés (pas de recherche)."""
        allr = self.review()
        self.combined = _load_json(self.cfg.out / "strategie_combinee.json") or {}
        self.session_rows = self.combined.get("horaires", []) or []
        self.scenario_rows = self.combined.get("scenarios", []) or []
        self.accounts = {n: _load_json(self.cfg.out / f"strategie_combinee_{n}.json")
                         for n in ("perso", "finance") if (self.cfg.out / f"strategie_combinee_{n}.json").exists()}
        self._load_combos()
        if not self.combined:
            self.say("Pas encore de stratégie combinée : lancez d'abord le Directeur (option D).")
        else:
            self.combine_combinations(allr)
            self.account_advice(allr)
        self.rank_combos(allr)
        if len(allr):
            self.build_cards()
        write_report(self)

    def combine_combinations(self, allr: pd.DataFrame) -> list[dict]:
        """LE CHEF DES COMBINAISONS : la stratégie combinée du Directeur + une autre combinaison (portefeuille du Chef
        FTMO, autre horaire, combinaison du direct, ou toutes) seraient-elles encore meilleures ENSEMBLE ?
        Il fusionne, puis retire / ajoute / règle le risque comme le Directeur. Gardé seulement si le challenge est
        réussi plus vite (même limite d'échecs, mêmes règles de risque)."""
        overall = self.combined
        self.combo_rows = []
        if not overall or not len(allr):
            self.say("Chef des combinaisons : pas encore de stratégie combinée à mélanger.")
            return []
        pool0 = getattr(self, "_pool0", None) or self._pool(allr)
        h = overall.get("horaire") or {}
        pool = self._session_pool(pool0, h.get("debut"), h.get("fin"))
        trades, windows, info = pool
        base = self._keys_of(overall, info)
        if not base:
            self._mix_note("les composants de la combinée ne sont plus dans les stratégies validées (refaites l'option D)")
            return []
        sources = []
        for r in self.session_rows:
            if r["horaire"] != h.get("nom"):
                sources.append((f"combinée de l'horaire {r['horaire']}", _load_json(self.cfg.out / r["fichier"])))
        pf = self.cfg.out / "portefeuille_ftmo.csv"
        if pf.exists():
            try:
                p = pd.read_csv(pf)
                sources.append(("portefeuille du Chef FTMO", {"composants": [
                    {"symbole": r.symbole, "timeframe": r.timeframe, "candidate": json.loads(r.candidate)}
                    for r in p.itertuples() if isinstance(getattr(r, "candidate", None), str)]}))
            except Exception:
                pass
        sources.append(("combinaison du direct (paper trading)", _load_json(self.cfg.out / "strategie_combinee_direct.json")))
        sources = [(n, c) for n, c in sources if self._keys_of(c, info)]
        if len(sources) > 1:
            every = {"composants": [x for _, c in sources for x in c["composants"]]}
            sources.append(("toutes les combinaisons ensemble", every))
        if not sources:
            self._mix_note("aucune autre combinaison à mélanger : il faut au moins un autre horaire, le portefeuille du "
                           "Chef FTMO (option 5) ou la combinaison du direct (option L)")
            return []
        cap = self.cfg.day_budget
        self.cfg.day_budget = float(overall.get("scenario_choisi") or cap)
        rg = overall.get("regles", {})
        base_res = overall.get("resultat")
        best, best_name = None, None
        self.say(f"Chef des combinaisons : la combinée du Directeur ({len(base)} composants) est-elle meilleure avec "
                 f"d'autres combinaisons ? J'essaie {len(sources)} mélanges.")
        try:
            for name, comb in sources:
                other = self._keys_of(comb, info)
                union = {**other, **base}  # les composants du Directeur gardent leur risque
                new = self.build_combined(allr, pool, quiet=True, start=(list(union), union, rg))
                res = new.get("resultat") if new else None
                if new:
                    self._register(f"Mélange : combinée du Directeur + {name}", "passer le challenge FTMO",
                                   {**new, "horaire": overall.get("horaire")})
                better = bool(res) and self._better(res, base_res)
                self.combo_rows.append({
                    "melange": f"Directeur + {name}", "ajoutes": len(set(other) - set(base)),
                    "composants": len(new["composants"]) if new else 0,
                    "attendus": expected_days(res) if res else None, "reussite": res.get("ftmo_pass") if res else None,
                    "echec": res.get("ftmo_echec_p1") if res else None,
                    "trades_mois": res.get("trades_mois") if res else None, "mieux": better})
                self.say(f"  Directeur + {name} : " + (self._summary(res) + (" -> MIEUX" if better else " -> pas mieux")
                                                        if res else "impossible (pas assez de période commune)"))
                if better and self._better(res, best["resultat"] if best else None):
                    best, best_name = new, name
        finally:
            self.cfg.day_budget = cap
        if best:
            best.update(horaire=overall.get("horaire"), scenario_choisi=overall.get("scenario_choisi"),
                        scenarios=overall.get("scenarios"), horaires=overall.get("horaires"),
                        nom="Stratégie combinée du Directeur (améliorée par le Chef des combinaisons)",
                        amelioree_avec=best_name)
            best["regles"]["day_budget"] = self.cfg.day_budget if not overall.get("scenario_choisi") \
                else float(overall["scenario_choisi"])
            if overall.get("essai"):
                best["essai"] = True
            hist = self.history_challenges(best)
            if hist:
                best["challenges_historique"] = hist
            self.combined = best
            self.say(f"Chef des combinaisons : OUI, avec {best_name} c'est meilleur -> {self._summary(best['resultat'])}. "
                     "C'est maintenant LA stratégie combinée (paper trading option C et bot).")
        else:
            self.say("Chef des combinaisons : aucun mélange ne fait mieux que la combinée du Directeur seule.")
        self.combined["melanges"] = self.combo_rows
        (self.cfg.out / "strategie_combinee.json").write_text(
            json.dumps(self.combined, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return self.combo_rows

    # --------------------------------------------------------------------------- 5. test multi-timeframes
    def multi_tf_test(self):
        self.multi_tf = []
        for comp in self.combined.get("composants", []):
            c = comp["candidate"]
            row = {"composant": f"{comp['symbole']} {comp['timeframe']} | {comp['strategie']}", "tfs": {}}
            for tf in self.cfg.timeframes:
                try:
                    df, cost = self.data(comp["symbole"], tf)
                    split = int(len(df) * 0.65)
                    sig = apply_filter(df, compute_signal(df, c["signal"]), c["filter"])
                    res = run_backtest(df.iloc[split:], sig.iloc[split:], RiskConfig(**c["risk"]), cost=cost,
                                       risk_pct=self.cfg.risk_pct)
                    row["tfs"][tf] = {"trades": res.trades, "avg_r": res.avg_r, "pf": res.profit_factor}
                except Exception as exc:
                    row["tfs"][tf] = {"erreur": str(exc).splitlines()[0]}
            good = [v for v in row["tfs"].values() if v.get("trades", 0) >= 10 and v.get("avg_r", -1) > 0]
            tested = [v for v in row["tfs"].values() if v.get("trades", 0) >= 10]
            row["robuste"] = bool(tested) and len(good) >= max(1, math.ceil(len(tested) / 2))
            row["positifs"] = f"{len(good)}/{len(tested)}"
            self.multi_tf.append(row)
            self.say(f"Test multi-TF de {row['composant']} : positif sur {row['positifs']} timeframes"
                     + (" -> robuste" if row["robuste"] else " -> spécifique à son timeframe"))

    # --------------------------------------------------------------------------- fiches
    def build_cards(self):
        """Une fiche détaillée par stratégie : composants de la stratégie combinée, stratégies validées,
        meilleure version de chaque stratégie du catalogue."""
        from .fiches import build_card, write_cards
        cards, seen = [], set()

        def stats_of(r):
            if r is None:
                return {}
            per = ""
            if pd.notna(r.get("donnees_debut")):
                per = f"{str(r['donnees_debut'])[:10]} → {str(r['donnees_fin'])[:10]}"
            return {"Verdict": r.get("verdict"), "Période testée": per,
                    "Walk-forward (périodes gagnantes)": _txt(r.get("walk_forward")),
                    "Trades hors-échantillon": None if pd.isna(r.get("trades_oos")) else int(r.get("trades_oos")),
                    "Trades par mois (environ)": r.get("trades_mois"),
                    "Trades gardés pendant un week-end (%)": r.get("week_end_pct"),
                    "Durée moyenne d'un trade (heures)": r.get("duree_moy_h"),
                    "Taux de réussite OOS (%)": r.get("wr_oos"),
                    "R moyen OOS": r.get("avgR_oos"), "Profit factor OOS": r.get("pf_oos"),
                    "Gain par mois (%)": r.get("gain_mois_pct"), "Drawdown max OOS (%)": r.get("dd_oos_pct"),
                    "Réussite FTMO seule (%)": r.get("ftmo_pass"),
                    "Auditeur anti-hasard": _txt(r.get("audit_detail")) or None,
                    "Météo du marché (R moyen hors-échantillon par type de marché)": _txt(r.get("meteo")) or None,
                    "Gain sur la période de test à 1 %/trade (%)": r.get("rendement_oos_pct_risque_bh"),
                    "Buy & hold sur la même période (%)": r.get("buy_hold_oos_pct"),
                    "Challenges réussis / ratés (tout l'historique)": _pair(r, "total"),
                    "Challenges réussis / ratés (hors-échantillon)": _pair(r, "oos"),
                    "Jours pour l'objectif": None if pd.isna(r.get("ftmo_jours_p1")) else int(r.get("ftmo_jours_p1"))}

        def row_for(sym, tf, cand):
            if not len(self.allr):
                return None
            k = candidate_key(cand)
            m = self.allr[(self.allr["symbole"] == sym) & (self.allr["timeframe"] == tf)]
            for _, r in m.iterrows():
                if candidate_key(json.loads(r["candidate"])) == k:
                    return r
            return None

        def add(cand, sym, tf, origin, risk=None, rules=None):
            key = (sym, tf, candidate_key(cand))
            same = same_rule_key(sym, tf, cand)
            if key in seen or same in seen:
                return
            seen.update({key, same})
            row = row_for(sym, tf, cand)
            periods = []
            if row is not None and _txt(row.get("par_periode")):
                try:
                    periods = json.loads(row["par_periode"])
                except (TypeError, ValueError):
                    periods = []
            card = build_card(cand, sym, tf, stats_of(row), risk, origin, rules, periods)
            self.card_ids[key] = card["id"]
            cards.append(card)

        rules = {}
        if self.combined:
            rg = self.combined.get("regles", {})
            rules = {"Perte possible max par jour (%)": rg.get("day_budget"),
                     "Perte totale max (%)": rg.get("total_budget"), "Positions ouvertes max": rg.get("max_open"),
                     "Positions max sur marchés corrélés (même sens)": rg.get("max_correles"),
                     "Pilote de risque du challenge": pilot_text(rg.get("pilote")),
                     "Frein de bonne journée": lock_text(rg.get("frein")),
                     "Arrêt après perte réalisée du jour (%)": rg.get("day_stop")}
            for i, comp in enumerate(self.combined.get("composants", []), 1):
                add(comp["candidate"], comp["symbole"], comp["timeframe"], f"stratégie combinée n°{i}",
                    comp["risk_pct"], rules)
        if len(self.allr):
            ok = self.allr[self.allr["_ok"]].sort_values(["ftmo_pass", "gain_mois_pct"], ascending=False)
            for _, r in ok.iterrows():
                add(json.loads(r["candidate"]), r["symbole"], r["timeframe"], kind(r), self.cfg.lab_risk_pct)
            for _, r in self.catalog_ranking().iterrows():
                add(json.loads(r["candidate"]), r["symbole"], r["timeframe"], "catalogue : meilleure version",
                    self.cfg.lab_risk_pct)
            for _, r in self.genius_rows().iterrows():  # toutes les lois des génies, validées ou non
                add(json.loads(r["candidate"]), r["symbole"], r["timeframe"], "loi d'un génie", self.cfg.lab_risk_pct)
        if cards:
            path = write_cards(cards, self.cfg.out)
            self.say(f"{len(cards)} fiches détaillées écrites : {path}")

    def genius_rows(self) -> pd.DataFrame:
        """Les lois découvertes par Einstein et Hawking, de la meilleure à la moins bonne hors-échantillon."""
        if not len(self.allr) or "equipe" not in self.allr.columns:
            return pd.DataFrame()
        g = self.allr[self.allr["equipe"].astype(str).eq("Génies")]
        return g.sort_values(["_ok", "avgR_oos"], ascending=[False, False])

    def catalog_ranking(self) -> pd.DataFrame:
        """Meilleure version de chaque stratégie du catalogue, tous marchés et timeframes confondus."""
        a = self.allr
        if not len(a) or "meilleure_version_de" not in a:
            return pd.DataFrame()
        cat = a[a["meilleure_version_de"].fillna("") != ""].copy()
        if not len(cat):
            return cat
        cat["_ok2"] = cat["_ok"].astype(int)
        cat = cat.sort_values(["_ok2", "avgR_oos", "gain_mois_pct"], ascending=False)
        return cat.drop_duplicates("meilleure_version_de").drop(columns="_ok2")

    def failles(self) -> list[dict]:
        out = []
        for sym in self.cfg.symbols:
            for tf in self.cfg.timeframes:
                p = self.cfg.out / f"{sym}_{tf}" / "failles.json"
                if p.exists():
                    for fa in json.loads(p.read_text(encoding="utf-8")):
                        out.append({**fa, "symbole": sym, "timeframe": tf})
        return out

    # --------------------------------------------------------------------------- campagne
    def rebuild_report(self):
        """Refait seulement les pages (directeur.html + fiches) avec les résultats déjà calculés : rapide."""
        allr = self.review()
        self.combined = _load_json(self.cfg.out / "strategie_combinee.json") or {}
        self.session_rows = self.combined.get("horaires", []) or []
        self.scenario_rows = self.combined.get("scenarios", []) or []
        self.combo_rows = self.combined.get("melanges", []) or []
        self.combo_note = self.combined.get("melanges_note")
        self.advice = self.combined.get("conseil_compte") or {}
        self.accounts = {n: _load_json(self.cfg.out / f"strategie_combinee_{n}.json")
                         for n in ("perso", "finance") if (self.cfg.out / f"strategie_combinee_{n}.json").exists()}
        self._load_combos()
        if len(allr):
            self.build_cards()
        write_report(self)
        self.say(f"Pages refaites : {self.cfg.out / 'directeur.html'} et fiches_strategies.html")

    def run(self):
        t0 = time.time()
        self.pass1()
        allr = self.review()
        if self.cfg.second_pass and len(allr):
            self.pass2(allr)
            allr = self.review()
        if len(allr):
            self.scenarios(allr)
            if self.combined:
                self.combine_combinations(allr)
                self.account_advice(allr)
            if self.cfg.comptes:
                from .comptes import run_accounts
                self.accounts = run_accounts(self, allr, overrides=getattr(self, "account_overrides", None))
            if self.combined:
                self.multi_tf_test()
            self.build_cards()
        self.live_analysis()
        self.rank_combos(allr)
        if self.cfg.avocat and len(allr):
            self.devils_advocate(allr)
        self.say(f"Campagne terminée en {(time.time() - t0) / 60:.0f} min. Rapport : {self.cfg.out / 'directeur.html'}")
        write_report(self)
        return self.combined


# ================================================================================ rapport
CSS = """
:root{--bg:#f7f8fa;--card:#fff;--fg:#1c2230;--mut:#667085;--line:#e4e7ec;--ok:#12805c;--bad:#b42318;--okbg:#e7f6ee;--acc:#2a78d6}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e6e8ee;--mut:#98a2b3;--line:#2a2f3a;--ok:#3ccb7f;--bad:#f97066;--okbg:#14301f;--acc:#3987e5}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:1250px;margin:auto;padding:20px 16px}h1{font-size:24px;margin:0}h2{font-size:17px;margin:28px 0 10px}
.mut{color:var(--mut)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin-top:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.card b{display:block;font-size:22px}
.scroll{overflow:auto;border:1px solid var(--line);border-radius:10px;max-height:620px}
table{width:100%;border-collapse:collapse;background:var(--card);font-size:12.5px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{position:sticky;top:0;background:var(--card)}
td.good{background:var(--okbg);color:var(--ok);font-weight:600}.pos{color:var(--ok);font-weight:600}.neg{color:var(--bad)}
pre{white-space:pre-wrap;font-size:12px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;max-height:520px;overflow:auto}
.warn{border-left:4px solid var(--bad);padding:10px 14px;background:var(--card);border-radius:6px}
"""


def expected_days(res: dict) -> float:
    """Jours de bourse attendus pour AVOIR un challenge réussi si on recommence après chaque échec :
    jours médians pour réussir / probabilité de réussite (90 % en 8 jours -> ~8,9 jours)."""
    p, d = res.get("ftmo_pass", float("nan")), res.get("ftmo_jours_p1", float("nan"))
    if p is None or d is None or not p == p or not d == d or p <= 0:
        return float("inf")
    return float(d) / (float(p) / 100)


def _direct_html(d) -> str:
    esc = html.escape
    r = getattr(d, "direct", None) or {}
    if not r.get("trades"):
        return "<p class='mut'>Pas encore de trades en paper trading : lancez-le (options 6, 7, 8, S ou C).</p>"
    top = "".join(f"<li>{esc(str(x['symbole']))} {esc(str(x['timeframe']))} | {esc(str(x['strategie'])[:90])} | "
                  f"{x['trades']} trades, {x['r_total']:+.1f}R</li>" for x in r.get("classement", [])[:5])
    comb = r.get("combinaison")
    ctext = (f"<p><b>Meilleure combinaison des stratégies qui tournent</b> : {len(comb['composants'])} stratégies, "
             f"gain en direct {comb['resultat']['rendement_pct']:+.2f} %, pire journée {comb['resultat']['pire_jour']:.2f} %."
             "</p>") if comb else ""
    return (f"<p class='mut'>{esc(r.get('message', ''))} ({r['trades']} trades, {r['strategies']} stratégies)</p>{ctext}"
            f"<p>Meilleurs setups en direct :</p><ol>{top}</ol><p><a href='direct.html'>Analyse complète du direct</a></p>")


def _accounts_html(d) -> str:
    from html import escape as esc

    from .boutons import button, slim
    acc = getattr(d, "accounts", {}) or {}
    if not acc:
        return "<p class='mut'>Pas encore calculé (python run.py comptes, ou option K du menu).</p>"
    out = ""
    for name, c in acc.items():
        if not c:
            out += f"<p class='mut'>{esc(name)} : pas de combinaison possible pour l'instant.</p>"
            continue
        r = c["resultat"]
        out += (f"<p><b>{esc(c['nom'])}</b> ({esc(c['but'])}) : {len(c['composants'])} composants, rendement médian "
                f"{_fmt(r['rendement_an_median'])} %/an ({_fmt(r['rendement_mois_median'], '{:.2f}')} %/mois), "
                f"problème {_fmt(r['p_probleme'])} % de chances sur un an. "
                f"<a href='compte_{esc(name)}.html'>Détails</a> {button(slim(c), 'Bot MT5 de ce compte')}</p>")
    return out


MGMT_FR = {"none": "aucune", "breakeven": "break-even à +1R", "trailing": "stop suiveur ATR",
           "paliers": "paliers (BE à +1R, +1R à +2R...)", "intelligente": "sortie intelligente"}


def _drawdown(daily: pd.DataFrame) -> dict:
    """Plus grosse baisse depuis un plus haut (en % du capital, positions ouvertes au pire moment de la journée)."""
    if daily is None or not len(daily):
        return {"dd_max": float("nan")}
    cum = daily["pnl"].to_numpy(float).cumsum()
    before = np.concatenate([[0.0], cum[:-1]])
    peak = np.maximum.accumulate(np.maximum(before, 0.0))
    low = before + np.minimum(daily["worst"].to_numpy(float), 0.0)
    return {"dd_max": round(float(np.max(peak - low)), 2)}


def _per_month(n, lo, hi) -> float:
    """Nombre de trades ramené à un mois (30,4 jours) sur la période [lo, hi]."""
    days = max(1.0, (pd.Timestamp(hi) - pd.Timestamp(lo)).days)
    return round(float(n) / days * 30.44, 1)


def _comp_tpm(trades, windows, k) -> float:
    lo, hi = windows[k]
    t = trades[k]
    e = pd.to_datetime(t["entry_time"].astype(str), format="mixed")
    return _per_month(int(((e >= lo) & (e <= hi)).sum()), lo, hi)


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _combos_html(rows, note=None, done=False) -> str:
    from html import escape as esc
    if not rows:
        if done and note:
            return f"<p class='warn'>Mélange impossible : {esc(note)}.</p>"
        return ("<p class='warn'>Pas encore fait sur ces résultats : menu <b>W</b> (mélange seulement, quelques minutes) "
                "ou <b>D</b> (campagne complète).</p>")
    body = "".join(
        f"<tr><td>{esc(r['melange'])}</td><td>{r['ajoutes']}</td><td>{r['composants']}</td>"
        f"<td><b>{_fmt(r.get('attendus'), '{:.0f}')}</b></td><td>{_fmt(r.get('reussite'))} %</td>"
        f"<td>{_fmt(r.get('echec'))} %</td><td>{_fmt(r.get('trades_mois'), '{:.0f}')}</td>"
        f"<td class='{'good' if r['mieux'] else ''}'><b>{'OUI, meilleur' if r['mieux'] else 'non'}</b></td></tr>"
        for r in rows)
    return ("<div class='scroll'><table><thead><tr><th>Mélange</th><th>Stratégies apportées</th><th>Composants gardés</th>"
            "<th>Challenge réussi en (jours attendus)</th><th>Réussite</th><th>Échec</th><th>Trades / mois</th>"
            f"<th>Meilleur que la combinée seule ?</th></tr></thead><tbody>{body}</tbody></table></div>")


def _advice_html(a) -> str:
    from html import escape as esc
    if not a:
        return "<p class='mut'>Pas encore calculé : menu W (rapide) ou D.</p>"
    body = "".join(
        f"<tr><td><b>{esc(r['compte'])}</b></td><td class='pos'><b>{_fmt(r.get('jours_attendus'), '{:.0f}')}</b></td>"
        f"<td>{_fmt(r.get('reussite'))} %</td><td>{_fmt(r.get('echec'))} %</td>"
        f"<td>{r.get('reussis_oos', '—')} / {r.get('rates_oos', '—')}</td>"
        f"<td>{_fmt(r.get('week_end_pct'), '{:.0f}')} %</td></tr>" for r in a.get("lignes", []))
    return ("<div class='scroll'><table><thead><tr><th>Compte</th><th>Financé en (jours de bourse attendus)</th>"
            "<th>Réussite</th><th>Échec</th><th>Challenges réussis / ratés (période de test)</th>"
            f"<th>Trades gardés le week-end</th></tr></thead><tbody>{body}</tbody></table></div>"
            + "".join(f"<p>{esc(t)}</p>" for t in a.get("texte", []))
            + "<p class='mut'>Règles utilisées — 1 étape : +10 %, perte max 3 %/jour, 10 % au total SUIVEUSE (fin de journée), "
              "meilleur jour ≤ 50 % du profit, part des profits 90 %. 2 étapes : +10 % puis +5 %, 5 %/jour, 10 % au total fixe, "
              "4 jours minimum par phase, part des profits 80 %. Pendant le challenge, aucun compte n'interdit de garder une "
              "position la nuit ou le week-end ; une fois financé, le Standard l'interdit le week-end (et autour des "
              "nouvelles), pas le Swing. Vérifiez les règles du moment sur ftmo.com.</p>")


def _wk_text(r) -> str:
    v, h = r.get("week_end_pct"), r.get("duree_moy_h")
    if v is None or v != v:
        return "—"
    return f"{v:.0f} % des trades (durée moyenne {h:.0f} h)" if h == h else f"{v:.0f} % des trades"


def _tpm_text(res: dict) -> str:
    v = res.get("trades_mois")
    if v is None:
        return "—"
    return f"~{v:.0f} trades/mois (~{v / 21:.1f} par jour de bourse)"


def _bh_text(d, c) -> str:
    """Buy & hold moyen (et sa pire baisse) des marchés de la stratégie combinée, sur la période de validation."""
    if not len(d.allr) or "buy_hold_oos_pct" not in d.allr.columns:
        return "—"
    vals, dds = [], []
    for sym, tf in {(x["symbole"], x["timeframe"]) for x in c.get("composants", [])}:
        m = d.allr[(d.allr["symbole"] == sym) & (d.allr["timeframe"] == tf)]
        if len(m) and pd.notna(m["buy_hold_oos_pct"].iloc[0]):
            vals.append(float(m["buy_hold_oos_pct"].iloc[0]))
            dds.append(float(m["buy_hold_dd_oos_pct"].iloc[0]))
    if not vals:
        return "—"
    return f"{np.mean(vals):+.1f} % (pire baisse {max(dds):.1f} %)"


def _slug(name: str) -> str:
    return name.replace("/", "").replace(" ", "").replace("h24h", "h") or "x"


def _with_corr(symbol: str, t: pd.DataFrame) -> pd.DataFrame:
    """Ajoute le groupe de corrélation et l'exposition (sens du trade x sens du marché) aux trades."""
    from .data import correlation_of
    cl, sign = correlation_of(symbol)
    expo = (t["side"].fillna(0).astype(int) * sign) if "side" in t.columns else 0
    return t.assign(cluster=cl, expo=expo)


def _pair(r, suffix: str) -> str | None:
    """« 7 réussis / 2 ratés » à partir des colonnes ftmo_reussis_<suffix> / ftmo_rates_<suffix>."""
    a, b = r.get(f"ftmo_reussis_{suffix}"), r.get(f"ftmo_rates_{suffix}")
    if a is None or b is None or (isinstance(a, float) and math.isnan(a)):
        return None
    return f"{int(a)} réussis / {int(b)} ratés"


def _hours_html(rows) -> str:
    """Section du rapport : la meilleure plage horaire de chaque stratégie (choisie puis contrôlée)."""
    if not rows:
        return ("<p class='mut'>Pas encore de plage horaire nettement meilleure (il faut au moins 40 trades par "
                "stratégie) ou recherche lancée sans les variantes horaires.</p>")
    esc = html.escape
    ok = [r for r in rows if r.get("ok")]
    lines = "".join(
        f"<tr><td>{esc(str(r['symbole']))} {esc(str(r['timeframe']))}</td><td>{esc(str(r['strategie'])[:90])}</td>"
        f"<td><b>{esc(r['nom'])}</b></td><td>{'confirmée' if r['ok'] else 'pas confirmée'}</td>"
        f"<td>{r['r_moyen_plage']:+.2f}R</td><td>{r['r_moyen_24h']:+.2f}R</td>"
        f"<td>{'' if r.get('r_moyen_controle_plage') is None else format(r['r_moyen_controle_plage'], '+.2f') + 'R'}"
        f" / {'' if r.get('r_moyen_controle_24h') is None else format(r['r_moyen_controle_24h'], '+.2f') + 'R'}</td>"
        f"<td>{r['trades_plage']} / {r['trades_total']}</td></tr>" for r in rows[:60])
    return (f"<p class='mut'>Pour chaque stratégie, toutes les plages (début 0 h à 23 h, durée 2 à 12 h, heure du serveur "
            f"MT5) sont essayées : la meilleure est CHOISIE sur 60 % de ses trades puis CONTRÔLÉE sur les 40 % suivants. "
            f"{len(ok)} plages confirmées sont devenues des stratégies « horaires » que le Chef des combinaisons peut "
            f"mélanger (chacune n'entre que dans SES heures ; le bot reçoit les heures de chaque composant).</p>"
            f"<table><tr><th>Marché</th><th>Stratégie</th><th>Meilleures heures</th><th>Contrôle</th>"
            f"<th>R moyen plage</th><th>R moyen 24 h</th><th>Contrôle plage / 24 h</th><th>Trades plage / total</th>"
            f"</tr>{lines}</table>")


def write_report(d: Director):
    from .boutons import button, script, single, slim
    esc = html.escape
    c = d.combined
    R = d.cfg.risk_pct
    bot1 = lambda sym, tf, cand: button(single(cand, sym, tf, R))  # bot d'UNE stratégie
    res = c.get("resultat", {}) if c else {}
    rules = c.get("regles", {}) if c else {}
    cards = ""
    if c:
        cards = "".join(f"<div class='card'><span class='mut'>{esc(k)}</span><b>{esc(v)}</b></div>" for k, v in [
            ("CHALLENGE RÉUSSI EN (jours de bourse attendus, reprises comprises)", _fmt(expected_days(res), "{:.0f}")),
            ("Réussite du challenge", f"{_fmt(res.get('ftmo_pass'))} %"),
            (f"Jours de bourse pour +{d.cfg.ftmo.target1:g} % (médiane)", _fmt(res.get("ftmo_jours_p1"), "{:.0f}")),
            ("Échec (limite de perte touchée)", f"{_fmt(res.get('ftmo_echec_p1'))} %"),
            ("Challenges enchaînés hors-échantillon", (lambda h: f"{h.get('reussis', '—')} réussis / {h.get('rates', '—')} ratés")(res.get("challenges_oos", {}))),
            ("Challenges enchaînés sur tout l'historique", (lambda h: f"{h['reussis']} réussis / {h['rates']} ratés" if h else "—")(c.get("challenges_historique"))),
            ("Gain sur la période de test (stratégie combinée)", f"{res.get('rendement_pct', float('nan')):+.1f} %"),
            ("Buy & hold sur la même période (moyenne des marchés utilisés)", _bh_text(d, c)),
            ("Composants", str(len(c["composants"]))),
            ("TRADES PAR MOIS (environ, tous composants ensemble)", _tpm_text(res)),
            ("Trades gardés pendant un week-end", _wk_text(res)),
            ("Pire journée (positions ouvertes au stop)", f"{res.get('pire_jour', 0):.2f} %"),
            ("Perte possible max par jour", f"{rules.get('day_budget', d.cfg.day_budget):g} %"),
            ("Arrêt journalier", "aucun" if rules.get("day_stop") is None else f"après -{rules['day_stop']:g} %"),
            ("Positions ouvertes max", str(rules.get("max_open") or "illimité")),
            ("Marchés corrélés dans le même sens (max)", str(rules.get("max_correles") or "illimité")),
            ("Pilote de risque du challenge", pilot_text(rules.get("pilote"))),
            ("Frein de bonne journée", lock_text(rules.get("frein"))),
            ("Dimensionnement par volatilité", vol_text(rules.get("volatilite")))])
    comp_rows = "".join(
        f"<tr><td>{i}</td><td>{esc(x['symbole'])}</td><td>{esc(x['timeframe'])}</td><td>{esc(x['strategie'])}</td>"
        f"<td>{esc(x['risque_config'])}{' <i>(variante R:R)</i>' if x.get('variante_rr') else ''}</td>"
        f"<td><b>{x['risk_pct']:g} %</b></td><td>{_fmt(x['reussite_seule'])} %</td><td>{_fmt(x.get('trades_mois'))}</td>"
        f"<td>{button(single(x['candidate'], x['symbole'], x['timeframe'], x['risk_pct']))}</td></tr>"
        for i, x in enumerate(c.get("composants", []) if c else [], 1))
    tfs = d.cfg.timeframes
    mtf = "".join(
        "<tr><td>" + esc(r["composant"]) + "</td>" + "".join(
            (lambda v: f"<td class='{'good' if v.get('trades', 0) >= 10 and v.get('avg_r', -1) > 0 else ''}'>"
                       + (esc(v['erreur'][:30]) if 'erreur' in v else f"{v['avg_r']:+.2f}R<br><span class='mut'>{v['trades']} trades</span>")
                       + "</td>")(r["tfs"].get(tf, {"erreur": "—"})) for tf in tfs)
        + f"<td><b>{'robuste' if r['robuste'] else 'spécifique'}</b> ({r['positifs']})</td></tr>" for r in d.multi_tf)
    chosen = c.get("scenario_choisi") if c else None
    chosen_h = (c.get("horaire") or {}).get("nom", "24h/24") if c else None
    is_chosen = lambda r: r["budget"] == chosen and r.get("horaire", "24h/24") == chosen_h
    scen_rows = "".join(
        f"<tr><td>{esc(r.get('horaire', '24h/24'))}</td>"
        f"<td class='{'good' if is_chosen(r) else ''}'><b>{r['budget']:g} %</b>{' (retenu)' if is_chosen(r) else ''}</td>"
        f"<td>{_fmt(r['reussite'])} %</td><td>{_fmt(r['jours'], '{:.0f}')}</td><td>{_fmt(r['echec'])} %</td>"
        f"<td>{r.get('reussis_oos', '—')} / {r.get('rates_oos', '—')}</td>"
        f"<td>{r['pire_jour']:.2f} %</td><td>{r['composants']}</td><td>{_fmt(r.get('trades_mois'), '{:.0f}')}</td>"
        f"<td>{esc(r['risques'])}</td><td>{esc(r['rr'])}</td></tr>"
        for r in d.scenario_rows)
    scen = ("<div class='scroll'><table><thead><tr><th>Horaire</th><th>Perte max par jour</th><th>Réussite</th>"
            f"<th>Jours pour +{d.cfg.ftmo.target1:g} %</th><th>Échec</th><th>Challenges réussis / ratés (OOS)</th><th>Pire journée</th><th>Composants</th>"
            f"<th>Trades / mois</th><th>Risque par trade (%)</th><th>R:R</th></tr></thead><tbody>{scen_rows}</tbody></table></div>"
            if scen_rows else "<p class='mut'>—</p>")
    sess_rows = "".join(
        f"<tr><td class='{'good' if r['horaire'] == chosen_h else ''}'><b>{esc(r['horaire'])}</b>"
        f"{' (retenu)' if r['horaire'] == chosen_h else ''}</td><td class='pos'><b>{_fmt(r.get('attendus'), '{:.0f}')}</b></td>"
        f"<td>{r['budget']:g} %</td>"
        f"<td class='pos'>{_fmt(r['reussite'])} %</td><td>{_fmt(r['jours'], '{:.0f}')}</td><td>{_fmt(r['echec'])} %</td>"
        f"<td>{r.get('reussis_oos', '—')} / {r.get('rates_oos', '—')}</td>"
        f"<td>{_fmt(r.get('reussis_hist'), '{:.0f}')} / {_fmt(r.get('rates_hist'), '{:.0f}')}</td>"
        f"<td>{_fmt(r.get('trades'), '{:.0f}')}</td><td><b>{_fmt(r.get('trades_mois'))}</b></td>"
        f"<td>{r['pire_jour']:.2f} %</td><td>{r['composants']}</td>"
        f"<td>{button(slim(_load_json(d.cfg.out / r['fichier'])), 'Bot MT5')}</td></tr>"
        for r in d.session_rows)
    sess = ("<div class='scroll'><table><thead><tr><th>Horaire (heure locale)</th>"
            "<th>Challenge réussi en (jours attendus, reprises comprises)</th><th>Meilleure perte max / jour</th>"
            f"<th>Réussite</th><th>Jours pour +{d.cfg.ftmo.target1:g} %</th><th>Échec</th>"
            "<th>Challenges réussis / ratés (OOS)</th><th>Réussis / ratés (tout l'historique)</th><th>Trades</th><th>Trades / mois</th>"
            f"<th>Pire journée</th><th>Composants</th><th>Bot de cet horaire</th></tr></thead><tbody>{sess_rows}</tbody></table></div>"
            if sess_rows else "<p class='mut'>—</p>")
    rev = "".join(
        f"<tr><td>{esc(r['symbole'])}</td><td>{esc(r['timeframe'])}</td><td>{r['validees']}</td>"
        f"<td>{_fmt(r['meilleure_ftmo'])} %</td><td>{_fmt(r['meilleur_gain_mois'], '{:+.2f}')} %</td>"
        f"<td>{r['inventions_validees']}/{r['inventions']}</td><td>{esc(r['note'])}</td></tr>" for r in d.review_rows)
    def fiche(sym, tf, cand, text):
        cid = d.card_ids.get((sym, tf, candidate_key(cand)))
        return f"<a href='fiches_strategies.html#{cid}'>{esc(text)}</a>" if cid else esc(text)

    best_rows = ""
    if len(d.allr):
        ok = d.allr[d.allr["_ok"]].sort_values(["ftmo_pass", "ftmo_jours_p1", "gain_mois_pct"],
                                               ascending=[False, True, False])
        ok = ok[~pd.Series([same_rule_key(r.symbole, r.timeframe, json.loads(r.candidate)) for r in ok.itertuples()],
                           index=ok.index).duplicated()].head(60)
        for i, (_, r) in enumerate(ok.iterrows(), 1):
            best_rows += (f"<tr><td><b>{i}</b></td><td>{fiche(r['symbole'], r['timeframe'], json.loads(r['candidate']), r['strategie'][:110])}</td>"
                          f"<td>{esc(kind(r))}</td><td>{esc(r['symbole'])}</td><td>{esc(r['timeframe'])}</td>"
                          f"<td>{esc(str(r['risque']))}</td><td class='pos'>{_fmt(r['ftmo_pass'])} %</td>"
                          f"<td>{_fmt(r['ftmo_jours_p1'], '{:.0f}')}</td>"
                          f"<td>{esc(_pair(r, 'total') or '—')}</td><td>{esc(_pair(r, 'oos') or '—')}</td>"
                          f"<td>{_fmt(r['gain_mois_pct'], '{:+.2f}')} %</td>"
                          f"<td>{_fmt(r['avgR_oos'], '{:+.2f}')}</td><td>{_fmt(r['wr_oos'], '{:.0f}')} %</td>"
                          f"<td>{_fmt(r['trades_mois'])}</td><td>{_fmt(r['dd_oos_pct'])} %</td>"
                          f"<td>{_fmt(r.get('week_end_pct'), '{:.0f}')} %</td><td>{_fmt(r.get('duree_moy_h'), '{:.0f}')} h</td>"
                          f"<td>{bot1(r['symbole'], r['timeframe'], r['candidate'])}</td></tr>")
    best_tbl = ("<div class='scroll'><table><thead><tr><th>#</th><th>Stratégie (lien vers la fiche)</th><th>Type</th>"
                "<th>Marché</th><th>TF</th><th>Réglage</th><th>Réussite FTMO seule</th><th>Jours pour l'objectif</th>"
                "<th>Challenges réussis / ratés (tout l'historique)</th><th>Challenges réussis / ratés (hors-échantillon)</th>"
                "<th>Gain / mois</th><th>R moyen</th><th>Réussite</th><th>Trades / mois</th><th>DD max</th>"
                "<th>Trades gardés le week-end</th><th>Durée moyenne</th><th>Bot</th></tr></thead>"
                f"<tbody>{best_rows}</tbody></table></div>") if best_rows else \
        "<p class='mut'>Aucune stratégie validée pour l'instant.</p>"
    from .strategies import REGISTRY as _REG
    cat = d.catalog_ranking()
    cat_rows = ""
    for i, (_, r) in enumerate(cat.iterrows(), 1):
        nm = r["meilleure_version_de"]
        cat_rows += (f"<tr><td>{i}</td><td>{fiche(r['symbole'], r['timeframe'], json.loads(r['candidate']), nm)}</td>"
                     f"<td>{esc(_REG[nm].family if nm in _REG else '')}</td><td>{esc(r['symbole'])} {esc(r['timeframe'])}</td>"
                     f"<td>{esc(str(r['risque']))}</td><td class='{'good' if r['_ok'] else ''}'>{esc(str(r['verdict']))}</td>"
                     f"<td>{_fmt(r['trades_oos'], '{:.0f}')}</td><td>{_fmt(r['avgR_oos'], '{:+.2f}')}</td>"
                     f"<td>{_fmt(r['pf_oos'], '{:.2f}')}</td><td>{_fmt(r['gain_mois_pct'], '{:+.2f}')} %</td>"
                     f"<td>{_fmt(r['ftmo_pass'])} %</td><td>{_fmt(r.get('trades_mois'))}</td>"
                     f"<td>{bot1(r['symbole'], r['timeframe'], r['candidate'])}</td></tr>")
    cat_tbl = ("<div class='scroll'><table><thead><tr><th>#</th><th>Stratégie</th><th>Famille</th><th>Meilleur marché</th>"
               "<th>Meilleur réglage</th><th>Verdict</th><th>Trades OOS</th><th>R moyen OOS</th><th>PF OOS</th>"
               "<th>Gain / mois</th><th>Réussite FTMO seule</th><th>Trades / mois</th><th>Bot</th></tr></thead>"
               f"<tbody>{cat_rows}</tbody></table></div>") if cat_rows else "<p class='mut'>—</p>"
    trial_rows = ""
    if len(d.allr):
        tr = d.allr[d.allr["verdict"].astype(str).str.startswith("À L'ESSAI")].sort_values("avgR_oos", ascending=False)
        for _, r in tr.head(60).iterrows():
            trial_rows += (f"<tr><td>{fiche(r['symbole'], r['timeframe'], json.loads(r['candidate']), r['strategie'][:110])}</td>"
                           f"<td>{esc(r['symbole'])}</td><td>{esc(r['timeframe'])}</td><td>{esc(str(r['risque']))}</td>"
                           f"<td>{_fmt(r['trades_oos'], '{:.0f}')}</td><td>{_fmt(r['avgR_oos'], '{:+.2f}')}</td>"
                           f"<td>{_fmt(r['pf_oos'], '{:.2f}')}</td><td>{_fmt(r['ftmo_pass'])} %</td>"
                           f"<td class='mut'>{esc(str(r['verdict']).split('—')[-1].strip())}</td>"
                           f"<td>{_fmt(r.get('trades_mois'))}</td>"
                           f"<td>{bot1(r['symbole'], r['timeframe'], r['candidate'])}</td></tr>")
    gen_rows = ""
    for _, r in d.genius_rows().head(80).iterrows():
        gen_rows += (f"<tr><td><b>{esc(str(r['trouve_par']).replace('Génie 1 ', '').replace('Génie 2 ', ''))}</b></td>"
                     f"<td>{fiche(r['symbole'], r['timeframe'], json.loads(r['candidate']), r['strategie'][:160])}</td>"
                     f"<td>{esc(r['symbole'])} {esc(r['timeframe'])}</td><td>{esc(str(r['risque']))}</td>"
                     f"<td class='{'good' if r['_ok'] else ''}'>{esc(str(r['verdict']))}</td>"
                     f"<td>{_fmt(r['trades_oos'], '{:.0f}')}</td><td>{_fmt(r['avgR_oos'], '{:+.2f}')}</td>"
                     f"<td>{_fmt(r['pf_oos'], '{:.2f}')}</td><td>{_fmt(r['ftmo_pass'])} %</td>"
                     f"<td>{_fmt(r.get('trades_mois'))}</td><td>{bot1(r['symbole'], r['timeframe'], r['candidate'])}</td></tr>")
    gen_tbl = ("<div class='scroll'><table><thead><tr><th>Génie</th><th>Loi (lien vers la fiche : formule et symboles)</th>"
               "<th>Marché</th><th>Réglage</th><th>Verdict hors-échantillon</th><th>Trades OOS</th><th>R moyen OOS</th>"
               f"<th>PF OOS</th><th>Réussite FTMO seule</th><th>Trades / mois</th><th>Bot</th></tr></thead><tbody>{gen_rows}</tbody></table></div>"
               if gen_rows else "<p class='mut'>Pas encore de découverte (relancez la recherche avec la nouvelle version).</p>")
    trial_tbl = ("<div class='scroll'><table><thead><tr><th>Stratégie</th><th>Marché</th><th>TF</th><th>Réglage</th>"
                 "<th>Trades OOS</th><th>R moyen OOS</th><th>PF OOS</th><th>Réussite FTMO seule</th>"
                 f"<th>Pourquoi pas validée</th><th>Trades / mois</th><th>Bot</th></tr></thead><tbody>{trial_rows}</tbody></table></div>"
                 if trial_rows else "<p class='mut'>Aucune.</p>")
    trial_banner = ('<p class="card" style="border-color:#d97706"><b>ATTENTION : stratégie combinée À L\'ESSAI.</b> '
                    "Aucune stratégie n'a passé toute la validation : celle-ci est faite de stratégies non validées mais "
                    "gagnantes hors-échantillon. Suivez-la en paper trading (option C) ; pas de bot tant qu'elle n'a "
                    "pas fait ses preuves en direct.</p>") if c and c.get("essai") else ""
    fl = d.failles()
    fl_rows = "".join(
        f"<tr><td>{esc(f['symbole'])} {esc(f['timeframe'])}</td><td>{esc(f.get('agent', ''))}</td>"
        f"<td>{esc(f.get('description', ''))}</td><td>{_fmt(f.get('t'))}</td><td>{_fmt(f.get('t_controle'))}</td>"
        f"<td>{esc(f.get('strategie', ''))}</td></tr>" for f in fl)
    fl_tbl = ("<div class='scroll'><table><thead><tr><th>Marché</th><th>Analyste</th><th>Faille</th><th>t (recherche)</th>"
              "<th>t (contrôle du Chef C)</th><th>Stratégie jouée</th></tr></thead>"
              f"<tbody>{fl_rows}</tbody></table></div>") if fl_rows else "<p class='mut'>Aucune faille confirmée.</p>"
    direc = "".join(f"<li>{esc(x)}</li>" for x in d.directives) or "<li class='mut'>Aucune : toutes les cases avaient des stratégies validées.</li>"
    doc = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Classement du Directeur</title><style>{CSS}</style></head><body><main>
<h1>Classement général du Directeur</h1>
<p><a href="fiches_strategies.html">Toutes les fiches détaillées des stratégies</a> · <a href="comparaison.html">Comparaison par marché et timeframe</a> · <a href="#melanges"><b>Mélange des stratégies combinées</b></a></p>
<p class="mut">{esc(d.cfg.ftmo.label())} · risque par trade {min(d.cfg.risk_levels):g} à {d.cfg.risk_pct:g} % ·
perte possible max {d.cfg.day_budget:g} % par jour · {len(d.cfg.symbols)} marchés × {len(tfs)} timeframes</p>
<h2>La stratégie combinée</h2>
{('<div class="cards">' + cards + '</div><p>' + button(slim(c), "Bot MT5 de la stratégie combinée") + '</p>') if c else '<p class="mut">Pas encore de stratégie combinée : aucune stratégie validée.</p>'}
{('<p class="mut">Composants tradés ENSEMBLE sur un seul compte. Période commune testée : ' + esc(' → '.join(res.get('fenetre', ('', '')))) + ', ' + str(res.get('trades', '')) + ' trades.</p>') if c else ''}
{('<div class="scroll"><table><thead><tr><th>N°</th><th>Marché</th><th>TF</th><th>Stratégie</th><th>Réglage</th><th>Risque par trade</th><th>Réussite seule</th><th>Trades / mois</th><th>Bot seul</th></tr></thead><tbody>' + comp_rows + '</tbody></table></div>') if c else ''}
<h2 id="melanges">Le Chef des combinaisons : la combinée serait-elle meilleure mélangée avec d'autres ?</h2>
<p class="mut">Il mélange la stratégie combinée du Directeur avec chaque autre combinaison (autres horaires, portefeuille du
Chef FTMO, combinaison du direct, toutes ensemble), puis retire ce qui ne sert à rien et règle le risque. Le mélange
n'est gardé que s'il fait réussir le challenge plus vite, sans plus d'échecs.
{esc(('Résultat : la stratégie combinée ci-dessus a été AMÉLIORÉE avec ' + c['amelioree_avec'] + '.') if c and c.get('amelioree_avec') else '')}</p>
{_combos_html(getattr(d, 'combo_rows', []), (c or {}).get('melanges_note'), 'melanges' in (c or {}))}
<h2 id="compte">Quel compte FTMO choisir ? 1 étape ou 2 étapes, Standard ou Swing</h2>
{_advice_html(getattr(d, 'advice', None) or (c or {}).get('conseil_compte'))}
<h2 id="heures">Meilleures heures de chaque stratégie</h2>
{_hours_html(getattr(d, 'hour_rows', None) or _load_json(d.cfg.out / 'heures_strategies.json'))}
<h2>Horaires : 24h/24 ou seulement le jour ?</h2>
<p class="mut">Même travail refait avec des entrées permises seulement dans l'horaire (heure locale, serveur MT5 moins
{d.cfg.server_offset:g} h). Les positions ouvertes gardent leur SL et TP chez le courtier après la fin de l'horaire.
Chaque horaire a son fichier (strategie_combinee_*.json) pour le paper trading et le bot.</p>
{sess}
<h2>Scénarios de perte max par jour</h2>
<p class="mut">Pour chaque scénario, le Directeur construit la meilleure stratégie combinée (composants, R:R, risque par trade).
Le scénario retenu est celui qui donne un challenge RÉUSSI le plus vite (jours attendus, reprises comprises),
en ratant au plus {d.cfg.max_fail:g} % des challenges.</p>
{scen}
{trial_banner}
<h2>Les autres comptes : compte perso et compte financé</h2>
{_accounts_html(d)}
<h2>Ce que dit le direct (paper trading)</h2>
{_direct_html(d)}
<h2 id="genies">Les découvertes des génies : Einstein et Hawking</h2>
<p class="mut">Deux génies qui travaillent seuls et inventent des lois mathématiques (physique : vitesse, énergie,
ressort, relativité ; maths et cosmologie : Hurst, entropie, queues, cycles, gravité), en se servant aussi des
stratégies des agents et des autres marchés. Aucun chef ne les confirme ; leurs lois passent seulement le même test
hors-échantillon que tout le monde, pour distinguer une vraie loi d'un hasard.</p>
{gen_tbl}
<h2>Stratégies à l'essai (non validées mais gagnantes hors-échantillon : paper trading seulement)</h2>
{trial_tbl}
<h2>Classement des meilleures stratégies validées</h2>
<p class="mut">Tous marchés, timeframes et équipes confondus. Réussite = challenge FTMO tradé avec cette stratégie SEULE
({d.cfg.lab_risk_pct:g} % par trade). La stratégie combinée ci-dessus les assemble pour aller plus vite.</p>
{best_tbl}
<h2>Classement du catalogue : la meilleure version de chaque stratégie</h2>
<p class="mut">Chacune des stratégies du catalogue (SMC, Stochastique, zones, chandeliers…) optimisée par les agents :
réglages, R:R, stop, gestion, filtre et sens. Résultats hors-échantillon ; seule la mention APPROUVÉ indique un edge validé.</p>
{cat_tbl}
<h2>Failles des algorithmes des banques (équipe C)</h2>
<p class="mut">Empreintes statistiques des gros acteurs trouvées par les 5 analystes et confirmées par le Chef C sur une période
qu'ils n'avaient pas vue. Chaque faille est aussi jouée comme stratégie et passe la validation finale.</p>
{fl_tbl}
<h2>Test sur tous les timeframes</h2>
<p class="mut">Chaque composant rejoué sans aucune réoptimisation sur les 35 % les plus récents de chaque timeframe de son marché (R moyen par trade).</p>
{('<div class="scroll"><table><thead><tr><th>Composant</th>' + ''.join(f'<th>{t}</th>' for t in tfs) + '<th>Verdict</th></tr></thead><tbody>' + mtf + '</tbody></table></div>') if mtf else '<p class="mut">—</p>'}
<h2>Revue des chefs et des agents</h2>
<div class="scroll"><table><thead><tr><th>Marché</th><th>TF</th><th>Validées</th><th>Meilleure réussite FTMO</th><th>Meilleur gain/mois</th><th>Inventions validées</th><th>Note du Directeur</th></tr></thead><tbody>{rev}</tbody></table></div>
<h2>Directives données</h2><ul>{direc}</ul>
<h2>Journal du Directeur</h2><pre>{esc(chr(10).join(d.journal))}</pre>
<p class="warn">Ces chiffres supposent que les stratégies continuent de se comporter comme sur la période que personne n'a vue
pendant la recherche. Confirmez en paper trading (menu, option « stratégie combinée ») avant tout challenge réel.</p>
</main>{script(d.cfg.out)}</body></html>"""
    (d.cfg.out / "directeur.html").write_text(doc, encoding="utf-8")
    from .resultats import write_results_page  # LA page unique : TOP 10 et tout le reste
    write_results_page(d.cfg.out, d.cfg.ftmo.label(), getattr(d, "commission_text", None))
