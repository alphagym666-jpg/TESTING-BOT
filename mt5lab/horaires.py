"""MEILLEURES HEURES de chaque stratégie : la plage horaire où elle trade le mieux.

Pour une stratégie, on essaie toutes les plages (début 0 h à 23 h, durée 2 à 12 h, en HEURE DU SERVEUR MT5, celle
des bougies) :
  1. la plage est CHOISIE sur la 1re partie des trades (60 %) : la plus solide (t = R moyen / écart x racine du
     nombre de trades), avec assez de trades et nettement mieux que sans horaire ;
  2. puis CONTRÔLÉE sur les 40 % suivants, jamais vus pendant le choix : au moins 10 trades dans la plage, gagnante,
     au moins +0,1R par trade de mieux que sans horaire et solide (t >= 1,5). Sinon : pas de plage (24 h/24).
Une stratégie « horaire » ne prend des trades QUE dans sa plage ; ses positions ouvertes continuent après.
Plusieurs stratégies chacune dans SES heures peuvent ensuite être combinées (le Chef des combinaisons s'en charge).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

LENGTHS = tuple(range(2, 13))   # toutes les durées de 2 h à 12 h (8h-11h, 9h-14h...)
SPLIT = 0.6
MIN_TRADES = 40          # trades de la stratégie avant de chercher sa plage
MIN_IN_WINDOW = 15       # trades dans la plage pendant le choix


def hours_of(times) -> np.ndarray:
    t = pd.DatetimeIndex(pd.to_datetime(pd.Series(times).astype(str), format="mixed")) \
        if not pd.api.types.is_datetime64_any_dtype(times) else pd.DatetimeIndex(times)
    return (t.hour + t.minute / 60.0).to_numpy(dtype=float)


def in_window(h: np.ndarray, a: float, b: float) -> np.ndarray:
    """Plage [a, b[ en heures ; b < a = plage qui passe minuit (ex. 22 h - 2 h)."""
    return (h >= a) & (h < b) if a < b else (h >= a) | (h < b)


def label(a: float, b: float) -> str:
    return f"{a:g}h-{b:g}h"


def horaire(a: float, b: float) -> dict:
    """Format « horaire » d'un composant (lu par le paper trading et donc par le bot) : heure du serveur MT5."""
    return {"nom": f"{label(a, b)} (heure du serveur MT5)", "debut": float(a), "fin": float(b),
            "decalage_serveur": 0.0}


def _t(r: np.ndarray) -> float:
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(len(r))) if sd > 0 else 0.0


def hour_profile(times, r) -> list[dict]:
    """HEURE PAR HEURE (0 h à 23 h, heure du serveur MT5) : trades, gagnants et R moyen de la stratégie à chaque heure
    d'ouverture. Montre où elle est à son apogée (là où se prennent le plus de bons trades)."""
    h = np.floor(hours_of(times)).astype(int)
    r = np.asarray(r, dtype=float)
    out = []
    for i in range(24):
        x = r[h == i]
        out.append({"h": i, "trades": int(len(x)), "gagnants": int((x > 0).sum()),
                    "r_moyen": round(float(x.mean()), 3) if len(x) else None, "r_total": round(float(x.sum()), 1)})
    return out


def best_window(times, r, min_trades: int = MIN_TRADES, min_in: int = MIN_IN_WINDOW) -> dict | None:
    """La meilleure plage horaire d'une stratégie (choisie sur 60 % des trades, contrôlée sur les 40 % suivants),
    ou None s'il n'y en a pas de nettement meilleure que 24 h/24."""
    r = np.asarray(r, dtype=float)
    if len(r) < min_trades:
        return None
    h = hours_of(times)
    order = np.argsort(pd.to_datetime(pd.Series(times).astype(str), format="mixed").to_numpy(), kind="stable")
    h, r = h[order], r[order]
    cut = int(len(r) * SPLIT)
    h1, r1, h2, r2 = h[:cut], r[:cut], h[cut:], r[cut:]
    t_all = _t(r1)
    best = None
    for a in range(24):
        for L in LENGTHS:
            b = (a + L) % 24
            m = in_window(h1, a, b)
            if m.sum() < min_in or m.sum() > 0.9 * len(r1):
                continue
            t = _t(r1[m])
            if t > t_all + 1.0 and r1[m].mean() > 0 and (best is None or t > best[0]):
                best = (t, a, b)
    if best is None:
        return None
    _, a, b = best
    m1, m2 = in_window(h1, a, b), in_window(h2, a, b)
    ctrl_in = float(r2[m2].mean()) if m2.sum() else float("nan")
    ctrl_all = float(r2.mean()) if len(r2) else float("nan")
    # contrôle sur des trades jamais vus : assez de trades, gagnant, nettement mieux que 24 h/24, et solide
    ok = m2.sum() >= 10 and ctrl_in > 0 and ctrl_in >= ctrl_all + 0.1 and _t(r2[m2]) >= 1.5
    mall = in_window(h, a, b)
    return {"debut": float(a), "fin": float(b), "nom": label(a, b), "ok": bool(ok), "profil": hour_profile(times, r),
            "trades_plage": int(mall.sum()), "trades_total": int(len(r)),
            "r_moyen_plage": round(float(r[mall].mean()), 3), "r_moyen_hors": round(float(r[~mall].mean()), 3)
            if (~mall).any() else None, "r_moyen_24h": round(float(r.mean()), 3),
            "r_total_plage": round(float(r[mall].sum()), 1), "r_total_24h": round(float(r.sum()), 1),
            "t_choix": round(best[0], 2), "r_moyen_controle_plage": None if math.isnan(ctrl_in) else round(ctrl_in, 3),
            "r_moyen_controle_24h": None if math.isnan(ctrl_all) else round(ctrl_all, 3)}


def filter_trades(trades: pd.DataFrame, hor: dict | None, col: str = "entry_time") -> pd.DataFrame:
    """Seulement les trades OUVERTS dans la plage horaire d'un composant (rien à faire sans horaire)."""
    if not hor or hor.get("debut") is None or trades is None or not len(trades):
        return trades
    h = hours_of(trades[col]) - float(hor.get("decalage_serveur", 0.0))
    return trades[in_window(np.mod(h, 24), float(hor["debut"]), float(hor["fin"]))]
