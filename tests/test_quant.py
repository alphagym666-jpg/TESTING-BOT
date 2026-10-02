"""Équipe E (desk quantitatif) : profil de volume, arbitrage statistique, rapport COT ; dimensionnement par volatilité."""
import numpy as np
import pandas as pd

from mt5lab.cot import add_cot, market_of, parse, uses_cot
from mt5lab.data import synthetic
from mt5lab.ftmo import apply_risk_rules, vol_factor
from mt5lab.inventions import feat


def test_volume_profile_uses_previous_days_only():
    df = synthetic(24 * 30, seed=4, freq="h")
    df["volume"] = 1.0
    df.iloc[24 * 10 + 5, df.columns.get_loc("volume")] = 1e6     # un énorme volume à une heure du jour 10
    x = feat(df, "vp_poc", 1)
    day = df.index.normalize()
    d10, d11 = day.unique()[10], day.unique()[11]
    poc11 = (df["close"] - x * 0)  # même index
    assert x[day == d10].notna().all()                         # le jour 10 utilise le profil du jour 9
    # le POC du jour 11 est au prix de la bougie au gros volume du jour 10 (à une case de 30 près)
    tp = ((df["high"] + df["low"] + df["close"]) / 3).iloc[24 * 10 + 5]
    from mt5lab.inventions import _profile_levels
    poc, val, vah = _profile_levels(df, 1)
    rng = df[day == d10]
    width = (rng[["high", "low", "close"]].mean(axis=1).max() - rng[["high", "low", "close"]].mean(axis=1).min()) / 30
    assert abs(poc[np.argmax(day == d11)] - tp) <= width + 1e-9
    assert (val[day == d11] <= poc[day == d11]).all() and (poc[day == d11] <= vah[day == d11]).all()
    assert poc11 is not None


def test_pair_z_detects_a_market_that_left_its_partner():
    rng = np.random.default_rng(1)
    r = rng.normal(0, 0.001, 1500)
    idx = pd.date_range("2025-01-01", periods=1500, freq="h")
    b = 100 * np.exp(np.cumsum(r))
    a = 50 * np.exp(np.cumsum(r + rng.normal(0, 0.0002, 1500)))
    a[-10:] *= 1.02                                            # A s'écarte brusquement de B
    df = pd.DataFrame({"open": a, "high": a, "low": a, "close": a, "ext:B": b}, index=idx)
    z = feat(df, "pair_z", 10, "B")
    assert z.iloc[-1] > 3 and abs(z.iloc[-200:-20].mean()) < 1


def test_cot_parse_and_no_lookahead():
    dates = pd.date_range("2020-01-07", periods=200, freq="7D")       # mardis
    raw = pd.DataFrame({
        "Market and Exchange Names": ["GOLD - COMMODITY EXCHANGE INC."] * 200 + ["JAPANESE YEN - CHICAGO MERCANTILE EXCHANGE"] * 200,
        "As of Date in Form YYYY-MM-DD": list(dates.strftime("%Y-%m-%d")) * 2,
        "Open Interest (All)": 1000,
        "Noncommercial Positions-Long (All)": list(np.random.default_rng(3).uniform(100, 600, 200)) * 2,
        "Noncommercial Positions-Short (All)": 100})
    t = parse(raw)
    assert set(t["marche"]) == {"GOLD", "JPY"}
    assert (t[t.marche == "JPY"]["net"] <= 0).all()                     # yen acheté = USDJPY baissier
    assert market_of("XAUUSD") == "GOLD" and market_of("USDJPY") == "JPY" and market_of("US100.cash") == "NASDAQ"
    assert market_of("GER40.cash") is None
    bars = pd.date_range("2022-01-01", "2023-06-01", freq="h")
    df = pd.DataFrame({"close": 1.0}, index=bars)
    out = add_cot(df, "XAUUSD", t)
    assert out["cot_z52"].notna().any()
    tue = dates[120]
    # la donnée du mardi n'apparaît qu'à partir du samedi suivant
    before, after = out.loc[tue + pd.Timedelta(days=3, hours=23), "cot_z52"], out.loc[tue + pd.Timedelta(days=4), "cot_z52"]
    assert before != after
    assert feat(out, "cot", 52).notna().any() and feat(df, "cot", 52).isna().all()
    assert uses_cot({"trigger": {"f": "cot", "n": 52}}) and not uses_cot({"f": "rsi"})


def test_volatility_targeting_cuts_risk_only_when_results_get_wild():
    assert vol_factor([0.1, -0.1, 0.2, -0.2, 0.1], 0.75) == 1.0
    assert 0.4 <= vol_factor([3, -3, 2.5, -2.5, 3], 0.75) < 0.5
    days = pd.bdate_range("2026-01-05", periods=30)
    r = np.tile([3.0, -3.0], 15)                                         # journées très agitées
    t = pd.DataFrame({"entry_time": days + pd.Timedelta(hours=9), "exit_time": days + pd.Timedelta(hours=12),
                      "r": r, "w": 1.0})
    kept = apply_risk_rules(t, vol_target={"cible": 0.75, "jours": 20})
    assert kept["w"].iloc[:5].eq(1.0).all() and kept["w"].iloc[-1] < 0.6
