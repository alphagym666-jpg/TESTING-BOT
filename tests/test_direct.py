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


def _cand(fast, slow):
    return {"signal": {"type": "single", "name": "ema_cross", "params": {"fast": fast, "slow": slow}}, "filter": "none",
            "risk": {"sl_mode": "atr", "sl_value": 1.5, "rr": 2.0, "management": "none", "max_hold": 200,
                     "direction": "both"}}


def _synthetic_data(sym, tf):
    import zlib

    from mt5lab.data import synthetic
    return synthetic(12000, seed=zlib.crc32(sym.encode()) % 1000), 0.00012


def test_backtest_of_a_combination():
    from mt5lab.backtest_combinee import backtest_combination
    comb = {"composants": [{"symbole": "XAUUSD", "timeframe": "H1", "candidate": _cand(9, 21), "risk_pct": 1.0},
                           {"symbole": "EURUSD", "timeframe": "H1", "candidate": _cand(12, 50), "risk_pct": 0.5},
                           {"symbole": "BIDON", "timeframe": "H1", "candidate": _cand(5, 20), "risk_pct": 1.0}],
            "regles": {"day_budget": 2.5}}

    def data(sym, tf):
        if sym == "BIDON":
            raise RuntimeError("pas de données")
        return _synthetic_data(sym, tf)
    steps = []
    res = backtest_combination(comb, data, n_sim=300, progress=lambda a, b, m: steps.append(a), log=lambda m: None)
    assert res["ok"] and steps[-1] == 4
    a, r = res["tout"], res["recent"]
    assert a["trades"] > r["trades"] > 0 and a["pire_jour"] > -2.5 * 1.15     # perte possible max par jour respectée
    assert res["composants"][2]["erreur"] and res["composants"][0]["trades"] > 0
    assert len(res["courbe"]) <= 502 and abs(res["courbe"][-1][1] - a["rendement_pct"]) < 0.05
    assert abs(sum(m["pct"] for m in res["mois"]) - a["rendement_pct"]) < 0.05
    assert res["debut_recent"] >= a["periode"][:10]
    json.dumps(res)


def test_platform_backtest_button(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace

    import mt5lab.plateforme as pf
    from mt5lab.ftmo import FtmoRules
    slots = {"s0": SimpleNamespace(symbol="XAUUSD", timeframe="H1", candidate=_cand(9, 21)),
             "s1": SimpleNamespace(symbol="EURUSD", timeframe="H1", candidate=_cand(12, 50))}
    eng = SimpleNamespace(out=tmp_path, slots=slots, ftmo=FtmoRules(), risk_pct=1.0, profile=None)
    eng._top10_job = {"etat": "fini", "resultat": {"top": [{"rang": 1, "nom": "N°1", "regles": {"day_budget": 2.5},
                      "composants": [{"strategie_id": "s0", "symbole": "XAUUSD", "timeframe": "H1", "risk_pct": 1.0},
                                     {"strategie_id": "s1", "symbole": "EURUSD", "timeframe": "H1", "risk_pct": 0.5}]}]}}
    monkeypatch.setattr(pf, "_platform_data", lambda e: lambda s, tf, cand=None: _synthetic_data(s, tf))
    assert pf.backtest_live(eng, "/api/backtest?top=1")["etat"] == "jamais"
    assert pf.backtest_live(eng, "/api/backtest?top=4&lancer=1")["etat"] == "erreur"
    job = pf.backtest_live(eng, "/api/backtest?top=1&lancer=1")
    for _ in range(300):
        if job["etat"] != "en cours":
            break
        time.sleep(0.2)
        job = pf.backtest_live(eng, "/api/backtest?top=1")
    assert job["etat"] == "fini", job["message"]
    assert job["resultat"]["tout"]["trades"] > 0 and list((tmp_path / "backtests").glob("*.json"))
    del eng._bt_jobs                                           # après un redémarrage : le backtest enregistré
    assert pf.backtest_live(eng, "/api/backtest?top=1")["etat"] == "fini"


def _bt_strategies():
    out = {}
    i = 0
    for sym in ("XAUUSD", "EURUSD", "US30"):
        for fast, slow in ((5, 20), (9, 21), (12, 50)):
            for rr in (1.5, 2.0):
                c = _cand(fast, slow)
                c["risk"]["rr"] = rr
                out[f"s{i}"] = {"symbole": sym, "timeframe": "H1", "candidate": c, "trades_direct": i % 4}
                i += 1
    return out


def test_top_backtest_two_years():
    from mt5lab.top_backtest import top_backtest
    strategies = _bt_strategies()
    live = pd.DataFrame([{"strategie_id": "s1", "symbole": "XAUUSD", "timeframe": "H1", "strategie": "x", "risque": "x",
                          "ouverture": f"2026-09-0{d} 10:00:00", "fermeture": f"2026-09-0{d} 12:00:00", "r": r}
                         for d, r in ((1, 2.0), (2, -1.0), (3, 2.0))])
    extras = [{"nom": "Stratégie combinée en paper : test", "keys": ["s0", "s7"], "weights": {"s0": 0.5, "s7": 0.5}}]
    phases = []
    loaded = []

    def data(sym, tf, cands):
        loaded.append((sym, tf, len(cands)))
        return _synthetic_data(sym, tf)
    res = top_backtest(strategies, data, extras=extras, live=live, n_sim=300, n_top=5,
                       progress=lambda ph, d, n: phases.append(ph), log=lambda m: None)
    assert res["ok"] and res["strategies_testees"] == len(strategies)
    assert len(loaded) == 3 and sum(n for _, _, n in loaded) == len(strategies)      # une fois par marché
    assert any("Chef des combinaisons" in p for p in phases)
    S, C = res["seules"], res["combinees"]
    assert 1 <= len(S) <= 5 and [e["rang"] for e in S] == list(range(1, len(S) + 1))
    for e in S + C:
        b = e["backtest"]
        assert b["ok"] and b["courbe"] and b["tout"]["trades"] > 0 and b["tout"]["annees"] <= 2.1
    names = [e.get("origine") for e in C]
    assert "Stratégie combinée en paper : test" in names                         # toujours montrée, même hors TOP
    for e in C:
        assert all("candidate" not in c and "trades_bt" in c for c in e["composants"])
    s1 = [e for e in S if e["composants"][0]["strategie_id"] == "s1"]
    if s1:
        assert s1[0]["direct"]["trades"] == 3 and s1[0]["direct"]["r_total"] == 3.0
    json.dumps(res, default=str)


def test_platform_top2ans_and_bots(tmp_path, monkeypatch):
    import time
    from types import SimpleNamespace

    import mt5lab.plateforme as pf
    from mt5lab.ftmo import FtmoRules
    st = _bt_strategies()
    slots = {k: SimpleNamespace(symbol=v["symbole"], timeframe="H1", candidate=v["candidate"], paused=False,
                                group="G" if k in ("s0", "s7") else "", risk_pct=0.5, capital=100_000.0)
             for k, v in st.items()}
    for k, sym in (("s0", "XAUUSD"), ("s7", "EURUSD"), ("s13", "US30")):
        _write_trades(tmp_path, k, sym, [2.0, -1.0, 1.0])
    eng = SimpleNamespace(out=tmp_path, slots=slots, recent=[], ftmo=FtmoRules(), risk_pct=1.0, profile=None,
                          groups={"G": SimpleNamespace(name="G")})
    monkeypatch.setattr(pf, "_platform_data", lambda e, years=None: lambda s, tf, cand=None: _synthetic_data(s, tf))
    assert pf.top2ans_live(eng)["etat"] == "jamais"
    job = pf.top2ans_live(eng, start=True)
    for _ in range(600):
        if job["etat"] != "en cours":
            break
        time.sleep(0.5)
    assert job["etat"] == "fini", job["message"]
    res = job["resultat"]
    assert res["strategies_testees"] == 3 and (tmp_path / "top_backtest_2ans.json").exists()
    assert any(e["origine"] == "Stratégie combinée en paper : G" for e in res["combinees"])
    got = []
    monkeypatch.setattr(pf, "_build_bot", lambda e, comb, root, cap, ftmo, risk: got.append(comb) or {"ok": True, "message": "ok"})
    rang = res["combinees"][0]["rang"]
    assert pf.make_bot(eng, f"/api/bot?bt2={rang}")["ok"]
    assert all(c["candidate"] == slots[c["strategie_id"]].candidate for c in got[0]["composants"])
    assert not pf.make_bot(eng, "/api/bot?bt2=99")["ok"]
    assert pf.make_bot(eng, "/api/bot?id=s13")["ok"]                    # une stratégie seule de la combinaison
    del eng._top2_job
    assert pf.top2ans_live(eng)["etat"] == "fini"                       # après un redémarrage


def test_cross_ranking_backtest_vs_paper():
    from mt5lab.top_backtest import top_backtest
    strategies = _bt_strategies()
    rng = np.random.default_rng(5)
    rows = []
    for k in strategies:
        p = 0.55 if int(k[1:]) % 3 == 0 else 0.25        # un tiers gagne en paper, le reste perd
        for d in pd.bdate_range("2026-08-03", periods=12):
            rows.append({"strategie_id": k, "symbole": strategies[k]["symbole"], "timeframe": "H1", "strategie": k,
                         "risque": "x", "ouverture": f"{d:%Y-%m-%d} 10:00:00", "fermeture": f"{d:%Y-%m-%d} 12:00:00",
                         "r": float(rng.choice([2.0, -1.0], p=[p, 1 - p]))})
    res = top_backtest(strategies, lambda s, tf, c: _synthetic_data(s, tf), live=pd.DataFrame(rows), n_sim=200,
                       n_top=5, log=lambda m: None)
    X = res["croise"]
    assert X["comparees"] > 0 and set(X["listes"]) == {"partout", "paper", "backtest"}
    for x in X["listes"]["partout"]:
        assert x["bt"]["r_moyen"] > 0 and x["paper"]["r_moyen"] > 0 and x["backtest"]["ok"]
    for x in X["listes"]["paper"]:
        assert x["rang_paper"] - x["rang_bt"] >= 25
    for x in X["listes"]["backtest"]:
        assert x["rang_bt"] - x["rang_paper"] >= 25
    p = X["listes"]["partout"]
    assert [x["rang"] for x in p] == list(range(1, len(p) + 1))
    assert all(min(a["rang_bt"], a["rang_paper"]) >= min(b["rang_bt"], b["rang_paper"]) for a, b in zip(p, p[1:]))
    json.dumps(res, default=str)
