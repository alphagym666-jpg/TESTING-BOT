"""Simule le package MetaTrader5 pour tester la connexion, le diagnostic et l'envoi d'ordres."""
import sys
import types
from types import SimpleNamespace

import numpy as np
import pytest

from mt5lab.data import synthetic


def make_fake(filling=2, max_bars=3000, trade_mode=0):
    df = synthetic(max_bars, seed=4)
    rates = np.zeros(len(df), dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
                                     ("close", "f8"), ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")])
    rates["time"] = df.index.astype("int64") // 10**9
    for k in ("open", "high", "low", "close"):
        rates[k] = df[k].to_numpy()
    rates["tick_volume"] = 100
    rates["spread"] = 12
    m = types.ModuleType("MetaTrader5")
    m.__version__ = "fake"
    m.sent = []
    m.ACCOUNT_TRADE_MODE_DEMO, m.ORDER_FILLING_FOK, m.ORDER_FILLING_IOC, m.ORDER_FILLING_RETURN = 0, 0, 1, 2
    m.TIMEFRAME_H1 = 16385
    m.TRADE_ACTION_DEAL, m.TRADE_ACTION_SLTP, m.ORDER_TYPE_BUY, m.ORDER_TYPE_SELL, m.ORDER_TIME_GTC = 1, 6, 0, 1, 0
    m.POSITION_TYPE_BUY = 0
    sym = SimpleNamespace(name="EURUSD.m", visible=True, spread=12, point=1e-5, digits=5, filling_mode=filling,
                          trade_tick_value=1.0, trade_tick_size=1e-5, volume_step=0.01, volume_min=0.01, volume_max=50)
    m.initialize = lambda **kw: True
    m.shutdown = lambda: None
    m.last_error = lambda: (1, "ok")
    m.account_info = lambda: SimpleNamespace(login=1, server="Fake-Demo", balance=10_000, currency="USD",
                                             trade_mode=trade_mode, leverage=100, trade_allowed=True)
    m.terminal_info = lambda: SimpleNamespace(connected=True, trade_allowed=True, company="Fake")
    m.symbol_info = lambda s: sym if s == "EURUSD.m" else None
    m.symbols_get = lambda pattern="*": [sym]
    m.symbol_select = lambda s, v: True
    m.symbol_info_tick = lambda s: SimpleNamespace(bid=rates["close"][-1], ask=rates["close"][-1] + 12e-5)
    m.copy_rates_from_pos = lambda s, tf, start, n: rates[-n:] if n <= max_bars else None
    m.positions_get = lambda symbol=None: []

    def order_send(req):
        m.sent.append(req)
        return SimpleNamespace(retcode=10009, comment="done")

    m.order_send = order_send
    return m


@pytest.fixture
def fake(monkeypatch):
    m = make_fake()
    monkeypatch.setitem(sys.modules, "MetaTrader5", m)
    monkeypatch.chdir("/")  # pas de .env local
    return m


def test_connect_resolve_rates_and_diagnose(fake, capsys):
    from mt5lab.data import MT5Connector
    with MT5Connector() as c:
        assert c.resolve("EURUSD") == "EURUSD.m"            # suffixe du courtier trouvé
        df = c.rates("EURUSD", "H1", 20000)                   # trop demandé -> repli automatique
        assert 500 <= len(df) <= 3000 and {"open", "close", "volume"} <= set(df.columns)
        assert c.filling_mode("EURUSD") == fake.ORDER_FILLING_IOC
        assert c.diagnose(["EURUSD"]) is True
    assert "Tout est prêt" in capsys.readouterr().out


def test_live_sends_order_with_sl_tp_and_lot(fake):
    from mt5lab.data import MT5Connector
    from mt5lab.live import LiveTrader
    cand = {"signal": {"type": "single", "name": "ema_cross", "params": {"fast": 5, "slow": 21}},
            "filter": "none", "risk": {"sl_mode": "atr", "sl_value": 1.5, "rr": 2.0, "management": "none",
                                       "max_hold": 200, "direction": "both"}}
    with MT5Connector() as c:
        t = LiveTrader(c, "EURUSD", "H1", cand, risk_pct=0.5, execute=True)
        side = 1
        t.open(side, c.rates("EURUSD", "H1", 1000).iloc[:-1])
    req = fake.sent[-1]
    assert req["symbol"] == "EURUSD.m" and req["type_filling"] == fake.ORDER_FILLING_IOC
    assert req["sl"] < req["price"] < req["tp"]
    assert abs((req["tp"] - req["price"]) - 2 * (req["price"] - req["sl"])) < 3e-5   # R:R 1:2
    assert 0.01 <= req["volume"] <= 50


def test_real_account_refused(monkeypatch):
    m = make_fake(trade_mode=2)
    monkeypatch.setitem(sys.modules, "MetaTrader5", m)
    monkeypatch.chdir("/")
    from mt5lab.data import MT5Connector
    from mt5lab.live import LiveTrader
    with MT5Connector() as c, pytest.raises(RuntimeError, match="RÉEL"):
        LiveTrader(c, "EURUSD", "H1", {"signal": {}, "filter": "none",
                                        "risk": {"sl_mode": "atr", "sl_value": 1.5, "rr": 2.0}}, execute=True)


def test_lab_all_timeframes_with_alias_and_comparison(monkeypatch, tmp_path):
    """NASDAQ -> US100.cash, plusieurs timeframes, puis page de comparaison et paper 'meilleures'."""
    m = make_fake(max_bars=3000)
    syms = {n: SimpleNamespace(name=n, visible=True, spread=12, point=1e-5, digits=5, filling_mode=2,
                               trade_tick_value=1.0, trade_tick_size=1e-5, volume_step=0.01, volume_min=0.01,
                               volume_max=50) for n in ("EURUSD", "US100.cash")}
    m.symbol_info = lambda s: syms.get(s)
    m.symbols_get = lambda pattern="*": [v for k, v in syms.items() if pattern.strip("*").upper() in k.upper()]
    m.TIMEFRAME_H4 = 16388
    monkeypatch.setitem(sys.modules, "MetaTrader5", m)
    monkeypatch.chdir(tmp_path)
    import run
    monkeypatch.setattr(sys, "argv", ["run.py", "lab", "--symbols", "NASDAQ", "EURUSD", "--timeframes", "H1", "H4",
                                      "--rounds", "1", "--budget", "40", "--workers", "1", "--seed", "1",
                                      "--commission", "EURUSD=5", "NASDAQ=0", "--no-invent"])
    run.main()
    for label in ("NASDAQ_H1", "NASDAQ_H4", "EURUSD_H1", "EURUSD_H4"):
        assert (tmp_path / "results" / label / "classement.csv").exists()
    html_ = (tmp_path / "results" / "comparaison.html").read_text(encoding="utf-8")
    assert "NASDAQ" in html_ and "H4" in html_
    comp = __import__("pandas").read_csv(tmp_path / "results" / "comparaison.csv")
    assert {"gain_mois_pct", "gain_mois_usd", "trades_mois"} <= set(comp.columns)

    from mt5lab.data import MT5Connector
    from mt5lab.paper import PaperEngine, load_best_slots
    slots = load_best_slots(tmp_path / "results", None, top=5)
    assert slots
    with MT5Connector() as c:
        eng = PaperEngine(c, slots, tmp_path / "paper", 0.5, {"EURUSD": 5.0, "NASDAQ": 0.0})
        assert eng.commission("EURUSD") == 5.0 and eng.commission("US100.cash") == 0.0


def test_rates_years_by_duration_and_coverage_warning(monkeypatch, capsys):
    """Historique demandé en années : période couverte affichée, avertissement si le courtier en fournit moins."""
    m = make_fake(max_bars=3000)
    full = m.copy_rates_from_pos("EURUSD.m", 16385, 0, 3000)
    m.copy_rates_range = lambda s, tf, a, b: full[(full["time"] >= int(a.timestamp())) & (full["time"] <= int(b.timestamp()))]
    monkeypatch.setitem(sys.modules, "MetaTrader5", m)
    monkeypatch.chdir("/")
    monkeypatch.setattr("time.sleep", lambda s: None)
    from mt5lab.data import MT5Connector
    with MT5Connector() as c:
        df = c.rates_years("EURUSD", "H1", 5)
    out = capsys.readouterr().out
    assert len(df) > 0 and "ans)" in out
    assert "ATTENTION" in out   # les données de test ne couvrent pas 5 ans
