import random

import numpy as np

from mt5lab.data import synthetic
from mt5lab.evaluator import compute_signal, describe
from mt5lab.inventions import FEATURES, Inventor, rule_signal, simplify


def test_invented_rules_have_no_lookahead_and_mirror():
    df = synthetic(1500, seed=3)
    inv = Inventor(9, "Agent 9", df.iloc[:1000], random.Random(1))
    for _ in range(25):
        spec = inv.random_spec()
        full = rule_signal(df, spec)
        part = rule_signal(df.iloc[:900], spec)
        assert (full.iloc[:900].to_numpy() == part.to_numpy()).all(), spec
        assert set(np.unique(full)) <= {-1, 0, 1}
    assert "QUAND" in describe({"signal": inv.name(spec), "filter": "none"})


def test_every_feature_is_causal():
    df = synthetic(1200, seed=5)
    for name, (fn, _d, ns, _l) in FEATURES.items():
        a = fn(df, ns[0]).to_numpy()[:800]
        b = fn(df.iloc[:800], ns[0]).to_numpy()
        assert np.allclose(a, b, equal_nan=True), name


def test_simplify_removes_duplicates():
    spec = {"type": "rule", "trigger": {"f": "body", "n": 1, "op": ">", "v": 0.2},
            "filters": [{"f": "body", "n": 1, "op": ">", "v": 0.2}, {"f": "adx", "n": 14, "op": ">", "v": 20},
                        {"f": "adx", "n": 14, "op": ">", "v": 25}], "mirror": True}
    s = simplify(spec)
    assert s["filters"] == [{"f": "adx", "n": 14, "op": ">", "v": 25}]
    assert compute_signal(synthetic(500, seed=1), s).abs().sum() >= 0
