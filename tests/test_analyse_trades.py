"""Analyse des trades du paper : stops et objectifs (excursions), quand ça marche, nouvelles, marchés macro."""
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd

from mt5lab.analyse_trades import contexte, excursions, variants


def _trades(n=60, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(n):
        mfe = float(rng.choice([0.3, 1.2, 2.5]))
        win = mfe >= 2.0
        r = 2.0 if win else -1.0
        after = (float(rng.choice([-0.2, 0.5])) if mfe >= 1 else None)
        h = 9 if i % 3 == 0 else 14
        rows.append({"strategie_id": "s1" if i % 2 else "s2", "symbole": "EURUSD", "timeframe": "H1",
                     "strategie": "st", "risque": "x", "ouverture": f"2026-09-{1 + i % 25:02d} {h:02d}:00:00",
                     "fermeture": f"2026-09-{1 + i % 25:02d} {h + 1:02d}:00:00",
                     "r": -1.0 if h == 9 else r, "mae_r": 1.0 if r < 0 else float(rng.choice([0.2, 0.6])),
                     "mfe_r": mfe, "min_apres_1r_r": "" if after is None else after,
                     "regime": "tendance calme" if i % 2 else "range nerveux",
                     "nouvelle_avant_min": 20 if i % 4 == 0 else 300, "nouvelle_apres_min": 1440})
    return pd.DataFrame(rows)


def test_variants_exact():
    r = np.array([2.0, -1.0, -1.0, 2.0])
    mae = np.array([0.3, 1.0, 1.0, 0.8])
    mfe = np.array([2.0, 1.5, 0.4, 2.1])
    a1 = np.array([0.4, -0.1, np.nan, -0.3])
    v = variants(r, mae, mfe, a1, 2.0)
    assert v["actuel"] == 2.0
    assert v["be_1r"] == 2.0 + 0.0 - 1.0 + 0.0          # le perdant passé à +1R et le gagnant revenu à l'entrée -> 0
    assert v["tp"]["1"] == 1.0 + 1.0 - 1.0 + 1.0 and "2" not in v["tp"]   # objectif plus loin : impossible à déduire
    assert v["sl"]["0.5"] == 2.0 / 0.5 - 1 - 1 - 1        # stop deux fois plus serré : seul le 1er gagnant survit


def test_excursions_and_context_tables():
    t = _trades()
    E = excursions(t, {"s1": 2.0, "s2": 2.0})
    assert E["global"]["trades"] == len(t) and len(E["strategies"]) == 2
    for x in E["strategies"]:
        assert x["conseil"] and "1" in x["tp"] and "0.5" in x["sl"]
    C = contexte(t)
    assert {"heure", "jour", "regime", "avant", "apres"} <= set(C["tables"])
    assert any("09 h" in x["quoi"] for x in C["a_eviter"])     # 9 h : toujours perdant
    json.dumps({"e": E, "c": C}, default=str)


def test_news_columns_and_distance():
    from mt5lab.data import add_news_cols, news_distance, uses_news
    news = pd.DataFrame({"time": pd.to_datetime(["2026-09-01 14:30", "2026-09-01 20:00", "2026-09-02 14:30"]),
                         "currency": ["USD", "EUR", "USD"], "importance": 3, "event": "x"})
    idx = pd.date_range("2026-09-01 14:00", periods=4, freq="h")
    df = add_news_cols(pd.DataFrame({"close": 1.0}, index=idx), news, {"USD"})
    assert list(df["news_next_min"])[:2] == [30.0, 1410.0] and list(df["news_prev_min"])[1:3] == [30.0, 90.0]
    assert df["news_prev_min"].iloc[0] == 1440.0                       # aucune annonce avant
    assert news_distance(news, {"USD"}, "2026-09-01 15:00") == (30.0, 1410.0)
    assert news_distance(None, {"USD"}, "2026-09-01 15:00") == (None, None)
    from mt5lab.banques import TEAM_E
    from mt5lab.inventions import FEATURES
    assert any(a[0] == 24 for a in TEAM_E) and "news_after" in FEATURES
    assert FEATURES["news_after"][0](df, 1).iloc[2] == 90.0
    assert uses_news({"trigger": {"f": "news_after", "n": 1}}) and not uses_news({"f": "rsi"})


def test_macro_markets_found_when_broker_has_them():
    from mt5lab.data import macro_symbols
    known = {"DXY": "USDX", "US500": "US500.cash", "EURUSD": "EURUSD"}

    def resolve(name):
        if name not in known:
            raise RuntimeError("introuvable")
        return known[name]
    conn = SimpleNamespace(resolve=resolve)
    assert macro_symbols(conn, ["EURUSD"], log=lambda m: None) == ["DXY", "US500"]
    assert macro_symbols(conn, ["EURUSD", "US500"], log=lambda m: None) == ["DXY"]   # déjà tradé


def test_platform_analyse_endpoint(tmp_path):
    from mt5lab.plateforme import analyse_trades_live
    t = _trades()
    t.to_csv(tmp_path / "trades.csv", index=False)
    pd.DataFrame([{"strategie_id": "s1", "symbole": "EURUSD", "timeframe": "H1", "strategie": "st", "risque": "x",
                   "groupe": "g", "raison_refus": "positions max atteintes", "r": -1.0}]).to_csv(tmp_path / "fantomes.csv", index=False)
    cfg = SimpleNamespace(rr=2.0)
    eng = SimpleNamespace(out=tmp_path, slots={"s1": SimpleNamespace(cfg=cfg), "s2": SimpleNamespace(cfg=cfg)},
                          recent=[], total_trades=len(t))
    res = analyse_trades_live(eng, "/api/analyse_trades")
    assert res["excursions"]["strategies"] and res["contexte"]["tables"] and res["fantomes"]["trades"] == 1
    assert "protègent" in res["fantomes"]["message"] and len(res["liste"]) == 2
    one = analyse_trades_live(eng, "/api/analyse_trades?id=s1")
    assert one["excursions"]["trades"] == 30
