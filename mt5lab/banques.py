"""Équipe C (algorithmes des banques) et équipe D (inventions institutionnelles).

Personne n'a accès aux algorithmes des banques. Ce que ces agents cherchent, ce sont leurs EMPREINTES mesurables
dans les prix : chasses aux stops, ouvertures de sessions et fixings, niveaux ronds, exécution autour du VWAP,
flux de fin de mois. Une « faille » est une situation après laquelle le prix a bougé dans un sens de façon
statistiquement significative.

Équipe C — 5 analystes (agents 11 à 15), supervisés par le Chef C :
  chaque analyste mesure, sur la 1re partie de l'historique (isa), le mouvement moyen des prix APRÈS chaque
  situation de sa spécialité (en ATR, sur plusieurs horizons). Il ne garde que les écarts nets (t >= 3).
  Le Chef C revérifie chaque faille sur une période que l'analyste n'a pas vue (isb) : même sens et t >= 1,5.

Équipe D — 5 inventeurs (agents 16 à 20), supervisés par le Chef D :
  ils partent des failles confirmées (comme point de départ et comme pistes d'indicateurs) et inventent des
  stratégies complètes, que le Chef D confirme ; puis tout passe la validation finale commune.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .inventions import BETWEEN, FEATURES, feat

HORIZONS = (3, 8, 20)
QUANTILES = (0.05, 0.1, 0.2, 0.3, 0.7, 0.8, 0.9, 0.95)


@dataclass
class BankAgent:
    number: int
    name: str
    role: str
    features: list
    tested: int = 0
    found: list = field(default_factory=list)

    @property
    def tag(self):
        return f"Agent {self.number:>2} {self.name}"


TEAM_C = [
    (11, "Chasseur de liquidité", "Chasses aux stops : balayage des plus hauts/bas puis retour", ["sweep", "prev_day_pos"]),
    (12, "Horloge institutionnelle", "Ouvertures de sessions, fixing, heures et jours", ["session_move", "hour", "dow", "minute"]),
    (13, "Niveaux ronds et ordres", "Niveaux psychologiques, plus hauts/bas de la veille", ["round_dist", "prev_day_pos"]),
    (14, "Exécution VWAP/TWAP", "Écart au VWAP du jour, pics de volume", ["vwap_dist", "vol_spike"]),
    (15, "Flux calendaires", "Fin de mois, jour de la semaine, range asiatique", ["month_end", "dow", "asia_pos"]),
]

TEAM_D = [
    (16, "Inventeur liquidité", "Stratégies autour des chasses aux stops"),
    (17, "Inventeur sessions", "Stratégies d'ouverture et d'horaires"),
    (18, "Inventeur niveaux", "Stratégies sur niveaux ronds et range de la veille"),
    (19, "Inventeur VWAP", "Stratégies de retour ou d'écart au VWAP"),
    (20, "Inventeur synthèse", "Combine toutes les failles et tous les indicateurs"),
]


def _stats(values: np.ndarray):
    v = values[np.isfinite(values)]
    if len(v) < 2:
        return 0.0, 0.0, len(v)
    sd = v.std(ddof=1)
    return float(v.mean()), float(v.mean() / sd * math.sqrt(len(v))) if sd > 0 else 0.0, len(v)


_FW: dict = {}


def _forward(df: pd.DataFrame, h: int) -> np.ndarray:
    """Mouvement des h bougies suivantes, en ATR (la cible à prévoir ; jamais utilisée comme signal)."""
    key = (id(df), len(df), h)
    if key not in _FW:
        from . import indicators as ind
        if len(_FW) > 50:
            _FW.clear()
        a = ind.atr(df, 14)
        _FW[key] = ((df["close"].shift(-h) - df["close"]) / a).to_numpy()
    return _FW[key]


def _conditions(df, f, n):
    """Conditions candidates pour un indicateur : seuils (quantiles) ou fenêtres (heures, jours...)."""
    if f in BETWEEN:
        lo, hi, widths, _ = BETWEEN[f]
        out = []
        for w in widths[:2]:
            for a in range(lo, hi - w + 1, max(1, w // 2 if w > 1 else 1)):
                out.append({"f": f, "n": 0, "op": "between", "v": [a, a + w]})
        return out
    x = feat(df, f, n).dropna()
    if FEATURES[f][1]:
        x = pd.concat([x, -x])
    if not len(x):
        return []
    qs = np.nanquantile(x, QUANTILES)
    return [{"f": f, "n": n, "op": ">" if q > np.nanmedian(x) else "<", "v": float(f"{q:.3g}")} for q in qs]


def _events(df, cond, side):
    """Bougies où la condition DEVIENT vraie, côté achat (side=1) ou vente miroir (side=-1)."""
    x = feat(df, cond["f"], cond["n"])
    if cond["op"] == "between":
        c = (x >= cond["v"][0]) & (x < cond["v"][1])
    else:
        if side < 0 and FEATURES[cond["f"]][1]:
            x = -x
        c = (x > cond["v"]) if cond["op"] == ">" else (x < cond["v"])
    c = c.fillna(False)
    return (c & ~c.shift(1, fill_value=False)).to_numpy()


def measure(df, cond, h, behaviour=None):
    """Mouvement moyen (en ATR) après la condition.

    Indicateur directionnel : achats quand la condition devient vraie + ventes miroir, résultats mis en commun.
    Fenêtre non directionnelle (heure, jour...) : on mesure un COMPORTEMENT du prix pendant la fenêtre :
    behaviour = +1 continuation du mouvement des 3 dernières bougies, -1 retournement.
    """
    fw = _forward(df, h)
    if cond["op"] == "between" or not FEATURES[cond["f"]][1]:
        ev = _events(df, cond, 1)
        mom = np.sign((df["close"] - df["close"].shift(3)).to_numpy())
        vals = (behaviour or 1) * mom[ev] * fw[ev]
        return _stats(vals)
    vals = np.concatenate([fw[_events(df, cond, 1)], -fw[_events(df, cond, -1)]])
    return _stats(vals)


def to_rule(cond, behaviour=None) -> dict:
    """Transforme une faille en stratégie jouable (règle au même format que les inventions)."""
    if cond["op"] == "between" or not FEATURES[cond["f"]][1]:
        trig = {"f": "momentum", "n": 5, "op": ">" if (behaviour or 1) > 0 else "<", "v": 0.0}
        return {"type": "rule", "trigger": trig, "filters": [cond], "mirror": True}
    return {"type": "rule", "trigger": cond, "filters": [], "mirror": True}


def describe_faille(a: dict) -> str:
    from .inventions import _cond_text
    txt = _cond_text(a["cond"])
    if a.get("behaviour"):
        txt += " -> le mouvement " + ("CONTINUE" if a["behaviour"] > 0 else "SE RETOURNE")
    else:
        txt += " -> le prix MONTE ensuite (et miroir : baisse dans la situation inverse)"
    return f"{txt} (sur {a['h']} bougies : {a['mean']:+.2f} ATR en moyenne, t = {a['t']:.1f}, {a['n']} cas)"


def scan(agent: BankAgent, df_train, df_check, min_t=3.0, min_n=30, check_t=1.5, max_keep=4, log=print):
    """Un analyste cherche ses failles sur df_train ; le Chef C les revérifie sur df_check."""
    found = []
    for f in agent.features:
        if f not in FEATURES:
            continue
        for n in FEATURES[f][2]:
            try:
                conds = _conditions(df_train, f, n)
            except Exception:
                continue
            for cond in conds:
                behaviours = (1, -1) if (cond["op"] == "between" or not FEATURES[f][1]) else (None,)
                for b in behaviours:
                    for h in HORIZONS:
                        agent.tested += 1
                        mean, t, cnt = measure(df_train, cond, h, b)
                        if cnt < min_n or abs(t) < min_t:
                            continue
                        c = dict(cond)
                        beh = b
                        if mean < 0:  # le marché fait l'inverse : on retourne la condition
                            if b is not None:
                                beh = -b
                            elif c["op"] != "between":
                                c = {**c, "op": "<" if c["op"] == ">" else ">", "v": -c["v"]}
                            mean, t = -mean, -t
                        found.append({"cond": c, "behaviour": beh, "h": h, "mean": mean, "t": t, "n": cnt,
                                      "agent": agent.tag, "agent_no": agent.number})
    # une seule faille par situation (le meilleur horizon), les plus nettes d'abord
    best: dict = {}
    for a in found:
        k = repr((a["cond"], a["behaviour"]))
        if k not in best or a["t"] > best[k]["t"]:
            best[k] = a
    ranked = sorted(best.values(), key=lambda a: -a["t"])
    confirmed = []
    for a in ranked:
        m2, t2, n2 = measure(df_check, a["cond"], a["h"], a["behaviour"])
        a["t_controle"], a["n_controle"] = t2, n2
        a["confirmee"] = bool(n2 >= max(10, min_n // 3) and t2 >= check_t and m2 > 0)
        if a["confirmee"]:
            confirmed.append(a)
        if len(confirmed) >= max_keep:
            break
    agent.found = confirmed
    log(agent.tag, f"{agent.tested} situations mesurées, {len(ranked)} failles candidates, "
                   f"{len(confirmed)} confirmées par le Chef C"
        + (f" | meilleure : {describe_faille(confirmed[0])}" if confirmed else ""))
    return confirmed
