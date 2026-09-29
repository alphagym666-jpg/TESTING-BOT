"""Gestionnaires de compte : compte perso (5 000 $, long terme) et compte financé (3 %/jour, 10 % au total)."""
import numpy as np
import pandas as pd

from mt5lab.comptes import AccountManager, ftmo_like, profile, simulate_long
from mt5lab.manager import DirectorConfig


def _daily(mean, sd, n=300, seed=1):
    rng = np.random.default_rng(seed)
    pnl = rng.normal(mean, sd, n)
    return pd.DataFrame({"pnl": pnl, "worst": np.minimum(pnl, 0) - 0.2}, index=pd.bdate_range("2024-01-01", periods=n))


def test_simulate_long_profiles():
    good, wild = _daily(0.08, 0.5), _daily(0.08, 2.5)
    fin = profile("finance")
    a, b = simulate_long(good, fin), simulate_long(wild, fin)
    assert abs(a["rendement_an_median"] - good["pnl"].mean() * 252) < 3 and a["p_probleme"] < 5
    assert b["p_probleme"] > 30                     # trop de volatilité : 3 %/jour touché
    per = profile("perso")
    c = simulate_long(good, per)
    assert c["rendement_an_median"] > a["rendement_an_median"] - 1   # composé >= simple quand ça gagne
    assert simulate_long(_daily(-0.1, 1.0), per)["p_perte_an"] > 80
    assert np.isnan(simulate_long(good.head(5), per)["rendement_an_median"])


def test_profile_rules_for_paper_and_bot():
    r = ftmo_like(profile("finance"))
    assert r.target1 == 0 and r.max_daily == 3 and r.max_total == 10 and r.best_day_pct == 0
    p = ftmo_like(profile("perso", capital=3000))
    assert p.target1 == 0 and p.max_total == 30 and profile("perso", capital=3000)["capital"] == 3000
    assert profile("perso")["risk_pct"] == 2.0 and profile("perso")["day_budget"] == 5.0


def _trades(mean, n=400, seed=0, start="2024-01-01"):
    rng = np.random.default_rng(seed)
    e = pd.date_range(start, periods=n, freq="15h")
    return pd.DataFrame({"entry_time": e, "exit_time": e + pd.Timedelta(hours=3),
                         "r": np.where(rng.random(n) < 0.45 + mean, 2.0, -1.0), "side": 1})


def test_account_manager_builds_safe_combination(tmp_path):
    cfg = DirectorConfig(["EURUSD", "XAUUSD"], ["H1"], out=tmp_path, rr_variants=False)
    trades = {"EURUSD_H1|a": _trades(0.05), "XAUUSD_H1|b": _trades(0.03, seed=1), "EURUSD_H1|c": _trades(-0.2, seed=2)}
    windows = {k: (pd.Timestamp("2024-01-01"), pd.Timestamp("2024-09-30")) for k in trades}
    info = {k: {"symbole": k.split("_")[0], "timeframe": "H1", "candidate": {"signal": {"type": "single", "name": k},
                "filter": "none", "risk": {"rr": 2.0}}, "strategie": k, "risque": "1:2", "seule_ftmo": 50.0,
                "attendu_r": 0.3, "attendu_wr": 50.0} for k in trades}
    for name in ("finance", "perso"):
        m = AccountManager(cfg, lambda s, t: None, profile(name), log=lambda x: None)
        comb = m.build_combined(pd.DataFrame(), pool=(trades, windows, info), quiet=True)
        keys = {c["strategie"] for c in comb["composants"]}
        assert "EURUSD_H1|c" not in keys                          # la perdante n'est jamais prise
        assert comb["resultat"]["p_probleme"] <= 5
        assert all(c["risk_pct"] <= profile(name)["risk_pct"] for c in comb["composants"])
        assert (2.0 in m.levels()) == (name == "perso")          # compte perso : jusqu'à 2 % par trade
        assert comb["regles"]["day_budget"] == profile(name)["day_budget"]


def test_paper_personal_account_compounds_and_never_stops_at_target(tmp_path, monkeypatch):
    import json
    import sys
    from test_paper import FakeMarket
    from mt5lab.strategies import REGISTRY, StrategyDef
    REGISTRY["_test_long"] = StrategyDef("_test_long", "test", lambda df: pd.Series(1, index=df.index, dtype=np.int8))
    mk = FakeMarket()
    monkeypatch.setitem(sys.modules, "MetaTrader5", mk.module())
    monkeypatch.chdir(tmp_path)
    try:
        from mt5lab.data import MT5Connector
        from mt5lab.paper import PaperEngine, load_combined_slots
        cand = {"signal": {"type": "single", "name": "_test_long", "params": {}}, "filter": "none",
                "risk": {"sl_mode": "atr", "sl_value": 1.0, "rr": 2.0, "management": "none", "max_hold": 200,
                         "direction": "both"}}
        comb = {"nom": "Compte perso", "profil": "perso", "composer": True, "capital": 5000,
                "regles": {"day_budget": 5.0, "total_budget": 30.0},
                "composants": [{"symbole": "EURUSD", "timeframe": "H1", "candidate": cand, "risk_pct": 1.0}]}
        f = tmp_path / "strategie_combinee_perso.json"
        f.write_text(json.dumps(comb), encoding="utf-8")
        slots, groups = load_combined_slots(tmp_path, 5000, path=str(f))
        eng = PaperEngine(MT5Connector().connect(verbose=False), slots, tmp_path / "paper", 1.0,
                          ftmo=ftmo_like(profile("perso")), groups=groups)
        g = next(iter(eng.groups.values()))
        assert g.compound and g.capital == 5000
        s = slots[0]
        g.balance = 6000.0                                  # +20 % : le risque suit le solde (60 $, pas 50 $)
        assert eng.risk_budget(eng.slots[s.id]) == 60.0
        assert not eng._target_reached(g)                    # jamais d'objectif : pas de micro-trades
        assert not eng._ftmo_check(g, 6000.0, "2026-01-05 10:00", True) and g.ftmo_status == "en cours"
        eng.profile = profile("perso")
        assert eng.snapshot()["profil"]["cle"] == "perso"
        assert mk.sent == []
    finally:
        REGISTRY.pop("_test_long", None)


def test_bot_for_account_profiles(tmp_path):
    from mt5lab.pont import generate_strategy_bot
    comb = {"profil": "finance", "composer": False, "regles": {"day_budget": 2.5, "total_budget": 10},
            "composants": [{"symbole": "XAUUSD", "timeframe": "H1", "strategie": "x", "risque_config": "1:2",
                            "risk_pct": 1.0, "candidate": {}}]}
    out = generate_strategy_bot(comb, tmp_path, 100_000, ftmo_like(profile("finance")), 1.0)
    src = (out / "LaboBot.mq5").read_text(encoding="utf-8-sig")
    assert "InpObjectif        = 0;" in src and "InpPerteJourMax    = 2.8;" in src and "InpMeilleurJour    = 0;" in src
    assert "InpComposer        = false;" in src
    assert "--profil finance" in (out / "LANCER_BOT.bat").read_text() and "--ftmo-target" not in (out / "LANCER_BOT.bat").read_text()
    comb.update(profil="perso", composer=True)
    out = generate_strategy_bot(comb, tmp_path, 5000, ftmo_like(profile("perso")), 1.0)
    comb["regles"]["day_budget"] = 5.0
    out = generate_strategy_bot(comb, tmp_path, 5000, ftmo_like(profile("perso")), 2.0)
    src = (out / "LaboBot.mq5").read_text(encoding="utf-8-sig")
    assert "InpComposer        = true;" in src and "InpPerteJourMax    = 5;" in src   # tout fermé à -5 % du jour
