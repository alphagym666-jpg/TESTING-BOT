"""Inventions inter-marchés : un marché qui en annonce un autre."""
import random

import numpy as np
import pandas as pd

from mt5lab.data import add_ext, synthetic
from mt5lab.inventions import EXT_FEATURES, Inventor, describe_rule, ext_symbols, rule_signal
from mt5lab.paper import ext_needed


def _pair():
    a = synthetic(3000, seed=1)
    b = synthetic(3000, seed=2)
    return a, b


def test_add_ext_never_uses_future():
    a, b = _pair()
    # l'autre marché a des bougies décalées de 30 min : on ne doit prendre que la dernière clôture DÉJÀ connue
    b2 = b.copy()
    b2.index = b2.index + pd.Timedelta(minutes=30)
    df = add_ext(a, {"NASDAQ": b2["close"]})
    t = df.index[100]
    known = b2["close"][b2.index <= t].iloc[-1]
    assert np.isclose(df["ext:NASDAQ"].iloc[100], known, rtol=1e-6)
    assert ext_symbols(df) == ["NASDAQ"]


def test_ext_rule_signal_and_text():
    a, b = _pair()
    df = add_ext(a, {"NASDAQ": b["close"]})
    spec = {"type": "rule", "name": "INVENTION A9-1", "trigger": {"f": "ext_mom", "n": 3, "s": "NASDAQ", "op": ">", "v": 1.0},
            "filters": [], "mirror": True}
    sig = rule_signal(df, spec)
    assert (sig != 0).sum() > 20
    assert "NASDAQ" in describe_rule(spec)
    assert ext_needed({"signal": spec}) == {"NASDAQ"}
    # sans la colonne de l'autre marché : aucune entrée (jamais d'erreur)
    assert (rule_signal(a, spec) == 0).all()


def test_inventor_uses_other_markets_only_when_available():
    a, b = _pair()
    inv = Inventor(9, "A9", add_ext(a, {"NASDAQ": b["close"]}), random.Random(0))
    conds = [inv.random_cond() for _ in range(200)]
    ext = [c for c in conds if c["f"] in EXT_FEATURES]
    assert ext and all(c["s"] == "NASDAQ" for c in ext)
    inv2 = Inventor(9, "A9", a, random.Random(0))
    assert not any(inv2.random_cond()["f"] in EXT_FEATURES for _ in range(200))


def test_pilot_reduces_risk_in_drawdown_and_lowers_failures():
    from mt5lab.ftmo import FtmoRules, count_challenges, pilot_factor, simulate
    assert pilot_factor({"type": "dd", "seuil": 3, "facteur": 0.5}, -3.5, 0) == 0.5
    assert pilot_factor({"type": "dd", "seuil": 3, "facteur": 0.5}, -1.0, 0) == 1.0
    assert pilot_factor({"type": "jour", "facteur": 0.5}, 0.0, -0.2) == 0.5
    assert pilot_factor({"type": "cible", "seuil": 2, "facteur": 0.5, "cible": 10}, 8.5, 0) == 0.5
    rng = np.random.default_rng(3)
    pnl = rng.normal(0.25, 1.4, 400)
    days = pd.bdate_range("2020-01-01", periods=400)
    daily = pd.DataFrame({"pnl": pnl, "worst": np.minimum(pnl, 0) - 0.3, "traded": True}, index=days)
    base = simulate(daily, FtmoRules(), 2000, seed=0)
    piloted = simulate(daily, FtmoRules(), 2000, seed=0, pilot={"type": "dd", "seuil": 3, "facteur": 0.5})
    assert piloted["ftmo_echec_p1"] < base["ftmo_echec_p1"]
    c = count_challenges(daily, FtmoRules(), {"type": "dd", "seuil": 3, "facteur": 0.5})
    assert c["reussis"] + c["rates"] >= 1
