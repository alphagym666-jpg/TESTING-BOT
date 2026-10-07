"""FIABILITÉ : est-ce qu'on peut se fier à une stratégie (seule ou combinée) pour la mettre sur un bot ?

Le piège : on teste des MILLIERS de stratégies et on garde les meilleures. Même sur des prix au hasard, les meilleures
de milliers ont l'air excellentes (+190 % en 2 mois, 99 % de réussite...). Ce sont surtout des chanceuses, et leur
résultat s'effondre en vrai (le paper trading fait -84 % du backtest). Une stratégie n'est « FIABLE » que si elle passe
TOUS ces contrôles :

1. HISTORIQUE  : au moins 1 an de données (quelques semaines = n'importe quoi peut avoir l'air bon).
2. TRADES      : au moins 100 trades, dont au moins 30 dans la période de contrôle.
3. PLUS QUE LA CHANCE : solidité t (R moyen / écart-type x racine du nombre de trades) au-dessus de ce que la chance
                 seule donne à la meilleure de N stratégies testées : racine(2 ln N) (4,2 pour 6 000 ; jamais sous 2).
4. CONTRÔLE    : sur la dernière partie (35 % la plus récente), toujours gagnante (t >= 1,5) et au moins 40 % du R
                 moyen de la première partie : le résultat ne vient pas d'une seule bonne période.
5. PAPER       : avec au moins 20 trades en paper trading, le paper ne doit pas être perdant.

GAIN RÉALISTE : seulement pour les fiables ; à partir de la période de CONTRÔLE (pas de tout l'historique), divisé
par 2 (en vrai : glissement, exécution, marché qui change, et la meilleure d'une liste est toujours un peu chanceuse),
SANS intérêts composés. C'est un ordre de grandeur, pas une promesse.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

MIN_YEARS = 1.0
MIN_TRADES = 100
MIN_TRADES_CTRL = 30
CTRL = 0.35
HAIRCUT = 0.5
MIN_PAPER = 20


def hurdle(n_tested: int) -> float:
    """t que la meilleure de N stratégies SANS avantage dépasse rarement : racine(2 ln N), au moins 2."""
    return round(max(2.0, math.sqrt(2 * math.log(max(int(n_tested), 2)))), 2)


def _t(r: np.ndarray) -> float:
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    return float(r.mean() / sd * math.sqrt(len(r))) if sd > 0 else (99.0 if r.mean() > 0 else -99.0)


def check(trades: pd.DataFrame, lo, hi, n_tested: int, risk_pct: float = 1.0, live: dict | None = None) -> dict:
    """trades : colonnes entry_time, r (en R) et w (risque du trade en % ; risk_pct par défaut).
    live : résumé du paper trading (trades, r_total). Renvoie les contrôles, le verdict et le gain réaliste."""
    out = {"fiable": False, "controles": [], "raison": "", "hurdle": hurdle(n_tested)}
    if trades is None or not len(trades):
        out["raison"] = "aucun trade"
        return out
    t = trades.sort_values("entry_time")
    r = t["r"].to_numpy(float)
    w = t["w"].to_numpy(float) if "w" in t.columns else np.full(len(r), float(risk_pct))
    rw = r * w / max(float(risk_pct), 1e-9)         # en R d'un trade à risk_pct (combinées : poids de chaque trade)
    lo, hi = pd.Timestamp(lo), pd.Timestamp(hi)
    years = max(0.0, (hi - lo).days / 365.25)
    cut_time = lo + (hi - lo) * (1 - CTRL)
    et = pd.to_datetime(t["entry_time"]).to_numpy()
    ctrl = et >= np.datetime64(cut_time)
    a, b = rw[~ctrl], rw[ctrl]
    t_all, t_b = _t(rw), _t(b)
    h = out["hurdle"]
    checks = [
        ("Historique", years >= MIN_YEARS, f"{years:.1f} an(s) de données (il faut au moins {MIN_YEARS:g} an)"),
        ("Nombre de trades", len(rw) >= MIN_TRADES and len(b) >= MIN_TRADES_CTRL,
         f"{len(rw)} trades dont {len(b)} dans la période de contrôle (il faut {MIN_TRADES} et {MIN_TRADES_CTRL})"),
        ("Plus que la chance", t_all >= h,
         f"solidité t = {t_all:.1f} (il faut {h:g} : {n_tested} stratégies testées, la chance seule en fait sortir de bonnes)"),
        ("Contrôle (période récente)", len(b) > 0 and b.mean() > 0 and t_b >= 1.5 and
         (len(a) == 0 or a.mean() <= 0 or b.mean() >= 0.4 * a.mean()),
         f"période récente : R moyen {b.mean() if len(b) else 0:+.2f} (t {t_b:.1f}) contre {a.mean() if len(a) else 0:+.2f} avant"),
    ]
    lt = (live or {}).get("trades") or 0
    if lt >= MIN_PAPER:
        checks.append(("Paper trading", (live.get("r_total") or 0) >= 0,
                       f"{lt} trades en paper : {live.get('r_total') or 0:+.1f}R"))
    else:
        checks.append(("Paper trading", True, f"{lt} trades en paper : pas encore assez pour juger ({MIN_PAPER} minimum)"))
    out["controles"] = [{"nom": n, "ok": bool(ok), "detail": d} for n, ok, d in checks]
    failed = [c for c in out["controles"] if not c["ok"]]
    out["fiable"] = not failed
    out["echecs"] = len(failed)
    out["raison"] = failed[0]["detail"] if failed else "passe tous les contrôles"
    out.update(t=round(t_all, 2), t_controle=round(t_b, 2), trades=int(len(rw)), trades_controle=int(len(b)),
               annees=round(years, 1), r_moyen=round(float(rw.mean()), 3),
               r_moyen_controle=round(float(b.mean()), 3) if len(b) else None)
    if out["fiable"]:
        months = max(1e-9, (hi - cut_time).days / 30.44)
        pct_mois = float((b * risk_pct).sum()) / months * HAIRCUT
        eq = np.cumsum(b * risk_pct)
        dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq)) if len(eq) else 0.0
        out["realiste"] = {
            "pct_mois": round(pct_mois, 2), "trades_mois": round(len(b) / months, 1),
            "baisse_typique_pct": round(dd, 1),
            "finance_usd_mois": round(100_000 * pct_mois / 100), "finance_usd_jour": round(100_000 * pct_mois / 100 / 21),
            "perso_usd_mois": round(5_000 * pct_mois * (2.0 / max(risk_pct, 1e-9)) / 100),
            "perso_usd_jour": round(5_000 * pct_mois * (2.0 / max(risk_pct, 1e-9)) / 100 / 21, 2),
            "mois_objectif_10pct": round(10.0 / pct_mois, 1) if pct_mois > 0 else None}
    return out
