"""Paper trading sur un faux MT5 : prix contrôlés, aucun ordre ne doit partir."""
import sys
import types
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from mt5lab.strategies import REGISTRY, StrategyDef

RATES_DT = [("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
            ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")]
TICK_DT = [("time", "i8"), ("bid", "f8"), ("ask", "f8"), ("time_msc", "i8")]


class FakeMarket:
    def __init__(self, n=300):
        t0 = 1_700_000_000
        self.rates = np.zeros(n, dtype=RATES_DT)
        self.rates["time"] = t0 + np.arange(n) * 3600
        base = 1.1 + 0.0005 * np.sin(np.arange(n) / 5)
        self.rates["open"], self.rates["close"] = base, base
        self.rates["high"], self.rates["low"] = base + 0.001, base - 0.001  # ATR ~ 0.002
        self.msc = int(self.rates["time"][-1]) * 1000
        self.bid, self.spread = 1.1, 0.0001
        self.ticks = np.zeros(0, dtype=TICK_DT)
        self.sent = []

    def new_bar(self):
        r = self.rates[-1].copy()
        r["time"] += 3600
        self.rates = np.append(self.rates, r)
        self.msc = int(r["time"]) * 1000 + 1

    def push_ticks(self, bids):
        rows = []
        for b in bids:
            self.msc += 100
            rows.append((self.msc // 1000, b, b + self.spread, self.msc))
        self.ticks = np.append(self.ticks, np.array(rows, dtype=TICK_DT))
        self.bid = bids[-1]

    def module(self):
        m = types.ModuleType("MetaTrader5")
        mk = self
        m.ACCOUNT_TRADE_MODE_DEMO, m.COPY_TICKS_ALL, m.TIMEFRAME_H1 = 0, -1, 16385
        sym = SimpleNamespace(name="EURUSD", visible=True, spread=10, point=1e-5, digits=5, filling_mode=2,
                              trade_tick_value=1.0, trade_tick_size=1e-5, volume_step=0.01, volume_min=0.01,
                              volume_max=100)
        m.initialize = lambda **kw: True
        m.shutdown = lambda: None
        m.last_error = lambda: (1, "ok")
        m.account_info = lambda: SimpleNamespace(login=1, server="Fake-Demo", balance=1000, currency="USD",
                                                 trade_mode=0, leverage=100, trade_allowed=True)
        m.terminal_info = lambda: SimpleNamespace(connected=True, trade_allowed=True, company="Fake")
        m.symbol_info = lambda s: sym if s == "EURUSD" else None
        m.symbols_get = lambda pattern="*": [sym]
        m.symbol_select = lambda s, v: True
        m.symbol_info_tick = lambda s: SimpleNamespace(bid=mk.bid, ask=mk.bid + mk.spread, time_msc=mk.msc)
        m.copy_rates_from_pos = lambda s, tf, start, n: mk.rates[-n:]
        m.copy_ticks_from = lambda s, dt, n, flags: mk.ticks[mk.ticks["time_msc"] >= int(dt.timestamp() * 1000)]
        m.positions_get = lambda symbol=None: []

        def order_send(req):  # ne doit JAMAIS être appelé en paper trading
            mk.sent.append(req)

        m.order_send = order_send
        return m


@pytest.fixture
def setup(monkeypatch, tmp_path):
    REGISTRY["_test_long"] = StrategyDef("_test_long", "test", lambda df: pd.Series(1, index=df.index, dtype=np.int8))
    mk = FakeMarket()
    monkeypatch.setitem(sys.modules, "MetaTrader5", mk.module())
    monkeypatch.chdir(tmp_path)
    yield mk, tmp_path
    REGISTRY.pop("_test_long", None)


def _engine(tmp_path, rr=2.0, risk_pct=1.0, commission=0.0):
    from mt5lab.data import MT5Connector
    from mt5lab.paper import PaperEngine, Slot
    cand = {"signal": {"type": "single", "name": "_test_long", "params": {}}, "filter": "none",
            "risk": {"sl_mode": "atr", "sl_value": 1.0, "rr": rr, "management": "none", "max_hold": 200,
                     "direction": "both"}}
    conn = MT5Connector().connect(verbose=False)
    return PaperEngine(conn, [Slot("s1", "EURUSD", "H1", cand)], tmp_path / "paper", risk_pct=risk_pct,
                       commission_per_lot=commission)


def test_take_profit_on_real_ticks(setup):
    mk, tmp = setup
    eng = _engine(tmp)
    eng.step()                                   # démarrage : pas d'entrée sur une bougie déjà close
    assert eng.slots["s1"].position is None
    mk.new_bar()
    eng.step()                                   # nouvelle bougie + signal -> achat fictif à l'ask
    p = eng.slots["s1"].position
    assert p is not None and p.entry == pytest.approx(1.1 + mk.spread)
    tp = p.tp
    mk.push_ticks([1.1005, 1.1010, tp + 0.00001, 1.1001])
    eng.step()
    s = eng.slots["s1"]
    assert s.position is None and s.trades == 1 and s.wins == 1
    assert s.history_r[-1] == pytest.approx(2.0, abs=0.02)
    assert s.balance == pytest.approx(100_000 * 1.02, rel=0.01)
    assert (tmp / "paper" / "trades.csv").exists()
    assert mk.sent == []                         # aucun ordre envoyé à MT5


def test_stop_loss_gap_gives_real_slippage(setup):
    mk, tmp = setup
    eng = _engine(tmp)
    eng.step()
    mk.new_bar()
    eng.step()
    p = eng.slots["s1"].position
    mk.push_ticks([p.sl - 0.0005])               # gap sous le stop : rempli au prix du tick, pas au SL
    eng.step()
    assert eng.slots["s1"].history_r[-1] < -1.05
    assert mk.sent == []


def test_state_survives_restart_and_dashboard(setup):
    from mt5lab.paper import write_dashboard
    mk, tmp = setup
    eng = _engine(tmp)
    eng.step()
    mk.new_bar()
    eng.step()
    write_dashboard(eng)
    assert "Paper trading" in (tmp / "paper" / "tableau_de_bord.html").read_text(encoding="utf-8")
    eng2 = _engine(tmp)                          # redémarrage
    assert eng2.slots["s1"].position is not None
    mk.push_ticks([eng2.slots["s1"].position.tp + 0.0001])
    eng2.step()
    assert eng2.slots["s1"].trades == 1


@pytest.mark.parametrize("commission", [0.0, 7.0])
def test_max_loss_half_percent_of_100k(setup, commission):
    """0,5 % de 100 000 : la perte au stop (commission incluse) ne dépasse jamais 500."""
    mk, tmp = setup
    eng = _engine(tmp, risk_pct=0.5, commission=commission)
    eng.step()
    mk.new_bar()
    eng.step()
    p = eng.slots["s1"].position
    assert p.risk_money + commission * p.lots <= 500 + 1e-6
    assert p.risk_money + commission * p.lots > 480          # on utilise bien le budget
    mk.push_ticks([p.sl])                                     # stop touché pile
    eng.step()
    assert -500 - 1e-6 <= eng.slots["s1"].pnl < 0
