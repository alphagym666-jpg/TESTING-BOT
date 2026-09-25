"""Catalogue de stratégies + filtres + combinaisons.

Chaque stratégie renvoie une Series d'entiers :
    +1 = signal d'achat à la clôture de la bougie (entrée à l'ouverture suivante)
    -1 = signal de vente
     0 = rien

Les stratégies sont regroupées par famille (tendance, retour à la moyenne, cassure,
momentum, price action, session). Chaque entrée possède une grille de paramètres que
les agents explorent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass(frozen=True)
class StrategyDef:
    name: str
    family: str
    func: Callable[..., pd.Series]
    grid: dict = field(default_factory=dict)
    description: str = ""


REGISTRY: dict[str, StrategyDef] = {}


def strategy(name: str, family: str, grid: dict, description: str = ""):
    def deco(fn):
        REGISTRY[name] = StrategyDef(name, family, fn, grid, description)
        return fn

    return deco


def _sig(long_cond: pd.Series, short_cond: pd.Series) -> pd.Series:
    long_cond = long_cond.fillna(False).astype(bool)
    short_cond = short_cond.fillna(False).astype(bool)
    out = np.where(long_cond, 1, np.where(short_cond, -1, 0))
    return pd.Series(out, index=long_cond.index, dtype=np.int8)


def _flip(direction: pd.Series) -> pd.Series:
    prev = direction.shift(1)
    return _sig((direction > 0) & (prev <= 0), (direction < 0) & (prev >= 0))


# ----------------------------------------------------------------------------- tendance
@strategy("sma_cross", "trend", {"fast": [5, 10, 20, 50], "slow": [20, 50, 100, 200]},
          "Croisement de moyennes mobiles simples")
def sma_cross(df, fast=10, slow=50):
    if fast >= slow:
        return pd.Series(0, index=df.index, dtype=np.int8)
    f, s = ind.sma(df["close"], fast), ind.sma(df["close"], slow)
    return _sig(ind.crossover(f, s), ind.crossunder(f, s))


@strategy("ema_cross", "trend", {"fast": [5, 9, 12, 21], "slow": [21, 50, 100, 200]},
          "Croisement de moyennes mobiles exponentielles")
def ema_cross(df, fast=9, slow=21):
    if fast >= slow:
        return pd.Series(0, index=df.index, dtype=np.int8)
    f, s = ind.ema(df["close"], fast), ind.ema(df["close"], slow)
    return _sig(ind.crossover(f, s), ind.crossunder(f, s))


@strategy("hma_turn", "trend", {"n": [9, 16, 21, 55]}, "Changement de pente de la Hull MA")
def hma_turn(df, n=21):
    h = ind.hma(df["close"], n)
    slope = np.sign(h.diff())
    return _flip(slope)


@strategy("triple_ema", "trend", {"a": [5, 8], "b": [13, 21], "c": [34, 55, 89]},
          "Alignement de 3 EMA (a>b>c)")
def triple_ema(df, a=8, b=21, c=55):
    ea, eb, ec = ind.ema(df["close"], a), ind.ema(df["close"], b), ind.ema(df["close"], c)
    d = pd.Series(np.where((ea > eb) & (eb > ec), 1, np.where((ea < eb) & (eb < ec), -1, 0)), index=df.index)
    return _flip(d)


@strategy("macd_cross", "trend", {"fast": [8, 12], "slow": [21, 26], "signal": [5, 9]},
          "Croisement MACD / ligne de signal")
def macd_cross(df, fast=12, slow=26, signal=9):
    line, sig, _ = ind.macd(df["close"], fast, slow, signal)
    return _sig(ind.crossover(line, sig), ind.crossunder(line, sig))


@strategy("macd_zero", "trend", {"fast": [8, 12], "slow": [21, 26]}, "MACD qui traverse zéro")
def macd_zero(df, fast=12, slow=26):
    line, _, _ = ind.macd(df["close"], fast, slow, 9)
    return _sig(ind.crossover(line, 0.0), ind.crossunder(line, 0.0))


@strategy("supertrend", "trend", {"n": [7, 10, 14], "mult": [2.0, 3.0, 4.0]}, "Retournement Supertrend")
def supertrend(df, n=10, mult=3.0):
    return _flip(ind.supertrend(df, n, mult))


@strategy("psar", "trend", {"step": [0.01, 0.02, 0.03], "max_step": [0.1, 0.2]}, "Retournement Parabolic SAR")
def psar(df, step=0.02, max_step=0.2):
    return _flip(ind.psar(df, step, max_step))


@strategy("adx_di", "trend", {"n": [10, 14, 20], "min_adx": [20, 25, 30]}, "Croisement DI+/DI- avec ADX fort")
def adx_di(df, n=14, min_adx=25):
    a, p, m = ind.adx(df, n)
    strong = a > min_adx
    return _sig(ind.crossover(p, m) & strong, ind.crossunder(p, m) & strong)


@strategy("ichimoku_tk", "trend", {"tenkan": [7, 9], "kijun": [22, 26]}, "Croisement Tenkan/Kijun au-dessus/sous le nuage")
def ichimoku_tk(df, tenkan=9, kijun=26):
    t, k, sa, sb = ind.ichimoku(df, tenkan, kijun, kijun * 2)
    top, bot = pd.concat([sa, sb], axis=1).max(axis=1), pd.concat([sa, sb], axis=1).min(axis=1)
    c = df["close"]
    return _sig(ind.crossover(t, k) & (c > top), ind.crossunder(t, k) & (c < bot))


@strategy("ichimoku_cloud", "trend", {"tenkan": [9], "kijun": [26, 30]}, "Cassure du nuage Ichimoku")
def ichimoku_cloud(df, tenkan=9, kijun=26):
    _, _, sa, sb = ind.ichimoku(df, tenkan, kijun, kijun * 2)
    top, bot = pd.concat([sa, sb], axis=1).max(axis=1), pd.concat([sa, sb], axis=1).min(axis=1)
    return _sig(ind.crossover(df["close"], top), ind.crossunder(df["close"], bot))


# ------------------------------------------------------------------ retour à la moyenne
@strategy("rsi_reversal", "mean_reversion", {"n": [7, 14, 21], "low": [20, 25, 30], "high": [70, 75, 80]},
          "RSI qui ressort des zones de survente/surachat")
def rsi_reversal(df, n=14, low=30, high=70):
    r = ind.rsi(df["close"], n)
    return _sig(ind.crossover(r, low), ind.crossunder(r, high))


@strategy("rsi2_connors", "mean_reversion", {"low": [5, 10], "high": [90, 95], "trend": [100, 200]},
          "RSI(2) de Larry Connors dans le sens de la tendance longue")
def rsi2_connors(df, low=10, high=90, trend=200):
    r = ind.rsi(df["close"], 2)
    t = ind.sma(df["close"], trend)
    return _sig((r < low) & (df["close"] > t), (r > high) & (df["close"] < t))


@strategy("bb_reversion", "mean_reversion", {"n": [14, 20, 30], "k": [1.5, 2.0, 2.5]},
          "Retour dans les bandes de Bollinger")
def bb_reversion(df, n=20, k=2.0):
    lo, _, up = ind.bollinger(df["close"], n, k)
    return _sig(ind.crossover(df["close"], lo), ind.crossunder(df["close"], up))


@strategy("keltner_reversion", "mean_reversion", {"n": [20], "mult": [1.5, 2.0, 2.5]}, "Retour dans le canal de Keltner")
def keltner_reversion(df, n=20, mult=2.0):
    lo, _, up = ind.keltner(df, n, mult)
    return _sig(ind.crossover(df["close"], lo), ind.crossunder(df["close"], up))


@strategy("stoch_reversal", "mean_reversion", {"k": [5, 14], "d": [3], "low": [20], "high": [80]},
          "Croisement stochastique en zone extrême")
def stoch_reversal(df, k=14, d=3, low=20, high=80):
    pk, pd_ = ind.stochastic(df, k, d)
    return _sig(ind.crossover(pk, pd_) & (pk < low), ind.crossunder(pk, pd_) & (pk > high))


@strategy("williams_r", "mean_reversion", {"n": [10, 14, 21]}, "Williams %R sort des extrêmes")
def williams_r(df, n=14):
    w = ind.williams_r(df, n)
    return _sig(ind.crossover(w, -80.0), ind.crossunder(w, -20.0))


@strategy("cci_reversal", "mean_reversion", {"n": [14, 20], "lvl": [100, 150, 200]}, "CCI revient de l'extrême")
def cci_reversal(df, n=20, lvl=100):
    c = ind.cci(df, n)
    return _sig(ind.crossover(c, -lvl), ind.crossunder(c, lvl))


@strategy("zscore_reversion", "mean_reversion", {"n": [20, 50], "z": [1.5, 2.0, 2.5]}, "Z-score du prix extrême")
def zscore_reversion(df, n=20, z=2.0):
    zs = ind.zscore(df["close"], n)
    return _sig(ind.crossover(zs, -z), ind.crossunder(zs, z))


# ------------------------------------------------------------------------------ cassure
@strategy("donchian_breakout", "breakout", {"n": [20, 55]}, "Turtle : cassure du plus haut/bas de N bougies")
def donchian_breakout(df, n=20):
    lo, _, up = ind.donchian(df, n)
    return _sig(ind.crossover(df["close"], up), ind.crossunder(df["close"], lo))


@strategy("bb_breakout", "breakout", {"n": [20], "k": [2.0, 2.5]}, "Clôture hors des bandes de Bollinger")
def bb_breakout(df, n=20, k=2.0):
    lo, _, up = ind.bollinger(df["close"], n, k)
    return _sig(ind.crossover(df["close"], up), ind.crossunder(df["close"], lo))


@strategy("keltner_breakout", "breakout", {"n": [20], "mult": [1.5, 2.0]}, "Clôture hors du canal de Keltner")
def keltner_breakout(df, n=20, mult=2.0):
    lo, _, up = ind.keltner(df, n, mult)
    return _sig(ind.crossover(df["close"], up), ind.crossunder(df["close"], lo))


@strategy("squeeze_breakout", "breakout", {"n": [20], "k": [2.0], "mult": [1.5]},
          "TTM Squeeze : Bollinger dans Keltner puis explosion")
def squeeze_breakout(df, n=20, k=2.0, mult=1.5):
    blo, _, bup = ind.bollinger(df["close"], n, k)
    klo, kmid, kup = ind.keltner(df, n, mult)
    squeeze = (bup < kup) & (blo > klo)
    released = squeeze.shift(1, fill_value=False) & ~squeeze
    return _sig(released & (df["close"] > kmid), released & (df["close"] < kmid))


@strategy("inside_bar", "breakout", {"confirm": [1]}, "Cassure d'une inside bar")
def inside_bar(df, confirm=1):
    h, l = df["high"], df["low"]
    inside = (h.shift(1) < h.shift(2)) & (l.shift(1) > l.shift(2))
    return _sig(inside & (df["close"] > h.shift(1)), inside & (df["close"] < l.shift(1)))


@strategy("range_breakout", "breakout", {"n": [5, 10, 20], "max_width_atr": [1.5, 3.0]},
          "Cassure d'un range serré (largeur < X ATR)")
def range_breakout(df, n=10, max_width_atr=2.0):
    lo, _, up = ind.donchian(df, n)
    tight = (up - lo) < max_width_atr * ind.atr(df, 14)
    return _sig(ind.crossover(df["close"], up) & tight, ind.crossunder(df["close"], lo) & tight)


@strategy("session_breakout", "session", {"start": [0, 7], "length": [3, 6]},
          "Cassure du range d'une session (ex. Asie -> Londres). Nécessite des données intraday.")
def session_breakout(df, start=0, length=6):
    if not isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(0, index=df.index, dtype=np.int8)
    hours = df.index.hour
    day = df.index.normalize()
    in_sess = (hours >= start) & (hours < start + length)
    hi = df["high"].where(in_sess).groupby(day).transform("max")
    lo = df["low"].where(in_sess).groupby(day).transform("min")
    after = hours >= start + length
    c = df["close"]
    long_b = after & (c > hi) & (c.shift(1) <= hi)
    short_b = after & (c < lo) & (c.shift(1) >= lo)
    # un seul trade par jour et par direction
    first_long = long_b & (long_b.astype(int).groupby(day).cumsum() == 1)
    first_short = short_b & (short_b.astype(int).groupby(day).cumsum() == 1)
    return _sig(first_long, first_short)


# ----------------------------------------------------------------------------- momentum
@strategy("roc_momentum", "momentum", {"n": [5, 10, 20], "thr": [0.5, 1.0, 2.0]}, "Rate of change franchit un seuil")
def roc_momentum(df, n=10, thr=1.0):
    r = ind.roc(df["close"], n)
    return _sig(ind.crossover(r, thr), ind.crossunder(r, -thr))


@strategy("rsi_50", "momentum", {"n": [9, 14, 21]}, "RSI qui traverse 50")
def rsi_50(df, n=14):
    r = ind.rsi(df["close"], n)
    return _sig(ind.crossover(r, 50.0), ind.crossunder(r, 50.0))


@strategy("macd_hist_turn", "momentum", {"fast": [12], "slow": [26], "signal": [9]}, "L'histogramme MACD change de pente")
def macd_hist_turn(df, fast=12, slow=26, signal=9):
    _, _, h = ind.macd(df["close"], fast, slow, signal)
    return _sig((h < 0) & (h.diff() > 0) & (h.diff().shift(1) <= 0), (h > 0) & (h.diff() < 0) & (h.diff().shift(1) >= 0))


@strategy("vwap_cross", "momentum", {"n": [20, 50]}, "Prix croise le VWAP glissant")
def vwap_cross(df, n=20):
    v = ind.vwap_rolling(df, n)
    return _sig(ind.crossover(df["close"], v), ind.crossunder(df["close"], v))


# ------------------------------------------------------------------------- price action
@strategy("engulfing", "price_action", {"min_body_atr": [0.3, 0.6]}, "Avalement haussier / baissier")
def engulfing(df, min_body_atr=0.5):
    o, c = df["open"], df["close"]
    body = (c - o).abs()
    big = body > min_body_atr * ind.atr(df, 14)
    bull = (c.shift(1) < o.shift(1)) & (c > o) & (c >= o.shift(1)) & (o <= c.shift(1))
    bear = (c.shift(1) > o.shift(1)) & (c < o) & (c <= o.shift(1)) & (o >= c.shift(1))
    return _sig(bull & big, bear & big)


@strategy("pin_bar", "price_action", {"wick_ratio": [2.0, 3.0]}, "Pin bar / marteau / étoile filante")
def pin_bar(df, wick_ratio=2.5):
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs().replace(0, 1e-12)
    upper = h - pd.concat([o, c], axis=1).max(axis=1)
    lower = pd.concat([o, c], axis=1).min(axis=1) - l
    return _sig((lower > wick_ratio * body) & (upper < body), (upper > wick_ratio * body) & (lower < body))


@strategy("three_bar_reversal", "price_action", {"n": [3]}, "Retournement sur 3 bougies")
def three_bar_reversal(df, n=3):
    c = df["close"]
    down = (c.shift(1) < c.shift(2)) & (c.shift(2) < c.shift(3))
    up = (c.shift(1) > c.shift(2)) & (c.shift(2) > c.shift(3))
    return _sig(down & (c > df["high"].shift(1)), up & (c < df["low"].shift(1)))


@strategy("fractal_breakout", "price_action", {"n": [2, 3]}, "Cassure du dernier fractal de Bill Williams")
def fractal_breakout(df, n=2):
    h, l = df["high"], df["low"]
    w = 2 * n + 1
    is_top = h.rolling(w, center=True).max() == h
    is_bot = l.rolling(w, center=True).min() == l
    # un fractal n'est confirmé que n bougies plus tard -> shift(n) pour éviter le look-ahead
    last_top = h.where(is_top).shift(n).ffill()
    last_bot = l.where(is_bot).shift(n).ffill()
    return _sig(ind.crossover(df["close"], last_top), ind.crossunder(df["close"], last_bot))


# =============================================================================== filtres
FILTERS: dict[str, dict] = {
    "none": {},
    "trend_ema200": {"n": 200},
    "trend_ema50": {"n": 50},
    "adx_strong": {"n": 14, "min_adx": 25},
    "adx_weak": {"n": 14, "max_adx": 20},
    "vol_high": {"n": 14, "pct": 0.5},
    "vol_low": {"n": 14, "pct": 0.5},
    "session_london_ny": {"start": 7, "end": 17},
    "kill_zones": {"windows": [(7, 10), (12, 15)]},   # killzones ICT Londres / New York (heure serveur)
    "chop_trending": {"n": 14, "max": 45},             # Choppiness bas = marché directionnel
    "chop_ranging": {"n": 14, "min": 55},              # Choppiness haut = marché en range
}


def apply_filter(df: pd.DataFrame, sig: pd.Series, name: str) -> pd.Series:
    if name == "none":
        return sig
    p = FILTERS[name]
    if name.startswith("trend_"):
        t = ind.ema(df["close"], p["n"])
        ok_long, ok_short = df["close"] > t, df["close"] < t
    elif name == "adx_strong":
        a, _, _ = ind.adx(df, p["n"])
        ok_long = ok_short = a > p["min_adx"]
    elif name == "adx_weak":
        a, _, _ = ind.adx(df, p["n"])
        ok_long = ok_short = a < p["max_adx"]
    elif name in ("vol_high", "vol_low"):
        a = ind.atr(df, p["n"])
        med = a.rolling(200, min_periods=50).median()
        ok_long = ok_short = (a > med) if name == "vol_high" else (a < med)
    elif name == "session_london_ny":
        if not isinstance(df.index, pd.DatetimeIndex):
            return sig
        hrs = pd.Series(df.index.hour, index=df.index)
        ok_long = ok_short = (hrs >= p["start"]) & (hrs < p["end"])
    elif name == "kill_zones":
        if not isinstance(df.index, pd.DatetimeIndex):
            return sig
        hrs = pd.Series(df.index.hour, index=df.index)
        ok_long = ok_short = pd.concat([(hrs >= a) & (hrs < b) for a, b in p["windows"]], axis=1).any(axis=1)
    elif name.startswith("chop_"):
        ch = ind.choppiness(df, p["n"])
        ok_long = ok_short = (ch < p["max"]) if name == "chop_trending" else (ch > p["min"])
    else:
        raise KeyError(name)
    ok_long, ok_short = ok_long.fillna(False), ok_short.fillna(False)
    out = sig.where(~((sig > 0) & ~ok_long), 0)
    out = out.where(~((out < 0) & ~ok_short), 0)
    return out.astype(np.int8)


def combine(a: pd.Series, b: pd.Series, mode: str = "confirm", window: int = 3) -> pd.Series:
    """Combine deux signaux.

    confirm : signal de A, validé si B a donné le même sens dans les `window` dernières bougies
    and     : A et B sur la même bougie
    or      : A ou B (A prioritaire en cas de conflit)
    """
    if mode == "and":
        return a.where(a == b, 0).astype(np.int8)
    if mode == "or":
        return a.where(a != 0, b).astype(np.int8)
    if mode == "confirm":
        b_long = (b > 0).astype(int).rolling(window + 1, min_periods=1).max() > 0
        b_short = (b < 0).astype(int).rolling(window + 1, min_periods=1).max() > 0
        return _sig((a > 0) & b_long, (a < 0) & b_short)
    raise ValueError(mode)


def expand_grid(grid: dict) -> list[dict]:
    import itertools

    if not grid:
        return [{}]
    keys = list(grid)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(grid[k] for k in keys))]


def families() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for s in REGISTRY.values():
        out.setdefault(s.family, []).append(s.name)
    return out


# enregistre les stratégies des autres modules dans REGISTRY
from . import strategies_plus, strategies_smc  # noqa: E402,F401
