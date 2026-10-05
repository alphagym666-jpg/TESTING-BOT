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
