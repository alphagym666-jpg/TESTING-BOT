"""Stochastique, divergences, chandeliers japonais et indicateurs supplémentaires."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from .strategies import _flip, _sig, strategy


def _S(arr, df):
    return pd.Series(arr, index=df.index)


# =============================================================================== stochastique
@strategy("stoch_trend_pullback", "stochastic", {"k": [5, 9, 14], "trend": [50, 100, 200], "low": [20, 30]},
          "Stochastique : croisement en survente DANS le sens de la tendance (pullback)")
def stoch_trend_pullback(df, k=14, trend=200, low=20):
    pk, pd_ = ind.stochastic(df, k, 3)
    t = ind.ema(df["close"], trend)
    up, dn = df["close"] > t, df["close"] < t
    return _sig(ind.crossover(pk, pd_) & (pd_ < low) & up, ind.crossunder(pk, pd_) & (pd_ > 100 - low) & dn)


@strategy("stoch_50", "stochastic", {"k": [9, 14, 21]}, "%K du Stochastique traverse 50")
def stoch_50(df, k=14):
    pk, _ = ind.stochastic(df, k, 3)
    return _sig(ind.crossover(pk, 50.0), ind.crossunder(pk, 50.0))


@strategy("stoch_pop", "stochastic", {"k": [8, 14], "lvl": [75, 80]},
          "Stochastic Pop (Jake Bernstein) : %K entre en surachat = momentum haussier")
def stoch_pop(df, k=14, lvl=80):
    pk, _ = ind.stochastic(df, k, 3)
    return _sig(ind.crossover(pk, float(lvl)), ind.crossunder(pk, float(100 - lvl)))


@strategy("stoch_hook", "stochastic", {"k": [5, 14], "low": [15, 20, 25]},
          "Crochet du %K en zone extrême (retournement de pente)")
def stoch_hook(df, k=14, low=20):
    pk, _ = ind.stochastic(df, k, 3)
    hook_up = (pk.shift(1) < low) & (pk > pk.shift(1)) & (pk.shift(1) < pk.shift(2))
    hook_dn = (pk.shift(1) > 100 - low) & (pk < pk.shift(1)) & (pk.shift(1) > pk.shift(2))
    return _sig(hook_up, hook_dn)


@strategy("stoch_double", "stochastic", {"slow": [14, 21], "fast": [5]},
          "Double stochastique : lent ET rapide en survente, puis le rapide remonte")
def stoch_double(df, slow=14, fast=5):
    s, _ = ind.stochastic(df, slow, 3)
    f, fd = ind.stochastic(df, fast, 3)
    return _sig((s < 25) & ind.crossover(f, fd) & (f < 25), (s > 75) & ind.crossunder(f, fd) & (f > 75))


@strategy("stoch_rsi", "stochastic", {"n": [14], "k": [3], "low": [10, 20]}, "Stochastique RSI en zone extrême")
def stoch_rsi(df, n=14, k=3, low=20):
    kk, dd = ind.stoch_rsi(df["close"], n, k, 3)
    return _sig(ind.crossover(kk, dd) & (kk < low), ind.crossunder(kk, dd) & (kk > 100 - low))


@strategy("stoch_macd", "stochastic", {"k": [14], "low": [20, 30]},
          "Stochastique qui sort de survente + histogramme MACD qui remonte")
def stoch_macd(df, k=14, low=20):
    pk, _ = ind.stochastic(df, k, 3)
    _, _, h = ind.macd(df["close"])
    return _sig(ind.crossover(pk, float(low)) & (h > h.shift(1)), ind.crossunder(pk, float(100 - low)) & (h < h.shift(1)))


# =============================================================================== divergences
def _divergence(df, osc: pd.Series, n: int, hidden: bool, max_gap: int = 60) -> pd.Series:
    ph, pl, ph_at, pl_at = ind.pivots(df, n)
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    o = osc.to_numpy()
    out = np.zeros(len(df), dtype=np.int8)
    prev_l = prev_h = None
    for i in range(len(df)):
        if pl_at[i] >= 0:
            p = pl_at[i]
            cur = (p, l[p], o[p])
            if prev_l and not np.isnan(cur[2]) and not np.isnan(prev_l[2]) and p - prev_l[0] <= max_gap:
                reg = cur[1] < prev_l[1] and cur[2] > prev_l[2]      # prix + bas, oscillateur + haut
                hid = cur[1] > prev_l[1] and cur[2] < prev_l[2]
                if (hid if hidden else reg):
                    out[i] = 1
            prev_l = cur
        if ph_at[i] >= 0:
            p = ph_at[i]
            cur = (p, h[p], o[p])
            if prev_h and not np.isnan(cur[2]) and not np.isnan(prev_h[2]) and p - prev_h[0] <= max_gap:
                reg = cur[1] > prev_h[1] and cur[2] < prev_h[2]
                hid = cur[1] < prev_h[1] and cur[2] > prev_h[2]
                if (hid if hidden else reg):
                    out[i] = -1
            prev_h = cur
    return pd.Series(out, index=df.index)


@strategy("div_rsi", "divergence", {"n": [3, 5], "rsi": [9, 14], "hidden": [False, True]},
          "Divergence RSI (classique = retournement, cachée = continuation)")
def div_rsi(df, n=3, rsi=14, hidden=False):
    return _divergence(df, ind.rsi(df["close"], rsi), n, hidden)


@strategy("div_macd", "divergence", {"n": [3, 5], "hidden": [False, True]}, "Divergence sur l'histogramme MACD")
def div_macd(df, n=3, hidden=False):
    _, _, h = ind.macd(df["close"])
    return _divergence(df, h, n, hidden)


@strategy("div_stoch", "divergence", {"n": [3, 5], "k": [14], "hidden": [False, True]}, "Divergence Stochastique")
def div_stoch(df, n=3, k=14, hidden=False):
    pk, _ = ind.stochastic(df, k, 3)
    return _divergence(df, pk, n, hidden)


@strategy("div_cci", "divergence", {"n": [3, 5], "hidden": [False]}, "Divergence CCI")
def div_cci(df, n=3, hidden=False):
    return _divergence(df, ind.cci(df, 20), n, hidden)


@strategy("div_obv", "divergence", {"n": [3, 5], "hidden": [False]}, "Divergence OBV (volume)")
def div_obv(df, n=5, hidden=False):
    return _divergence(df, ind.obv(df), n, hidden)


# =========================================================================== chandeliers
def _parts(df):
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    upper = h - pd.concat([o, c], axis=1).max(axis=1)
    lower = pd.concat([o, c], axis=1).min(axis=1) - l
    return o, h, l, c, body, upper, lower, ind.atr(df, 14)


def _at_extreme(df, n=20):
    """Contexte : bougie proche de la bande de Bollinger basse (achat) / haute (vente)."""
    lo, _, up = ind.bollinger(df["close"], n, 2.0)
    return df["low"] <= lo, df["high"] >= up


@strategy("morning_evening_star", "candlestick", {"context": [False, True]}, "Étoile du matin / du soir")
def morning_evening_star(df, context=False):
    o, h, l, c, body, up, lw, a = _parts(df)
    small = body.shift(1) < 0.3 * a
    bull = (c.shift(2) < o.shift(2)) & (body.shift(2) > 0.6 * a) & small & (c > o) & (c > (o.shift(2) + c.shift(2)) / 2)
    bear = (c.shift(2) > o.shift(2)) & (body.shift(2) > 0.6 * a) & small & (c < o) & (c < (o.shift(2) + c.shift(2)) / 2)
    if context:
        lo_ctx, hi_ctx = _at_extreme(df)
        bull &= lo_ctx.shift(1, fill_value=False) | lo_ctx
        bear &= hi_ctx.shift(1, fill_value=False) | hi_ctx
    return _sig(bull, bear)


@strategy("hammer_star", "candlestick", {"ratio": [2.0, 3.0], "context": [True, False]},
          "Marteau / étoile filante (avec ou sans contexte de bande de Bollinger)")
def hammer_star(df, ratio=2.0, context=True):
    o, h, l, c, body, up, lw, a = _parts(df)
    b = body.clip(lower=1e-12)
    bull = (lw > ratio * b) & (up < 0.5 * b + 1e-12)
    bear = (up > ratio * b) & (lw < 0.5 * b + 1e-12)
    if context:
        lo_ctx, hi_ctx = _at_extreme(df)
        bull &= lo_ctx
        bear &= hi_ctx
    return _sig(bull, bear)


@strategy("harami", "candlestick", {"context": [False, True]}, "Harami haussier / baissier")
def harami(df, context=False):
    o, h, l, c, body, up, lw, a = _parts(df)
    inside = (pd.concat([o, c], axis=1).max(axis=1) < pd.concat([o.shift(1), c.shift(1)], axis=1).max(axis=1)) & \
             (pd.concat([o, c], axis=1).min(axis=1) > pd.concat([o.shift(1), c.shift(1)], axis=1).min(axis=1))
    bull = inside & (c.shift(1) < o.shift(1)) & (c > o) & (body.shift(1) > 0.6 * a)
    bear = inside & (c.shift(1) > o.shift(1)) & (c < o) & (body.shift(1) > 0.6 * a)
    if context:
        lo_ctx, hi_ctx = _at_extreme(df)
        bull &= lo_ctx.shift(1, fill_value=False)
        bear &= hi_ctx.shift(1, fill_value=False)
    return _sig(bull, bear)


@strategy("tweezer", "candlestick", {"tol_atr": [0.05, 0.15]}, "Tweezer bottom / top (pinces)")
def tweezer(df, tol_atr=0.1):
    o, h, l, c, body, up, lw, a = _parts(df)
    bull = ((l - l.shift(1)).abs() <= tol_atr * a) & (c.shift(1) < o.shift(1)) & (c > o)
    bear = ((h - h.shift(1)).abs() <= tol_atr * a) & (c.shift(1) > o.shift(1)) & (c < o)
    return _sig(bull, bear)


@strategy("three_soldiers_crows", "candlestick", {"min_body": [0.4, 0.7]}, "Trois soldats blancs / trois corbeaux noirs")
def three_soldiers_crows(df, min_body=0.5):
    o, h, l, c, body, up, lw, a = _parts(df)
    big = body > min_body * a
    g = (c > o) & big
    r = (c < o) & big
    bull = g & g.shift(1, fill_value=False) & g.shift(2, fill_value=False) & (c > c.shift(1)) & (c.shift(1) > c.shift(2))
    bear = r & r.shift(1, fill_value=False) & r.shift(2, fill_value=False) & (c < c.shift(1)) & (c.shift(1) < c.shift(2))
    return _sig(bull, bear)


@strategy("marubozu", "candlestick", {"min_body": [1.0, 1.5], "max_wick": [0.05, 0.15]},
          "Marubozu : grosse bougie sans mèche (bougie de momentum « sans rejet »)")
def marubozu(df, min_body=1.2, max_wick=0.1):
    o, h, l, c, body, up, lw, a = _parts(df)
    ok = (body > min_body * a) & (up <= max_wick * body) & (lw <= max_wick * body)
    return _sig(ok & (c > o), ok & (c < o))


@strategy("doji_reversal", "candlestick", {"max_body": [0.05, 0.1]}, "Doji en bande de Bollinger + confirmation")
def doji_reversal(df, max_body=0.1):
    o, h, l, c, body, up, lw, a = _parts(df)
    doji = (body <= max_body * (h - l).replace(0, np.nan)).shift(1, fill_value=False)
    lo_ctx, hi_ctx = _at_extreme(df)
    return _sig(doji & lo_ctx.shift(1, fill_value=False) & (c > h.shift(1)),
                doji & hi_ctx.shift(1, fill_value=False) & (c < l.shift(1)))


@strategy("outside_bar", "candlestick", {"min_range": [1.0, 1.5]}, "Outside bar (englobante) de retournement")
def outside_bar(df, min_range=1.2):
    o, h, l, c, body, up, lw, a = _parts(df)
    outside = (h > h.shift(1)) & (l < l.shift(1)) & ((h - l) > min_range * a)
    rng = (h - l).replace(0, np.nan)
    return _sig(outside & ((c - l) / rng > 0.75), outside & ((h - c) / rng > 0.75))


@strategy("heikin_ashi", "candlestick", {"strong": [False, True]},
          "Heikin Ashi : changement de couleur (option : bougie sans mèche opposée)")
def heikin_ashi(df, strong=True):
    ha = ind.heikin_ashi(df)
    green = ha["close"] > ha["open"]
    if strong:
        no_low = (ha[["open", "close"]].min(axis=1) - ha["low"]) <= 1e-12
        no_high = (ha["high"] - ha[["open", "close"]].max(axis=1)) <= 1e-12
        return _sig(green & no_low & ~green.shift(1, fill_value=True), ~green & no_high & green.shift(1, fill_value=False))
    return _sig(green & ~green.shift(1, fill_value=True), ~green & green.shift(1, fill_value=False))


@strategy("nr7_breakout", "breakout", {"n": [4, 7]}, "NR7 / NR4 : cassure après la plus petite bougie des N dernières")
def nr7_breakout(df, n=7):
    rng = df["high"] - df["low"]
    nr = (rng == rng.rolling(n, min_periods=n).min()).shift(1, fill_value=False)
    return _sig(nr & (df["close"] > df["high"].shift(1)), nr & (df["close"] < df["low"].shift(1)))


# ===================================================================== autres indicateurs
@strategy("dema_tema_cross", "trend", {"kind": ["dema", "tema"], "fast": [9, 21], "slow": [50, 100]},
          "Croisement DEMA / TEMA (moyennes à faible retard)")
def dema_tema_cross(df, kind="dema", fast=9, slow=50):
    f = getattr(ind, kind)
    a, b = f(df["close"], fast), f(df["close"], slow)
    return _sig(ind.crossover(a, b), ind.crossunder(a, b))


@strategy("kama_cross", "trend", {"n": [10, 20]}, "Prix croise la moyenne adaptative de Kaufman")
def kama_cross(df, n=10):
    k = ind.kama(df["close"], n)
    return _sig(ind.crossover(df["close"], k), ind.crossunder(df["close"], k))


@strategy("aroon_cross", "trend", {"n": [14, 25]}, "Croisement Aroon Up / Down")
def aroon_cross(df, n=25):
    up, dn = ind.aroon(df, n)
    return _sig(ind.crossover(up, dn) & (up > 70), ind.crossunder(up, dn) & (dn > 70))


@strategy("vortex_cross", "trend", {"n": [14, 21]}, "Croisement de l'indicateur Vortex")
def vortex_cross(df, n=14):
    p, m = ind.vortex(df, n)
    return _sig(ind.crossover(p, m), ind.crossunder(p, m))


@strategy("alligator", "trend", {"mode": ["reveil", "prix"]}, "Alligator de Bill Williams (réveil ou prix au-dessus)")
def alligator(df, mode="reveil"):
    jaw, teeth, lips = ind.alligator(df)
    if mode == "reveil":
        d = pd.Series(np.where((lips > teeth) & (teeth > jaw), 1, np.where((lips < teeth) & (teeth < jaw), -1, 0)), index=df.index)
        return _flip(d)
    top = pd.concat([jaw, teeth, lips], axis=1).max(axis=1)
    bot = pd.concat([jaw, teeth, lips], axis=1).min(axis=1)
    return _sig(ind.crossover(df["close"], top), ind.crossunder(df["close"], bot))


@strategy("chandelier_flip", "trend", {"n": [14, 22], "mult": [2.0, 3.0]}, "Retournement du Chandelier Exit")
def chandelier_flip(df, n=22, mult=3.0):
    return _flip(ind.chandelier_dir(df, n, mult))


@strategy("linreg_slope", "trend", {"n": [20, 50]}, "Pente de la régression linéaire change de signe")
def linreg_slope(df, n=20):
    return _flip(np.sign(ind.linreg_slope(df["close"], n)))


@strategy("ema_pullback", "trend", {"fast": [20, 21], "mid": [50], "slow": [200]},
          "Pullback sur l'EMA 20 dans une tendance EMA 50 > EMA 200")
def ema_pullback(df, fast=20, mid=50, slow=200):
    e1, e2, e3 = (ind.ema(df["close"], n) for n in (fast, mid, slow))
    up, dn = (e2 > e3) & (df["close"] > e2), (e2 < e3) & (df["close"] < e2)
    return _sig(up & (df["low"] <= e1) & (df["close"] > e1), dn & (df["high"] >= e1) & (df["close"] < e1))


@strategy("trix_signal", "momentum", {"n": [9, 15], "sig": [9]}, "TRIX croise sa ligne de signal")
def trix_signal(df, n=15, sig=9):
    t = ind.trix(df["close"], n)
    s = ind.ema(t, sig)
    return _sig(ind.crossover(t, s), ind.crossunder(t, s))


@strategy("awesome_osc", "momentum", {"mode": ["zero", "saucer"]}, "Awesome Oscillator : zéro ou « soucoupe »")
def awesome_osc(df, mode="zero"):
    ao = ind.awesome(df)
    if mode == "zero":
        return _sig(ind.crossover(ao, 0.0), ind.crossunder(ao, 0.0))
    d = ao.diff()
    bull = (ao > 0) & (d.shift(1) < 0) & (d > 0)
    bear = (ao < 0) & (d.shift(1) > 0) & (d < 0)
    return _sig(bull, bear)


@strategy("fisher_cross", "momentum", {"n": [9, 10, 20]}, "Fisher Transform croise sa valeur précédente en zone extrême")
def fisher_cross(df, n=10):
    f = ind.fisher(df, n)
    p = f.shift(1)
    return _sig(ind.crossover(f, p) & (f < -1.5), ind.crossunder(f, p) & (f > 1.5))


@strategy("cmo_reversal", "mean_reversion", {"n": [9, 14], "lvl": [40, 50]}, "Chande Momentum Oscillator sort des extrêmes")
def cmo_reversal(df, n=14, lvl=50):
    m = ind.cmo(df["close"], n)
    return _sig(ind.crossover(m, float(-lvl)), ind.crossunder(m, float(lvl)))


@strategy("ultimate_osc", "mean_reversion", {"low": [30, 35]}, "Ultimate Oscillator (Larry Williams)")
def ultimate_osc(df, low=30):
    u = ind.ultimate(df)
    return _sig(ind.crossover(u, float(low)), ind.crossunder(u, float(100 - low)))


@strategy("mfi_reversal", "mean_reversion", {"n": [14], "low": [10, 20]}, "Money Flow Index (RSI pondéré par le volume)")
def mfi_reversal(df, n=14, low=20):
    m = ind.mfi(df, n)
    return _sig(ind.crossover(m, float(low)), ind.crossunder(m, float(100 - low)))


@strategy("bb_percent_b", "mean_reversion", {"n": [20], "low": [0.0, 0.05], "trend": [100, 200]},
          "%b de Bollinger < 0 dans une tendance haussière (Connors)")
def bb_percent_b(df, n=20, low=0.0, trend=200):
    lo, _, up = ind.bollinger(df["close"], n, 2.0)
    pb = (df["close"] - lo) / (up - lo).replace(0, np.nan)
    t = ind.sma(df["close"], trend)
    return _sig((pb < low) & (df["close"] > t), (pb > 1 - low) & (df["close"] < t))


@strategy("envelope_reversion", "mean_reversion", {"n": [20, 50], "pct": [0.5, 1.0, 2.0]},
          "Enveloppes de moyenne mobile (±x %)")
def envelope_reversion(df, n=20, pct=1.0):
    m = ind.sma(df["close"], n)
    lo, up = m * (1 - pct / 100), m * (1 + pct / 100)
    return _sig(ind.crossover(df["close"], lo), ind.crossunder(df["close"], up))


@strategy("obv_breakout", "momentum", {"n": [20, 50]}, "Prix et OBV font un nouveau plus haut/bas de N bougies")
def obv_breakout(df, n=20):
    o = ind.obv(df)
    c = df["close"]
    hi_c, lo_c = c.rolling(n, min_periods=n).max().shift(1), c.rolling(n, min_periods=n).min().shift(1)
    hi_o, lo_o = o.rolling(n, min_periods=n).max().shift(1), o.rolling(n, min_periods=n).min().shift(1)
    return _sig((c > hi_c) & (o > hi_o) & (c.shift(1) <= hi_c), (c < lo_c) & (o < lo_o) & (c.shift(1) >= lo_c))


@strategy("coppock_turn", "momentum", {"n": [10]}, "Courbe de Coppock qui remonte sous zéro / redescend au-dessus")
def coppock_turn(df, n=10):
    k = ind.coppock(df["close"], 14, 11, n)
    d = k.diff()
    return _sig((k < 0) & (d > 0) & (d.shift(1) <= 0), (k > 0) & (d < 0) & (d.shift(1) >= 0))


@strategy("elder_impulse", "momentum", {"n": [13, 21]}, "Système Impulse d'Elder (pente EMA + histogramme MACD)")
def elder_impulse(df, n=13):
    e = ind.ema(df["close"], n)
    _, _, h = ind.macd(df["close"])
    d = pd.Series(np.where((e.diff() > 0) & (h.diff() > 0), 1, np.where((e.diff() < 0) & (h.diff() < 0), -1, 0)), index=df.index)
    return _flip(d)


@strategy("elder_ray", "momentum", {"n": [13]}, "Elder Ray : bear power remonte sous zéro dans une tendance haussière")
def elder_ray(df, n=13):
    bull, bear = ind.elder_ray(df, n)
    e = ind.ema(df["close"], n)
    up, dn = e > e.shift(1), e < e.shift(1)
    return _sig(up & (bear < 0) & (bear > bear.shift(1)) & (bear.shift(1) <= bear.shift(2)),
                dn & (bull > 0) & (bull < bull.shift(1)) & (bull.shift(1) >= bull.shift(2)))
