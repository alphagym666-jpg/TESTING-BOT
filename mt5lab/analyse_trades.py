"""ANALYSE DES TRADES du paper trading : ce que chaque trade réel nous apprend.

1. STOPS & OBJECTIFS (excursions) : pour chaque trade, jusqu'où le prix est allé contre lui (MAE) et en sa
   faveur (MFE), en R. On en déduit, sur des prix RÉELS :
     - le stop au point d'entrée à +1R aurait-il sauvé des trades ?   (min après +1R <= 0 -> sorti à 0)
     - un objectif plus proche aurait-il rapporté plus ?               (MFE >= k -> gagné k)
     - un stop plus serré (même risque en argent, donc plus de lots) ?  (MAE >= s -> -1R, sinon R / s)
   Calculs exacts sur les prix vus (sans les frais en plus : avec un stop plus serré, spread et commission pèsent
   plus lourd en R). Un objectif plus LOIN ou un stop plus LARGE ne peuvent pas se déduire : à tester en backtest.
2. QUAND ÇA MARCHE : résultats par heure, jour, type de marché et proximité des annonces importantes.
3. TRADES REFUSÉS (fantômes) : signaux refusés par les règles de la stratégie combinée, suivis quand même.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TP_LEVELS = (0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0)
SL_LEVELS = (0.5, 0.75)
DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MIN_STRAT = 10        # trades avec excursions avant d'analyser une stratégie
MIN_BUCKET = 15       # trades avant de juger une heure / un jour / un type de marché


def _num(t: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(t[col], errors="coerce") if col in t.columns else pd.Series(np.nan, index=t.index)


def _tstat(r) -> float:
    r = np.asarray(r, dtype=float)
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    if sd <= 0:   # toujours le même résultat (ex. toujours -1R) : écart maximal
        return 0.0 if r.mean() == 0 else float(np.sign(r.mean()) * 99.0)
    return round(float(r.mean() / sd * math.sqrt(len(r))), 2)


def _bucket(r: np.ndarray) -> dict:
    return {"trades": int(len(r)), "reussite": round(float((r > 0).mean() * 100), 0) if len(r) else None,
            "r_moyen": round(float(r.mean()), 3) if len(r) else None, "r_total": round(float(r.sum()), 1),
            "t": _tstat(r)}


# ---------------------------------------------------------------------------------------- 1. stops & objectifs
def variants(r: np.ndarray, mae: np.ndarray, mfe: np.ndarray, after1r: np.ndarray, rr: float | None) -> dict:
    """R total réel et avec chaque variante (BE à +1R, objectif plus proche, stop plus serré)."""
    out = {"actuel": round(float(r.sum()), 2)}
    reached = (mfe >= 1.0) & ~np.isnan(after1r)          # +1R touché : le stop serait passé au point d'entrée
    be = np.where(reached & (np.nan_to_num(after1r, nan=1.0) <= 0), 0.0, r)   # puis revenu à l'entrée -> 0
    out["be_1r"] = round(float(be.sum()), 2)
    out["tp"] = {f"{k:g}": round(float(np.where(mfe >= k, k, r).sum()), 2)
                 for k in TP_LEVELS if rr is None or k < rr - 1e-9}
    out["sl"] = {f"{s:g}": round(float(np.where(mae >= s, -1.0, r / s).sum()), 2) for s in SL_LEVELS}
    return out


def excursions(t: pd.DataFrame, rr_of: dict) -> dict:
    """Une ligne par stratégie (au moins MIN_STRAT trades avec excursions) + le bilan de tous les trades."""
    t = t.assign(mae=_num(t, "mae_r"), mfe=_num(t, "mfe_r"), a1=_num(t, "min_apres_1r_r"), r=_num(t, "r"))
    t = t[t["mae"].notna() & t["mfe"].notna() & t["r"].notna()]
    out = {"trades": int(len(t)), "strategies": [], "global": None}
    if not len(t):
        out["message"] = ("Pas encore de trade avec ses excursions (MAE/MFE) : elles sont enregistrées pour chaque "
                          "trade fermé à partir de cette version.")
        return out
    r, mae, mfe = t["r"].to_numpy(float), t["mae"].to_numpy(float), t["mfe"].to_numpy(float)
    lose, win = r < 0, r > 0
    out["global"] = {"trades": int(len(t)), "perdants_passes_1r": round(float((lose & (mfe >= 1)).sum() / max(1, lose.sum()) * 100), 0),
                     "gagnants_mae_med": round(float(np.median(mae[win])), 2) if win.any() else None,
                     "perdants_mfe_med": round(float(np.median(mfe[lose])), 2) if lose.any() else None,
                     "be_1r": variants(r, mae, mfe, t["a1"].to_numpy(float), None)["be_1r"],
                     "actuel": round(float(r.sum()), 1)}
    rows = []
    for sid, g in t.groupby("strategie_id"):
        if len(g) < MIN_STRAT:
            continue
        r, mae, mfe, a1 = (g[c].to_numpy(float) for c in ("r", "mae", "mfe", "a1"))
        rr = rr_of.get(sid)
        v = variants(r, mae, mfe, a1, rr)
        lose, win = r < 0, r > 0
        cands = [("actuel", "réglage actuel", v["actuel"]), ("be_1r", "stop au point d'entrée à +1R", v["be_1r"])]
        cands += [(f"tp_{k}", f"objectif à {k.replace('.', ',')}R", x) for k, x in v["tp"].items()]
        cands += [(f"sl_{k}", f"stop {k.replace('.', ',')} fois plus près (même risque en argent)", x)
                  for k, x in v["sl"].items()]
        best = max(cands, key=lambda c: c[2])
        gain = best[2] - v["actuel"]
        conseil = (f"{best[1]} : {gain:+.1f}R sur {len(g)} trades" if best[0] != "actuel" and gain >= max(2.0, 0.1 * abs(v["actuel"]))
                   else "garder le réglage actuel")
        rows.append({"strategie_id": sid, "symbole": g["symbole"].iloc[0], "timeframe": g["timeframe"].iloc[0],
                     "strategie": g["strategie"].iloc[0], "risque": g["risque"].iloc[0], "trades": int(len(g)),
                     "rr": rr, "r_actuel": v["actuel"], "r_be": v["be_1r"], "tp": v["tp"], "sl": v["sl"],
                     "meilleur": best[1], "meilleur_r": best[2], "gain_r": round(gain, 1), "conseil": conseil,
                     "perdants_passes_1r": round(float((lose & (mfe >= 1)).sum() / max(1, lose.sum()) * 100), 0),
                     "gagnants_mae_med": round(float(np.median(mae[win])), 2) if win.any() else None,
                     "perdants_mfe_med": round(float(np.median(mfe[lose])), 2) if lose.any() else None})
    rows.sort(key=lambda x: -x["gain_r"])
    out["strategies"] = rows[:300]
    n_better = sum(1 for x in rows if x["conseil"] != "garder le réglage actuel")
    out["message"] = (f"{len(t)} trades avec excursions, {len(rows)} stratégies avec au moins {MIN_STRAT} trades : "
                      f"{n_better} gagneraient à changer de stop ou d'objectif d'après les prix réels.")
    return out


# ------------------------------------------------------------------------------------------ 2. quand ça marche
def _news_bucket(v, after: bool) -> str | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if v < 30:
        return "moins de 30 min " + ("après" if after else "avant")
    if v < 120:
        return "30 min à 2 h " + ("après" if after else "avant")
    return "plus de 2 h " + ("après" if after else "avant") if v < 1440 else "aucune annonce dans la journée"


def contexte(t: pd.DataFrame) -> dict:
    """Résultats par heure, jour, type de marché, proximité des annonces ; points forts et moments à éviter."""
    t = t.assign(r=_num(t, "r"))
    t = t[t["r"].notna()]
    out = {"trades": int(len(t)), "tables": {}, "a_eviter": [], "points_forts": []}
    if not len(t):
        out["message"] = "Pas encore de trade."
        return out
    when = pd.to_datetime(t["ouverture"].astype(str), errors="coerce", format="mixed")
    t = t.assign(heure=when.dt.hour, jour=when.dt.dayofweek)
    overall = float(t["r"].mean())
    dims = {"heure": ("Heure d'ouverture (heure du serveur MT5)", lambda x: f"{int(x):02d} h"),
            "jour": ("Jour de la semaine", lambda x: DAYS[int(x)]),
            "regime": ("Type de marché à l'ouverture", str),
            "avant": ("Annonce importante avant le trade", str),
            "apres": ("Annonce importante après l'ouverture", str)}
    if "regime" in t.columns:
        t["regime"] = t["regime"].replace({"": np.nan, "nan": np.nan})
    t["avant"] = [_news_bucket(v, True) for v in _num(t, "nouvelle_avant_min")]
    t["apres"] = [_news_bucket(v, False) for v in _num(t, "nouvelle_apres_min")]
    for key, (title, lab) in dims.items():
        if key not in t.columns:
            continue
        rows = []
        for val, g in t.dropna(subset=[key]).groupby(key):
            if isinstance(val, str) and not val.strip():
                continue
            b = {"valeur": lab(val), **_bucket(g["r"].to_numpy(float))}
            rows.append(b)
            if b["trades"] >= MIN_BUCKET and b["t"] <= -2.0 and b["r_moyen"] < 0:
                out["a_eviter"].append({"quoi": f"{title.split(' (')[0].lower()} : {b['valeur']}", **b})
            elif b["trades"] >= MIN_BUCKET and b["t"] >= 2.0 and b["r_moyen"] > max(overall, 0):
                out["points_forts"].append({"quoi": f"{title.split(' (')[0].lower()} : {b['valeur']}", **b})
        if rows:
            out["tables"][key] = {"titre": title, "lignes": rows}
    out["a_eviter"].sort(key=lambda x: x["t"])
    out["points_forts"].sort(key=lambda x: -x["t"])
    out["global"] = _bucket(t["r"].to_numpy(float))
    n_ctx = int(t["avant"].notna().sum())
    out["message"] = (f"{len(t)} trades (heure et jour pour tous ; type de marché et annonces pour les trades pris "
                      f"depuis cette version : {n_ctx} avec annonces). Un moment « à éviter » demande au moins "
                      f"{MIN_BUCKET} trades et un écart net (t <= -2). À vérifier dans le backtest avant d'en faire un filtre.")
    return out


# ---------------------------------------------------------------------------------------------- 3. fantômes
def fantomes(g: pd.DataFrame | None) -> dict:
    """Bilan des signaux refusés par les règles de la stratégie combinée (trades fantômes)."""
    out = {"trades": 0, "par_raison": [], "par_strategie": []}
    if g is None or not len(g):
        out["message"] = ("Pas encore de trade fantôme : ils apparaissent quand la stratégie combinée refuse un signal "
                          "(perte possible max du jour, positions max, marchés corrélés, frein, horaire...).")
        return out
    g = g.assign(r=_num(g, "r"))
    g = g[g["r"].notna()]
    out["trades"] = int(len(g))
    for why, x in g.groupby("raison_refus"):
        out["par_raison"].append({"raison": why, **_bucket(x["r"].to_numpy(float))})
    out["par_raison"].sort(key=lambda x: x["r_total"])
    for sid, x in g.groupby("strategie_id"):
        out["par_strategie"].append({"strategie_id": sid, "symbole": x["symbole"].iloc[0],
                                     "timeframe": x["timeframe"].iloc[0], "strategie": x["strategie"].iloc[0],
                                     "groupe": x["groupe"].iloc[0], **_bucket(x["r"].to_numpy(float))})
    out["par_strategie"].sort(key=lambda x: -x["r_total"])
    out["par_strategie"] = out["par_strategie"][:100]
    tot = float(g["r"].sum())
    out["total"] = _bucket(g["r"].to_numpy(float))
    out["message"] = (f"{len(g)} signaux refusés suivis jusqu'au bout : au total {tot:+.1f}R. "
                      + ("Les règles vous ont ÉVITÉ ces pertes : elles protègent le compte." if tot < 0 else
                         "Ces trades auraient GAGNÉ : les règles coûtent des gains (mais elles évitent les grosses "
                         "journées perdantes qui font rater le challenge ; à comparer avec la perte max du jour).")
                      + " Les fantômes sont suivis sans gestion (stop, objectif, signal opposé, durée max).")
    return out


def analyse(trades: pd.DataFrame, ghosts: pd.DataFrame | None, rr_of: dict, sid=None, sym=None, tf=None) -> dict:
    def flt(d):
        if d is None or not len(d):
            return d
        if sid:
            d = d[d["strategie_id"] == sid]
        if sym:
            d = d[d["symbole"] == sym]
        if tf:
            d = d[d["timeframe"] == tf]
        return d
    t = flt(trades)
    return {"excursions": excursions(t, rr_of), "contexte": contexte(t), "fantomes": fantomes(flt(ghosts))}
