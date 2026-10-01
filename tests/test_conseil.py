"""Le Conseil : les meilleures stratégies d'une case votent ensemble."""
import numpy as np
import pandas as pd

from mt5lab.conseil import describe_vote, members_of, proposals, vote_signal
from mt5lab.evaluator import compute_signal, describe
from mt5lab.strategies import REGISTRY, StrategyDef


def _setup():
    idx = pd.date_range("2024-01-01", periods=12, freq="h")
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}, index=idx)
    a = [0, 1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 0]
    b = [0, 0, 1, 0, 0, 0, 0, 0, 0, -1, 0, 0]
    REGISTRY["_va"] = StrategyDef("_va", "test", lambda d: pd.Series(np.array(a, dtype=np.int8), index=d.index))
    REGISTRY["_vb"] = StrategyDef("_vb", "test", lambda d: pd.Series(np.array(b, dtype=np.int8), index=d.index))
    mem = [{"signal": {"type": "single", "name": n, "params": {}}, "filter": "none"} for n in ("_va", "_vb")]
    return df, mem


def test_vote_needs_agreement_within_window():
    df, mem = _setup()
    try:
        s1 = vote_signal(df, {"type": "vote", "members": mem, "min": 2, "window": 1}).tolist()
        assert s1 == [0] * 12                                   # jamais d'accord sur la même bougie
        s3 = compute_signal(df, {"type": "vote", "members": mem, "min": 2, "window": 3}).tolist()
        assert s3[2] == 1 and s3.count(1) == 1                  # B confirme A une bougie plus tard -> ACHAT
        assert -1 not in s3                                     # les ventes sont trop éloignées (6 bougies)
        s5 = vote_signal(df, {"type": "vote", "members": mem, "min": 2, "window": 5}).tolist()
        assert s5.count(-1) == 1
        txt = describe({"signal": {"type": "vote", "name": "CONSEIL 2/2", "members": mem, "min": 2, "window": 3},
                        "filter": "none"})
        assert txt.startswith("CONSEIL 2/2 (2 sur 2") and "_va" in txt and "_vb" in txt
    finally:
        REGISTRY.pop("_va", None), REGISTRY.pop("_vb", None)


def test_members_are_different_and_proposals_are_votes():
    risk = {"sl_mode": "atr", "sl_value": 1.0, "rr": 2.0, "management": "none", "max_hold": 50, "direction": "both"}
    c = lambda n, p=None: {"signal": {"type": "single", "name": n, "params": p or {}}, "filter": "none", "risk": risk}
    members = members_of([], [c("ema_cross", {"fast": 9}), c("ema_cross", {"fast": 20}), c("rsi_reversal"),
                              c("donchian_breakout")])
    assert [m["signal"]["name"] for m in members] == ["ema_cross", "rsi_reversal", "donchian_breakout"]
    props = proposals(members)
    assert props and all(p["signal"]["type"] == "vote" for p in props)
    assert {len(p["signal"]["members"]) for p in props} == {2, 3}
    assert {p["signal"]["min"] for p in props if len(p["signal"]["members"]) == 3} == {2, 3}


def test_vote_card_and_bot_text():
    from mt5lab.fiches import build_card, entry_text
    df, mem = _setup()
    try:
        sig = {"type": "vote", "name": "CONSEIL 2/2", "members": mem, "min": 2, "window": 3}
        lines = entry_text(sig)
        assert "Le Conseil" in lines[0] and any("Membre 2" in x for x in lines)
        card = build_card({"signal": sig, "filter": "none", "risk": {"sl_mode": "atr", "sl_value": 1.0, "rr": 2.0,
                                                                      "management": "none", "max_hold": 50,
                                                                      "direction": "both"}}, "EURUSD", "H1")
        assert "vote_signal" in card["code_python"] and describe_vote(sig) in card["nom"]
    finally:
        REGISTRY.pop("_va", None), REGISTRY.pop("_vb", None)
