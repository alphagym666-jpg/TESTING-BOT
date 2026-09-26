"""Coûts réels : spread de chaque bougie, swaps, filtre des nouvelles."""
from types import SimpleNamespace

import numpy as np
import pandas as pd

from mt5lab.backtest import RiskConfig, run_backtest
from mt5lab.data import MT5Connector, load_news, news_blocked, news_mask, synthetic


def _news():
    return pd.DataFrame({"time": pd.to_datetime(["2020-01-01 14:30", "2020-01-02 09:00"]),
                         "currency": ["USD", "JPY"], "importance": [3, 3], "event": ["NFP", "BoJ"]})


def test_news_mask_only_relevant_currency_and_window():
    idx = pd.date_range("2020-01-01 13:00", periods=24 * 12, freq="5min")
    m = news_mask(idx, _news(), {"EUR", "USD"}, 30)
    blocked = idx[m]
    assert blocked.min() == pd.Timestamp("2020-01-01 14:00") and blocked.max() == pd.Timestamp("2020-01-01 15:00")
    assert not news_mask(idx, _news(), {"EUR", "GBP"}, 30).any()
    # au-delà de H1 : pas de filtre (chaque bougie contiendrait une annonce)
    assert not news_mask(pd.date_range("2020-01-01", periods=50, freq="4h"), _news(), {"USD"}, 30).any()
    assert news_blocked(_news(), {"USD"}, "2020-01-01 14:50:00", 30)
    assert not news_blocked(_news(), {"USD"}, "2020-01-01 15:10:00", 30)


def test_load_news_csv(tmp_path):
    p = tmp_path / "news.csv"
    p.write_text("time,currency,importance,event\n2020-01-01 14:30,usd,3,NFP\n2020-01-01 10:00,EUR,2,PMI\n")
    df = load_news(p)
    assert len(df) == 1 and df["currency"].iloc[0] == "USD"


def test_backtest_uses_bar_spread_swaps_and_news():
    df = synthetic(3000, seed=3)
    sig = pd.Series(np.where(np.arange(len(df)) % 7 == 0, 1, 0), index=df.index)
    cfg = RiskConfig("atr", 1.5, 2.0, "none", 200, "both")
    base = run_backtest(df, sig, cfg, cost=0.0)
    costly = df.assign(cost=0.0005, swap_long=-0.0002, swap_short=-0.0002)
    c = run_backtest(costly, sig, cfg, cost=0.0)
    assert c.trades == base.trades and c.avg_r < base.avg_r
    blocked = df.assign(news_block=True)
    assert run_backtest(blocked, sig, cfg).trades == 0


def test_enrich_adds_cost_swap_and_news():
    conn = MT5Connector.__new__(MT5Connector)
    info = SimpleNamespace(point=1e-5, trade_tick_value=1.0, trade_tick_size=1e-5, swap_mode=1, swap_long=-7.0,
                           swap_short=2.0, currency_base="EUR", currency_profit="USD", visible=True)
    conn.symbol_info = lambda s: info
    idx = pd.date_range("2020-01-01 13:00", periods=48, freq="5min")
    df = pd.DataFrame({"open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1, "volume": 1, "spread": 12}, index=idx)
    out = conn.enrich(df, "EURUSD", 0, 5.0, _news(), 30)
    assert np.isclose(out["cost"].iloc[0], 12e-5 + 5.0 * 1e-5)
    assert np.isclose(out["swap_long"].iloc[0], -7e-5) and np.isclose(out["swap_short"].iloc[0], 2e-5)
    assert out["news_block"].any() and not out["news_block"].all()
    assert conn.currencies("XAUUSD") == {"EUR", "USD"}


def test_count_challenges_chains_real_history():
    from mt5lab.ftmo import FtmoRules, count_challenges
    days = pd.bdate_range("2020-01-01", periods=30)
    # +3 %/jour : réussi en 4 jours (min 4 jours), puis une journée à -4 % = raté, puis réussi encore...
    pnl = [3, 3, 3, 3, -4, 3, 3, 3, 3] + [0] * 21
    daily = pd.DataFrame({"pnl": pnl, "worst": np.minimum(pnl, 0), "traded": [p != 0 for p in pnl]}, index=days)
    c = count_challenges(daily, FtmoRules())
    assert c["reussis"] == 2 and c["rates"] == 1 and c["jours_moyens"] == 4
    assert c["liste"][1]["resultat"] == "raté"
