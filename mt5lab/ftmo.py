"""Simulateur de challenge FTMO.

Règles par défaut (réglables) : challenge FTMO phase 1
  Objectif : +10 %
  Perte max journalière : 3 % du capital initial    Perte max totale : 10 % du capital initial
  Minimum 4 jours de trading    Phase 2 : désactivée (target2 = 0), mettre 5 pour la simuler aussi

Méthode :
1. Les trades (heure d'entrée, heure de sortie, résultat en R) sont convertis en journées de trading.
   Pour chaque journée on calcule le P&L et le PIRE moment de la journée : P&L réalisé + chaque position encore
   ouverte comptée comme si elle était à son stop (-1R). C'est volontairement prudent.
2. Monte Carlo : on rejoue des milliers de challenges en tirant des blocs de 5 jours réels au hasard
   (garde l'enchaînement des bonnes et mauvaises périodes) jusqu'à réussite, échec, ou limite de temps.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def to_dt(values) -> pd.DatetimeIndex:
    """Dates de trades : les trades D1 sont écrits '2019-01-28', les intraday '2019-01-28 13:00:00'."""
    return pd.DatetimeIndex(pd.to_datetime(pd.Series(values).astype(str), format="mixed"))


@dataclass(frozen=True)
class FtmoRules:
    target1: float = 10.0
    target2: float = 0.0        # 0 = pas de phase 2
    max_daily: float = 3.0
    max_total: float = 10.0
    min_days: int = 4
    horizon_days: int = 250     # jours de bourse simulés au max (~1 an ; FTMO n'impose plus de limite de temps)

    def label(self) -> str:
        p2 = f", phase 2 +{self.target2:g} %" if self.target2 > 0 else ""
        return (f"objectif +{self.target1:g} %{p2}, perte max {self.max_daily:g} %/jour et {self.max_total:g} % au total, "
                f"min {self.min_days} jours de trading")


def daily_table(trades: pd.DataFrame, risk_pct: float, start=None, end=None) -> pd.DataFrame:
    """trades : colonnes entry_time, exit_time, r (et optionnellement w = risque en % de CE trade).

    Renvoie une ligne par jour ouvré : P&L %, pire moment de la journée %, jour tradé ?
    Le pire moment compte chaque position encore ouverte comme si elle était à son stop.
    """
    if trades is None or not len(trades):
        return pd.DataFrame(columns=["pnl", "worst", "traded"])
    t = trades.sort_values("exit_time").reset_index(drop=True)
    entry = to_dt(t["entry_time"]).to_numpy()
    exit_ = to_dt(t["exit_time"]).to_numpy()
    w = t["w"].to_numpy(dtype=float) if "w" in t.columns else np.full(len(t), float(risk_pct))
    pnl = t["r"].to_numpy(dtype=float) * w
    # risque encore ouvert au moment de chaque sortie (hors la position qui sort), pondéré par le risque de chacune
    o_ent, o_ex = np.argsort(entry, kind="stable"), np.argsort(exit_, kind="stable")
    cw_ent = np.concatenate([[0.0], np.cumsum(w[o_ent])])
    cw_ex = np.concatenate([[0.0], np.cumsum(w[o_ex])])
    open_w = cw_ent[np.searchsorted(entry[o_ent], exit_, "left")] - cw_ex[np.searchsorted(exit_[o_ex], exit_, "right")]
    open_w = np.maximum(open_w, 0.0)
    day = pd.DatetimeIndex(exit_).normalize()
    df = pd.DataFrame({"day": day, "pnl": pnl, "open_w": open_w})
    df["cum"] = df.groupby("day")["pnl"].cumsum()
    df["worst"] = df["cum"] - df["open_w"]
    g = df.groupby("day").agg(pnl=("pnl", "sum"), worst=("worst", "min"))
    g["worst"] = np.minimum(g["worst"], 0.0)
    entry_days = set(pd.DatetimeIndex(entry).normalize())
    start = pd.Timestamp(start).normalize() if start is not None else g.index.min()
    end = pd.Timestamp(end).normalize() if end is not None else g.index.max()
    days = pd.bdate_range(start, end).union(g.index)
    out = g.reindex(days, fill_value=0.0)
    out["traded"] = [d in entry_days for d in out.index]
    return out


def apply_risk_rules(trades: pd.DataFrame, day_stop: float | None = None, max_open: int | None = None,
                     default_w: float = 0.5, day_budget: float | None = None, safety: float = 1.1) -> pd.DataFrame:
    """Règles de risque du Directeur, appliquées dans l'ordre chronologique des entrées :

    - day_budget : un trade n'est pris que si  perte déjà réalisée aujourd'hui + risque des positions ouvertes
                   + risque du nouveau trade  <= day_budget %. Même si tous les stops sautent, la journée ne perd
                   pas plus de day_budget % (hors gap par-dessus un stop). Chaque risque est compté avec une marge
                   `safety` (+10 %) pour couvrir spread, commission et glissement au stop.
    - day_stop   : plus de nouveau trade dans la journée une fois la perte réalisée du jour <= -day_stop %
    - max_open   : pas plus de max_open positions ouvertes en même temps
    """
    if trades is None or not len(trades) or (day_stop is None and max_open is None and day_budget is None):
        return trades
    import heapq
    t = trades.copy()
    if "w" not in t.columns:
        t["w"] = default_w
    t["e_dt"], t["x_dt"] = to_dt(t["entry_time"]), to_dt(t["exit_time"])
    t = t.sort_values("e_dt").reset_index(drop=True)
    keep = np.zeros(len(t), dtype=bool)
    open_heap: list[tuple] = []          # (heure de sortie, P&L en %, risque en %)
    realized: dict = {}                  # jour -> P&L réalisé %
    for i, row in enumerate(t.itertuples()):
        while open_heap and open_heap[0][0] <= row.e_dt:
            x, p, _w = heapq.heappop(open_heap)
            realized[x.normalize()] = realized.get(x.normalize(), 0.0) + p
        today = realized.get(row.e_dt.normalize(), 0.0)
        if day_stop is not None and today <= -day_stop:
            continue
        if max_open is not None and len(open_heap) >= max_open:
            continue
        if day_budget is not None:
            open_risk = sum(o[2] for o in open_heap) * safety
            if max(0.0, -today) + open_risk + row.w * safety > day_budget + 1e-9:
                continue
        keep[i] = True
        heapq.heappush(open_heap, (row.x_dt, row.r * row.w, row.w))
    return t.loc[keep].drop(columns=["e_dt", "x_dt"]).reset_index(drop=True)


def _phase(pnl, worst, traded, target, rules: FtmoRules, n: int, rng, block: int = 5):
    L = len(pnl)
    H = rules.horizon_days
    nb = -(-H // block)
    starts = rng.integers(0, max(1, L - block + 1), size=(n, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n, -1)[:, :H]
    idx = np.minimum(idx, L - 1)
    P, W, T = pnl[idx], worst[idx], traded[idx]
    cum_after = np.cumsum(P, axis=1)
    cum_before = cum_after - P
    fail = (W <= -rules.max_daily) | (cum_before + W <= -rules.max_total)
    tdays = np.cumsum(T, axis=1)
    ok = (cum_after >= target) & (tdays >= rules.min_days)
    first_fail = np.where(fail.any(1), fail.argmax(1), H + 1)
    first_pass = np.where(ok.any(1), ok.argmax(1), H + 1)
    passed = first_pass < first_fail
    failed = first_fail < first_pass
    days_to_pass = first_pass[passed] + 1
    return {
        "pass": float(passed.mean()),
        "fail": float(failed.mean()),
        "timeout": float(1 - passed.mean() - failed.mean()),
        "days_median": float(np.median(days_to_pass)) if len(days_to_pass) else float("nan"),
    }


def simulate(daily: pd.DataFrame, rules: FtmoRules = FtmoRules(), n: int = 3000, seed: int = 0) -> dict:
    """Probabilités de réussir chaque phase et le challenge complet, et jours de bourse médians."""
    if daily is None or len(daily) < 15:
        return {"ftmo_p1": np.nan, "ftmo_p2": np.nan, "ftmo_pass": np.nan, "ftmo_jours_p1": np.nan,
                "ftmo_jours_p2": np.nan, "ftmo_echec_p1": np.nan, "ftmo_jours_hist": len(daily) if daily is not None else 0}
    rng = np.random.default_rng(seed)
    pnl = daily["pnl"].to_numpy(float)
    worst = daily["worst"].to_numpy(float)
    traded = daily["traded"].to_numpy(bool)
    p1 = _phase(pnl, worst, traded, rules.target1, rules, n, rng)
    p2 = _phase(pnl, worst, traded, rules.target2, rules, n, rng) if rules.target2 > 0 else None
    total = p1["pass"] * (p2["pass"] if p2 else 1.0)
    return {"ftmo_p1": round(p1["pass"] * 100, 1), "ftmo_p2": round(p2["pass"] * 100, 1) if p2 else np.nan,
            "ftmo_pass": round(total * 100, 1),
            "ftmo_jours_p1": p1["days_median"], "ftmo_jours_p2": p2["days_median"] if p2 else np.nan,
            "ftmo_echec_p1": round(p1["fail"] * 100, 1), "ftmo_jours_hist": int(len(daily))}


def build_portfolio(trades_by_key: dict[str, pd.DataFrame], windows: dict[str, tuple], risk_pct: float,
                    candidates: list[str], rules: FtmoRules = FtmoRules(), max_size: int = 6,
                    min_window_days: int = 45, n: int = 3000, log=print,
                    names: dict[str, str] | None = None) -> tuple[list[str], dict]:
    """Chef FTMO : ajoute une à une les stratégies qui augmentent le plus la probabilité de passer le challenge.

    Les stratégies sont combinées sur leur période hors-échantillon commune (au moins min_window_days jours).
    """
    def better(new: dict, old: dict | None) -> bool:
        """Plus de réussite (> +0,5 pt), ou autant de réussite (à 0,5 pt près) et au moins 10 % plus rapide."""
        if old is None:
            return not np.isnan(new["ftmo_pass"])
        if np.isnan(new["ftmo_pass"]):
            return False
        if new["ftmo_pass"] > old["ftmo_pass"] + 0.5:
            return True
        d_new, d_old = new["ftmo_jours_p1"], old["ftmo_jours_p1"]
        return (new["ftmo_pass"] >= old["ftmo_pass"] - 0.5 and not np.isnan(d_new)
                and (np.isnan(d_old) or d_new <= 0.9 * d_old))

    chosen: list[str] = []
    steps: list[dict] = []
    best_res: dict | None = None
    while len(chosen) < max_size:
        gain_key, gain_res = None, None
        for k in candidates:
            if k in chosen:
                continue
            keys = chosen + [k]
            lo = max(windows[x][0] for x in keys)
            hi = min(windows[x][1] for x in keys)
            if (hi - lo).days < min_window_days:
                continue
            merged = pd.concat([trades_by_key[x] for x in keys], ignore_index=True)
            ent, ext = to_dt(merged["entry_time"]), to_dt(merged["exit_time"])
            merged = merged[(ent >= lo) & (ext <= hi)]
            res = simulate(daily_table(merged, risk_pct, lo, hi), rules, n, seed=0)
            if better(res, best_res) and (gain_res is None or better(res, gain_res)):
                gain_key, gain_res = k, res
        if gain_key is None:
            break
        chosen.append(gain_key)
        best_res = gain_res
        best_res["etapes"] = steps
        steps.append({"cle": gain_key, "reussite": best_res["ftmo_pass"], "jours": best_res["ftmo_jours_p1"]})
        log(f"Chef FTMO : + stratégie {len(chosen)} -> réussite {best_res['ftmo_pass']:.1f} % "
            f"(+{rules.target1:g} % en ~{best_res['ftmo_jours_p1']:.0f} jours de bourse)\n"
            f"             = {(names or {}).get(gain_key, gain_key)}")
    best_res = best_res or {}
    return chosen, best_res
