"""LE CONSEIL : un nouvel employé qui fait VOTER les meilleures stratégies d'un marché.

Idée : chaque stratégie se trompe souvent seule. Quand 2 ou 3 stratégies DIFFÉRENTES (tendance, retour à la moyenne,
invention, loi d'un génie...) donnent le même sens en même temps, le signal est souvent plus fiable.

Signal « vote » : {"type": "vote", "name": "CONSEIL ...", "members": [{"signal": ..., "filter": ...}, ...],
                   "min": 2, "window": 3}
  - chaque membre « vote » pendant `window` bougies après son signal (+1 achat, -1 vente) ;
  - ACHAT quand au moins `min` membres votent achat en même temps (et que le compte vient d'être atteint) ;
    VENTE en miroir.

Le Conseil choisit ses membres d'après la période de RECHERCHE seulement (jamais la validation), puis ses votes
passent exactement la même validation hors-échantillon que tout le monde (famille de tests à part, seuil corrigé).
"""
from __future__ import annotations

import itertools
import json
import math

import numpy as np
import pandas as pd

TAG = "Le Conseil (vote des meilleures stratégies)"
_CACHE: dict = {}


def _member_signal(df: pd.DataFrame, m: dict) -> pd.Series:
    from .evaluator import compute_signal, signal_key
    from .strategies import apply_filter
    key = (id(df), len(df), str(df.index[0]) if len(df) else "", signal_key(m["signal"]), m.get("filter", "none"))
    if key not in _CACHE:
        if len(_CACHE) > 300:
            _CACHE.clear()
        _CACHE[key] = apply_filter(df, compute_signal(df, m["signal"]), m.get("filter", "none")).astype(np.int8)
    return _CACHE[key]


def vote_signal(df: pd.DataFrame, sig: dict) -> pd.Series:
    w = max(1, int(sig.get("window", 3)))
    k = max(1, int(sig.get("min", 2)))
    longs = np.zeros(len(df), dtype=np.int16)
    shorts = np.zeros(len(df), dtype=np.int16)
    for m in sig["members"]:
        s = _member_signal(df, m)
        longs += ((s > 0).astype(np.int8).rolling(w, min_periods=1).max() > 0).to_numpy(dtype=np.int16)
        shorts += ((s < 0).astype(np.int8).rolling(w, min_periods=1).max() > 0).to_numpy(dtype=np.int16)
    buy = (longs >= k) & (shorts == 0)
    sell = (shorts >= k) & (longs == 0)
    prev_buy = np.concatenate([[False], buy[:-1]])
    prev_sell = np.concatenate([[False], sell[:-1]])
    out = np.where(buy & ~prev_buy, 1, np.where(sell & ~prev_sell, -1, 0)).astype(np.int8)
    return pd.Series(out, index=df.index)


def describe_vote(sig: dict) -> str:
    from .evaluator import describe
    parts = " | ".join(describe({"signal": m["signal"], "filter": m.get("filter", "none")})[:60] for m in sig["members"])
    return f"{sig.get('name', 'CONSEIL')} ({sig.get('min', 2)} sur {len(sig['members'])} d'accord en " \
           f"{sig.get('window', 3)} bougies) : {parts}"


def members_of(findings, ingredients, n: int = 6) -> list[dict]:
    """Les membres possibles : les meilleures stratégies DIFFÉRENTES de la case (score de la recherche)."""
    from .evaluator import signal_key
    out, seen = [], set()
    pool = [(f.score, f.candidate) for f in sorted(findings, key=lambda f: f.score, reverse=True)]
    pool += [(None, c) for c in ingredients]
    for _, c in pool:
        sig = c["signal"]
        if sig.get("type") == "vote":
            continue
        base = (sig.get("name") if sig.get("type") == "single" else
                sig["a"].get("name") if sig.get("type") == "combo" else signal_key(sig))  # vraiment différentes
        if base in seen:
            continue
        seen.add(base)
        out.append(c)
        if len(out) >= n:
            break
    return out


def proposals(members: list[dict]) -> list[dict]:
    """Votes à 2 (les 2 d'accord) et à 3 (2 ou 3 d'accord), fenêtres de 1 et 3 bougies, avec le stop / l'objectif
    du meilleur membre et quelques R:R."""
    out = []
    for size in (2, 3):
        for combo in itertools.combinations(range(len(members)), size):
            ms = [members[i] for i in combo]
            mem = [{"signal": m["signal"], "filter": m.get("filter", "none")} for m in ms]
            for k in sorted({2, size}):
                for w in (1, 3, 5):
                    sig = {"type": "vote", "name": f"CONSEIL {k}/{size}", "members": mem, "min": k, "window": w}
                    for rr in sorted({ms[0]["risk"].get("rr"), 1.5, 2.0, 3.0}, key=lambda x: (x is None, x or 0)):
                        risk = {**ms[0]["risk"], "rr": rr}
                        if rr is None and risk.get("management") == "breakeven":
                            continue
                        out.append({"signal": sig, "filter": "none", "risk": risk})
    return out


def run_conseil(ev, cfg, journal, rules, findings, ingredients=()):
    """Construit et note les votes sur la période de recherche. Renvoie des Findings (validés plus tard)."""
    from .agents import Finding
    from .evaluator import score
    members = members_of(findings, list(ingredients))
    journal.log(TAG, f"Je fais voter les {len(members)} meilleures stratégies différentes de cette case")
    if len(members) < 2:
        journal.log(TAG, "Pas assez de stratégies différentes pour faire voter un conseil.")
        return []
    cands = proposals(members)
    found = []
    for c, res in ev.evaluate(cands, "is"):
        sc = score(res, rules.min_trades_is)
        if math.isfinite(sc):
            found.append(Finding(c, res, sc, TAG))
    found.sort(key=lambda f: f.score, reverse=True)
    best = found[:12]
    if best:
        journal.log(TAG, f"{len(cands)} votes essayés, {len(found)} tiennent la route en recherche ; je présente les "
                         f"{len(best)} meilleurs. N°1 : {describe_vote(best[0].candidate['signal'])[:120]}")
    return best


def to_json(sig: dict) -> str:
    return json.dumps(sig, ensure_ascii=False)
