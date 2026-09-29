"""L'ingénieur de vitesse : la version compilée (numba) donne EXACTEMENT les mêmes résultats que la version Python."""
import numpy as np
import pandas as pd

from mt5lab import backtest as bt
from mt5lab.data import synthetic


def test_compiled_and_python_backtests_are_identical(monkeypatch):
    rng = np.random.default_rng(1)
    df = synthetic(3000, seed=2)
    df = df.assign(cost=rng.uniform(5e-5, 3e-4, len(df)), swap_long=-2e-5, swap_short=1e-5,
                   news_block=rng.random(len(df)) < 0.05)
    sig = pd.Series(rng.choice([0, 0, 0, 1, -1], len(df)).astype(np.int8), index=df.index)
    cfgs = bt.all_risk_configs(("both", "long"))[::5]
    fast = [bt.run_backtest(df, sig, c, cost=1e-4) for c in cfgs]
    monkeypatch.setattr(bt, "_core_fast", bt._core)
    slow = [bt.run_backtest(df, sig, c, cost=1e-4) for c in cfgs]
    for a, b in zip(fast, slow):
        assert a.trades == b.trades and np.isclose(a.avg_r, b.avg_r) and np.isclose(a.max_dd_r, b.max_dd_r)


def test_pivots_vectorized_matches_definition():
    from mt5lab.indicators import pivots
    df = synthetic(800, seed=3)
    h = df["high"].to_numpy()
    ph, _, ph_at, _ = pivots(df, 3)
    for i in range(6, len(df)):
        p = i - 3
        w = h[p - 3: i + 1]
        expect = h[p] == w.max() and np.argmax(w) == 3
        assert (not np.isnan(ph[i])) == expect and (ph_at[i] == p) == expect
