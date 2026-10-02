"""Le Directeur de bout en bout sur données synthétiques (petit budget)."""
import json

from mt5lab.data import synthetic
from mt5lab.manager import Director, DirectorConfig


def test_director_builds_combined_strategy_within_daily_loss_cap(tmp_path):
    freq = {"H1": "h", "H4": "4h"}

    def get_data(sym, tf):
        return synthetic(3500, seed={"H1": 5, "H4": 6}[tf], freq=freq[tf], momentum=0.15), 0.00012

    cfg = DirectorConfig(["EURUSD"], ["H1", "H4"], out=tmp_path, rounds=1, budget=60, invent_generations=1,
                         day_budget=2.5, day_budgets=(0.5, 1.0, 2.5), catalog=False, bank_teams=False)
    d = Director(cfg, get_data, log=lambda m: None)
    comb = d.run()
    assert (tmp_path / "directeur.html").exists()
    if not comb:  # aucune stratégie validée sur ces données : le Directeur le dit sans inventer de résultat
        assert "Pas encore de stratégie combinée" in (tmp_path / "directeur.html").read_text(encoding="utf-8")
        return
    saved = json.loads((tmp_path / "strategie_combinee.json").read_text(encoding="utf-8"))
    assert saved["scenario_choisi"] in (0.5, 1.0, 2.5)
    assert saved["regles"]["total_budget"] == 10.0
    assert saved["resultat"]["trades_mois"] > 0 and all("trades_mois" in c for c in saved["composants"])
    assert isinstance(saved.get("melanges"), list)      # le Chef des combinaisons est passé
    assert "Chef des combinaisons" in (tmp_path / "directeur.html").read_text(encoding="utf-8")
    assert "TRADES PAR MOIS" in (tmp_path / "directeur.html").read_text(encoding="utf-8")
    assert all(c["risk_pct"] <= 1.0 for c in saved["composants"])
    for row in saved["scenarios"]:
        assert row["pire_jour"] >= -row["budget"] * 1.05  # la pire journée respecte le plafond (à 5 % près : frais)
    assert "reussis" in saved["challenges_historique"] and "rates" in saved["challenges_historique"]
    assert "reussis_oos" in saved["scenarios"][0]
    assert {r["horaire"] for r in saved["scenarios"]} <= {"24h/24", "8h-17h", "8h-13h"}
    assert saved["horaire"]["nom"] in ("24h/24", "8h-17h", "8h-13h")
    assert len(saved["horaires"]) >= 1
    assert (tmp_path / f"strategie_combinee_{saved['horaires'][0]['fichier'].split('_')[-1]}").exists()
    assert "24h/24 ou seulement le jour" in (tmp_path / "directeur.html").read_text(encoding="utf-8")
    # les autres comptes : compte perso (5 000 $) et compte financé, construits avec les mêmes stratégies
    html = (tmp_path / "directeur.html").read_text(encoding="utf-8")
    assert "compte perso et compte financé" in html
    assert html.count('class="labbot"') >= 1 and "api/botfichier" in html   # boutons Bot MT5 partout
    for name in ("perso", "finance"):
        f = tmp_path / f"strategie_combinee_{name}.json"
        if f.exists():
            acc = json.loads(f.read_text(encoding="utf-8"))
            assert acc["profil"] == name and acc["resultat"]["p_probleme"] <= 5 or acc.get("essai")
            assert acc["capital"] == (5000 if name == "perso" else 100_000) and acc["composer"] == (name == "perso")
            assert all(c["risk_pct"] <= (2.0 if name == "perso" else 1.0) for c in acc["composants"])
            assert (tmp_path / f"compte_{name}.html").exists()
    # pages refaites sans rien recalculer (option R du menu) : mêmes boutons Bot MT5
    (tmp_path / "directeur.html").unlink()
    Director(cfg, get_data, log=lambda m: None).rebuild_report()
    again = (tmp_path / "directeur.html").read_text(encoding="utf-8")
    assert "labbot" in again and "24h/24 ou seulement le jour" in again
    assert "labbot" in (tmp_path / "fiches_strategies.html").read_text(encoding="utf-8")
    # option W : seulement le mélange des stratégies combinées, sur les résultats déjà calculés
    d2 = Director(cfg, get_data, log=lambda m: None)
    d2.mix_only()
    mixed = json.loads((tmp_path / "strategie_combinee.json").read_text(encoding="utf-8"))
    assert "melanges" in mixed
    adv = mixed.get("conseil_compte")
    assert adv and {r["compte"] for r in adv["lignes"]} >= {"FTMO 1 étape (Standard)", "FTMO 2 étapes (Standard ou Swing)"}
    assert 'id="compte"' in (tmp_path / "directeur.html").read_text(encoding="utf-8")
    page = (tmp_path / "directeur.html").read_text(encoding="utf-8")
    assert 'id="melanges"' in page and ("Meilleur que la combinée seule" in page or "Mélange impossible" in page)
    if mixed["melanges"]:
        assert all(r["melange"].startswith("Directeur + ") for r in mixed["melanges"])
    # LA page unique : TOP 10 de toutes les stratégies combinées, avec leurs chiffres et leurs bots
    top = json.loads((tmp_path / "toutes_les_combinees.json").read_text(encoding="utf-8"))
    assert len(top) >= 3 and [e["rang"] for e in top] == list(range(1, len(top) + 1))
    assert any(e.get("choisie") for e in top)
    n1 = json.loads((tmp_path / "strategie_n1.json").read_text(encoding="utf-8"))
    assert n1["composants"] == top[0]["comb"]["composants"] and n1["nom"].startswith("N°1 du TOP 10")
    for e in top[:10]:
        r = e["res"]
        assert r["trades_mois"] is not None and r["dd_max"] >= 0 and r["fenetre"] and r["jours_periode"] > 0
        assert e["comb"]["composants"] and "candidate" in e["comb"]["composants"][0]
    page = (tmp_path / "resultats.html").read_text(encoding="utf-8")
    assert "TOP 10 des stratégies combinées" in page and page.count('class="labbot"') >= 3
    assert "Plus grosse baisse" in page and "Période analysée" in page and "Glissement" in page
    assert "Compte financé" in page and "Quel compte FTMO acheter" in page
    # gestion des trades : les 5 gestions comparées pour chaque stratégie validée
    g = json.loads((tmp_path / "gestion_trades.json").read_text(encoding="utf-8"))
    assert g and all(r["meilleure"] in r["modes"] for r in g)
    assert {"paliers", "intelligente"} <= set().union(*(r["modes"] for r in g))
    assert "Gestion des trades" in page
    # l'avocat du diable et la page dédiée au TOP 10
    av = json.loads((tmp_path / "avocat_du_diable.json").read_text(encoding="utf-8"))
    assert av["cases"] and av["niveau"] in ("bon", "moyen", "danger", "neutre") and av["verdict"]
    t10 = (tmp_path / "top10.html").read_text(encoding="utf-8")
    assert "TOP 10 des stratégies combinées" in t10 and "Avocat du diable" in t10
    assert t10.count("Créer le bot MT5 de la n°") == min(10, len(top)) and "Trades par mois" in t10
