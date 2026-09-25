import numpy as np
import pandas as pd
import pytest

from mt5lab.backtest import RiskConfig, run_backtest
from mt5lab.data import synthetic
from mt5lab.evaluator import compute_signal
from mt5lab.strategies import REGISTRY, expand_grid


@pytest.fixture(scope="module")
def df():
    return synthetic(1500, seed=1)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_no_lookahead(df, name):
    """Le signal d'une bougie ne doit pas changer quand on ajoute des bougies futures."""
    params = expand_grid(REGISTRY[name].grid)[0]
    full = REGISTRY[name].func(df, **params)
    cut = 1000
    partial = REGISTRY[name].func(df.iloc[:cut], **params)
    assert (full.iloc[:cut].to_numpy() == partial.to_numpy()).all(), name
    assert set(np.unique(full)) <= {-1, 0, 1}


def _bars(rows):
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="h")
    d = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    d["volume"] = 1.0
    return d


def test_take_profit_and_stop_loss():
    # 20 bougies plates (ATR ~ 1) puis un signal d'achat
    rows = [(100, 100.5, 99.5, 100)] * 20
    rows += [(100, 100.2, 99.8, 100)]          # bougie du signal (i=20)
    rows += [(100, 100.5, 99.9, 100.3)]        # entrée à 100 (i=21)
    rows += [(100.3, 103.0, 100.2, 102.8)]     # touche le TP 1:2
    df = _bars(rows)
    sig = pd.Series(0, index=df.index)
    sig.iloc[20] = 1
    res, trades = run_backtest(df, sig, RiskConfig("pct", 1.0, 2.0), return_trades=True)
    assert res.trades == 1 and trades.iloc[0]["r"] == pytest.approx(2.0)

    rows[-1] = (100.3, 100.4, 98.0, 98.5)      # touche le SL à 99
    df = _bars(rows)
    res, trades = run_backtest(df, sig, RiskConfig("pct", 1.0, 2.0), return_trades=True)
    assert trades.iloc[0]["r"] == pytest.approx(-1.0)


def test_same_bar_sl_and_tp_counts_as_loss():
    rows = [(100, 100.5, 99.5, 100)] * 21 + [(100, 100.1, 99.9, 100), (100, 105, 95, 100)]
    df = _bars(rows)
    sig = pd.Series(0, index=df.index)
    sig.iloc[20] = 1
    _, trades = run_backtest(df, sig, RiskConfig("pct", 1.0, 2.0), return_trades=True)
    assert trades.iloc[0]["r"] == pytest.approx(-1.0)


def test_costs_reduce_result(df):
    sig = compute_signal(df, {"type": "single", "name": "ema_cross", "params": {"fast": 9, "slow": 21}})
    a = run_backtest(df, sig, RiskConfig())
    b = run_backtest(df, sig, RiskConfig(), cost=0.0005)
    assert a.trades == b.trades > 0 and b.avg_r < a.avg_r


def test_lot_size():
    from types import SimpleNamespace
    from mt5lab.live import lot_size
    info = SimpleNamespace(trade_tick_value=1.0, trade_tick_size=0.00001, point=0.00001,
                           volume_step=0.01, volume_min=0.01, volume_max=100)
    # 10 000 $ x 1 % = 100 $ de risque ; SL de 50 pips = 500 ticks x 1 $ = 500 $/lot -> 0.2 lot
    assert lot_size(info, 10_000, 1.0, 0.0050) == pytest.approx(0.2)
