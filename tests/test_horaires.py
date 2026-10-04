"""Meilleures heures de chaque stratégie : choix + contrôle, heure par heure, paper, bots, Directeur."""
import json
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from mt5lab.horaires import best_window, filter_trades, horaire, hour_profile, in_window


def _trades(effect=(8, 11), n=500, seed=2):
    rng = np.random.default_rng(seed)
    t = pd.Timestamp("2024-01-01") + pd.to_timedelta(np.sort(rng.integers(0, 700 * 24, n)), unit="h")
    h = t.hour
    p = np.full(n, 0.33) if effect is None else np.where((h >= effect[0]) & (h < effect[1]), 0.62, 0.28)
    return pd.DataFrame({"entry_time": t, "exit_time": t + pd.Timedelta(hours=1),
                         "r": np.where(rng.random(n) < p, 2.0, -1.0)})


def test_finds_any_window_and_rejects_noise():
    t = _trades()
    b = best_window(t["entry_time"], t["r"])
    assert b and b["ok"] and b["debut"] <= 9 and b["fin"] >= 10 and b["fin"] - b["debut"] <= 6
    assert b["r_moyen_plage"] > b["r_moyen_24h"] and len(b["profil"]) == 24
    noise = sum(bool((x := best_window(*_trades(None, seed=s)[["entry_time", "r"]].T.values)) and x["ok"])
                for s in range(40))
    assert noise <= 4                                         # au hasard : presque jamais « confirmée »


def test_hour_by_hour_profile_and_filters():
    t = _trades()
    prof = hour_profile(t["entry_time"], t["r"])
    best = max((p for p in prof if p["trades"] >= 10), key=lambda p: p["r_moyen"])
    assert 8 <= best["h"] < 11 and sum(p["trades"] for p in prof) == len(t)
    h = np.array([22.5, 1.0, 3.0, 12.0])
    assert list(in_window(h, 22, 2)) == [True, True, False, False]   # plage qui passe minuit
    sub = filter_trades(t, horaire(8, 11))
    assert len(sub) and sub["entry_time"].dt.hour.between(8, 10).all()
    assert filter_trades(t, None) is t


def test_paper_component_hours_and_bot_file(monkeypatch, tmp_path):
    from mt5lab.paper import in_session, load_combined_slots
    from mt5lab.pont import generate_strategy_bot
    assert in_session("2026-09-01 09:30:00", (8.0, 11.0, 0.0)) and not in_session("2026-09-01 11:00:00", (8.0, 11.0, 0.0))
    assert in_session("2026-09-01 23:00:00", (22.0, 2.0, 0.0)) and in_session("2026-09-01 01:00:00", (22.0, 2.0, 0.0))
    cand = {"signal": {"type": "single", "name": "ema_cross", "params": {"fast": 9, "slow": 21}}, "filter": "none",
            "risk": {"sl_mode": "atr", "sl_value": 1.5, "rr": 2.0, "management": "none", "max_hold": 200, "direction": "both"}}
    comb = {"nom": "test", "regles": {"day_budget": 2.5, "total_budget": 10},
            "composants": [{"symbole": "EURUSD", "timeframe": "H1", "candidate": cand, "strategie": "a", "risque_config": "x",
                            "risk_pct": 1.0, "horaire": horaire(8, 11)},
                           {"symbole": "XAUUSD", "timeframe": "H1", "candidate": cand, "strategie": "b", "risque_config": "x",
                            "risk_pct": 0.5}]}
    out = generate_strategy_bot(comb, tmp_path, 100_000)
    assert "entrées : 8h-11h" in (out / "LaboBot.mq5").read_text(encoding="utf-8-sig")
    assert "8h-11h" in (out / "LISEZMOI_BOT.txt").read_text(encoding="utf-8")
    slots, groups = load_combined_slots(tmp_path, path=out / "strategie.json")   # ce que relance LANCER_BOT.bat
    assert slots[0].session == (8.0, 11.0, 0.0) and slots[1].session is None


def test_paper_does_not_enter_outside_component_hours(monkeypatch, tmp_path):
    from tests.test_paper import FakeMarket
    from mt5lab.strategies import REGISTRY, StrategyDef
    REGISTRY["_test_long"] = StrategyDef("_test_long", "test", lambda df: pd.Series(1, index=df.index, dtype=np.int8))
    mk = FakeMarket()
    monkeypatch.setitem(sys.modules, "MetaTrader5", mk.module())
    monkeypatch.chdir(tmp_path)
    from mt5lab.data import MT5Connector
    from mt5lab.paper import PaperEngine, Slot
    cand = {"signal": {"type": "single", "name": "_test_long", "params": {}}, "filter": "none",
            "risk": {"sl_mode": "atr", "sl_value": 1.0, "rr": 2.0, "management": "none", "max_hold": 200, "direction": "both"}}
    hour = pd.Timestamp(int(mk.rates["time"][-1]) + 3600, unit="s").hour
    slots = [Slot("dans", "EURUSD", "H1", cand, session=(hour, (hour + 2) % 24, 0.0)),
             Slot("hors", "EURUSD", "H1", cand, session=((hour + 5) % 24, (hour + 7) % 24, 0.0))]
    eng = PaperEngine(MT5Connector().connect(verbose=False), slots, tmp_path / "paper", risk_pct=1.0)
    eng.step()
    mk.new_bar()
    eng.step()
    assert eng.slots["dans"].position is not None and eng.slots["hors"].position is None
    REGISTRY.pop("_test_long", None)


def test_director_hour_variants_and_components(tmp_path):
    from mt5lab.manager import Director, DirectorConfig
    d = Director(DirectorConfig(symbols=["EURUSD"], timeframes=["H1"], out=tmp_path), lambda s, t: None,
                 log=lambda m: None)
    t = _trades()
    key = "EURUSD_H1|abc"
    trades, windows = {key: t}, {key: (t["entry_time"].min(), t["exit_time"].max())}
    info = {key: {"symbole": "EURUSD", "timeframe": "H1", "candidate": {"x": 1}, "strategie": "strat", "risque": "r",
                  "seule_ftmo": 10.0}}
    d._hour_variants(trades, windows, info)
    v = [k for k in info if "|h" in k]
    assert len(v) == 1 and info[v[0]]["horaire"]["debut"] <= 9 and "heures" in info[v[0]]["strategie"]
    assert (trades[v[0]]["entry_time"].dt.hour.between(info[v[0]]["horaire"]["debut"], info[v[0]]["horaire"]["fin"] - 1)).all()
    assert json.loads((tmp_path / "heures_strategies.json").read_text())[0]["ok"]


def test_platform_bot_with_hours_and_perso_profile(tmp_path, monkeypatch):
    import mt5lab.plateforme as pf
    from mt5lab.ftmo import FtmoRules
    cand = {"signal": {"type": "single", "name": "ema_cross", "params": {}}, "filter": "none",
            "risk": {"sl_mode": "atr", "sl_value": 1.5, "rr": 2.0, "management": "none", "max_hold": 200, "direction": "both"}}
    slots = {"s0": SimpleNamespace(symbol="EURUSD", timeframe="H1", candidate=cand, risk_pct=None, capital=100_000.0)}
    eng = SimpleNamespace(out=tmp_path / "paper", slots=slots, ftmo=FtmoRules(), risk_pct=1.0, profile=None)
    got = []
    monkeypatch.setattr(pf, "_build_bot", lambda e, comb, root, cap, ftmo, risk: got.append((comb, cap, ftmo, risk)) or
                        {"ok": True, "message": "ok"})
    pf.make_bot(eng, "/api/bot?id=s0@8-11")
    assert got[-1][0]["composants"][0]["horaire"]["debut"] == 8.0
    pf.make_bot(eng, "/api/bot?id=s0@8-11&profil=perso")
    comb, cap, ftmo, risk = got[-1]
    assert cap == 5000.0 and comb["profil"] == "perso" and comb["composants"][0]["risk_pct"] == 2.0
    assert comb["regles"]["day_budget"] == 5.0 and comb["composants"][0]["horaire"]["fin"] == 11.0


def test_top_backtest_hours_and_one_year_projections():
    import zlib

    from mt5lab.data import synthetic
    from mt5lab.top_backtest import top_backtest
    strategies = {}
    for i, sym in enumerate(("EURUSD", "XAUUSD")):
        for j, (f, s) in enumerate(((5, 20), (9, 21), (12, 50))):
            strategies[f"s{i}{j}"] = {"symbole": sym, "timeframe": "H1", "candidate": {
                "signal": {"type": "single", "name": "ema_cross", "params": {"fast": f, "slow": s}}, "filter": "none",
                "risk": {"sl_mode": "atr", "sl_value": 1.5, "rr": 2.0, "management": "none", "max_hold": 200,
                         "direction": "both"}}}
    res = top_backtest(strategies, lambda s, tf, c: (synthetic(15000, seed=zlib.crc32(s.encode()) % 1000), 0.00012),
                       n_sim=200, n_top=5, log=lambda m: None)
    assert res["heures"] and all(len(h["profil"]) == 24 for h in res["heures"])
    assert res["strategies_testees"] == 6 and "variantes_horaires" in res
    for e in res["seules"] + res["combinees"]:
        f, p = e["comptes"]["finance"], e["comptes"]["perso"]
        assert f["capital"] == 100_000 and p["capital"] == 5000
        assert p["gain_jour_usd"] == pytest.approx(p["gain_an_usd"] / 252, abs=1)
    assert res["perso"] and res["perso"][0]["rang"] == 1
    for e in res["seules"]:
        c = e["composants"][0]
        if "@" in c["strategie_id"]:
            assert c["horaire"]["debut"] is not None and c["base_id"] in strategies
    json.dumps(res, default=str)


def test_live_hour_variants_for_top10_of_paper():
    from mt5lab.plateforme import _live_hour_variants
    t = _trades()
    live = pd.DataFrame({"strategie_id": "s1", "symbole": "EURUSD", "timeframe": "H1", "strategie": "a", "risque": "x",
                         "ouverture": t["entry_time"].astype(str), "fermeture": t["exit_time"].astype(str), "r": t["r"]})
    t2, st = _live_hour_variants(live, {"s1": {"symbole": "EURUSD", "timeframe": "H1", "candidate": {}}})
    v = [k for k in st if "@" in k]
    assert len(v) == 1 and st[v[0]]["base_id"] == "s1" and (t2["strategie_id"] == v[0]).sum() < len(live)
    assert set(t2["strategie_id"]) == {"s1", v[0]}


def test_backtest_button_respects_component_hours():
    import zlib

    from mt5lab.backtest_combinee import backtest_combination
    from mt5lab.data import synthetic
    cand = {"signal": {"type": "single", "name": "ema_cross", "params": {"fast": 5, "slow": 20}}, "filter": "none",
            "risk": {"sl_mode": "atr", "sl_value": 1.5, "rr": 2.0, "management": "none", "max_hold": 200, "direction": "both"}}
    data = lambda s, tf: (synthetic(8000, seed=zlib.crc32(s.encode()) % 1000), 0.00012)
    full = backtest_combination({"composants": [{"symbole": "EURUSD", "timeframe": "H1", "candidate": cand, "risk_pct": 1}],
                                 "regles": {"day_budget": 2.5}}, data, n_sim=100, log=lambda m: None)
    part = backtest_combination({"composants": [{"symbole": "EURUSD", "timeframe": "H1", "candidate": cand, "risk_pct": 1,
                                                 "horaire": horaire(8, 12)}], "regles": {"day_budget": 2.5}},
                                data, n_sim=100, log=lambda m: None)
    assert 0 < part["tout"]["trades"] < full["tout"]["trades"] * 0.4
