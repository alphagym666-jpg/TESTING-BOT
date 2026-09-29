"""Analyse du DIRECT : ce que disent les trades fictifs réellement pris par la plateforme de paper trading.

Les trades du paper trading sont la meilleure vérification possible : ils sont pris sur des prix que personne n'a
jamais vus pendant la recherche. Cette analyse :
1. classe chaque stratégie suivie en direct (R total, R moyen, réussite, significativité, meilleur jour) ;
2. cherche la meilleure COMBINAISON de stratégies qui tournent (sur un seul compte, 1 % max par trade, perte
   possible max 2,5 %/jour, 10 % au total) pour réussir le challenge le plus vite ;
3. dit clairement combien de jours de données il y a : quelques jours ne prouvent rien, quelques semaines oui.
"""
from __future__ import annotations

import html
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .ftmo import FtmoRules, apply_risk_rules, count_challenges, daily_table, simulate, to_dt

MIN_DAYS_RELIABLE = 20  # jours de bourse de données en direct avant de faire confiance aux chiffres


def expected_days(res: dict) -> float:
    p, d = res.get("ftmo_pass", float("nan")), res.get("ftmo_jours_p1", float("nan"))
    if p is None or d is None or not p == p or not d == d or p <= 0:
        return float("inf")
    return float(d) / (float(p) / 100)


def load_live(root: str | Path) -> tuple[pd.DataFrame, dict]:
    """Tous les trades du paper trading trouvés sous root (trades.csv de chaque fenêtre de paper trading)
    et la définition de chaque stratégie (strategies.json écrit par la plateforme)."""
    frames, strategies = [], {}
    for path in sorted(Path(root).glob("**/trades.csv")):
        try:
            t = pd.read_csv(path)
        except Exception:
            continue
        if not len(t) or "strategie_id" not in t.columns:
            continue
        t["source"] = path.parent.name
        frames.append(t)
        sj = path.parent / "strategies.json"
        if sj.exists():
            try:
                strategies.update(json.loads(sj.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
    if not frames:
        return pd.DataFrame(), strategies
    t = pd.concat(frames, ignore_index=True)
    # la même stratégie suivie dans deux fenêtres (exploration + meilleures...) : un trade n'est compté qu'une fois
    t = t.drop_duplicates(subset=["strategie_id", "ouverture", "fermeture"]).reset_index(drop=True)
    t["r"] = pd.to_numeric(t["r"], errors="coerce")
    return t.dropna(subset=["r"]), strategies


def strategy_table(t: pd.DataFrame, min_trades: int = 5) -> pd.DataFrame:
    rows = []
    for sid, g in t.groupby("strategie_id"):
        r = g["r"].to_numpy(float)
        n = len(r)
        sd = r.std(ddof=1) if n > 1 else 0.0
        day = to_dt(g["fermeture"]).normalize()
        by_day = pd.Series(r, index=day).groupby(level=0).sum()
        pos = by_day[by_day > 0].sum()
        cum = np.cumsum(r)
        rows.append({
            "strategie_id": sid, "symbole": g["symbole"].iloc[0], "timeframe": g["timeframe"].iloc[0],
            "strategie": g["strategie"].iloc[0], "risque": g["risque"].iloc[0], "trades": n,
            "reussite_pct": round(float((r > 0).mean() * 100), 1), "r_moyen": round(float(r.mean()), 3),
            "r_total": round(float(r.sum()), 2), "t": round(float(r.mean() / sd * math.sqrt(n)), 2) if sd > 0 else 0.0,
            "jours": int(by_day.size),
            "meilleur_jour_part": round(float(by_day.max() / pos * 100), 0) if pos > 0 else None,
            "dd_r": round(float(np.max(np.maximum.accumulate(np.concatenate([[0], cum]))[1:] - cum)), 2) if n else 0.0,
            "fiable": n >= min_trades,
        })
    tab = pd.DataFrame(rows)
    if len(tab):
        tab = tab.sort_values(["fiable", "t", "r_total"], ascending=False).reset_index(drop=True)
    return tab


def analyse(root: str | Path, rules: FtmoRules = FtmoRules(), risk_pct: float = 1.0, day_budget: float = 2.5,
            total_budget: float = 10.0, min_trades: int = 5, max_components: int = 6, n_sim: int = 1500,
            trades: pd.DataFrame | None = None, strategies: dict | None = None) -> dict:
    """Classement du direct + meilleure combinaison des stratégies qui tournent."""
    if trades is None:
        trades, strategies = load_live(root)
    strategies = strategies or {}
    out = {"trades": int(len(trades)), "strategies": 0, "jours": 0, "fiable": False, "classement": [],
           "combinaison": None, "message": ""}
    if not len(trades):
        out["message"] = "Pas encore de trades en paper trading : laissez tourner la plateforme."
        return out
    t = trades.assign(entry_time=to_dt(trades["ouverture"]), exit_time=to_dt(trades["fermeture"]))
    lo, hi = t["entry_time"].min().normalize(), t["exit_time"].max().normalize()
    n_days = len(pd.bdate_range(lo, hi))
    tab = strategy_table(t, min_trades)
    out.update(strategies=int(len(tab)), jours=n_days, fiable=n_days >= MIN_DAYS_RELIABLE,
               periode=f"{lo:%Y-%m-%d} → {hi:%Y-%m-%d}", classement=tab.head(100).to_dict("records"))

    cand = tab[tab["fiable"] & (tab["r_total"] > 0) & (tab["r_moyen"] > 0)].head(30)["strategie_id"].tolist()
    by_id = {sid: g[["entry_time", "exit_time", "r"]].reset_index(drop=True) for sid, g in t.groupby("strategie_id")}

    def evaluate(keys, weights):
        merged = pd.concat([by_id[k].assign(w=weights[k]) for k in keys], ignore_index=True)
        merged = apply_risk_rules(merged, None, None, risk_pct, day_budget=day_budget)
        if total_budget:
            merged = merged[merged["r"].notna()]
        d = daily_table(merged, risk_pct, lo, hi)
        res = simulate(d, rules, n_sim, seed=0) if len(d) >= 15 else {}
        c = count_challenges(d, rules)
        res.update(rendement_pct=float(d["pnl"].sum()) if len(d) else 0.0,
                   pire_jour=float(d["worst"].min()) if len(d) else 0.0, trades=int(len(merged)),
                   reussis=c["reussis"], rates=c["rates"], par_jour=float(d["pnl"].sum()) / max(n_days, 1))
        res["jours_attendus"] = expected_days(res)
        return res

    def better(a, b):
        if b is None:
            return True
        a_ok = a.get("ftmo_echec_p1", 0) <= 2.0 and a["pire_jour"] > -day_budget * 1.05
        b_ok = b.get("ftmo_echec_p1", 0) <= 2.0 and b["pire_jour"] > -day_budget * 1.05
        if a_ok != b_ok:
            return a_ok
        ea, eb = a["jours_attendus"], b["jours_attendus"]
        if math.isfinite(ea) or math.isfinite(eb):
            return ea < eb * 0.95 or (ea <= eb * 1.05 and a["rendement_pct"] > b["rendement_pct"])
        return a["par_jour"] > b["par_jour"] * 1.02  # données trop courtes pour simuler : gain par jour
    keys, weights, best = [], {}, None
    while cand and len(keys) < max_components:
        pick = None
        for k in cand:
            if k in keys:
                continue
            for w in sorted({0.5, risk_pct}):
                res = evaluate(keys + [k], {**weights, k: w})
                if better(res, best) and (pick is None or better(res, pick[2])):
                    pick = (k, w, res)
        if pick is None:
            break
        keys.append(pick[0])
        weights[pick[0]] = pick[1]
        best = pick[2]
    if keys:
        info = {r["strategie_id"]: r for r in out["classement"]}
        comps = []
        for k in keys:
            s = strategies.get(k, {})
            r = info.get(k) or tab[tab["strategie_id"] == k].iloc[0].to_dict()
            comps.append({"strategie_id": k, "symbole": s.get("symbole", r["symbole"]),
                          "timeframe": s.get("timeframe", r["timeframe"]), "candidate": s.get("candidate"),
                          "strategie": r["strategie"], "risque_config": r["risque"], "risk_pct": weights[k],
                          "trades_direct": r["trades"], "r_total_direct": r["r_total"]})
        out["combinaison"] = {"nom": "Meilleure combinaison du DIRECT (paper trading)", "source": "direct",
                              "regles": {"day_budget": day_budget, "total_budget": total_budget, "day_stop": None,
                                         "max_open": None},
                              "resultat": {k: v for k, v in best.items() if not isinstance(v, (list, dict))},
                              "composants": comps, "jours_de_donnees": n_days,
                              "cree_le": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")}
    if not out["fiable"]:
        out["message"] = (f"Seulement {n_days} jours de bourse de trades en direct : c'est trop court pour conclure "
                          f"(il en faut au moins {MIN_DAYS_RELIABLE}). Les chiffres vont bouger ; laissez tourner.")
    else:
        out["message"] = f"{n_days} jours de bourse de trades en direct : les chiffres commencent à être fiables."
    return out


def write_report(result: dict, path: Path) -> Path:
    esc = html.escape
    rows = "".join(
        f"<tr><td>{i}</td><td>{esc(str(r['symbole']))} {esc(str(r['timeframe']))}</td><td>{esc(str(r['strategie'])[:110])}</td>"
        f"<td>{esc(str(r['risque']))}</td><td>{r['trades']}</td><td>{r['reussite_pct']} %</td><td>{r['r_moyen']:+.2f}</td>"
        f"<td><b>{r['r_total']:+.2f}</b></td><td>{r['t']}</td><td>{r['jours']}</td>"
        f"<td>{'' if r['meilleur_jour_part'] is None else str(int(r['meilleur_jour_part'])) + ' %'}</td></tr>"
        for i, r in enumerate(result.get("classement", [])[:60], 1))
    comb = result.get("combinaison")
    comb_html = "<p>Pas encore de combinaison gagnante en direct.</p>"
    if comb:
        res = comb["resultat"]
        items = "".join(f"<li>{esc(c['symbole'])} {esc(c['timeframe'])} | {esc(c['strategie'][:100])} | "
                        f"{esc(c['risque_config'])} | <b>{c['risk_pct']:g} %/trade</b> ({c['trades_direct']} trades, "
                        f"{c['r_total_direct']:+.1f}R en direct)</li>" for c in comb["composants"])
        ftmo = (f"réussite simulée {res.get('ftmo_pass', float('nan')):.0f} %, challenge réussi en "
                f"~{res.get('jours_attendus'):.0f} jours attendus" if math.isfinite(res.get("jours_attendus", math.inf))
                else "pas assez de jours pour simuler le challenge")
        comb_html = (f"<p><b>Gain en direct : {res['rendement_pct']:+.2f} %</b> · pire journée {res['pire_jour']:.2f} % · "
                     f"{res['trades']} trades · challenges enchaînés : {res['reussis']} réussis / {res['rates']} ratés · "
                     f"{esc(ftmo)}</p><ol>{items}</ol><p class='mut'>Enregistrée dans strategie_combinee_direct.json : "
                     "suivez-la en paper trading (menu C avec --combinee-fichier) ou créez son bot.</p>")
    page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Analyse du direct</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px;color:#1b1f24;background:#fafaf8}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #e3e3df;padding:6px 8px;text-align:left}}th{{background:#f1f1ee}}.mut{{color:#666}}
.warn{{border:1px solid #d97706;border-radius:10px;padding:10px 14px;background:#fff7ed}}</style></head><body>
<h1>Analyse du direct (paper trading)</h1>
<p class="{'mut' if result.get('fiable') else 'warn'}">{esc(result.get('message', ''))}</p>
<p class="mut">{result.get('trades', 0)} trades · {result.get('strategies', 0)} stratégies · {esc(str(result.get('periode', '')))}</p>
<h2>Meilleure combinaison des stratégies qui tournent</h2>{comb_html}
<h2>Classement des stratégies en direct</h2>
<table><tr><th>#</th><th>Marché</th><th>Stratégie</th><th>Réglage</th><th>Trades</th><th>Réussite</th><th>R moyen</th>
<th>R total</th><th>t</th><th>Jours</th><th>Meilleur jour (part du profit)</th></tr>{rows}</table></body></html>"""
    path.write_text(page, encoding="utf-8")
    return path


def run_direct(results_dir: str | Path, rules: FtmoRules = FtmoRules(), risk_pct: float = 1.0,
               day_budget: float = 2.5, total_budget: float = 10.0, log=print) -> dict:
    results_dir = Path(results_dir)
    res = analyse(results_dir, rules, risk_pct, day_budget, total_budget)
    write_report(res, results_dir / "direct.html")
    if res.get("combinaison") and all(c.get("candidate") for c in res["combinaison"]["composants"]):
        (results_dir / "strategie_combinee_direct.json").write_text(
            json.dumps(res["combinaison"], indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    log(f"[direct] {res['trades']} trades en direct, {res['strategies']} stratégies, {res['jours']} jours. "
        + res["message"])
    if res.get("combinaison"):
        r = res["combinaison"]["resultat"]
        log(f"[direct] meilleure combinaison : {len(res['combinaison']['composants'])} stratégies, gain {r['rendement_pct']:+.2f} %, "
            f"pire journée {r['pire_jour']:.2f} %")
    return res
