"""BACKTEST d'une stratégie combinée (par ex. une combinaison du TOP 10 du direct) sur TOUT l'historique MT5.

Chaque composant est rejoué sur l'historique de SON marché et de SON timeframe (mêmes coûts que la recherche :
spread de chaque bougie, commission, glissement mesuré, swaps, blocage autour des nouvelles). Ensuite tous les
trades vont sur UN seul compte, avec les mêmes règles de risque que le paper trading et le bot (risque de chaque
composant, perte possible max par jour), puis :
  - courbe du compte, gain par année et par mois, pire journée, drawdown max, trades par mois ;
  - challenges FTMO enchaînés sur les vrais jours (réussis / ratés) et simulation (réussite, échecs, jours) ;
  - les mêmes chiffres sur la PÉRIODE RÉCENTE seule (35 % la plus récente de l'historique).

ATTENTION : les stratégies ont été trouvées en cherchant sur une partie de ce même historique : le résultat sur
tout l'historique est donc optimiste. La période récente (celle que la recherche garde pour la validation) et le
paper trading sont les chiffres honnêtes.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .backtest import RiskConfig, run_backtest
from .direct import expected_days
from .evaluator import compute_signal
from .ftmo import FtmoRules, apply_risk_rules, count_challenges, daily_table, holding_stats, simulate, to_dt
from .strategies import apply_filter

RECENT = 0.35   # part la plus récente de l'historique (la période de validation de la recherche)


def _clean(v):
    if isinstance(v, (np.floating, float)):
        return None if not math.isfinite(float(v)) else round(float(v), 3)
    if isinstance(v, np.integer):
        return int(v)
    return v


def _stats(t: pd.DataFrame, daily: pd.DataFrame, rules: FtmoRules, n_sim: int, lo, hi) -> dict:
    from .manager import _drawdown, _per_month
    res = simulate(daily, rules, n_sim, seed=0) if len(daily) >= 15 else {}
    c = count_challenges(daily, rules)
    r = t["r"].to_numpy(float) if len(t) else np.zeros(0)
    res.update(_drawdown(daily))
    res.update(rendement_pct=float(daily["pnl"].sum()) if len(daily) else 0.0,
               pire_jour=float(daily["worst"].min()) if len(daily) else 0.0,
               meilleur_jour=float(daily["pnl"].max()) if len(daily) else 0.0,
               trades=int(len(t)), trades_mois=_per_month(len(t), lo, hi),
               reussite_trades=round(float((r > 0).mean() * 100), 1) if len(r) else None,
               r_moyen=round(float(r.mean()), 3) if len(r) else None,
               jours_negatifs_pct=round(float((daily["pnl"] < 0).sum() / max(1, daily["traded"].sum()) * 100), 1)
               if len(daily) else None,
               reussis=c["reussis"], rates=c["rates"], jours_moyens=c["jours_moyens"],
               periode=f"{pd.Timestamp(lo):%Y-%m-%d} → {pd.Timestamp(hi):%Y-%m-%d}",
               annees=round((pd.Timestamp(hi) - pd.Timestamp(lo)).days / 365.25, 1))
    res["jours_attendus"] = expected_days(res)
    res.update(holding_stats(t))
    return {k: _clean(v) for k, v in res.items() if not isinstance(v, (list, dict))}


def component_trades(df: pd.DataFrame, cost: float, cand: dict, w: float, start=None, sig=None) -> pd.DataFrame:
    """Trades d'UNE stratégie sur l'historique (à partir de start si donné : le signal est calculé sur tout
    l'historique chargé, pour que les indicateurs soient déjà « chauds » au début de la période)."""
    if sig is None:
        sig = apply_filter(df, compute_signal(df, cand["signal"]), cand.get("filter", "none"))
    if start is not None:
        m = df.index >= start
        df, sig = df[m], sig[m]
    if len(df) < 2:
        return pd.DataFrame(columns=["entry_time", "exit_time", "r", "side"])
    _, tr = run_backtest(df, sig, RiskConfig(**cand["risk"]), cost=cost, risk_pct=w, return_trades=True)
    if not len(tr):
        return pd.DataFrame(columns=["entry_time", "exit_time", "r", "side"])
    return tr[["entry_time", "exit_time", "r", "side"]]


def account_report(parts: list, rows: list, lo, hi, rules: FtmoRules, rules_c: dict, risk_pct: float,
                   n_sim: int = 3000) -> dict:
    """Tous les trades (parts : un DataFrame par composant, colonnes entry_time, exit_time, r, side, w, comp)
    sur UN seul compte avec les règles de risque, puis les chiffres, la courbe, les mois et les années."""
    from .manager import _with_corr
    out = {"composants": rows, "ok": False, "message": ""}
    parts = [p for p in parts if p is not None and len(p)]
    if not parts or lo is None or hi is None or hi <= lo:
        out["message"] = "Aucun trade dans l'historique (ou pas de données MT5 pour ces marchés)."
        return out
    parts = [_with_corr(rows[int(p["comp"].iloc[0])]["symbole"], p) for p in parts]
    t = pd.concat(parts, ignore_index=True)
    t = t[(to_dt(t["entry_time"]) >= lo) & (to_dt(t["exit_time"]) <= hi)]
    n_raw = len(t)
    t = apply_risk_rules(t, rules_c.get("day_stop"), rules_c.get("max_open"), risk_pct,
                         day_budget=rules_c.get("day_budget", 2.5), max_corr=rules_c.get("max_correles"),
                         day_lock=rules_c.get("frein"), vol_target=rules_c.get("volatilite"))
    t = t[t["r"].notna()].reset_index(drop=True)
    if not len(t):
        out["message"] = "Aucun trade dans l'historique commun des stratégies."
        return out
    for k, g in t.groupby("comp"):
        rows[int(k)].update(trades=int(len(g)), r_total=round(float(g["r"].sum()), 1),
                            reussite=round(float((g["r"] > 0).mean() * 100), 0))
    daily = daily_table(t, risk_pct, lo, hi)
    split = daily.index[int(len(daily) * (1 - RECENT))] if len(daily) > 20 else daily.index[0]
    rec_t = t[to_dt(t["entry_time"]) >= split]
    out.update(ok=True, refuses=int(n_raw - len(t)), regles=rules.label(),
               tout=_stats(t, daily, rules, n_sim, lo, hi),
               recent=_stats(rec_t, daily[daily.index >= split], rules, n_sim, split, hi))
    # courbe du compte (en %, max 500 points) et gain par mois / par année
    cum = daily["pnl"].cumsum()
    step = max(1, len(cum) // 500)
    pts = cum.iloc[::step]
    if pts.index[-1] != cum.index[-1]:
        pts = pd.concat([pts, cum.iloc[-1:]])
    out["courbe"] = [[f"{d:%Y-%m-%d}", round(float(v), 2)] for d, v in pts.items()]
    out["debut_recent"] = f"{split:%Y-%m-%d}"
    m = daily["pnl"].groupby([daily.index.year, daily.index.month]).sum()
    out["mois"] = [{"annee": int(y), "mois": int(mo), "pct": round(float(v), 2)} for (y, mo), v in m.items()]
    y = daily.groupby(daily.index.year).agg(pct=("pnl", "sum"), pire=("worst", "min"))
    years = to_dt(t["entry_time"]).year
    out["annees"] = [{"annee": int(k), "pct": round(float(v.pct), 2), "pire_jour": round(float(v.pire), 2),
                      "trades": int((years == k).sum())} for k, v in y.iterrows()]
    a, r = out["tout"], out["recent"]
    out["message"] = (f"{a['annees']} ans d'historique commun ({a['periode']}) : {a['trades']} trades, "
                      f"{a['rendement_pct']:+.1f} %, {a['reussis']} challenges réussis / {a['rates']} ratés. "
                      f"Période récente seule ({r['periode']}) : {r['rendement_pct']:+.1f} %, "
                      f"{r['reussis']} réussis / {r['rates']} ratés.")
    return out


def backtest_combination(comb: dict, get_data, rules: FtmoRules = FtmoRules(), risk_pct: float = 1.0,
                         n_sim: int = 3000, progress=None, log=print, start=None) -> dict:
    """comb : {"composants": [{symbole, timeframe, candidate, risk_pct}, ...], "regles": {...}}
    get_data(symbole, timeframe) -> (df, coût) comme pour la recherche. start : début du backtest (sinon tout
    l'historique commun)."""
    rules_c = comb.get("regles") or {}
    comps = comb.get("composants") or []
    parts, rows, lo, hi = [], [], None, None
    for i, c in enumerate(comps, 1):
        if progress:
            progress(i - 1, len(comps) + 1, f"{c['symbole']} {c['timeframe']} : historique et backtest")
        w = float(c.get("risk_pct") or risk_pct)
        row = {"symbole": c["symbole"], "timeframe": c["timeframe"], "strategie": c.get("strategie", ""),
               "risk_pct": w, "trades": 0, "r_total": 0.0, "erreur": None}
        try:
            df, cost = get_data(c["symbole"], c["timeframe"])
            tr = component_trades(df, cost, c["candidate"], w, start)
        except Exception as exc:
            row["erreur"] = str(exc)[:200]
            rows.append(row)
            log(f"[backtest] {c['symbole']} {c['timeframe']} : impossible ({exc})")
            continue
        first = df.index[0] if start is None else max(df.index[0], pd.Timestamp(start))
        lo = first if lo is None else max(lo, first)
        hi = df.index[-1] if hi is None else min(hi, df.index[-1])
        row.update(debut=f"{first:%Y-%m-%d}", fin=f"{df.index[-1]:%Y-%m-%d}")
        parts.append(tr.assign(w=w, comp=len(rows)))
        rows.append(row)
    if progress:
        progress(len(comps), len(comps) + 1, "un seul compte, règles de risque et challenges FTMO")
    out = account_report(parts, rows, lo, hi, rules, rules_c, risk_pct, n_sim)
    if progress:
        progress(len(comps) + 1, len(comps) + 1, "terminé")
    return out
