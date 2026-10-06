"""Ergonomie de la plateforme : Mes bots, ouverture de dossier, calcul automatique de la nuit, page."""
import json
import os
import time
from datetime import datetime
from types import SimpleNamespace

import mt5lab.plateforme as pf


def _bot(root, name, active=True, group=True):
    d = root / "bots" / name
    (d / "paper").mkdir(parents=True)
    (d / "strategie.json").write_text(json.dumps({"nom": name, "cree_le": "2026-10-05 09:00", "capital": 100000,
        "composants": [{"symbole": "GER40", "timeframe": "H1", "strategie": "s", "risk_pct": 1.0,
                        "horaire": {"nom": "8h-11h (heure du serveur MT5)", "debut": 8, "fin": 11}}]}))
    st = d / "paper" / "etat.json"
    st.write_text(json.dumps({"groups": {"G": {"balance": 102000, "trades": 10, "pnl": 2000, "ftmo_status": "en cours",
                                                "trade_days": ["a", "b"]}}} if group else {}))
    if not active:
        old = time.time() - 3600
        os.utime(st, (old, old))
    return d


def test_my_bots_list(tmp_path):
    eng = SimpleNamespace(out=tmp_path / "paper")
    _bot(tmp_path, "actif")
    _bot(tmp_path, "arrete", active=False)
    (tmp_path / "bots" / "vide").mkdir()
    res = pf.bots_live(eng)
    by = {b["nom"]: b for b in res["bots"]}
    assert set(by) == {"actif", "arrete"} and by["actif"]["etat"] == "actif" and by["arrete"]["etat"] == "arrêté"
    assert by["actif"]["paper"]["profit_pct"] == 2.0 and by["actif"]["composants"][0]["horaire"].startswith("8h-11h")
    assert res["bots"][0]["nom"] == "actif"                        # les bots actifs d'abord


def test_open_folder_only_inside_bot_folders(tmp_path):
    eng = SimpleNamespace(out=tmp_path / "paper")
    d = _bot(tmp_path, "b1")
    assert not pf.open_bot_folder(eng, f"/api/ouvrir?dossier={tmp_path}")["ok"]          # en dehors : refusé
    r = pf.open_bot_folder(eng, f"/api/ouvrir?dossier={d}")
    assert str(d.resolve()) in r["message"]                                              # Linux : chemin donné


def test_nightly_run_once_per_night(monkeypatch):
    eng = SimpleNamespace(_auto_once=True)
    calls = []
    monkeypatch.setattr(pf, "run_nightly_once", lambda e, w: calls.append(1))
    times = iter([datetime(2026, 10, 5, 1, 59), datetime(2026, 10, 5, 2, 0), datetime(2026, 10, 5, 2, 30)])
    monkeypatch.delenv("LABO_AUTO_HEURE", raising=False)
    pf._nightly(eng, hour=2, now=lambda: next(times), wait=lambda s: None)
    assert calls == [1] and eng._auto["dernier"] == "2026-10-05" and eng._auto["etat"].startswith("fini")
    monkeypatch.setenv("LABO_AUTO_HEURE", "-1")
    eng2 = SimpleNamespace()
    pf._nightly(eng2, now=lambda: datetime(2026, 10, 5, 2, 0), wait=lambda s: None)
    assert eng2._auto["etat"] == "désactivé"


def test_nightly_pass_and_telegram_summary(monkeypatch):
    sent = []
    eng = SimpleNamespace(notifier=SimpleNamespace(send=sent.append))
    monkeypatch.setattr(pf, "top10_live", lambda e, start=False: {"etat": "fini"})
    monkeypatch.setattr(pf, "top2ans_live", lambda e, start=False: {"etat": "fini", "resultat": {"general": [
        {"type": "combinée", "composants": [{"symbole": "GER40", "timeframe": "H1",
                                             "horaire": {"nom": "8h-11h (heure du serveur MT5)"}}]}]}})
    out = pf.run_nightly_once(eng, wait=lambda s: None)
    assert set(out) == {"top10", "top2ans"} and "GER40 H1 8h-11h" in sent[0]


def test_page_has_sections_home_bots_and_modes():
    for x in ("SECTIONS", "viewHome", "viewBots", "showModal", "postRender", "verdict", "ficheExtra", "modeBtn",
              "Tout recalculer maintenant", "@media (max-width:760px)"):
        assert x in pf.PAGE


def test_start_bot_only_inside_bot_folders(tmp_path):
    eng = SimpleNamespace(out=tmp_path / "paper")
    d = _bot(tmp_path, "b1")
    assert not pf.start_bot(eng, f"/api/demarrer?dossier={d}")["ok"]                    # pas de LANCER_BOT.bat
    (d / "LANCER_BOT.bat").write_text("@echo off")
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "LANCER_BOT.bat").write_text("@echo off")
    assert "refusé" in pf.start_bot(eng, f"/api/demarrer?dossier={tmp_path / 'x'}")["message"]
    r = pf.start_bot(eng, f"/api/demarrer?dossier={d}")
    assert "LANCER_BOT.bat" in r["message"]                                              # Linux : à la main


def test_all_combinations_tab(tmp_path, monkeypatch):
    """Onglet « Combinaisons » : Chef (direct), backtest 2 ans, bonnes partout, perso 5k, Directeur ; catégories
    « FTMO déjà réussi » et « challenge » ; chaque ligne sait créer ses deux bots."""
    comp = {"strategie_id": "a", "symbole": "GER40", "timeframe": "H1", "strategie": "s", "risk_pct": 1.0,
            "horaire": {"debut": 8, "fin": 11}}
    chef = {"rang": 1, "nom": "N°1", "conforme": True, "composants": [comp],
            "resultat": {"ftmo_pass": 80, "ftmo_echec_p1": 1.0, "jours_attendus": 12, "reussis": 3, "rates": 0}}
    bt = {"rang": 1, "nom": "C1", "conforme": True, "composants": [comp], "backtest": {"tout": {"ftmo_pass": 70, "reussis": 0, "rates": 2}},
          "comptes": {"perso": {"gain_jour_usd": 12.0}}}
    monkeypatch.setattr(pf, "top10_live", lambda e, start=False: {"resultat": {"top": [chef]}})
    monkeypatch.setattr(pf, "top2ans_live", lambda e, start=False, debut=None: {"resultat": {
        "combinees": [bt], "combinees_croisees": [dict(bt, rang=2)], "perso": [dict(bt, k="c", rang_source=1, rang=1)]}})
    (tmp_path / "strategie_combinee_perso.json").write_text(json.dumps({"profil": "perso", "composants": [dict(comp, candidate={"x": 1})],
                                                                        "resultat": {"ftmo_pass": 60}}))
    eng = SimpleNamespace(out=tmp_path / "paper", groups={}, slots={})
    res = pf.combos_live(eng)
    src = [r["source"] for r in res["lignes"]]
    assert src.count("chef") == 1 and "bt2" in src and "partout" in src and "perso" in src and "directeur" in src
    by = {r["source"]: r for r in res["lignes"]}
    assert by["chef"]["ref"] == {"top": 1} and by["partout"]["ref"] == {"bt2x": 2} and by["perso"]["ref"] == {"bt2": 1}
    assert by["directeur"]["ref"] == {"fichier": "strategie_combinee_perso.json"} and by["directeur"]["profil"] == "perso"
    assert res["nombre"]["reussi"] == 1 and by["chef"]["reussis"] == 3
    for x in ("viewCombos", "Bot compte perso 5k", "FTMO déjà réussi", "/api/demarrer", "X-Labo", "chez vous"):
        assert x in pf.PAGE


def test_server_offset_for_display():
    """Décalage serveur MT5 / PC (affichage seulement : les stratégies utilisent l'heure du serveur directement)."""
    from mt5lab.paper import server_offset
    now = 1_760_000_000.0
    # FTMO GMT+3, PC au Québec GMT-4 : le serveur a 7 h d'avance
    assert server_offset(now + 3 * 3600 + 2, now, -4 * 3600) == 7
    assert server_offset(now + 3 * 3600 - 3 * 3600 * 0.9, now, -4 * 3600) is None      # prix trop vieux
    from mt5lab.pont import _local
    assert _local({"debut": 8, "fin": 11}, 7).strip() == "(= 1h-4h à l'heure de votre PC)"


def test_bot_from_directeur_file(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(pf, "_build_bot", lambda eng, comb, root, cap, ftmo, risk: seen.update(comb=comb, cap=cap, risk=risk) or {"ok": True})
    comp = {"symbole": "GER40", "timeframe": "H1", "strategie": "s", "risk_pct": 2.0, "candidate": {"x": 1}}
    (tmp_path / "strategie_combinee_perso.json").write_text(json.dumps({"profil": "perso", "composants": [comp]}))
    (tmp_path / "strategie_combinee.json").write_text(json.dumps({"composants": [dict(comp, risk_pct=1.0)]}))
    eng = SimpleNamespace(out=tmp_path / "paper", ftmo=None, risk_pct=1.0)
    assert pf.make_bot(eng, "/api/bot?fichier=strategie_combinee_perso.json")["ok"]
    assert seen["cap"] == 5000 and seen["risk"] == 2.0 and seen["comb"]["composer"]      # risques du compte perso gardés
    assert pf.make_bot(eng, "/api/bot?fichier=strategie_combinee.json")["ok"] and seen["cap"] == 100_000
    assert not pf.make_bot(eng, "/api/bot?fichier=../secret.json")["ok"]


def test_mt5_folder_found_without_terminal_info(tmp_path):
    """« MT5 introuvable ('NoneType'...) » : si le module MT5 ne répond pas, on trouve le terminal dans APPDATA."""
    from mt5lab.pont import mt5_folders
    prog = tmp_path / "Program Files" / "MetaTrader 5"
    prog.mkdir(parents=True)
    old, new = tmp_path / "MetaQuotes" / "Terminal" / "AAA", tmp_path / "MetaQuotes" / "Terminal" / "BBB"
    for d in (old, new):
        (d / "MQL5").mkdir(parents=True)
    (new / "origin.txt").write_text(str(prog), encoding="utf-16")
    os.utime(old / "MQL5", (1, 1))
    fake = SimpleNamespace(terminal_info=lambda: None)
    assert mt5_folders(fake, appdata=str(tmp_path)) == (new, prog)
    good = SimpleNamespace(terminal_info=lambda: SimpleNamespace(data_path=str(old), path=str(prog)))
    assert mt5_folders(good, appdata=str(tmp_path)) == (old, prog)


def test_live_trades_drop_1970(tmp_path):
    """« période 1970-01-01 → … 14 808 jours de bourse » : un trade sans vraie date ne fausse plus tout."""
    from mt5lab.paper import _now
    (tmp_path / "trades.csv").write_text("strategie_id,ouverture,fermeture,r\na,2026-10-01 10:00:00,2026-10-01 11:00:00,1\n"
                                         "a,1970-01-01 00:00:00,2026-10-02 11:00:00,2\n", encoding="utf-8")
    eng = SimpleNamespace(out=tmp_path, slots={"a": None}, recent=[])
    t = pf._live_trades(eng)
    assert len(t) == 1 and t["r"].iloc[0] == 1
    assert _now(SimpleNamespace(time_msc=0, time=1_760_000_000)).startswith("2025")
