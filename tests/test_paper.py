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


def test_platform_serves_live_state(setup):
    import json
    import urllib.request

    from mt5lab.plateforme import start_server
    mk, tmp = setup
    eng = _engine(tmp)
    eng.step()
    mk.new_bar()
    eng.step()
    srv = start_server(eng, 0, open_browser=False)  # port 0 = port libre choisi par le système
    try:
        port = srv.server_address[1]
        srv.publish(eng.snapshot())
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode()
        assert "Plateforme paper trading" in page
        d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/etat").read())
        pos = d["positions"][0]
        assert pos["sens"] == "ACHAT" and pos["sl"] < pos["entree"] < pos["tp"]
        assert d["ftmo"]["max_daily"] == 3.0
    finally:
        srv.shutdown()


def test_exploration_covers_every_strategy_and_rr(tmp_path):
    from mt5lab.backtest import RR_LEVELS
    from mt5lab.paper import load_exploration_slots
    slots = load_exploration_slots(tmp_path, ["EURUSD"], ["H1", "M15"])
    names = {s.candidate["signal"]["name"] for s in slots}
    assert len(names) >= len([n for n in REGISTRY if not n.startswith("_")])
    assert {s.cfg.rr for s in slots} == set(RR_LEVELS)


def test_reconnects_when_mt5_comes_back(setup, monkeypatch):
    """MT5 fermé : le paper trading attend et se reconnecte sans perdre ses positions."""
    import mt5lab.paper as paper_mod
    mk, tmp = setup
    eng = _engine(tmp)
    eng.step()
    mk.new_bar()
    eng.step()
    calls = {"n": 0}

    def flaky_connect(verbose=True):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("Échec de connexion à MT5 : terminal fermé")
        return eng.c

    monkeypatch.setattr(eng.c, "connect", flaky_connect)
    monkeypatch.setattr(paper_mod.time, "sleep", lambda s: None)
    eng._reconnect()
    assert calls["n"] == 3 and eng.slots["s1"].position is not None


def test_exploration_includes_exact_validated_and_portfolio(tmp_path):
    import json

    import pandas as pd

    from mt5lab.paper import load_exploration_slots
    cand = {"signal": {"type": "single", "name": "ema_cross", "params": {"fast": 9, "slow": 50}}, "filter": "trend_ema200",
            "risk": {"sl_mode": "swing", "sl_value": 10, "rr": 2.5, "management": "breakeven", "max_hold": 200,
                     "direction": "long"}}
    row = {"verdict": "APPROUVÉ", "score_is": 3.0, "avgR_oos": 0.3, "wr_oos": 45.0, "candidate": json.dumps(cand)}
    (tmp_path / "EURUSD_H1").mkdir()
    pd.DataFrame([row]).to_csv(tmp_path / "EURUSD_H1" / "classement.csv", index=False)
    pd.DataFrame([{"ordre": 4, "symbole": "EURUSD", "timeframe": "H1", "candidate": json.dumps(cand)}]).to_csv(
        tmp_path / "portefeuille_ftmo.csv", index=False)
    slots = load_exploration_slots(tmp_path, ["EURUSD"], ["H1"])
    exact = [s for s in slots if s.candidate == cand]
    assert len(exact) == 1 and exact[0].verdict == "portefeuille FTMO n°4"


def test_combined_strategy_shares_one_account_and_respects_daily_budget(setup):
    from mt5lab.data import MT5Connector
    from mt5lab.paper import PaperEngine, Slot
    mk, tmp = setup
    mk_cand = lambda rr: {"signal": {"type": "single", "name": "_test_long", "params": {}}, "filter": "none",
                          "risk": {"sl_mode": "atr", "sl_value": 1.0, "rr": rr, "management": "none",
                                   "max_hold": 200, "direction": "both"}}
    slots = [Slot(f"c{i}", "EURUSD", "H1", mk_cand(rr), group="combo", risk_pct=0.9) for i, rr in enumerate((2.0, 3.0))]
    conn = MT5Connector().connect(verbose=False)
    eng = PaperEngine(conn, slots, tmp / "paper", risk_pct=0.5,
                      groups={"combo": {"capital": 100_000, "day_budget": 1.0, "max_open": None, "day_stop": None}})
    eng.step()
    mk.new_bar()
    eng.step()
    g = eng.groups["combo"]
    opened = [s for s in eng.slots.values() if s.position]
    assert len(opened) == 1 and g.skipped == 1           # le 2e trade dépasserait 1 % de perte possible
    assert opened[0].position.risk_money == pytest.approx(900, rel=0.02)   # 0,9 % du compte partagé
    mk.push_ticks([opened[0].position.tp + 0.0001])
    eng.step()
    assert g.trades == 1 and g.balance == pytest.approx(100_000 + 2 * 900, rel=0.03)
    snap = eng.snapshot()
    assert snap["groupes"][0]["composants"] and snap["groupes"][0]["ftmo"] == "en cours"
    assert mk.sent == []


def test_combined_total_loss_cap(setup):
    """Garde des 10 % : si le compte a déjà perdu 9,5 %, un trade de 0,9 % (frais compris) est refusé."""
    from mt5lab.data import MT5Connector
    from mt5lab.paper import PaperEngine, Slot
    mk, tmp = setup
    cand = {"signal": {"type": "single", "name": "_test_long", "params": {}}, "filter": "none",
            "risk": {"sl_mode": "atr", "sl_value": 1.0, "rr": 2.0, "management": "none", "max_hold": 200,
                     "direction": "both"}}
    conn = MT5Connector().connect(verbose=False)
    eng = PaperEngine(conn, [Slot("c", "EURUSD", "H1", cand, group="g", risk_pct=0.9)], tmp / "paper", 0.5,
                      groups={"g": {"capital": 100_000, "day_budget": 2.5, "total_budget": 10.0}})
    g = eng.groups["g"]
    g.balance, g.day = 90_500, "2000-01-01"  # perte subie les jours précédents
    eng.step()
    mk.new_bar()
    eng.step()
    assert g.ftmo_status == "en cours"
    assert eng.slots["c"].position is None and g.skipped == 1


def test_exploration_labels_failles_and_catalog_best(tmp_path):
    import json

    import pandas as pd

    from mt5lab.paper import load_exploration_slots
    faille = {"signal": {"type": "rule", "name": "FAILLE A12-1", "trigger": {"f": "momentum", "n": 5, "op": ">", "v": 0.0},
                         "filters": [{"f": "hour", "n": 0, "op": "between", "v": [8, 10]}], "mirror": True},
              "filter": "none", "risk": {"sl_mode": "atr", "sl_value": 1.5, "rr": 2.0, "management": "none",
                                         "max_hold": 200, "direction": "both"}}
    best = {"signal": {"type": "single", "name": "smc_fvg", "params": {"min_atr": 0.3, "mode": "rejet", "max_age": 30}},
            "filter": "kill_zones", "risk": {"sl_mode": "swing", "sl_value": 10, "rr": 3.0, "management": "breakeven",
                                             "max_hold": 200, "direction": "long"}}
    rows = [{"verdict": "rejeté", "score_is": 2.0, "equipe": "C", "meilleure_version_de": "", "candidate": json.dumps(faille)},
            {"verdict": "rejeté", "score_is": 1.0, "equipe": "Optimiseur du catalogue", "meilleure_version_de": "smc_fvg",
             "candidate": json.dumps(best)}]
    (tmp_path / "EURUSD_H1").mkdir()
    pd.DataFrame(rows).to_csv(tmp_path / "EURUSD_H1" / "classement.csv", index=False)
    slots = load_exploration_slots(tmp_path, ["EURUSD"], ["H1"])
    labels = {s.verdict for s in slots}
    assert "faille des banques" in labels and "catalogue : meilleure version" in labels
    assert any(s.candidate == best for s in slots)


def test_quality_controller_pauses_and_excludes_from_combined(setup):
    """Contrôleur de qualité : 20 trades à -1R alors que la recherche attendait +0,3R -> pause ;
    en pause, le composant trade « à blanc » (hors compte combiné) ; le Directeur lit la pause."""
    import json as _json
    from mt5lab.data import MT5Connector
    from mt5lab.paper import PaperEngine, Slot
    mk, tmp = setup
    cand = {"signal": {"type": "single", "name": "_test_long", "params": {}}, "filter": "none",
            "risk": {"sl_mode": "atr", "sl_value": 1.0, "rr": 2.0, "management": "none", "max_hold": 200,
                     "direction": "both"}}
    conn = MT5Connector().connect(verbose=False)
    s = Slot("c", "EURUSD", "H1", cand, group="g", risk_pct=0.5, expected_avg_r=0.3)
    eng = PaperEngine(conn, [s], tmp / "paper", 0.5, groups={"g": {"capital": 100_000, "day_budget": 2.5}})
    s.history_r = list(np.random.default_rng(1).normal(-1.0, 0.3, 25))
    s.trades = 25
    eng.quality_check(s, "2024-01-01 10:00:00")
    assert s.paused and "attendu" in s.pause_reason
    eng.step()
    mk.new_bar()
    eng.step()
    assert s.position is not None and s.position.shadow        # trade suivi mais hors du compte combiné
    g = eng.groups["g"]
    mk.push_ticks([s.position.tp + 0.0001])
    eng.step()
    assert g.trades == 0 and g.balance == 100_000 and s.trades == 26
    eng.save()
    q = _json.loads((tmp / "paper" / "controle_qualite.json").read_text(encoding="utf-8"))
    assert q["strategies"][0]["en_pause"]
    from mt5lab.manager import Director
    assert Director._same_symbol("NASDAQ", "US100.cash") and not Director._same_symbol("EURUSD", "GBPUSD")
    s.history_r += [2.0] * 20
    eng.quality_check(s, "2024-02-01 10:00:00")
    assert not s.paused
    assert mk.sent == []
