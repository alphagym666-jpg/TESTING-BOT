"""Contrôle de FIABILITÉ : la meilleure de milliers de stratégies au hasard ne doit PAS passer ; un vrai avantage oui."""
import numpy as np
import pandas as pd

from mt5lab.fiabilite import check, hurdle
from mt5lab.top_backtest import realistic_accounts


def _trades(r, start="2024-01-01", days=700):
    idx = pd.to_datetime(start) + pd.to_timedelta(np.linspace(0, days, len(r)), unit="D")
    return pd.DataFrame({"entry_time": idx, "r": r}), idx[0], idx[-1]


def test_best_of_thousands_of_random_strategies_is_not_reliable():
    rng = np.random.default_rng(0)
    n_tested = 3000
    best = max((rng.choice([-1.0, 2.0], size=150, p=[2 / 3, 1 / 3]) for _ in range(n_tested)), key=lambda r: r.sum())
    t, lo, hi = _trades(best)
    res = check(t, lo, hi, n_tested)
    assert best.sum() > 30                       # la « meilleure » a l'air excellente...
    assert not res["fiable"]                     # ...mais ce n'est que de la chance
    assert "realiste" not in res and realistic_accounts(res).get("non_fiable")


def test_real_edge_with_history_passes_and_gives_a_modest_realistic_gain():
    rng = np.random.default_rng(1)
    r = rng.choice([-1.0, 2.0], size=600, p=[0.55, 0.45])      # +0,35R par trade en moyenne, sur 2 ans
    t, lo, hi = _trades(r)
    res = check(t, lo, hi, 3000, live={"trades": 25, "r_total": 3.0})
    assert res["fiable"], res["raison"]
    real = res["realiste"]
    assert 0 < real["pct_mois"] < 10 and abs(real["finance_usd_mois"] - 1000 * real["pct_mois"]) < 10
    acc = realistic_accounts(res)
    assert acc["realiste"] and acc["perso"]["gain_mois_usd"] == real["perso_usd_mois"]


def test_short_history_or_bad_paper_or_one_good_period_fail():
    rng = np.random.default_rng(2)
    r = rng.choice([-1.0, 2.0], size=600, p=[0.55, 0.45])
    t, lo, hi = _trades(r, days=60)                             # 2 mois : trop court
    assert check(t, lo, hi, 100)["controles"][0]["ok"] is False
    t, lo, hi = _trades(r)
    assert not check(t, lo, hi, 100, live={"trades": 30, "r_total": -8.0})["fiable"]   # le paper contredit
    r2 = np.concatenate([np.full(400, 1.0), rng.choice([-1.0, 1.0], size=300)])       # bonne seulement au début
    t, lo, hi = _trades(r2)
    assert not check(t, lo, hi, 100)["fiable"]
    assert hurdle(6000) > hurdle(100) >= 2.0
