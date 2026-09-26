"""L'équipe C doit retrouver une faille plantée volontairement, et rien sur du bruit."""
import numpy as np
import pandas as pd

from mt5lab.banques import BankAgent, TEAM_C, scan, to_rule
from mt5lab.evaluator import compute_signal


def _market(seed, planted):
    rng = np.random.default_rng(seed)
    n = 9000
    idx = pd.date_range("2021-01-04", periods=n, freq="h")
    r = rng.normal(0, 1e-3, n)
    if planted:  # entre 8h et 10h, le mouvement des 3 dernières heures continue
        for i in range(3, n):
            if 8 <= idx[i].hour < 10:
                r[i] += 0.8e-3 * np.sign(r[i - 3:i].sum())
    c = 1.1 * np.exp(np.cumsum(r))
    o = np.r_[1.1, c[:-1]]
    w = np.abs(rng.normal(0, 4e-4, n)) * c
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + w, "low": np.minimum(o, c) - w, "close": c,
                         "volume": 100.0}, index=idx)


def test_clock_analyst_finds_planted_session_effect():
    df = _market(1, planted=True)
    agent = BankAgent(*TEAM_C[1])  # Horloge institutionnelle
    found = scan(agent, df.iloc[:6000], df.iloc[6000:], log=lambda *a: None)
    assert any(f["cond"]["f"] == "hour" and f["cond"]["v"][0] <= 9 < f["cond"]["v"][1] and f["behaviour"] == 1
               for f in found), found
    sig = compute_signal(df, to_rule(found[0]["cond"], found[0]["behaviour"]))
    assert set(np.unique(sig)) <= {-1, 0, 1} and (sig != 0).sum() > 0


def test_no_faille_confirmed_on_noise():
    df = _market(2, planted=False)
    total = 0
    for t in TEAM_C:
        total += len(scan(BankAgent(*t), df.iloc[:6000], df.iloc[6000:], log=lambda *a: None))
    assert total <= 1  # le bruit peut exceptionnellement passer une fois ; la validation finale le rejette ensuite
