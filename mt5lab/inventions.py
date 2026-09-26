"""Invention de nouvelles stratégies par les agents.

Une stratégie inventée est une RÈGLE :
    déclencheur (une condition qui devient vraie) ET filtres (conditions qui doivent rester vraies)
    ex. « RSI(14)-50 passe au-dessus de -18  ET  ADX(14) > 23  ET  prix > EMA(50) de 0.4 ATR »
Les ventes sont le miroir exact des achats (les indicateurs directionnels sont inversés).

Les seuils ne sont pas inventés au hasard : ils sont tirés des quantiles des indicateurs sur le marché observé
(l'agent « regarde le marché bouger »). Chaque agent fait évoluer une population de règles sur une 1re partie
de l'historique ; son chef d'équipe confirme (ou non) sur une 2e partie que l'agent n'a pas utilisée ;
la validation finale se fait ensuite sur la période hors-échantillon que personne n'a vue.
"""
from __future__ import annotations

import copy
import math
import random

import numpy as np
import pandas as pd

from . import indicators as ind

# nom -> (fonction(df, n) -> Series, directionnel ?, valeurs possibles de n, libellé)
FEATURES: dict[str, tuple] = {}


def feature(name, directional, ns, label):
    def deco(fn):
        FEATURES[name] = (fn, directional, ns, label)
        return fn
    return deco


def _atr(df):
    return ind.atr(df, 14).replace(0, np.nan)


@feature("dist_ema", True, [20, 50, 100, 200], "écart prix/EMA({n}) en ATR")
def f_dist_ema(df, n):
    return (df["close"] - ind.ema(df["close"], n)) / _atr(df)


@feature("ema_slope", True, [20, 50, 100], "pente EMA({n}) en ATR")
def f_ema_slope(df, n):
    return ind.ema(df["close"], n).diff(3) / _atr(df)


@feature("rsi", True, [2, 7, 14, 21], "RSI({n})-50")
def f_rsi(df, n):
    return ind.rsi(df["close"], n) - 50


@feature("stoch", True, [5, 9, 14, 21], "Stochastique({n})-50")
def f_stoch(df, n):
    return ind.stochastic(df, n, 3)[0] - 50


@feature("cci", True, [14, 20], "CCI({n})/100")
def f_cci(df, n):
    return ind.cci(df, n) / 100


@feature("bb_b", True, [20, 50], "%b Bollinger({n})-0.5")
def f_bb(df, n):
    lo, _, up = ind.bollinger(df["close"], n, 2.0)
    return (df["close"] - lo) / (up - lo).replace(0, np.nan) - 0.5


@feature("zscore", True, [20, 50], "Z-score({n})")
def f_z(df, n):
    return ind.zscore(df["close"], n)


@feature("macd_h", True, [12], "histogramme MACD en ATR")
def f_macd(df, n):
    return ind.macd(df["close"], n, n * 2 + 2, 9)[2] / _atr(df)


@feature("momentum", True, [5, 10, 20], "variation sur {n} bougies en ATR")
def f_mom(df, n):
    return (df["close"] - df["close"].shift(n)) / _atr(df)


@feature("don_pos", True, [10, 20, 55], "position dans le canal Donchian({n})")
def f_don(df, n):
    lo, _, up = ind.donchian(df, n)
    return (df["close"] - lo) / (up - lo).replace(0, np.nan) - 0.5


@feature("supertrend", True, [10], "direction Supertrend({n})")
def f_st(df, n):
    return ind.supertrend(df, n, 3.0)


@feature("smc_trend", True, [3, 5], "structure SMC (BOS/CHoCH, swings {n})")
def f_smc(df, n):
    from .strategies_smc import structure
    return pd.Series(structure(df, n)["trend"], index=df.index)


@feature("body", True, [1], "corps de la bougie en ATR")
def f_body(df, n):
    return (df["close"] - df["open"]) / _atr(df)


@feature("wick", True, [1], "mèche basse - mèche haute en ATR")
def f_wick(df, n):
    lower = df[["open", "close"]].min(axis=1) - df["low"]
    upper = df["high"] - df[["open", "close"]].max(axis=1)
    return (lower - upper) / _atr(df)


@feature("ha", True, [1], "couleur Heikin Ashi")
def f_ha(df, n):
    ha = ind.heikin_ashi(df)
    return np.sign(ha["close"] - ha["open"])


@feature("adx", False, [14], "ADX({n})")
def f_adx(df, n):
    return ind.adx(df, n)[0]


@feature("vol_ratio", False, [14], "ATR / ATR médian (volatilité relative)")
def f_vol(df, n):
    a = ind.atr(df, n)
    return a / a.rolling(200, min_periods=50).median()


@feature("chop", False, [14], "Choppiness({n})")
def f_chop(df, n):
    return ind.choppiness(df, n)


@feature("hour", False, [0], "heure du serveur")
def f_hour(df, n):
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(df.index.hour, index=df.index, dtype=float)
    return pd.Series(np.nan, index=df.index)


# ---------------------------------------------------- empreintes des algorithmes institutionnels (équipe C)
def _dt(df):
    return isinstance(df.index, pd.DatetimeIndex)


def _nan(df):
    return pd.Series(np.nan, index=df.index)


@feature("dow", False, [0], "jour de la semaine (0 = lundi)")
def f_dow(df, n):
    return pd.Series(df.index.dayofweek, index=df.index, dtype=float) if _dt(df) else _nan(df)


@feature("minute", False, [0], "minute de l'heure")
def f_minute(df, n):
    return pd.Series(df.index.minute, index=df.index, dtype=float) if _dt(df) else _nan(df)


@feature("month_end", False, [0], "jours avant la fin du mois")
def f_month_end(df, n):
    if not _dt(df):
        return _nan(df)
    return pd.Series((df.index + pd.offsets.MonthEnd(0) - df.index.normalize()).days, index=df.index, dtype=float)


@feature("round_dist", True, [1], "écart au niveau rond le plus proche, en ATR")
def f_round(df, n):
    c = df["close"]
    step = 10.0 ** np.round(np.log10((c * 0.002).clip(lower=1e-12)))
    return (c - np.round(c / step) * step) / _atr(df)


def _prev_day(df):
    day = df.index.normalize()
    d = df.groupby(day).agg(high=("high", "max"), low=("low", "min")).shift(1)
    return d["high"].reindex(day).to_numpy(), d["low"].reindex(day).to_numpy()


@feature("prev_day_pos", True, [1], "position dans le range de la veille (-0.5 = plus bas, +0.5 = plus haut)")
def f_prev_day(df, n):
    if not _dt(df):
        return _nan(df)
    hi, lo = _prev_day(df)
    return pd.Series((df["close"].to_numpy() - lo) / np.where(hi - lo > 0, hi - lo, np.nan) - 0.5, index=df.index)


@feature("sweep", True, [3, 6, 12], "chasse aux stops récente sur {n} bougies (+1 = sous un plus bas, -1 = au-dessus d'un plus haut)")
def f_sweep(df, n):
    from .strategies_smc import structure
    st = structure(df, 3)
    lo, hi = st["last_sl"], st["last_sh"]
    l, h, c = df["low"].to_numpy(), df["high"].to_numpy(), df["close"].to_numpy()
    up = pd.Series(((l < lo) & (c > lo)).astype(float), index=df.index).rolling(n, min_periods=1).max()
    dn = pd.Series(((h > hi) & (c < hi)).astype(float), index=df.index).rolling(n, min_periods=1).max()
    return up - dn


@feature("session_move", True, [0, 7, 13], "mouvement depuis l'ouverture de la session de {n}h, en ATR")
def f_session(df, n):
    if not _dt(df):
        return _nan(df)
    day = df.index.normalize()
    started = df.index.hour >= n
    first_open = df["open"].where(started).groupby(day).transform("first")
    return (df["close"] - first_open).where(started) / _atr(df)


@feature("vwap_dist", True, [1], "écart au VWAP du jour, en ATR")
def f_vwap(df, n):
    if not _dt(df):
        return _nan(df)
    day = df.index.normalize()
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].replace(0, 1)
    vwap = (tp * vol).groupby(day).cumsum() / vol.groupby(day).cumsum()
    return (df["close"] - vwap) / _atr(df)


@feature("vol_spike", False, [20, 50], "volume / volume moyen sur {n} bougies")
def f_volspike(df, n):
    v = df["volume"].astype(float)
    return v / v.rolling(n, min_periods=n).mean().replace(0, np.nan)


@feature("asia_pos", True, [7], "position par rapport au range asiatique (0h-{n}h)")
def f_asia(df, n):
    if not _dt(df):
        return _nan(df)
    day = df.index.normalize()
    asia = df.index.hour < n
    hi = df["high"].where(asia).groupby(day).transform("max")
    lo = df["low"].where(asia).groupby(day).transform("min")
    return ((df["close"] - lo) / (hi - lo).replace(0, np.nan) - 0.5).where(~asia)


@feature("htf_trend", True, [60, 240, 1440], "tendance du timeframe supérieur ({n} min, EMA 50 : +1 haussière, -1 baissière)")
def f_htf(df, n):
    from .strategies import bar_minutes, htf_direction
    if not bar_minutes(df) < n:
        return _nan(df)
    return htf_direction(df, n, "ema50")


# caractéristiques jouées en fenêtres (entre a et b) : bornes et largeurs possibles
BETWEEN = {"hour": (0, 24, [2, 3, 4, 6, 8], "h"), "dow": (0, 5, [1, 2, 3], " (jour)"), "minute": (0, 60, [5, 10, 15, 30], " min")}
BANK_FEATURES = ["sweep", "prev_day_pos", "round_dist", "session_move", "vwap_dist", "vol_spike", "asia_pos",
                 "month_end", "dow", "minute", "hour"]

_CACHE: dict = {}


def feat(df: pd.DataFrame, name: str, n: int) -> pd.Series:
    key = (id(df), len(df), df.index[0], df.index[-1], name, n)
    if key not in _CACHE:
        if len(_CACHE) > 600:
            _CACHE.clear()
        _CACHE[key] = FEATURES[name][0](df, n).astype(float)
    return _CACHE[key]


def _cond(df, c, side: int) -> pd.Series:
    x = feat(df, c["f"], c["n"])
    if c["op"] == "between":  # heures : non directionnel
        lo, hi = c["v"]
        return (x >= lo) & (x < hi)
    if side < 0 and FEATURES[c["f"]][1]:
        x = -x  # miroir pour les ventes
    return (x > c["v"]) if c["op"] == ">" else (x < c["v"])


def rule_signal(df: pd.DataFrame, spec: dict) -> pd.Series:
    out = np.zeros(len(df), dtype=np.int8)
    for side in (1, -1):
        if side < 0 and not spec.get("mirror", True):
            continue
        trig = _cond(df, spec["trigger"], side).fillna(False)
        edge = trig & ~trig.shift(1, fill_value=False)  # la condition DEVIENT vraie
        ok = edge
        for c in spec.get("filters", []):
            ok = ok & _cond(df, c, side).fillna(False)
        out = np.where((out == 0) & ok.to_numpy(), side, out)
    return pd.Series(out.astype(np.int8), index=df.index)


def _cond_text(c):
    lab = FEATURES[c["f"]][3].format(n=c["n"])
    if c["op"] == "between":
        unit = BETWEEN.get(c["f"], (0, 24, [], "h"))[3]
        return f"{lab} entre {c['v'][0]}{unit} et {c['v'][1]}{unit}"
    return f"{lab} {c['op']} {c['v']:g}"


def describe_rule(spec: dict) -> str:
    txt = f"{spec.get('name', 'INVENTION')} : QUAND {_cond_text(spec['trigger'])}"
    if spec.get("filters"):
        txt += " ET " + " ET ".join(_cond_text(c) for c in spec["filters"])
    return txt + (" (miroir en vente)" if spec.get("mirror", True) else " (achats seulement)")


# ============================================================================ évolution
AGENT_POOLS = {
    1: ["dist_ema", "ema_slope", "supertrend", "adx", "don_pos", "smc_trend"],
    2: ["rsi", "stoch", "bb_b", "zscore", "cci", "vol_ratio", "adx"],
    3: ["don_pos", "vol_ratio", "hour", "body", "adx", "chop"],
    4: ["macd_h", "momentum", "rsi", "ema_slope", "adx"],
    5: ["body", "wick", "ha", "smc_trend", "don_pos", "dist_ema"],
    6: list(FEATURES),
    7: list(FEATURES),
    8: list(FEATURES),
    9: list(FEATURES),
    10: list(FEATURES),
    # équipe D : inventeurs qui partent des failles trouvées par l'équipe C
    16: ["sweep", "prev_day_pos", "don_pos", "rsi", "body", "wick"],
    17: ["session_move", "hour", "dow", "minute", "momentum", "adx"],
    18: ["round_dist", "prev_day_pos", "stoch", "bb_b", "zscore"],
    19: ["vwap_dist", "vol_spike", "zscore", "macd_h", "rsi"],
    20: list(FEATURES),
}


def simplify(spec: dict) -> dict:
    """Supprime les conditions redondantes (même indicateur, même sens : on garde la plus stricte)."""
    trig = spec["trigger"]
    kept: dict = {}
    for c in spec["filters"]:
        if c["op"] == "between":
            key = (c["f"], "between")
            kept.setdefault(key, c)
            continue
        key = (c["f"], c["n"], c["op"])
        if key == (trig["f"], trig["n"], trig["op"]):
            continue  # déjà couvert par le déclencheur
        old = kept.get(key)
        if old is None or (c["op"] == ">" and c["v"] > old["v"]) or (c["op"] == "<" and c["v"] < old["v"]):
            kept[key] = c
    spec["filters"] = list(kept.values())[:3]
    return spec


class Inventor:
    """Fait évoluer des règles sur les données d'un agent."""

    def __init__(self, agent_no: int, agent_tag: str, df: pd.DataFrame, rng: random.Random, bias=None):
        self.bias = [f for f in (bias or []) if f in FEATURES]  # indicateurs recommandés par le Directeur
        self.no = agent_no
        self.tag = agent_tag
        self.df = df
        self.rng = rng
        self.pool = AGENT_POOLS.get(agent_no, list(FEATURES))
        self.counter = 0
        self._q: dict = {}

    def _quantiles(self, f, n):
        k = (f, n)
        if k not in self._q:
            x = feat(self.df, f, n).dropna()
            if FEATURES[f][1]:
                x = pd.concat([x, -x])  # seuils symétriques pour le miroir achat/vente
            self._q[k] = [float(f"{float(v):.3g}") for v in np.nanquantile(x, [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8,
                                                                             0.9, 0.95])] if len(x) else [0.0]
        return self._q[k]

    def random_cond(self, allow_hour=True) -> dict:
        pool = [f for f in self.pool if allow_hour or f not in BETWEEN] or ["momentum"]
        favored = [f for f in self.bias if allow_hour or f not in BETWEEN]
        if favored and self.rng.random() < 0.5:  # une fois sur deux, une piste du Directeur
            pool = favored
        f = self.rng.choice(pool)
        n = self.rng.choice(FEATURES[f][2])
        if f in BETWEEN:
            lo, hi, widths, _ = BETWEEN[f]
            a = self.rng.randrange(lo, hi - 1)
            return {"f": f, "n": 0, "op": "between", "v": [a, min(hi, a + self.rng.choice(widths))]}
        return {"f": f, "n": n, "op": self.rng.choice([">", "<"]), "v": self.rng.choice(self._quantiles(f, n))}

    def random_spec(self) -> dict:
        trig = self.random_cond(allow_hour=False)
        filters = [self.random_cond() for _ in range(self.rng.choice([1, 1, 2, 2, 3]))]
        return simplify({"type": "rule", "trigger": trig, "filters": filters, "mirror": self.rng.random() < 0.85})

    def mutate(self, spec: dict) -> dict:
        s = copy.deepcopy(spec)
        r = self.rng.random()
        conds = [s["trigger"]] + s["filters"]
        if r < 0.45:  # ajuste un seuil (quantile voisin)
            c = self.rng.choice(conds)
            if c["op"] == "between":
                lo, hi = BETWEEN.get(c["f"], (0, 24))[:2]
                a = max(lo, min(hi - 2, c["v"][0] + self.rng.choice([-1, 1])))
                c["v"] = [a, min(hi, a + max(1, c["v"][1] - c["v"][0] + self.rng.choice([-1, 0, 1])))]
            else:
                q = self._quantiles(c["f"], c["n"])
                i = min(range(len(q)), key=lambda k: abs(q[k] - c["v"]))
                c["v"] = q[max(0, min(len(q) - 1, i + self.rng.choice([-1, 1])))]
        elif r < 0.6:  # change la période
            c = self.rng.choice(conds)
            c["n"] = self.rng.choice(FEATURES[c["f"]][2]) if c["op"] != "between" else 0
            if c["op"] != "between":
                c["v"] = self.rng.choice(self._quantiles(c["f"], c["n"]))
        elif r < 0.75 and len(s["filters"]) < 3:
            s["filters"].append(self.random_cond())
        elif r < 0.85 and s["filters"]:
            s["filters"].pop(self.rng.randrange(len(s["filters"])))
        elif r < 0.93:
            c = self.rng.choice(conds)
            if c["op"] != "between":
                c["op"] = "<" if c["op"] == ">" else ">"
        else:
            s["trigger"] = self.random_cond(allow_hour=False)
        return simplify(s)

    def crossover(self, a: dict, b: dict) -> dict:
        s = copy.deepcopy(a)
        pool = b["filters"] + [b["trigger"]]
        if pool:
            s["filters"] = (s["filters"][:1] + [copy.deepcopy(self.rng.choice(pool))])[:3]
        return simplify(s)

    def name(self, spec: dict) -> dict:
        self.counter += 1
        spec = copy.deepcopy(spec)
        spec["name"] = f"INVENTION A{self.no}-{self.counter}"
        return spec


INVENTION_RISKS = [
    {"sl_mode": "atr", "sl_value": 1.5, "rr": 1.5, "management": "none", "max_hold": 200, "direction": "both"},
    {"sl_mode": "atr", "sl_value": 1.5, "rr": 2.0, "management": "none", "max_hold": 200, "direction": "both"},
    {"sl_mode": "atr", "sl_value": 2.0, "rr": 3.0, "management": "none", "max_hold": 200, "direction": "both"},
    {"sl_mode": "atr", "sl_value": 1.5, "rr": None, "management": "none", "max_hold": 200, "direction": "both"},
]


def _fitness(res: dict, min_trades: int) -> float:
    if res["trades"] < min_trades:
        return -math.inf
    return res["sharpe"]


def invent(inventor: Inventor, evaluator, part_train: str, part_check: str, generations: int = 6, pop: int = 40,
           attempts: int = 3, min_trades: int = 25, seeds: list[dict] | None = None, log=print) -> dict:
    """Un agent invente ; son chef confirme sur part_check. Renvoie le meilleur candidat et le verdict du chef."""
    best_overall = None
    for attempt in range(1, attempts + 1):
        population = [copy.deepcopy(s) for s in (seeds or [])][: pop // 2]
        population += [inventor.random_spec() for _ in range(pop - len(population))]
        scored: list[tuple[float, dict, dict]] = []
        for gen in range(generations):
            cands = [{"signal": s, "filter": "none", "risk": r} for s in population for r in INVENTION_RISKS]
            results = evaluator.evaluate(cands, part_train)
            best_per_spec: dict[str, tuple] = {}
            for c, res in results:
                k = repr(sorted(c["signal"].items()))
                f = _fitness(res, min_trades)
                if k not in best_per_spec or f > best_per_spec[k][0]:
                    best_per_spec[k] = (f, c, res)
            scored = sorted(best_per_spec.values(), key=lambda t: t[0], reverse=True)
            elite = [t[1]["signal"] for t in scored[: max(4, pop // 5)] if math.isfinite(t[0])]
            if not elite:
                elite = [inventor.random_spec() for _ in range(4)]
            children = []
            while len(children) < pop - len(elite):
                if len(elite) > 1 and inventor.rng.random() < 0.3:
                    children.append(inventor.mutate(inventor.crossover(*inventor.rng.sample(elite, 2))))
                else:
                    children.append(inventor.mutate(inventor.rng.choice(elite)))
            population = elite + children
        # le chef vérifie les 5 meilleures inventions sur une période que l'agent n'a pas utilisée
        finalists = [t for t in scored[:5] if math.isfinite(t[0])]
        if not finalists:
            continue
        checks = evaluator.evaluate([t[1] for t in finalists], part_check)
        chk = {repr(sorted(c["signal"].items())): res for c, res in checks}
        for f, cand, res_train in finalists:
            res_chk = chk.get(repr(sorted(cand["signal"].items())))
            confirmed = bool(res_chk and res_chk["trades"] >= max(10, min_trades // 3) and res_chk["avg_r"] > 0
                             and res_chk["profit_factor"] >= 1.1 and res_chk["sharpe"] >= 1.0)
            entry = {"candidate": cand, "train": res_train, "check": res_chk, "confirmed": confirmed,
                     "attempt": attempt}
            if confirmed:
                entry["candidate"] = {**cand, "signal": inventor.name(cand["signal"])}
                log(inventor.tag, f"invention confirmée par le chef (essai {attempt}) : "
                                  f"{res_chk['trades']} trades, {res_chk['avg_r']:+.2f}R/trade, PF {res_chk['profit_factor']:.2f}")
                return entry
            if best_overall is None or (res_chk and best_overall["check"] and
                                        res_chk["sharpe"] > best_overall["check"]["sharpe"]):
                best_overall = entry
        log(inventor.tag, f"essai {attempt} : le chef refuse mes inventions (pas assez solides sur sa période), je recommence")
    if best_overall:
        best_overall["candidate"] = {**best_overall["candidate"],
                                     "signal": inventor.name(best_overall["candidate"]["signal"])}
    return best_overall or {}
