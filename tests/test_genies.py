"""Les génies : formules mathématiques inventées par programmation génétique."""
import random

import numpy as np

from mt5lab.data import add_ext, synthetic
from mt5lab.evaluator import compute_signal, describe
from mt5lab.genies import PRIMS, Genius, eval_expr, expr_text, formula_signal, nodes


def test_every_primitive_is_causal():
    df = add_ext(synthetic(1500, seed=4), {"NASDAQ": synthetic(1500, seed=9)["close"]})
    leaves = [{"f": p} for p in PRIMS] + [{"f": "ext_rel", "s": "NASDAQ"}]
    for leaf in leaves:
        a = eval_expr(df, leaf).to_numpy()[:1000]
        b = eval_expr(df.iloc[:1000], leaf).to_numpy()
        assert np.allclose(a, b, equal_nan=True), leaf


def test_random_formulas_are_causal_and_readable():
    df = synthetic(1500, seed=4)
    g = Genius("EINSTEIN", "Génie 1 Einstein", "Physicien : x", "E", random.Random(1), [], [])
    for _ in range(25):
        spec = g.mutate(g.random_spec())
        assert nodes(spec["expr"]) <= 15
        a = formula_signal(df, spec).to_numpy()[:1000]
        b = formula_signal(df.iloc[:1000], spec).to_numpy()
        assert (a == b).all(), expr_text(spec["expr"])
        assert set(np.unique(a)) <= {-1, 0, 1}
        txt = describe({"signal": {**spec, "name": "LOI D'EINSTEIN n°1"}, "filter": "none"})
        assert "LOI D'EINSTEIN" in txt and "z[" in txt


def test_genius_uses_agent_strategies_and_other_markets():
    df = add_ext(synthetic(1500, seed=4), {"GER40": synthetic(1500, seed=5)["close"]})
    strat = {"sig": {"type": "single", "name": "ema_cross", "params": {"fast": 9, "slow": 21}}, "flt": "none",
             "label": "Agent 1"}
    g = Genius("HAWKING", "Génie 2 Hawking", "Cosmologiste : x", "H", random.Random(3), ["GER40"], [strat])
    leaves = [g.leaf() for _ in range(400)]
    assert any(l.get("f") == "strat" for l in leaves) and any(l.get("f") == "ext_rel" for l in leaves)
    spec = {"type": "formula", "expr": {"op": "mul", "a": {"f": "strat", **strat}, "b": {"f": "hurst"}}, "k": 1.0}
    assert (compute_signal(df, spec) != 0).sum() > 0
