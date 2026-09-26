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

from .backtest import RR_LEVELS, RiskConfig, run_backtest
from .compare import build_comparison
from .data import DEFAULT_YEARS
from .evaluator import candidate_key, compute_signal, describe
from .ftmo import FtmoRules, apply_risk_rules, daily_table, simulate, to_dt
from .lab import LabConfig, run_lab
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
    seed: int = 7


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
        self.allr = pd.DataFrame()
        self.card_ids: dict = {}
        self.multi_tf: list[dict] = []

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
                         catalog=c.catalog, bank_teams=c.bank_teams)

    def run_cell(self, sym, tf, lab_cfg: LabConfig, why: str):
        try:
            df, cost = self.data(sym, tf)
        except Exception as exc:
            self.say(f"{sym} {tf} : pas de données ({exc}), case ignorée")
            return None
        if len(df) < 1000:
            self.say(f"{sym} {tf} : seulement {len(df)} bougies, case ignorée (il en faut 1000)")
            return None
        self.say(f"{sym} {tf} : {why}")
        return run_lab(df, cost, lab_cfg, f"{sym}_{tf}", self.cfg.out / f"{sym}_{tf}")

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
                    if got is not None and got >= need * 0.9:
                        self.say(f"{sym} {tf} : je reprends le travail déjà fait par les chefs ({got:.1f} ans testés)")
                        continue
                    self.say(f"{sym} {tf} : l'ancienne recherche ne couvrait que "
                             f"{'une période inconnue' if got is None else f'{got:.1f} ans'} ; j'exige {need:g} ans, "
                             f"je la fais refaire")
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
    def _pool(self, allr: pd.DataFrame):
        """Stratégies validées (sans doublons) + leurs variantes de R:R qui restent gagnantes hors-échantillon."""
        ok = allr[allr["_ok"] & allr["oos_debut"].notna()].copy()
        trades, windows, info = {}, {}, {}
        for label in (ok["symbole"] + "_" + ok["timeframe"]).unique():
            path = self.cfg.out / label / "trades_oos.csv"
            if not path.exists():
                continue
            t = pd.read_csv(path)
            t["entry_time"], t["exit_time"] = to_dt(t["entry_time"]), to_dt(t["exit_time"])
            for k, g in t.groupby("key"):
                trades[f"{label}|{k}"] = g[["entry_time", "exit_time", "r"]].reset_index(drop=True)
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
                             "strategie": r.strategie, "risque": r.risque, "seule_ftmo": r.ftmo_pass, "variante": False}
        if self.cfg.rr_variants:
            n_var = 0
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
                for rr in RR_LEVELS:
                    if rr == c["risk"]["rr"] or (rr is None and c["risk"]["management"] == "breakeven"):
                        continue
                    v = copy.deepcopy(c)
                    v["risk"]["rr"] = rr
                    res, tr = run_backtest(df[mask], sig[mask], RiskConfig(**v["risk"]), cost=cost,
                                           risk_pct=self.cfg.lab_risk_pct, return_trades=True)
                    if res.trades < 20 or res.avg_r <= 0 or res.profit_factor < 1.1:
                        continue
                    k2 = f"{base['symbole']}_{base['timeframe']}|{candidate_key(v)}"
                    if k2 in info:
                        continue
                    trades[k2] = tr[["entry_time", "exit_time", "r"]].reset_index(drop=True)
                    windows[k2] = (lo, hi)
                    solo = simulate(daily_table(trades[k2], self.cfg.lab_risk_pct, lo, hi), self.cfg.ftmo, 1500, seed=0)
                    info[k2] = {**base, "candidate": v, "risque": RiskConfig(**v["risk"]).label(),
                                "seule_ftmo": solo["ftmo_pass"], "variante": True}
                    n_var += 1
            self.say(f"Variantes de R:R : {n_var} variantes restent gagnantes hors-échantillon et rejoignent le choix")
        return trades, windows, info

    def levels(self) -> list[float]:
        """Niveaux de risque autorisés : <= risque max, et un seul stop (+10 % de frais) doit tenir dans le budget du jour."""
        return sorted(l for l in self.cfg.risk_levels if l <= self.cfg.risk_pct and l * 1.1 <= self.cfg.day_budget + 1e-9)

    def _eval(self, keys, weights, day_stop, max_open, trades, windows, n=1500):
        lo = max(windows[k][0] for k in keys)
        hi = min(windows[k][1] for k in keys)
        if (hi - lo).days < self.cfg.min_window_days:
            return None
        parts = []
        for k in keys:
            t = trades[k].assign(w=weights[k])
            e = pd.to_datetime(t["entry_time"].astype(str), format="mixed")
            x = pd.to_datetime(t["exit_time"].astype(str), format="mixed")
            parts.append(t[(e >= lo) & (x <= hi)])
        merged = apply_risk_rules(pd.concat(parts, ignore_index=True), day_stop, max_open, self.cfg.risk_pct,
                                  day_budget=self.cfg.day_budget)
        daily = daily_table(merged, self.cfg.risk_pct, lo, hi)
        res = simulate(daily, self.cfg.ftmo, n, seed=0)
        res["fenetre"] = (str(lo.date()), str(hi.date()))
        res["trades"] = int(len(merged))
        res["pire_jour"] = float(daily["worst"].min()) if len(daily) else 0.0
        return res

    def _better(self, a, b) -> bool:
        """Échecs sous le seuil toléré d'abord ; puis plus de réussite ; à réussite égale (±0,5 pt), plus rapide ;
        puis moins d'échecs."""
        if a is None or math.isnan(a.get("ftmo_pass", float("nan"))):
            return False
        if b is None:
            return True
        a_ok = a.get("ftmo_echec_p1", 100) <= self.cfg.max_fail
        b_ok = b.get("ftmo_echec_p1", 100) <= self.cfg.max_fail
        if a_ok != b_ok:
            return a_ok
        if a["ftmo_pass"] > b["ftmo_pass"] + 0.5:
            return True
        if a["ftmo_pass"] < b["ftmo_pass"] - 0.5:
            return False
        da, db = a.get("ftmo_jours_p1", np.nan), b.get("ftmo_jours_p1", np.nan)
        if not np.isnan(da) and (np.isnan(db) or da < db * 0.95):
            return True
        return (not np.isnan(da) and not np.isnan(db) and da <= db * 1.05
                and a.get("ftmo_echec_p1", 100) < b.get("ftmo_echec_p1", 100) - 0.5)

    def build_combined(self, allr: pd.DataFrame, pool=None, quiet=False) -> dict:
        trades, windows, info = pool or self._pool(allr)
        say = (lambda m: None) if quiet else self.say
        if not trades:
            say("Pas encore de stratégie validée avec des trades : impossible de construire la stratégie combinée.")
            return {}
        def solo(k):
            v = info[k]["seule_ftmo"]
            return -1.0 if v is None or v != v else float(v)
        cand = sorted(info, key=solo, reverse=True)[:40]
        R = self.cfg.risk_pct
        say(f"Je construis la stratégie combinée à partir de {len(cand)} stratégies validées "
                 f"(tous marchés et timeframes).")
        keys: list[str] = []
        weights: dict = {}
        rules = {"day_stop": None, "max_open": None}
        best = None
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
                        res = self._eval(keys + [k], w, rules["day_stop"], rules["max_open"], trades, windows)
                        if self._better(res, best) and (pick_res is None or self._better(res, pick_res)):
                            pick, pick_res, pick_w = k, res, lvl
                if pick:
                    keys.append(pick)
                    weights[pick] = pick_w
                    best = pick_res
                    improved = True
                    say(f"+ composant {len(keys)} : {info[pick]['symbole']} {info[pick]['timeframe']} | "
                             f"{info[pick]['strategie']} à {pick_w:g} %/trade -> réussite {best['ftmo_pass']:.1f} %, "
                             f"+{self.cfg.ftmo.target1:g} % en ~{_fmt(best['ftmo_jours_p1'], '{:.0f}')} jours")
            if not keys:
                break
            # b) régler l'arrêt journalier et le nombre max de positions
            before = best
            for ds in day_stops:
                for mo in (None, 2, 3, 4, 6):
                    if mo is not None and mo >= len(keys) + 1:
                        continue
                    res = self._eval(keys, weights, ds, mo, trades, windows)
                    if self._better(res, best):
                        best, rules = res, {"day_stop": ds, "max_open": mo}
            # c) régler le risque de chaque composant (jamais au-dessus du risque max)
            for k in list(keys):
                for w in sorted(self.levels(), reverse=True):
                    trial = {**weights, k: round(w, 4)}
                    res = self._eval(keys, trial, rules["day_stop"], rules["max_open"], trades, windows)
                    if self._better(res, best):
                        best, weights = res, trial
            # d) retirer ce qui ne sert plus
            for k in list(keys):
                if len(keys) <= 1:
                    break
                rest = [x for x in keys if x != k]
                res = self._eval(rest, weights, rules["day_stop"], rules["max_open"], trades, windows)
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
                     f"max positions {rules['max_open'] or 'illimité'} -> réussite {best['ftmo_pass']:.1f} %, "
                     f"~{_fmt(best['ftmo_jours_p1'], '{:.0f}')} jours")
        if not keys:
            return {}
        final = self._eval(keys, weights, rules["day_stop"], rules["max_open"], trades, windows, n=5000)
        comps = [{"symbole": info[k]["symbole"], "timeframe": info[k]["timeframe"], "candidate": info[k]["candidate"],
                  "strategie": info[k]["strategie"], "risque_config": info[k]["risque"], "risk_pct": weights[k],
                  "reussite_seule": info[k]["seule_ftmo"], "variante_rr": info[k].get("variante", False)}
                 for k in keys]
        rules = {**rules, "day_budget": self.cfg.day_budget, "total_budget": self.cfg.total_budget}
        self._last_setup = (list(keys), dict(weights), dict(rules))
        combined = {"nom": "Stratégie combinée du Directeur", "ftmo_regles": self.cfg.ftmo.label(),
                    "risque_max_par_trade": R, "regles": rules, "resultat": final, "composants": comps,
                    "cree_le": time.strftime("%Y-%m-%d %H:%M")}
        say(f"STRATÉGIE COMBINÉE : {len(keys)} composants, réussite {final['ftmo_pass']:.1f} %, "
                 f"+{self.cfg.ftmo.target1:g} % en ~{_fmt(final['ftmo_jours_p1'], '{:.0f}')} jours de bourse, "
                 f"échec {_fmt(final['ftmo_echec_p1'])} %, pire journée {final['pire_jour']:.2f} %")
        return combined

    def scenarios(self, allr: pd.DataFrame) -> dict:
        """Une stratégie combinée par scénario de perte max par jour ; on garde celle qui passe le plus vite."""
        pool = self._pool(allr)
        if not pool[0]:
            self.say("Pas encore de stratégie validée avec des trades : impossible de construire la stratégie combinée.")
            return {}
        cap = self.cfg.day_budget
        budgets = sorted({b for b in self.cfg.day_budgets if b <= cap} | {cap})
        self.scenario_rows = []
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
                r = self._eval(keys, w, rules.get("day_stop"), rules.get("max_open"), *pool[:2], n=5000)
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
                                    "reussite_seule": info[k]["seule_ftmo"],
                                    "variante_rr": info[k].get("variante", False)} for k in keys],
                    "cree_le": time.strftime("%Y-%m-%d %H:%M")}
            self.scenario_rows.append({"budget": b, "reussite": res["ftmo_pass"], "jours": res["ftmo_jours_p1"],
                                       "echec": res["ftmo_echec_p1"], "pire_jour": res["pire_jour"],
                                       "composants": len(comb["composants"]),
                                       "risques": ", ".join(f"{c['risk_pct']:g}" for c in comb["composants"]),
                                       "rr": ", ".join(RiskConfig(**c["candidate"]["risk"]).label().split("|")[1].strip()
                                                       for c in comb["composants"])})
            self.say(f"Scénario perte max {b:g} %/jour : réussite {res['ftmo_pass']:.1f} %, +{self.cfg.ftmo.target1:g} % "
                     f"en ~{_fmt(res['ftmo_jours_p1'], '{:.0f}')} jours, échec {_fmt(res['ftmo_echec_p1'])} %, "
                     f"pire journée {res['pire_jour']:.2f} %, {len(comb['composants'])} composants")
            if self._better(res, best["resultat"] if best else None):
                best, best_b = comb, b
        self.cfg.day_budget = cap
        if not best:
            return {}
        best["scenarios"] = self.scenario_rows
        best["scenario_choisi"] = best_b
        self.combined = best
        (self.cfg.out / "strategie_combinee.json").write_text(
            json.dumps(best, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        res = best["resultat"]
        self.say(f"MEILLEUR SCÉNARIO : perte max {best_b:g} %/jour -> réussite {res['ftmo_pass']:.1f} %, "
                 f"+{self.cfg.ftmo.target1:g} % en ~{_fmt(res['ftmo_jours_p1'], '{:.0f}')} jours de bourse")
        for i, c in enumerate(best["composants"], 1):
            self.say(f"  {i}. {c['symbole']} {c['timeframe']} | {c['strategie']} | {c['risque_config']} | "
                     f"{c['risk_pct']:g} %/trade" + (" (variante R:R)" if c.get("variante_rr") else ""))
        return best

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
                    "Trades hors-échantillon": None if pd.isna(r.get("trades_oos")) else int(r.get("trades_oos")),
                    "Taux de réussite OOS (%)": r.get("wr_oos"),
                    "R moyen OOS": r.get("avgR_oos"), "Profit factor OOS": r.get("pf_oos"),
                    "Gain par mois (%)": r.get("gain_mois_pct"), "Drawdown max OOS (%)": r.get("dd_oos_pct"),
                    "Réussite FTMO seule (%)": r.get("ftmo_pass"),
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
            card = build_card(cand, sym, tf, stats_of(row_for(sym, tf, cand)), risk, origin, rules)
            self.card_ids[key] = card["id"]
            cards.append(card)

        rules = {}
        if self.combined:
            rg = self.combined.get("regles", {})
            rules = {"Perte possible max par jour (%)": rg.get("day_budget"),
                     "Perte totale max (%)": rg.get("total_budget"), "Positions ouvertes max": rg.get("max_open"),
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
        if cards:
            path = write_cards(cards, self.cfg.out)
            self.say(f"{len(cards)} fiches détaillées écrites : {path}")

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
                self.multi_tf_test()
            self.build_cards()
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


def write_report(d: Director):
    esc = html.escape
    c = d.combined
    res = c.get("resultat", {}) if c else {}
    rules = c.get("regles", {}) if c else {}
    cards = ""
    if c:
        cards = "".join(f"<div class='card'><span class='mut'>{esc(k)}</span><b>{esc(v)}</b></div>" for k, v in [
            ("Réussite du challenge", f"{_fmt(res.get('ftmo_pass'))} %"),
            (f"Jours de bourse pour +{d.cfg.ftmo.target1:g} % (médiane)", _fmt(res.get("ftmo_jours_p1"), "{:.0f}")),
            ("Échec (limite de perte touchée)", f"{_fmt(res.get('ftmo_echec_p1'))} %"),
            ("Composants", str(len(c["composants"]))),
            ("Pire journée (positions ouvertes au stop)", f"{res.get('pire_jour', 0):.2f} %"),
            ("Perte possible max par jour", f"{rules.get('day_budget', d.cfg.day_budget):g} %"),
            ("Arrêt journalier", "aucun" if rules.get("day_stop") is None else f"après -{rules['day_stop']:g} %"),
            ("Positions ouvertes max", str(rules.get("max_open") or "illimité"))])
    comp_rows = "".join(
        f"<tr><td>{i}</td><td>{esc(x['symbole'])}</td><td>{esc(x['timeframe'])}</td><td>{esc(x['strategie'])}</td>"
        f"<td>{esc(x['risque_config'])}{' <i>(variante R:R)</i>' if x.get('variante_rr') else ''}</td>"
        f"<td><b>{x['risk_pct']:g} %</b></td><td>{_fmt(x['reussite_seule'])} %</td></tr>"
        for i, x in enumerate(c.get("composants", []) if c else [], 1))
    tfs = d.cfg.timeframes
    mtf = "".join(
        "<tr><td>" + esc(r["composant"]) + "</td>" + "".join(
            (lambda v: f"<td class='{'good' if v.get('trades', 0) >= 10 and v.get('avg_r', -1) > 0 else ''}'>"
                       + (esc(v['erreur'][:30]) if 'erreur' in v else f"{v['avg_r']:+.2f}R<br><span class='mut'>{v['trades']} trades</span>")
                       + "</td>")(r["tfs"].get(tf, {"erreur": "—"})) for tf in tfs)
        + f"<td><b>{'robuste' if r['robuste'] else 'spécifique'}</b> ({r['positifs']})</td></tr>" for r in d.multi_tf)
    chosen = c.get("scenario_choisi") if c else None
    scen_rows = "".join(
        f"<tr><td class='{'good' if r['budget'] == chosen else ''}'><b>{r['budget']:g} %</b>{' (retenu)' if r['budget'] == chosen else ''}</td>"
        f"<td>{_fmt(r['reussite'])} %</td><td>{_fmt(r['jours'], '{:.0f}')}</td><td>{_fmt(r['echec'])} %</td>"
        f"<td>{r['pire_jour']:.2f} %</td><td>{r['composants']}</td><td>{esc(r['risques'])}</td><td>{esc(r['rr'])}</td></tr>"
        for r in d.scenario_rows)
    scen = ("<div class='scroll'><table><thead><tr><th>Perte max par jour</th><th>Réussite</th>"
            f"<th>Jours pour +{d.cfg.ftmo.target1:g} %</th><th>Échec</th><th>Pire journée</th><th>Composants</th>"
            f"<th>Risque par trade (%)</th><th>R:R</th></tr></thead><tbody>{scen_rows}</tbody></table></div>"
            if scen_rows else "<p class='mut'>—</p>")
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
                          f"<td>{_fmt(r['ftmo_jours_p1'], '{:.0f}')}</td><td>{_fmt(r['gain_mois_pct'], '{:+.2f}')} %</td>"
                          f"<td>{_fmt(r['avgR_oos'], '{:+.2f}')}</td><td>{_fmt(r['wr_oos'], '{:.0f}')} %</td>"
                          f"<td>{_fmt(r['trades_mois'])}</td><td>{_fmt(r['dd_oos_pct'])} %</td></tr>")
    best_tbl = ("<div class='scroll'><table><thead><tr><th>#</th><th>Stratégie (lien vers la fiche)</th><th>Type</th>"
                "<th>Marché</th><th>TF</th><th>Réglage</th><th>Réussite FTMO seule</th><th>Jours pour l'objectif</th>"
                "<th>Gain / mois</th><th>R moyen</th><th>Réussite</th><th>Trades / mois</th><th>DD max</th></tr></thead>"
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
                     f"<td>{_fmt(r['ftmo_pass'])} %</td></tr>")
    cat_tbl = ("<div class='scroll'><table><thead><tr><th>#</th><th>Stratégie</th><th>Famille</th><th>Meilleur marché</th>"
               "<th>Meilleur réglage</th><th>Verdict</th><th>Trades OOS</th><th>R moyen OOS</th><th>PF OOS</th>"
               "<th>Gain / mois</th><th>Réussite FTMO seule</th></tr></thead>"
               f"<tbody>{cat_rows}</tbody></table></div>") if cat_rows else "<p class='mut'>—</p>"
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
<p><a href="fiches_strategies.html">Toutes les fiches détaillées des stratégies</a> · <a href="comparaison.html">Comparaison par marché et timeframe</a></p>
<p class="mut">{esc(d.cfg.ftmo.label())} · risque par trade {min(d.cfg.risk_levels):g} à {d.cfg.risk_pct:g} % ·
perte possible max {d.cfg.day_budget:g} % par jour · {len(d.cfg.symbols)} marchés × {len(tfs)} timeframes</p>
<h2>La stratégie combinée</h2>
{('<div class="cards">' + cards + '</div>') if c else '<p class="mut">Pas encore de stratégie combinée : aucune stratégie validée.</p>'}
{('<p class="mut">Composants tradés ENSEMBLE sur un seul compte. Période commune testée : ' + esc(' → '.join(res.get('fenetre', ('', '')))) + ', ' + str(res.get('trades', '')) + ' trades.</p>') if c else ''}
{('<div class="scroll"><table><thead><tr><th>N°</th><th>Marché</th><th>TF</th><th>Stratégie</th><th>Réglage</th><th>Risque par trade</th><th>Réussite seule</th></tr></thead><tbody>' + comp_rows + '</tbody></table></div>') if c else ''}
<h2>Scénarios de perte max par jour</h2>
<p class="mut">Pour chaque scénario, le Directeur construit la meilleure stratégie combinée (composants, R:R, risque par trade).
Le scénario retenu est celui qui passe le challenge le plus souvent, puis le plus vite.</p>
{scen}
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
</main></body></html>"""
    (d.cfg.out / "directeur.html").write_text(doc, encoding="utf-8")
