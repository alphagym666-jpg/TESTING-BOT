"""Analyse du direct : meilleurs setups et meilleure combinaison à partir des trades du paper trading."""
import json

import numpy as np
import pandas as pd

from mt5lab.direct import analyse, run_direct


def _write_trades(folder, sid, sym, r_list, start="2026-08-03"):
    folder.mkdir(parents=True, exist_ok=True)
    days = pd.bdate_range(start, periods=len(r_list))
    rows = [{"strategie_id": sid, "symbole": sym, "timeframe": "H1", "strategie": f"strat {sid}", "risque": "SL atr=1.5",
             "ouverture": f"{d:%Y-%m-%d} 10:00:00", "fermeture": f"{d:%Y-%m-%d} 12:00:00", "r": r}
            for d, r in zip(days, r_list)]
    path = folder / "trades.csv"
    old = pd.read_csv(path) if path.exists() else pd.DataFrame()
    pd.concat([old, pd.DataFrame(rows)]).to_csv(path, index=False)
    sj = folder / "strategies.json"
    st = json.loads(sj.read_text()) if sj.exists() else {}
    st[sid] = {"symbole": sym, "timeframe": "H1", "candidate": {"signal": {"type": "single", "name": "ema_cross",
               "params": {"fast": 9, "slow": 21}}, "filter": "none", "risk": {"sl_mode": "atr", "sl_value": 1.5,
               "rr": 2.0, "management": "none", "max_hold": 200, "direction": "both"}}}
    sj.write_text(json.dumps(st))


def test_live_ranking_and_best_combination(tmp_path):
    rng = np.random.default_rng(0)
    paper = tmp_path / "paper"
    _write_trades(paper, "bon", "XAUUSD", list(rng.choice([2.0, -1.0], 40, p=[0.55, 0.45])))
    _write_trades(paper, "bon2", "EURUSD", list(rng.choice([1.5, -1.0], 40, p=[0.6, 0.4])))
    _write_trades(paper, "mauvais", "GER40", list(rng.choice([1.0, -1.0], 40, p=[0.3, 0.7])))
    _write_trades(tmp_path / "paper_meilleures", "bon", "XAUUSD", [2.0] * 3)  # doublons d'une autre fenêtre ignorés
    res = analyse(tmp_path)
    ids = [r["strategie_id"] for r in res["classement"]]
    assert ids.index("bon") < ids.index("mauvais") and res["jours"] >= 40 and res["fiable"]
    comb = res["combinaison"]
    assert comb and "mauvais" not in [c["strategie_id"] for c in comb["composants"]]
    assert all(c["candidate"] for c in comb["composants"]) and comb["resultat"]["pire_jour"] >= -2.7
    out = run_direct(tmp_path, log=lambda m: None)
    assert (tmp_path / "direct.html").exists() and (tmp_path / "strategie_combinee_direct.json").exists()
    assert out["trades"] == res["trades"]


def test_live_short_data_warns(tmp_path):
    _write_trades(tmp_path / "paper", "a", "XAUUSD", [1.0, -1.0, 2.0, 1.0, 1.5])
    res = analyse(tmp_path)
    assert not res["fiable"] and "trop court" in res["message"]
