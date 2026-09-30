"""Calibration des coûts réels : spread mesuré en paper trading + glissement du bot, ajoutés aux tests."""
import csv
import json

from mt5lab.couts_reels import MIN_TRADES, calibrate, extra_points
from mt5lab.paper import TRADE_FIELDS
from mt5lab.surveillant import BotWatcher


def _write_trades(path, sym, spread, n):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=TRADE_FIELDS)
        w.writeheader()
        for i in range(n):
            w.writerow({"strategie_id": "s", "symbole": sym, "ouverture": f"2026-01-01 {i:02d}:00",
                        "spread_entree_pts": spread})


def test_calibration_from_paper_and_bot(tmp_path):
    _write_trades(tmp_path / "paper" / "trades.csv", "XAUUSD", 30, MIN_TRADES)
    _write_trades(tmp_path / "paper_bis" / "trades.csv", "EURUSD", 12, MIN_TRADES - 1)   # pas assez de mesures
    alerts = []
    bw = BotWatcher(tmp_path / "exec.csv", alerts.append, slip_log=tmp_path / "paper" / "glissements.csv")
    for i in range(MIN_TRADES):
        bw.signal_open(f"k{i}", "x", 2000.0)
    (tmp_path / "exec.csv").write_text("".join(f"t;OPEN;k{i};XAUUSD;2000.05;1;1\n" for i in range(MIN_TRADES)))
    bw.poll(lambda s: 0.01)
    cal = calibrate(tmp_path)
    assert cal["XAUUSD"]["spread_pts"] == 30 and cal["XAUUSD"]["glissement_pts"] == 5
    assert cal["EURUSD"]["spread_pts"] is None
    assert json.loads((tmp_path / "couts_reels.json").read_text())["XAUUSD"]["n_spread"] == MIN_TRADES
    extra, why = extra_points(cal, "XAUUSD", 20)
    assert extra == 10 + 2 * 5 and "glissement" in why
    assert extra_points(cal, "EURUSD", 10)[0] == 0 and extra_points(None, "XAUUSD", 20)[0] == 0
    assert extra_points(cal, "XAUUSD", 40)[0] == 10   # spread historique déjà plus large : seul le glissement


def test_bot_watcher_alerte_si_aucun_signe_de_vie(tmp_path):
    """Bot posé avec un ancien InpFichier : il n'écrit jamais dans exec_<fichier> -> alerte claire, une seule fois."""
    import time as _t
    alerts = []
    bw = BotWatcher(tmp_path / "exec_labo_signaux_18334626.csv", alerts.append, silent_after=0.1, missed_after=0.05)
    bw.signal_open("k1", "XAUUSD M30 test", 4187.32)
    _t.sleep(0.15)
    bw.poll()
    bw.poll()
    assert sum("AUCUN signe de vie" in a for a in alerts) == 1
    assert any("NON exécuté" in a and "InpFichier = labo_signaux_18334626.csv" in a for a in alerts)
    assert bw.status()["fichier"] == "labo_signaux_18334626.csv" and not bw.status()["vivant"]


def test_labobot_un_seul_graphique_et_fichier_affiche():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "mql5" / "LaboBot.mq5").read_text(encoding="utf-8-sig")
    assert "tourne déjà sur un autre graphique" in src and "return(INIT_FAILED)" in src
    # le verrou dépend du fichier de signaux (unique par stratégie), pas du numéro magique
    assert '"LaboBot_vivant_" + InpFichier' in src and '"LaboBot_vivant_" + IntegerToString' not in src
    assert 'Print("LaboBot démarré : fichier des signaux ", InpFichier' in src
    assert "FileIsExist(InpFichier, FILE_COMMON)" in src and "Fichier écouté" in src
