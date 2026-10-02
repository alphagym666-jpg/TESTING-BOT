"""Règles FTMO 2026 : perte max suiveuse (1 étape), week-end (compte Standard financé), choix du compte."""
import numpy as np
import pandas as pd

from mt5lab.backtest import RiskConfig, run_backtest
from mt5lab.ftmo import FtmoRules, count_challenges, holding_stats
from mt5lab.paper import near_weekend


def test_trailing_max_loss_follows_end_of_day_high():
    one, fixed = FtmoRules(trailing=True), FtmoRules(trailing=False)
    assert one.floor(0.0) == -10 and one.floor(4.0) == -6 and one.floor(15.0) == 0   # jamais au-dessus du départ
    assert fixed.floor(4.0) == -10
    # +4 % puis -10,4 % : en suiveuse le plancher est monté à -6 % -> raté ; en fixe (-10 %) : pas encore
    idx = pd.bdate_range("2026-01-05", periods=6)
    daily = pd.DataFrame({"pnl": [2.0, 2.0, -2.8, -2.8, -2.8, -2.0], "worst": [0.0, 0.0, -2.8, -2.8, -2.8, -2.0],
                          "traded": True}, index=idx)
    r_one = count_challenges(daily, FtmoRules(trailing=True, best_day_pct=0))
    r_fix = count_challenges(daily, FtmoRules(trailing=False, best_day_pct=0))
    assert r_one["rates"] == 1 and r_fix["rates"] == 0
    assert "suiveuse" in one.label()


def test_holding_stats_counts_weekends():
    t = pd.DataFrame({"entry_time": pd.to_datetime(["2026-01-09 20:00", "2026-01-12 10:00", "2026-01-13 10:00"]),
                      "exit_time": pd.to_datetime(["2026-01-12 09:00", "2026-01-12 14:00", "2026-01-14 10:00"])})
    st = holding_stats(t)  # vendredi -> lundi : week-end ; les 2 autres non
    assert abs(st["week_end_pct"] - 33.3) < 0.1 and st["duree_moy_h"] > 10


def test_backtest_can_close_before_weekend():
    idx = pd.date_range("2026-01-05", "2026-01-23", freq="h")
    idx = idx[idx.dayofweek < 5]
    n = len(idx)
    close = 1.0 + np.arange(n) * 1e-4
    df = pd.DataFrame({"open": close, "high": close + 5e-5, "low": close - 5e-5, "close": close}, index=idx)
    sig = pd.Series(0, index=idx, dtype=np.int8)
    sig.iloc[30] = 1  # un achat le lundi, objectif lointain, sortie seulement sur durée max
    cfg = RiskConfig(sl_mode="atr", sl_value=50.0, rr=50.0, management="none", max_hold=400)
    _, held = run_backtest(df, sig, cfg, return_trades=True)
    _, closed = run_backtest(df, sig, cfg, return_trades=True, weekend_exit=True)
    assert holding_stats(held)["week_end_pct"] == 100
    assert holding_stats(closed)["week_end_pct"] == 0 and closed["exit_time"].iloc[0].dayofweek == 4


def test_near_weekend_server_time():
    assert near_weekend("2026-10-02 21:30:00") and not near_weekend("2026-10-02 21:30:00", close=True)
    assert near_weekend("2026-10-02 22:05:00", close=True) and near_weekend("2026-10-03 10:00:00")
    assert not near_weekend("2026-10-01 23:00:00")
