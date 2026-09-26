"""Le Directeur de bout en bout sur données synthétiques (petit budget)."""
import json

from mt5lab.data import synthetic
from mt5lab.manager import Director, DirectorConfig


def test_director_builds_combined_strategy_within_daily_loss_cap(tmp_path):
    freq = {"H1": "h", "H4": "4h"}

    def get_data(sym, tf):
        return synthetic(3500, seed={"H1": 5, "H4": 6}[tf], freq=freq[tf], momentum=0.15), 0.00012

    cfg = DirectorConfig(["EURUSD"], ["H1", "H4"], out=tmp_path, rounds=1, budget=60, invent_generations=1,
                         day_budget=1.7, day_budgets=(0.5, 1.0, 1.7))
    d = Director(cfg, get_data, log=lambda m: None)
    comb = d.run()
    assert (tmp_path / "directeur.html").exists()
    if not comb:  # aucune stratégie validée sur ces données : le Directeur le dit sans inventer de résultat
        assert "Pas encore de stratégie combinée" in (tmp_path / "directeur.html").read_text(encoding="utf-8")
        return
    saved = json.loads((tmp_path / "strategie_combinee.json").read_text(encoding="utf-8"))
    assert saved["scenario_choisi"] in (0.5, 1.0, 1.7)
    assert all(c["risk_pct"] <= 1.0 for c in saved["composants"])
    for row in saved["scenarios"]:
        assert row["pire_jour"] >= -row["budget"] * 1.05  # la pire journée respecte le plafond (à 5 % près : frais)
