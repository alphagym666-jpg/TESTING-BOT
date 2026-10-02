"""CALIBRATION DES COÛTS RÉELS : ce que coûte vraiment un trade en direct, mesuré sur le paper trading et le bot.

- spread réel à l'entrée : colonne « spread_entree_pts » de chaque trades.csv du paper trading (prix réels MT5) ;
- glissement du bot : écart en points entre le prix du signal et le prix obtenu par le bot MT5
  (fichier glissements.csv écrit par le surveillant).

Ces mesures sont ajoutées aux coûts de la recherche (colonne « cost ») : les agents ne peuvent plus retenir une
stratégie qui ne gagne que sur papier, avec un spread parfait et aucun glissement. Il faut au moins
MIN_TRADES mesures par marché pour qu'elles comptent.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

MIN_TRADES = 10
DEFAULT_SLIP_FRACTION = 0.5  # glissement supposé tant qu'il n'est pas mesuré : la moitié du spread médian par trade
FILE = "couts_reels.json"


def _median(v):
    return round(float(np.median(v)), 2) if v else None


def calibrate(results_dir: Path, save: bool = True) -> dict:
    """Mesure {symbole: {"spread_pts", "glissement_pts", "n_spread", "n_glissement"}} dans tout le dossier."""
    results_dir = Path(results_dir)
    spreads: dict[str, list] = {}
    slips: dict[str, list] = {}
    seen = set()
    for p in results_dir.glob("**/trades.csv"):
        try:
            with open(p, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    k = (r.get("symbole"), r.get("ouverture"), r.get("strategie_id"))
                    if k in seen or not r.get("symbole"):
                        continue
                    seen.add(k)
                    try:
                        sp = float(r.get("spread_entree_pts") or "nan")
                    except ValueError:
                        continue
                    if sp == sp and sp >= 0:
                        spreads.setdefault(r["symbole"], []).append(sp)
        except OSError:
            continue
    for p in results_dir.glob("**/glissements.csv"):
        try:
            for ln in p.read_text(encoding="utf-8").splitlines():
                f = ln.split(";")
                if len(f) >= 3:
                    try:
                        slips.setdefault(f[1], []).append(abs(float(f[2])))
                    except ValueError:
                        pass
        except OSError:
            continue
    out = {}
    for sym in sorted(set(spreads) | set(slips)):
        sp, sl = spreads.get(sym, []), slips.get(sym, [])
        out[sym] = {"spread_pts": _median(sp) if len(sp) >= MIN_TRADES else None,
                    "glissement_pts": _median(sl) if len(sl) >= MIN_TRADES else None,
                    "n_spread": len(sp), "n_glissement": len(sl)}
    if save and out:
        (results_dir / FILE).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def extra_points(calib: dict | None, symbol: str, hist_spread_pts: float) -> tuple[float, str]:
    """Coût aller-retour à AJOUTER (en points) : spread réel au-delà du spread historique + glissement à l'entrée
    et à la sortie. Retourne (points, explication)."""
    c = (calib or {}).get(symbol) or {}
    extra, why = 0.0, []
    if c.get("spread_pts") is not None and c["spread_pts"] > hist_spread_pts:
        extra += c["spread_pts"] - hist_spread_pts
        why.append(f"spread réel {c['spread_pts']:g} pts au lieu de {hist_spread_pts:g}")
    if c.get("glissement_pts"):
        extra += 2 * c["glissement_pts"]
        why.append(f"glissement du bot {c['glissement_pts']:g} pts x2 (entrée + sortie)")
    elif hist_spread_pts > 0:  # pas encore mesuré en direct : estimation prudente
        est = round(DEFAULT_SLIP_FRACTION * hist_spread_pts, 2)
        extra += est
        why.append(f"glissement estimé {est:g} pts (pas encore mesuré en direct)")
    return extra, " + ".join(why)
