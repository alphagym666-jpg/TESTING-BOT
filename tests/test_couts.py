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


def test_correlation_groups_and_risk_rule():
    from mt5lab.data import correlation_of
    from mt5lab.ftmo import apply_risk_rules
    assert correlation_of("US100.cash") == ("INDICES", 1) == correlation_of("GER40.cash") == correlation_of("US30.cash")
    assert correlation_of("EURUSD")[0] == correlation_of("USDJPY")[0] == "DOLLAR"
    assert correlation_of("EURUSD")[1] == -correlation_of("USDJPY")[1]
    assert correlation_of("XAUUSD") == ("", 0)
    t = pd.DataFrame({"entry_time": pd.to_datetime(["2024-01-02 10:00", "2024-01-02 10:05", "2024-01-02 10:10"]),
                      "exit_time": pd.to_datetime(["2024-01-02 12:00"] * 3), "r": [1.0, 1.0, 1.0], "w": 0.5,
                      "cluster": ["INDICES", "INDICES", "INDICES"], "expo": [1, 1, -1]})
    kept = apply_risk_rules(t, max_corr=1)
    assert len(kept) == 2 and list(kept["expo"]) == [1, -1]   # 2e achat d'indice refusé, la vente passe
    assert len(apply_risk_rules(t, max_corr=None, max_open=None, day_stop=None)) == 3


def test_weekend_spread_not_applied_to_history():
    """Le samedi, MT5 affiche un spread énorme : il ne doit pas pénaliser les trades historiques."""
    df = synthetic(3000, seed=3)
    sig = pd.Series(np.where(np.arange(len(df)) % 7 == 0, 1, 0), index=df.index)
    cfg = RiskConfig("atr", 1.5, 2.0, "none", 200, "both")
    d = df.assign(cost=0.0001)
    normal = run_backtest(d, sig, cfg, cost=0.0001)
    weekend = run_backtest(d, sig, cfg, cost=0.01)          # spread « du moment » 100x plus large
    assert weekend.avg_r == normal.avg_r
    assert run_backtest(d, sig, cfg, cost_mult=2.0).avg_r < normal.avg_r


def test_enrich_uses_at_least_median_spread():
    conn = MT5Connector.__new__(MT5Connector)
    info = SimpleNamespace(point=1e-5, trade_tick_value=1.0, trade_tick_size=1e-5, swap_mode=0, visible=True,
                           currency_base="EUR", currency_profit="USD")
    conn.symbol_info = lambda s: info
    idx = pd.date_range("2024-01-01", periods=5, freq="h")
    df = pd.DataFrame({"open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1, "volume": 1,
                       "spread": [2, 10, 10, 10, 40]}, index=idx)
    out = conn.enrich(df, "EURUSD")
    assert np.allclose(out["cost"], np.array([10, 10, 10, 10, 40]) * 1e-5)


def test_mark_trials_keeps_positive_oos_rejects_only():
    from mt5lab.lab import mark_trials
    b = pd.DataFrame({"verdict": ["APPROUVÉ", "rejeté : edge OOS non significatif", "rejeté : espérance OOS négative/faible",
                                  "rejeté : edge OOS non significatif", "rejeté : trop peu de trades OOS"],
                      "trades_oos": [50, 40, 40, 40, 5], "avgR_oos": [0.3, 0.12, -0.1, 0.02, 0.5],
                      "pf_oos": [1.6, 1.3, 0.9, 1.01, 2.0], "sharpe_oos": [3, 1.5, -1, 0.2, 2]})
    v = mark_trials(b)["verdict"].tolist()
    assert v[0] == "APPROUVÉ" and v[1].startswith("À L'ESSAI") and "non validée : edge" in v[1]
    assert v[2].startswith("rejeté") and v[3].startswith("rejeté") and v[4].startswith("rejeté")


def test_bot_refuses_trial_combined(tmp_path):
    import json as _json
    import pytest
    from mt5lab.pont import generate_bot
    (tmp_path / "strategie_combinee.json").write_text(_json.dumps({"essai": True, "composants": []}), encoding="utf-8")
    with pytest.raises(SystemExit):
        generate_bot(tmp_path)
    assert (generate_bot(tmp_path, forcer=True) / "LaboBot.mq5").exists()


def test_best_day_rule_raises_target():
    """Règle du meilleur jour (50 %) : une journée à +6 % oblige à atteindre plus de +12 %."""
    from mt5lab.ftmo import FtmoRules, count_challenges
    r = FtmoRules()
    assert r.target_needed(10, 6) == 12 and r.target_needed(10, 4) == 10
    days = pd.bdate_range("2020-01-01", periods=10)
    pnl = [6, 1, 1, 2, 1, 1.5, 1, 0, 0, 0]          # +10 au 4e jour, mais meilleur jour 6 > 50 % de 10
    daily = pd.DataFrame({"pnl": pnl, "worst": 0.0, "traded": True}, index=days)
    c = count_challenges(daily, r)
    assert c["reussis"] == 1 and c["liste"][0]["jours"] == 6    # réussi quand le total dépasse 12 (6+1+1+2+1+1.5)
    c0 = count_challenges(daily, FtmoRules(best_day_pct=0))
    assert c0["liste"][0]["jours"] == 4


def test_auditor_flags_luck_and_concentration():
    from mt5lab.lab import audit_trades
    solide = audit_trades(np.tile([1.0, -0.5, 0.8, -0.4, 1.2, 0.3], 10), 0.2)
    assert solide["audit"] == "OK" and not solide["audit_grave"]
    chanceux = audit_trades(np.array([15.0] + [-0.3] * 29), 0.1)          # tout vient d'un seul trade
    assert chanceux["audit_grave"] and "3 trades" in chanceux["audit_detail"]
    retard = audit_trades(np.tile([1.0, -0.5, 0.8, -0.4], 10), -0.05)      # ne tient pas une bougie de retard
    assert retard["audit_grave"] and "retardée" in retard["audit_detail"]
