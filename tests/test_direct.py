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


def test_top10_of_live_combinations(tmp_path):
    from mt5lab.direct import top_combinations
    rng = np.random.default_rng(3)
    paper = tmp_path / "paper"
    for i, (sym, p) in enumerate([("XAUUSD", 0.55), ("EURUSD", 0.6), ("US30", 0.5), ("GBPUSD", 0.55),
                                  ("GER40", 0.25)]):
        _write_trades(paper, f"s{i}", sym, list(rng.choice([2.0, -1.0], 45, p=[p, 1 - p])))
    trades, strategies = __import__("mt5lab.direct", fromlist=["load_live"]).load_live(tmp_path)
    seen = []
    res = top_combinations(trades, strategies, n_seeds=3, n_sim=300, n_quick=100,
                           progress=lambda d, n: seen.append((d, n)))
    top = res["top"]
    assert 2 <= len(top) <= 10 and [c["rang"] for c in top] == list(range(1, len(top) + 1))
    assert seen and seen[-1][0] == seen[-1][1]
    keys = [frozenset(x["strategie_id"] for x in c["composants"]) for c in top]
    assert len(set(keys)) == len(keys)                            # 10 combinaisons différentes
    assert all("s4" not in k for k in keys)                       # la perdante n'entre jamais
    for c in top:
        r = c["resultat"]
        assert all(x["candidate"] and x["risk_pct"] <= 1.0 for x in c["composants"])
        assert r["pire_jour"] >= -2.5 * 1.1 and r["dd_max"] >= 0 and r["trades_mois"] > 0
    json.dumps(res, default=str)                                   # enregistrable


def test_top10_empty_live():
    from mt5lab.direct import top_combinations
    res = top_combinations(pd.DataFrame())
    assert res["top"] == [] and "laissez tourner" in res["message"]


def test_platform_top10_button(tmp_path):
    import time
    from types import SimpleNamespace

    from mt5lab.ftmo import FtmoRules
    from mt5lab.plateforme import top10_live
    rng = np.random.default_rng(4)
    for i, sym in enumerate(["XAUUSD", "EURUSD", "US30"]):
        _write_trades(tmp_path, f"s{i}", sym, list(rng.choice([2.0, -1.0], 30, p=[0.55, 0.45])))
    st = json.loads((tmp_path / "strategies.json").read_text())
    slots = {k: SimpleNamespace(symbol=v["symbole"], timeframe="H1", candidate=v["candidate"], paused=False)
             for k, v in st.items()}
    eng = SimpleNamespace(out=tmp_path, slots=slots, recent=[], ftmo=FtmoRules(), risk_pct=1.0, profile=None)
    assert top10_live(eng)["etat"] == "jamais"
    job = top10_live(eng, start=True)
    for _ in range(600):
        if job["etat"] != "en cours":
            break
        time.sleep(0.5)
    assert job["etat"] == "fini", job["message"]
    assert job["resultat"]["top"] and (tmp_path / "top10_direct.json").exists()
    del eng._top10_job                                            # après un redémarrage : le dernier TOP 10 enregistré
    again = top10_live(eng)
    assert again["etat"] == "fini" and again["resultat"]["top"][0]["rang"] == 1


def test_platform_top10_bot(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import mt5lab.plateforme as pf
    from mt5lab.ftmo import FtmoRules
    _write_trades(tmp_path / "paper", "s0", "XAUUSD", [2.0, -1.0, 2.0] * 10)
    st = json.loads((tmp_path / "paper" / "strategies.json").read_text())
    slots = {"s0": SimpleNamespace(symbol="XAUUSD", timeframe="H1", candidate=st["s0"]["candidate"], capital=100_000.0)}
    eng = SimpleNamespace(out=tmp_path / "paper", slots=slots, ftmo=FtmoRules(), risk_pct=1.0, profile={"cle": "perso"})
    eng._top10_job = {"etat": "fini", "resultat": {"top": [{"rang": 1, "nom": "N°1", "regles": {}, "resultat": {},
                      "composants": [{"strategie_id": "s0", "symbole": "XAUUSD", "timeframe": "H1", "risk_pct": 0.5}]}]}}
    got = {}
    monkeypatch.setattr(pf, "_build_bot", lambda e, comb, root, cap, ftmo, risk: got.update(
        comb=comb, cap=cap, ftmo=ftmo, risk=risk) or {"ok": True, "message": "ok"})
    assert pf.make_bot(eng, "/api/bot?top=1")["ok"]
    assert got["comb"]["composants"][0]["candidate"] == st["s0"]["candidate"] and got["risk"] == 0.5
    assert got["ftmo"].target1 == 10.0 and got["cap"] == 100_000.0 and "profil" not in got["comb"]
    assert not pf.make_bot(eng, "/api/bot?top=5")["ok"]
