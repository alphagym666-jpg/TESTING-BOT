"""Évaluation parallèle des candidats (signal + filtre + gestion du risque).

Un « candidat » est un dict JSON-sérialisable :
    {
      "signal": {"type": "single", "name": "ema_cross", "params": {"fast": 9, "slow": 21}}
                | {"type": "combo", "a": <single>, "b": <single>, "mode": "confirm", "window": 3},
      "filter": "trend_ema200",
      "risk": {"sl_mode": "atr", "sl_value": 1.5, "rr": 2.0, "management": "none", "max_hold": 200, "direction": "both"}
    }
"""
from __future__ import annotations

import json
import math
import os
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from .backtest import EMPTY, RiskConfig, run_backtest
from .strategies import REGISTRY, apply_filter, combine

_STATE: dict = {}


def signal_key(sig: dict) -> str:
    return json.dumps(sig, sort_keys=True)


def candidate_key(c: dict) -> str:
    return json.dumps(c, sort_keys=True)


def describe(c: dict) -> str:
    def one(s):
        p = ",".join(f"{k}={v}" for k, v in s["params"].items())
        return f"{s['name']}({p})"

    s = c["signal"]
    txt = one(s) if s["type"] == "single" else f"{one(s['a'])} {s['mode'].upper()}[{s['window']}] {one(s['b'])}"
    if c.get("filter", "none") != "none":
        txt += f" + filtre {c['filter']}"
    return txt


def compute_signal(df: pd.DataFrame, sig: dict) -> pd.Series:
    if sig["type"] == "single":
        return REGISTRY[sig["name"]].func(df, **sig["params"])
    a = compute_signal(df, sig["a"])
    b = compute_signal(df, sig["b"])
    return combine(a, b, sig["mode"], sig.get("window", 3))


def _init(datasets: dict):
    _STATE["data"] = datasets
    _STATE["cache"] = {}


def _signals_for(part: str, sig: dict, flt: str) -> pd.Series:
    cache = _STATE["cache"]
    key = (part, signal_key(sig), flt)
    if key not in cache:
        df = _STATE["data"][part]["df"]
        cache[key] = apply_filter(df, compute_signal(df, sig), flt)
        if len(cache) > 4000:  # garde la mémoire sous contrôle
            cache.clear()
    return cache[key]


def _eval_group(args):
    """Évalue un groupe de candidats partageant le même signal+filtre (le signal n'est calculé qu'une fois)."""
    part, sig, flt, risks = args
    d = _STATE["data"][part]
    out = []
    try:
        s = _signals_for(part, sig, flt)
    except Exception as exc:  # paramètres invalides -> résultat vide
        return [({"signal": sig, "filter": flt, "risk": r}, EMPTY.to_dict(), str(exc)) for r in risks]
    for r in risks:
        res = run_backtest(d["df"], s, RiskConfig(**r), cost=d["cost"], risk_pct=d["risk_pct"])
        out.append(({"signal": sig, "filter": flt, "risk": r}, res.to_dict(), None))
    return out


def score(res: dict, min_trades: int) -> float:
    """Score = t-stat de l'espérance en R (récompense l'edge ET le nombre de trades), pénalisé si trop peu de trades."""
    if res["trades"] < min_trades:
        return -math.inf
    penalty = 1.0 + max(0.0, res["max_dd_r"] - 15) / 30  # drawdown profond = pénalité
    return res["sharpe"] / penalty


class Evaluator:
    """Pool de processus partagé par tous les agents."""

    def __init__(self, df_is: pd.DataFrame, df_oos: pd.DataFrame, cost: float, risk_pct: float = 1.0,
                 workers: int | None = None):
        self.datasets = {
            "is": {"df": df_is, "cost": cost, "risk_pct": risk_pct},
            "oos": {"df": df_oos, "cost": cost, "risk_pct": risk_pct},
        }
        self.workers = workers or max(1, (os.cpu_count() or 2))
        self.pool = None
        self.seen: dict[str, dict] = {}  # mémo global : aucun agent ne reteste le même candidat
        self.n_evals = 0

    def __enter__(self):
        if self.workers > 1:
            self.pool = ProcessPoolExecutor(self.workers, initializer=_init, initargs=(self.datasets,))
        else:
            _init(self.datasets)
        return self

    def __exit__(self, *exc):
        if self.pool:
            self.pool.shutdown(cancel_futures=True)

    def evaluate(self, candidates: list[dict], part: str = "is") -> list[tuple[dict, dict]]:
        """Renvoie [(candidat, résultat)] ; les candidats déjà vus sont servis depuis le mémo."""
        todo, results = {}, []
        for c in candidates:
            k = part + candidate_key(c)
            if k in self.seen:
                results.append((c, self.seen[k]))
            else:
                g = (signal_key(c["signal"]), c.get("filter", "none"))
                todo.setdefault(g, (c["signal"], c.get("filter", "none"), {}))[2][candidate_key(c["risk"])] = c["risk"]
        groups = [(part, sig, flt, list(risks.values())) for sig, flt, risks in todo.values()]
        if not groups:
            return results
        mapper = self.pool.map(_eval_group, groups, chunksize=max(1, len(groups) // (self.workers * 4) or 1)) \
            if self.pool else map(_eval_group, groups)
        for batch in mapper:
            for c, res, _err in batch:
                self.seen[part + candidate_key(c)] = res
                results.append((c, res))
                self.n_evals += 1
        return results
