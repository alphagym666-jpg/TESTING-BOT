"""Bibliothèque d'indicateurs techniques (vectorisés, numpy/pandas).

Toutes les fonctions prennent un DataFrame OHLC (colonnes open/high/low/close/volume)
ou une Series et renvoient des Series alignées sur l'index d'origine.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def wma(s: pd.Series, n: int) -> pd.Series:
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n, min_periods=n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)


def hma(s: pd.Series, n: int) -> pd.Series:
    """Hull Moving Average."""
    half = max(int(n / 2), 1)
    root = max(int(np.sqrt(n)), 1)
    return wma(2 * wma(s, half) - wma(s, n), root)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0).where(gain.notna())


def macd(s: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(s, fast) - ema(s, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(s, n)
    sd = s.rolling(n, min_periods=n).std(ddof=0)
    return mid - k * sd, mid, mid + k * sd


def keltner(df: pd.DataFrame, n: int = 20, mult: float = 2.0):
    mid = ema(df["close"], n)
    a = atr(df, n)
    return mid - mult * a, mid, mid + mult * a


def donchian(df: pd.DataFrame, n: int = 20):
    """Canal de Donchian basé sur les n bougies PRÉCÉDENTES (pas de look-ahead)."""
    upper = df["high"].rolling(n, min_periods=n).max().shift(1)
    lower = df["low"].rolling(n, min_periods=n).min().shift(1)
    return lower, (upper + lower) / 2, upper


def stochastic(df: pd.DataFrame, k: int = 14, d: int = 3):
    ll = df["low"].rolling(k, min_periods=k).min()
    hh = df["high"].rolling(k, min_periods=k).max()
    pk = 100 * (df["close"] - ll) / (hh - ll).replace(0, np.nan)
    return pk, pk.rolling(d, min_periods=d).mean()


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hh = df["high"].rolling(n, min_periods=n).max()
    ll = df["low"].rolling(n, min_periods=n).min()
    return -100 * (hh - df["close"]) / (hh - ll).replace(0, np.nan)


def cci(df: pd.DataFrame, n: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    m = tp.rolling(n, min_periods=n).mean()
    md = tp.rolling(n, min_periods=n).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    return (tp - m) / (0.015 * md.replace(0, np.nan))


def adx(df: pd.DataFrame, n: int = 14):
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean(), plus_di, minus_di


def supertrend(df: pd.DataFrame, n: int = 10, mult: float = 3.0) -> pd.Series:
    """Renvoie la direction du Supertrend : +1 haussier, -1 baissier."""
    a = atr(df, n).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2).to_numpy()
    close = df["close"].to_numpy()
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    direction = np.zeros(len(df))
    fu, fl = upper.copy(), lower.copy()
    for i in range(1, len(df)):
        if np.isnan(a[i]):
            continue
        fu[i] = upper[i] if (np.isnan(fu[i - 1]) or upper[i] < fu[i - 1] or close[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lower[i] if (np.isnan(fl[i - 1]) or lower[i] > fl[i - 1] or close[i - 1] < fl[i - 1]) else fl[i - 1]
        if direction[i - 1] <= 0:
            direction[i] = 1 if close[i] > fu[i - 1] else -1
        else:
            direction[i] = -1 if close[i] < fl[i - 1] else 1
    return pd.Series(direction, index=df.index)


def ichimoku(df: pd.DataFrame, tenkan: int = 9, kijun: int = 26, senkou: int = 52):
    def mid(n):
        return (df["high"].rolling(n, min_periods=n).max() + df["low"].rolling(n, min_periods=n).min()) / 2

    t, k = mid(tenkan), mid(kijun)
    span_a = ((t + k) / 2).shift(kijun)
    span_b = mid(senkou).shift(kijun)
    return t, k, span_a, span_b


def psar(df: pd.DataFrame, step: float = 0.02, max_step: float = 0.2) -> pd.Series:
    """Parabolic SAR : renvoie la direction (+1 / -1)."""
    high, low = df["high"].to_numpy(), df["low"].to_numpy()
    n = len(df)
    direction = np.zeros(n)
    if n < 3:
        return pd.Series(direction, index=df.index)
    bull = True
    af = step
    ep = high[0]
    sar = low[0]
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
            if low[i] < sar:
                bull, sar, ep, af = False, ep, low[i], step
            elif high[i] > ep:
                ep, af = high[i], min(af + step, max_step)
        else:
            sar = max(sar, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
            if high[i] > sar:
                bull, sar, ep, af = True, ep, high[i], step
            elif low[i] < ep:
                ep, af = low[i], min(af + step, max_step)
        direction[i] = 1 if bull else -1
    return pd.Series(direction, index=df.index)


def zscore(s: pd.Series, n: int = 20) -> pd.Series:
    return (s - s.rolling(n, min_periods=n).mean()) / s.rolling(n, min_periods=n).std(ddof=0).replace(0, np.nan)


def roc(s: pd.Series, n: int = 10) -> pd.Series:
    return 100 * s.pct_change(n)


def vwap_rolling(df: pd.DataFrame, n: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"].replace(0, 1)
    return (tp * vol).rolling(n, min_periods=n).sum() / vol.rolling(n, min_periods=n).sum()


def crossover(a: pd.Series, b) -> pd.Series:
    b = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossunder(a: pd.Series, b) -> pd.Series:
    b = b if isinstance(b, pd.Series) else pd.Series(b, index=a.index)
    return (a < b) & (a.shift(1) >= b.shift(1))


# ------------------------------------------------------------------ indicateurs supplémentaires
def dema(s: pd.Series, n: int) -> pd.Series:
    e = ema(s, n)
    return 2 * e - ema(e, n)


def tema(s: pd.Series, n: int) -> pd.Series:
    e1 = ema(s, n)
    e2 = ema(e1, n)
    return 3 * e1 - 3 * e2 + ema(e2, n)


def kama(s: pd.Series, n: int = 10, fast: int = 2, slow: int = 30) -> pd.Series:
    """Kaufman Adaptive Moving Average."""
    x = s.to_numpy(dtype=float)
    out = np.full(len(x), np.nan)
    fc, sc = 2 / (fast + 1), 2 / (slow + 1)
    for i in range(n, len(x)):
        change = abs(x[i] - x[i - n])
        vol = np.sum(np.abs(np.diff(x[i - n: i + 1])))
        er = change / vol if vol > 0 else 0.0
        a = (er * (fc - sc) + sc) ** 2
        out[i] = x[i] if np.isnan(out[i - 1]) else out[i - 1] + a * (x[i] - out[i - 1])
    return pd.Series(out, index=s.index)


def stoch_rsi(s: pd.Series, n: int = 14, k: int = 3, d: int = 3):
    r = rsi(s, n)
    lo, hi = r.rolling(n, min_periods=n).min(), r.rolling(n, min_periods=n).max()
    st = 100 * (r - lo) / (hi - lo).replace(0, np.nan)
    kk = st.rolling(k, min_periods=k).mean()
    return kk, kk.rolling(d, min_periods=d).mean()


def aroon(df: pd.DataFrame, n: int = 25):
    up = df["high"].rolling(n + 1, min_periods=n + 1).apply(lambda x: np.argmax(x) / n * 100, raw=True)
    dn = df["low"].rolling(n + 1, min_periods=n + 1).apply(lambda x: np.argmin(x) / n * 100, raw=True)
    return up, dn


def vortex(df: pd.DataFrame, n: int = 14):
    vmp = (df["high"] - df["low"].shift(1)).abs().rolling(n, min_periods=n).sum()
    vmm = (df["low"] - df["high"].shift(1)).abs().rolling(n, min_periods=n).sum()
    tr = true_range(df).rolling(n, min_periods=n).sum()
    return vmp / tr, vmm / tr


def trix(s: pd.Series, n: int = 15) -> pd.Series:
    e = ema(ema(ema(s, n), n), n)
    return 100 * e.pct_change()


def awesome(df: pd.DataFrame) -> pd.Series:
    mid = (df["high"] + df["low"]) / 2
    return sma(mid, 5) - sma(mid, 34)


def alligator(df: pd.DataFrame):
    mid = (df["high"] + df["low"]) / 2
    smma = lambda n: mid.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    # les décalages vers l'avant de Bill Williams sont de simples shift() : pas de look-ahead
    return smma(13).shift(8), smma(8).shift(5), smma(5).shift(3)  # mâchoire, dents, lèvres


def fisher(df: pd.DataFrame, n: int = 10) -> pd.Series:
    mid = ((df["high"] + df["low"]) / 2).to_numpy()
    out = np.full(len(mid), np.nan)
    v_prev, f_prev = 0.0, 0.0
    for i in range(n - 1, len(mid)):
        w = mid[i - n + 1: i + 1]
        lo, hi = w.min(), w.max()
        v = 0.33 * 2 * ((mid[i] - lo) / (hi - lo) - 0.5) + 0.67 * v_prev if hi > lo else v_prev
        v = min(max(v, -0.999), 0.999)
        f = 0.5 * np.log((1 + v) / (1 - v)) + 0.5 * f_prev
        out[i], v_prev, f_prev = f, v, f
    return pd.Series(out, index=df.index)


def cmo(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).rolling(n, min_periods=n).sum()
    dn = (-d.clip(upper=0)).rolling(n, min_periods=n).sum()
    return 100 * (up - dn) / (up + dn).replace(0, np.nan)


def ultimate(df: pd.DataFrame, a: int = 7, b: int = 14, c: int = 28) -> pd.Series:
    pc = df["close"].shift(1)
    bp = df["close"] - pd.concat([df["low"], pc], axis=1).min(axis=1)
    tr = pd.concat([df["high"], pc], axis=1).max(axis=1) - pd.concat([df["low"], pc], axis=1).min(axis=1)
    avg = lambda n: bp.rolling(n, min_periods=n).sum() / tr.rolling(n, min_periods=n).sum()
    return 100 * (4 * avg(a) + 2 * avg(b) + avg(c)) / 7


def mfi(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    mf = tp * df["volume"]
    pos = mf.where(tp > tp.shift(1), 0.0).rolling(n, min_periods=n).sum()
    neg = mf.where(tp < tp.shift(1), 0.0).rolling(n, min_periods=n).sum()
    return 100 - 100 / (1 + pos / neg.replace(0, np.nan))


def obv(df: pd.DataFrame) -> pd.Series:
    return (np.sign(df["close"].diff()).fillna(0) * df["volume"]).cumsum()


def chandelier_dir(df: pd.DataFrame, n: int = 22, mult: float = 3.0) -> pd.Series:
    a = atr(df, n)
    long_stop = df["high"].rolling(n, min_periods=n).max() - mult * a
    short_stop = df["low"].rolling(n, min_periods=n).min() + mult * a
    c = df["close"].to_numpy()
    ls, ss = long_stop.to_numpy(), short_stop.to_numpy()
    d = np.zeros(len(c))
    for i in range(1, len(c)):
        if np.isnan(ls[i - 1]) or np.isnan(ss[i - 1]):
            continue
        if c[i] > ss[i - 1]:
            d[i] = 1
        elif c[i] < ls[i - 1]:
            d[i] = -1
        else:
            d[i] = d[i - 1]
    return pd.Series(d, index=df.index)


def choppiness(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tr = true_range(df).rolling(n, min_periods=n).sum()
    rng = df["high"].rolling(n, min_periods=n).max() - df["low"].rolling(n, min_periods=n).min()
    return 100 * np.log10(tr / rng.replace(0, np.nan)) / np.log10(n)


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    ha_c = ((df["open"] + df["high"] + df["low"] + df["close"]) / 4).to_numpy()
    o = df["open"].to_numpy()
    ha_o = np.empty(len(df))
    ha_o[0] = (o[0] + df["close"].iloc[0]) / 2
    for i in range(1, len(df)):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2
    ha_h = np.maximum.reduce([df["high"].to_numpy(), ha_o, ha_c])
    ha_l = np.minimum.reduce([df["low"].to_numpy(), ha_o, ha_c])
    return pd.DataFrame({"open": ha_o, "high": ha_h, "low": ha_l, "close": ha_c}, index=df.index)


def linreg_slope(s: pd.Series, n: int = 20) -> pd.Series:
    x = np.arange(n) - (n - 1) / 2
    den = (x ** 2).sum()
    return s.rolling(n, min_periods=n).apply(lambda y: np.dot(x, y) / den, raw=True)


def coppock(s: pd.Series, r1: int = 14, r2: int = 11, n: int = 10) -> pd.Series:
    return wma(roc(s, r1) + roc(s, r2), n)


def elder_ray(df: pd.DataFrame, n: int = 13):
    e = ema(df["close"], n)
    return df["high"] - e, df["low"] - e


def pivots(df: pd.DataFrame, n: int = 3):
    """Points pivots (swing high / swing low) CONFIRMÉS n bougies après, sans look-ahead.

    Renvoie des tableaux numpy :
      ph_conf[i] = valeur du swing high confirmé à la bougie i (nan sinon), idem pl_conf
      ph_at[i]   = index de la bougie du pivot confirmé à i (-1 sinon)
    """
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    N = len(df)
    ph_conf = np.full(N, np.nan)
    pl_conf = np.full(N, np.nan)
    ph_at = np.full(N, -1)
    pl_at = np.full(N, -1)
    for i in range(2 * n, N):
        p = i - n
        wh, wl = h[p - n: i + 1], l[p - n: i + 1]
        if h[p] == wh.max() and np.argmax(wh) == n:
            ph_conf[i], ph_at[i] = h[p], p
        if l[p] == wl.min() and np.argmin(wl) == n:
            pl_conf[i], pl_at[i] = l[p], p
    return ph_conf, pl_conf, ph_at, pl_at
