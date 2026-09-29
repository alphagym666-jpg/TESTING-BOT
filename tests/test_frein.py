"""Frein de bonne journée : après +X % gagnés dans la journée, plus de trade (ou risque réduit) jusqu'au lendemain."""
import pandas as pd
import pytest

from mt5lab.ftmo import apply_risk_rules, lock_text
from test_paper import setup  # noqa: F401  (fixture)


def _trades():
    return pd.DataFrame({"entry_time": pd.to_datetime(["2024-01-02 09:00", "2024-01-02 12:00", "2024-01-02 15:00",
                                                       "2024-01-03 09:00"]),
                         "exit_time": pd.to_datetime(["2024-01-02 11:00", "2024-01-02 14:00", "2024-01-02 17:00",
                                                      "2024-01-03 11:00"]),
                         "r": [2.0, 1.0, -1.0, 1.0], "w": 1.0})


def test_day_lock_stops_after_good_day_until_tomorrow():
    kept = apply_risk_rules(_trades(), day_lock={"seuil": 1.5, "facteur": 0})
    # +2 % à 11h -> les trades de 12h et 15h sont refusés ; le lendemain on retrade
    assert list(kept["entry_time"].dt.hour) == [9, 9] and len(kept) == 2
    assert len(apply_risk_rules(_trades(), day_lock=None, max_open=None)) == 4


def test_day_lock_can_reduce_risk_instead():
    kept = apply_risk_rules(_trades(), day_lock={"seuil": 1.5, "facteur": 0.5})
    assert len(kept) == 4 and list(kept["w"]) == [1.0, 0.5, 0.5, 1.0]
    assert "x0.5" in lock_text({"seuil": 1.5, "facteur": 0.5}) and "plus de nouveau" in lock_text({"seuil": 2, "facteur": 0})
    assert lock_text(None) == "aucun"


def test_paper_group_respects_day_lock(setup):  # noqa: F811
    from mt5lab.data import MT5Connector
    from mt5lab.paper import PaperEngine, Slot
    mk, tmp = setup
    cand = {"signal": {"type": "single", "name": "_test_long", "params": {}}, "filter": "none",
            "risk": {"sl_mode": "atr", "sl_value": 1.0, "rr": 2.0, "management": "none", "max_hold": 200,
                     "direction": "both"}}
    conn = MT5Connector().connect(verbose=False)
    eng = PaperEngine(conn, [Slot("c", "EURUSD", "H1", cand, group="g", risk_pct=1.0)], tmp / "paper", 1.0,
                      groups={"g": {"capital": 100_000, "day_budget": 2.5, "day_lock": {"seuil": 1.5, "facteur": 0}}})
    g = eng.groups["g"]
    eng.step()
    g.day_realized = 2_000.0      # +2 % déjà gagnés aujourd'hui
    mk.new_bar()
    eng.step()
    assert eng.slots["c"].position is None and g.skipped >= 1
    snap = eng.snapshot()["groupes"][0]["regles"]
    assert snap["frein_actif"] and "1.5" in snap["frein"]
    g.day_lock = {"seuil": 1.5, "facteur": 0.5}   # variante : risque divisé par 2
    assert eng.risk_budget(eng.slots["c"]) == pytest.approx(500)
    assert mk.sent == []
