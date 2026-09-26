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

import html
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .backtest import RiskConfig, run_backtest
from .compare import build_comparison
from .evaluator import candidate_key, compute_signal, describe
from .ftmo import FtmoRules, apply_risk_rules, daily_table, simulate
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
    day_budget: float = 1.0            # perte max possible par jour (réalisé + positions ouvertes + nouveau trade)
    lab_risk_pct: float = 0.5          # risque utilisé pendant la recherche des chefs (pour noter les stratégies)
    ftmo: FtmoRules = field(default_factory=FtmoRules)
    rounds: int = 3
    budget: int = 1000
    invent_generations: int = 8
    reuse: bool = True                 # reprendre les résultats déjà calculés
    second_pass: bool = True           # relancer les cases faibles en mode intensif
    max_components: int = 8
    min_window_days: int = 45
    seed: int = 7


def _fmt(v, f="{:.1f}"):
    try:
        return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f.format(v)
    except (TypeError, ValueError):
        return str(v)


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
                         invent_attempts=3 + (2 if intensive else 0), seeds=seeds or [], invent_bias=bias or [])

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

    # --------------------------------------------------------------------------- 1. passe 1
    def pass1(self):
        lv = self.levels()
        self.say(f"Mission : {self.cfg.ftmo.label()} ; risque par trade essayé : {', '.join(f'{x:g}' for x in lv)} % ; "
                 f"perte possible max {self.cfg.day_budget:g} % par jour ; "
                 f"{len(self.cfg.symbols)} marchés × {len(self.cfg.timeframes)} timeframes")
        for sym in self.cfg.symbols:
            for tf in self.cfg.timeframes:
                done = (self.cfg.out / f"{sym}_{tf}" / "classement.csv").exists()
                if done and self.cfg.reuse:
                    self.say(f"{sym} {tf} : je reprends le travail déjà fait par les chefs")
                    continue
                self.run_cell(sym, tf, self.lab_cfg(), "je confie la case aux 2 chefs et à leurs 10 agents")

    # --------------------------------------------------------------------------- 2. revue
    def review(self) -> pd.DataFrame:
        allr = build_comparison(self.cfg.out, self.cfg.capital, self.cfg.ftmo, self.cfg.lab_risk_pct)
        if not len(allr):
            self.say("Aucun résultat à examiner.")
            return allr
        allr = allr[allr["symbole"].isin(self.cfg.symbols) & allr["timeframe"].isin(self.cfg.timeframes)]
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
        ok = allr[allr["_ok"] & allr["oos_debut"].notna()].copy()
        trades, windows, info = {}, {}, {}
        for label in (ok["symbole"] + "_" + ok["timeframe"]).unique():
            path = self.cfg.out / label / "trades_oos.csv"
            if not path.exists():
                continue
            t = pd.read_csv(path)
            for k, g in t.groupby("key"):
                trades[f"{label}|{k}"] = g[["entry_time", "exit_time", "r"]].reset_index(drop=True)
        for r in ok.itertuples():
            key = f"{r.symbole}_{r.timeframe}|{candidate_key(json.loads(r.candidate))}"
            if key in trades and key not in info:
                windows[key] = (pd.Timestamp(r.oos_debut), pd.Timestamp(r.oos_fin))
                info[key] = {"symbole": r.symbole, "timeframe": r.timeframe, "candidate": json.loads(r.candidate),
                             "strategie": r.strategie, "risque": r.risque, "seule_ftmo": r.ftmo_pass}
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

    @staticmethod
    def _better(a, b) -> bool:
        """Plus de réussite ; à réussite égale (±0,5 pt), plus rapide ; puis moins d'échecs."""
        if a is None or math.isnan(a.get("ftmo_pass", float("nan"))):
            return False
        if b is None:
            return True
        if a["ftmo_pass"] > b["ftmo_pass"] + 0.5:
            return True
        if a["ftmo_pass"] < b["ftmo_pass"] - 0.5:
            return False
        da, db = a.get("ftmo_jours_p1", np.nan), b.get("ftmo_jours_p1", np.nan)
        if not np.isnan(da) and (np.isnan(db) or da < db * 0.95):
            return True
        return (not np.isnan(da) and not np.isnan(db) and da <= db * 1.05
                and a.get("ftmo_echec_p1", 100) < b.get("ftmo_echec_p1", 100) - 0.5)

    def build_combined(self, allr: pd.DataFrame) -> dict:
        trades, windows, info = self._pool(allr)
        if not trades:
            self.say("Pas encore de stratégie validée avec des trades : impossible de construire la stratégie combinée.")
            return {}
        def solo(k):
            v = info[k]["seule_ftmo"]
            return -1.0 if v is None or v != v else float(v)
        cand = sorted(info, key=solo, reverse=True)[:30]
        R = self.cfg.risk_pct
        self.say(f"Je construis la stratégie combinée à partir de {len(cand)} stratégies validées "
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
                    self.say(f"+ composant {len(keys)} : {info[pick]['symbole']} {info[pick]['timeframe']} | "
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
                    self.say(f"- je retire {info[k]['strategie']} ({info[k]['symbole']} {info[k]['timeframe']}) : "
                             f"elle n'apportait plus rien")
            if best is before:
                break
            ds_txt = "aucun" if rules["day_stop"] is None else f"-{rules['day_stop']:g} %"
            self.say(f"Réglages après le tour {rnd + 1} : arrêt journalier {ds_txt}, "
                     f"max positions {rules['max_open'] or 'illimité'} -> réussite {best['ftmo_pass']:.1f} %, "
                     f"~{_fmt(best['ftmo_jours_p1'], '{:.0f}')} jours")
        if not keys:
            return {}
        final = self._eval(keys, weights, rules["day_stop"], rules["max_open"], trades, windows, n=5000)
        comps = [{"symbole": info[k]["symbole"], "timeframe": info[k]["timeframe"], "candidate": info[k]["candidate"],
                  "strategie": info[k]["strategie"], "risque_config": info[k]["risque"], "risk_pct": weights[k],
                  "reussite_seule": info[k]["seule_ftmo"]} for k in keys]
        rules = {**rules, "day_budget": self.cfg.day_budget}
        self.combined = {"nom": "Stratégie combinée du Directeur", "ftmo_regles": self.cfg.ftmo.label(),
                         "risque_max_par_trade": R, "regles": rules, "resultat": final, "composants": comps,
                         "cree_le": time.strftime("%Y-%m-%d %H:%M")}
        (self.cfg.out / "strategie_combinee.json").write_text(
            json.dumps(self.combined, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        self.say(f"STRATÉGIE COMBINÉE : {len(keys)} composants, réussite {final['ftmo_pass']:.1f} %, "
                 f"+{self.cfg.ftmo.target1:g} % en ~{_fmt(final['ftmo_jours_p1'], '{:.0f}')} jours de bourse, "
                 f"échec {_fmt(final['ftmo_echec_p1'])} %, pire journée {final['pire_jour']:.2f} %")
        return self.combined

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

    # --------------------------------------------------------------------------- campagne
    def run(self):
        t0 = time.time()
        self.pass1()
        allr = self.review()
        if self.cfg.second_pass and len(allr):
            self.pass2(allr)
            allr = self.review()
        if len(allr):
            self.build_combined(allr)
            if self.combined:
                self.multi_tf_test()
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
        f"<td>{esc(x['risque_config'])}</td><td><b>{x['risk_pct']:g} %</b></td><td>{_fmt(x['reussite_seule'])} %</td></tr>"
        for i, x in enumerate(c.get("composants", []) if c else [], 1))
    tfs = d.cfg.timeframes
    mtf = "".join(
        "<tr><td>" + esc(r["composant"]) + "</td>" + "".join(
            (lambda v: f"<td class='{'good' if v.get('trades', 0) >= 10 and v.get('avg_r', -1) > 0 else ''}'>"
                       + (esc(v['erreur'][:30]) if 'erreur' in v else f"{v['avg_r']:+.2f}R<br><span class='mut'>{v['trades']} trades</span>")
                       + "</td>")(r["tfs"].get(tf, {"erreur": "—"})) for tf in tfs)
        + f"<td><b>{'robuste' if r['robuste'] else 'spécifique'}</b> ({r['positifs']})</td></tr>" for r in d.multi_tf)
    rev = "".join(
        f"<tr><td>{esc(r['symbole'])}</td><td>{esc(r['timeframe'])}</td><td>{r['validees']}</td>"
        f"<td>{_fmt(r['meilleure_ftmo'])} %</td><td>{_fmt(r['meilleur_gain_mois'], '{:+.2f}')} %</td>"
        f"<td>{r['inventions_validees']}/{r['inventions']}</td><td>{esc(r['note'])}</td></tr>" for r in d.review_rows)
    direc = "".join(f"<li>{esc(x)}</li>" for x in d.directives) or "<li class='mut'>Aucune : toutes les cases avaient des stratégies validées.</li>"
    doc = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Rapport du Directeur</title><style>{CSS}</style></head><body><main>
<h1>Rapport du Directeur</h1>
<p class="mut">{esc(d.cfg.ftmo.label())} · risque par trade {min(d.cfg.risk_levels):g} à {d.cfg.risk_pct:g} % ·
perte possible max {d.cfg.day_budget:g} % par jour · {len(d.cfg.symbols)} marchés × {len(tfs)} timeframes</p>
<h2>La stratégie combinée</h2>
{('<div class="cards">' + cards + '</div>') if c else '<p class="mut">Pas encore de stratégie combinée : aucune stratégie validée.</p>'}
{('<p class="mut">Composants tradés ENSEMBLE sur un seul compte. Période commune testée : ' + esc(' → '.join(res.get('fenetre', ('', '')))) + ', ' + str(res.get('trades', '')) + ' trades.</p>') if c else ''}
{('<div class="scroll"><table><thead><tr><th>N°</th><th>Marché</th><th>TF</th><th>Stratégie</th><th>Réglage</th><th>Risque par trade</th><th>Réussite seule</th></tr></thead><tbody>' + comp_rows + '</tbody></table></div>') if c else ''}
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
