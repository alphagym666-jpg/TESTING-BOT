"""Gestion des trades : paliers (BE à +1R, +1R à +2R...) et sortie intelligente, dans la recherche et en direct."""
import numpy as np
import pandas as pd

from mt5lab.backtest import MANAGEMENT, RiskConfig, run_backtest


def _path():
    closes = [100.0] * 21 + [100.5, 101.2, 102.2, 102.6, 101.5, 100.5, 99.5, 98.5, 97.5] + [97.5] * 10
    c = np.array(closes)
    o = np.concatenate([[100.0], c[:-1]])
    idx = pd.date_range("2026-01-05", periods=len(c), freq="h")
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) + 0.1, "low": np.minimum(o, c) - 0.1, "close": c},
                      index=idx)
    sig = pd.Series(0, index=idx, dtype=np.int8)
    sig.iloc[19] = 1  # achat : entrée à l'ouverture de la bougie 20 (100), stop 1 % = 1 point = 1R
    return df, sig


def test_steps_and_smart_exit_keep_the_gains():
    df, sig = _path()
    r = {}
    for m in ("none", "breakeven", "paliers", "intelligente"):
        cfg = RiskConfig(sl_mode="pct", sl_value=1.0, rr=5.0, management=m, max_hold=100)
        _, t = run_backtest(df, sig, cfg, return_trades=True)
        r[m] = float(t["r"].iloc[0])
    assert r["none"] < -0.9                         # sans gestion : le trade revient au stop initial
    assert abs(r["breakeven"]) < 0.15               # break-even : ni gain ni perte
    assert 0.9 < r["paliers"] < 1.1                 # +2,6R atteint -> stop remonté à +1R
    assert r["intelligente"] > r["paliers"]         # sortie au retournement : encore mieux
    assert {"paliers", "intelligente"} <= set(MANAGEMENT)


def test_paper_moves_stop_by_steps_and_sends_it_to_the_bot(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from test_paper import FakeMarket
    from mt5lab.strategies import REGISTRY, StrategyDef
    REGISTRY["_test_long"] = StrategyDef("_test_long", "test", lambda d: pd.Series(1, index=d.index, dtype=np.int8))
    mk = FakeMarket()
    monkeypatch.setitem(sys.modules, "MetaTrader5", mk.module())
    monkeypatch.chdir(tmp_path)
    try:
        from mt5lab.data import MT5Connector
        from mt5lab.paper import PaperEngine, Position, Slot
        from mt5lab.pont import SignalBridge
        cand = {"signal": {"type": "single", "name": "_test_long", "params": {}}, "filter": "none",
                "risk": {"sl_mode": "atr", "sl_value": 1.0, "rr": 5.0, "management": "paliers", "max_hold": 200,
                         "direction": "both"}}
        eng = PaperEngine(MT5Connector().connect(verbose=False), [Slot("c", "EURUSD", "H1", cand, group="g")],
                          tmp_path / "paper", 1.0, groups={"g": {"capital": 100_000, "day_budget": 2.5}},
                          bridge=SignalBridge(tmp_path / "sig.csv"))
        s = eng.slots["c"]
        s.position = Position(1, 1.1000, 1.0980, 1.1100, 0.0020, 1.0, 200.0, "2026-01-05 10:00:00", 1.0)
        tick = SimpleNamespace(bid=1.1045, ask=1.1046, time_msc=1_767_607_200_000)
        eng._manage_steps(s, s.position, s.cfg, 1.1045, 0, 0.002, 1.1045, tick)   # +2,25R
        assert abs(s.position.sl - 1.1020) < 1e-9                                 # stop à +1R
        assert "MOVE" in (tmp_path / "sig.csv").read_text()                       # envoyé au bot
        smart = RiskConfig(**{**cand["risk"], "management": "intelligente"})
        eng._manage_steps(s, s.position, smart, 1.1045, -1, 0.002, 1.1045, tick)  # signal inverse -> fermeture
        assert s.position is None and s.trades == 1
        assert mk.sent == []
    finally:
        REGISTRY.pop("_test_long", None)
