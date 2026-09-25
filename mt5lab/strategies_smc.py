"""Smart Money Concepts, zones et retours sur zone, sessions ICT.

Tout est calculé bougie par bougie sans look-ahead : un swing high/low n'existe qu'une fois
confirmé (n bougies plus tard), une zone ne peut être jouée qu'après sa création.

Modes d'entrée sur une zone (paramètre `mode`) :
  touche      : dès que le prix revient dans la zone (et ne clôture pas au-delà)
  rejet       : retour dans la zone + bougie de rejet (mèche >= corps, clôture dans le bon sens)
  sans_rejet  : retour dans la zone SANS mèche de rejet (le prix revient « proprement » et on entre quand même)
  cassure     : le prix traverse la zone et clôture au-delà sans rejet -> on joue la cassure (zone invalidée)
Une zone est « fraîche » : elle n'est jouée qu'au premier retour, puis elle est consommée.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from .strategies import _sig, strategy

MODES = ["touche", "rejet", "sans_rejet", "cassure"]


# ============================================================================ moteurs communs
def structure(df: pd.DataFrame, n: int = 3) -> dict:
    """Structure de marché : BOS (break of structure) et CHoCH (change of character)."""
    ph, pl, ph_at, pl_at = ind.pivots(df, n)
    c = df["close"].to_numpy()
    N = len(df)
    out = {k: np.zeros(N, dtype=bool) for k in ("bos_up", "bos_dn", "choch_up", "choch_dn")}
    trend = np.zeros(N)
    last_sh = np.full(N, np.nan)
    last_sl = np.full(N, np.nan)
    sh_idx = np.full(N, -1)
    sl_idx = np.full(N, -1)
    sh = sl = np.nan
    shi = sli = -1
    sh_broken = sl_broken = True
    t = 0
    for i in range(N):
        if not np.isnan(ph[i]):
            sh, shi, sh_broken = ph[i], ph_at[i], False
        if not np.isnan(pl[i]):
            sl, sli, sl_broken = pl[i], pl_at[i], False
        if not sh_broken and c[i] > sh:
            sh_broken = True
            out["choch_up" if t == -1 else "bos_up"][i] = True
            t = 1
        elif not sl_broken and c[i] < sl:
            sl_broken = True
            out["choch_dn" if t == 1 else "bos_dn"][i] = True
            t = -1
        trend[i], last_sh[i], last_sl[i], sh_idx[i], sl_idx[i] = t, sh, sl, shi, sli
    out.update(trend=trend, last_sh=last_sh, last_sl=last_sl, sh_idx=sh_idx, sl_idx=sl_idx)
    return out


def zone_signals(df: pd.DataFrame, zones: dict, mode: str = "touche", max_age: int = 100,
                 breaker: bool = False, allowed: np.ndarray | None = None) -> pd.Series:
    """Joue des zones. zones = {bougie_de_création: [(sens, bas, haut), ...]}.

    sens +1 = zone d'achat (demande / OB haussier), -1 = zone de vente.
    breaker=True : on ne joue pas la zone elle-même ; quand elle est cassée, elle devient une zone
    inverse (breaker block / inverse FVG) qui sera jouée au retour.
    """
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    N = len(df)
    out = np.zeros(N, dtype=np.int8)
    active: list[list] = []  # [sens, bas, haut, né_à, est_breaker]
    for i in range(N):
        keep, new = [], []
        for z in active:
            side, bot, top, born, is_brk = z
            if i - born > max_age:
                continue
            touched = l[i] <= top if side > 0 else h[i] >= bot
            if not touched:
                keep.append(z)
                continue
            body = abs(c[i] - o[i])
            broken = c[i] < bot if side > 0 else c[i] > top
            playable = (not breaker) or is_brk
            if broken:
                if breaker and not is_brk:
                    new.append([-side, bot, top, i, True])
                elif playable and mode == "cassure":
                    wick = (h[i] - max(o[i], c[i])) if side > 0 else (min(o[i], c[i]) - l[i])
                    if body > wick and out[i] == 0:
                        out[i] = -side
                continue  # zone invalidée
            if playable and mode != "cassure" and (allowed is None or allowed[i]):
                wick = (min(o[i], c[i]) - l[i]) if side > 0 else (h[i] - max(o[i], c[i]))
                mid = (bot + top) / 2
                rej = wick >= max(body, 1e-12) and ((c[i] >= mid) if side > 0 else (c[i] <= mid))
                if (mode == "touche" or (mode == "rejet" and rej) or (mode == "sans_rejet" and not rej)) and out[i] == 0:
                    out[i] = side
            if breaker and not is_brk:
                keep.append(z)  # l'OB d'origine reste surveillé jusqu'à sa cassure
            # sinon : zone consommée au premier retour
        active = keep + new + [[s, b, t, i, False] for s, b, t in zones.get(i, [])]
    return pd.Series(out, index=df.index, dtype=np.int8)


def _add(zones, i, z):
    zones.setdefault(i, []).append(z)


def order_block_zones(df, n=3, use_body=False) -> tuple[dict, dict]:
    """OB haussier = dernière bougie baissière avant l'impulsion qui casse la structure (et inversement)."""
    st = structure(df, n)
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    zones: dict = {}
    for i in np.flatnonzero(st["bos_up"] | st["choch_up"]):
        start = st["sl_idx"][i] if st["sl_idx"][i] >= 0 else max(0, i - 30)
        for j in range(i - 1, max(start, i - 60) - 1, -1):
            if c[j] < o[j]:
                _add(zones, i, (1, l[j], o[j] if use_body else h[j]))
                break
    for i in np.flatnonzero(st["bos_dn"] | st["choch_dn"]):
        start = st["sh_idx"][i] if st["sh_idx"][i] >= 0 else max(0, i - 30)
        for j in range(i - 1, max(start, i - 60) - 1, -1):
            if c[j] > o[j]:
                _add(zones, i, (-1, o[j] if use_body else l[j], h[j]))
                break
    return zones, st


def fvg_zones(df, min_atr=0.2) -> dict:
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    a = ind.atr(df, 14).to_numpy()
    zones: dict = {}
    for i in range(2, len(df)):
        if np.isnan(a[i]):
            continue
        if l[i] - h[i - 2] >= min_atr * a[i]:
            _add(zones, i, (1, h[i - 2], l[i]))
        elif l[i - 2] - h[i] >= min_atr * a[i]:
            _add(zones, i, (-1, h[i], l[i - 2]))
    return zones


def supply_demand_zones(df, base_k=0.5, exp_k=1.5, max_base=3) -> dict:
    """Zones offre/demande : 1 à max_base petites bougies (base) suivies d'une bougie explosive."""
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    a = ind.atr(df, 14).to_numpy()
    body = np.abs(c - o)
    zones: dict = {}
    for i in range(max_base + 1, len(df)):
        if np.isnan(a[i]) or body[i] < exp_k * a[i]:
            continue
        k = 0
        while k < max_base and body[i - 1 - k] < base_k * a[i]:
            k += 1
        if k == 0:
            continue
        b0, b1 = i - k, i  # base = [b0, i-1]
        if c[i] > o[i]:  # rallye -> zone de demande
            _add(zones, i, (1, l[b0:b1].min(), np.maximum(o[b0:b1], c[b0:b1]).max()))
        else:            # chute -> zone d'offre
            _add(zones, i, (-1, np.minimum(o[b0:b1], c[b0:b1]).min(), h[b0:b1].max()))
    return zones


def _hours(df):
    return df.index.hour.to_numpy() if isinstance(df.index, pd.DatetimeIndex) else None


def _prev_day_levels(df):
    if not isinstance(df.index, pd.DatetimeIndex):
        return None, None
    day = df.index.normalize()
    daily = df.groupby(day).agg(high=("high", "max"), low=("low", "min"))
    prev = daily.shift(1)
    return prev["high"].reindex(day).to_numpy(), prev["low"].reindex(day).to_numpy()


# ================================================================================ SMC
@strategy("smc_order_block", "smc", {"n": [2, 3, 5], "mode": ["touche", "rejet", "sans_rejet"],
                                      "max_age": [50, 150], "discount": [False, True]},
          "Order block SMC : retour sur l'OB après un BOS/CHoCH (option premium/discount)")
def smc_order_block(df, n=3, mode="touche", max_age=100, discount=False):
    zones, st = order_block_zones(df, n)
    sig = zone_signals(df, zones, mode, max_age)
    if not discount:
        return sig
    # achats seulement en discount (< 50 % du dernier range), ventes seulement en premium
    mid = (st["last_sh"] + st["last_sl"]) / 2
    c = df["close"].to_numpy()
    ok = np.where(sig > 0, c < mid, np.where(sig < 0, c > mid, True))
    return sig.where(ok, 0).astype(np.int8)


@strategy("smc_ob_body", "smc", {"n": [3, 5], "mode": ["touche", "rejet"], "max_age": [100]},
          "Order block défini sur le corps (open -> mèche) au lieu de la bougie entière")
def smc_ob_body(df, n=3, mode="touche", max_age=100):
    zones, _ = order_block_zones(df, n, use_body=True)
    return zone_signals(df, zones, mode, max_age)


@strategy("smc_breaker", "smc", {"n": [3, 5], "mode": ["touche", "rejet", "sans_rejet"], "max_age": [100, 200]},
          "Breaker block : OB cassé qui devient support/résistance inverse, joué au retour")
def smc_breaker(df, n=3, mode="touche", max_age=150):
    zones, _ = order_block_zones(df, n)
    return zone_signals(df, zones, mode, max_age, breaker=True)


@strategy("smc_fvg", "smc", {"min_atr": [0.1, 0.3, 0.5], "mode": ["touche", "rejet", "sans_rejet"],
                              "max_age": [30, 100]},
          "Fair Value Gap (imbalance) : retour dans le FVG")
def smc_fvg(df, min_atr=0.3, mode="touche", max_age=50):
    return zone_signals(df, fvg_zones(df, min_atr), mode, max_age)


@strategy("smc_fvg_trend", "smc", {"min_atr": [0.2, 0.5], "n": [3, 5], "mode": ["touche", "rejet"]},
          "FVG joué seulement dans le sens de la structure (BOS)")
def smc_fvg_trend(df, min_atr=0.3, n=3, mode="touche"):
    st = structure(df, n)
    sig = zone_signals(df, fvg_zones(df, min_atr), mode, 60)
    t = st["trend"]
    return sig.where(((sig > 0) & (t > 0)) | ((sig < 0) & (t < 0)), 0).astype(np.int8)


@strategy("smc_ifvg", "smc", {"min_atr": [0.2, 0.5], "mode": ["touche", "rejet"], "max_age": [60, 150]},
          "Inverse FVG : FVG invalidé qui s'inverse, joué au retour")
def smc_ifvg(df, min_atr=0.3, mode="touche", max_age=100):
    return zone_signals(df, fvg_zones(df, min_atr), mode, max_age, breaker=True)


@strategy("smc_bos", "smc", {"n": [2, 3, 5, 8]}, "Break of structure : continuation de tendance")
def smc_bos(df, n=3):
    st = structure(df, n)
    return _sig(pd.Series(st["bos_up"], index=df.index), pd.Series(st["bos_dn"], index=df.index))


@strategy("smc_choch", "smc", {"n": [2, 3, 5, 8]}, "Change of character : premier signe de retournement")
def smc_choch(df, n=3):
    st = structure(df, n)
    return _sig(pd.Series(st["choch_up"], index=df.index), pd.Series(st["choch_dn"], index=df.index))


@strategy("smc_liquidity_sweep", "smc", {"n": [3, 5, 10], "trend_only": [False, True]},
          "Liquidity sweep / stop hunt : mèche sous le dernier swing low puis clôture au-dessus")
def smc_liquidity_sweep(df, n=5, trend_only=False):
    st = structure(df, n)
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    lo, hi = st["last_sl"], st["last_sh"]
    long_ = (l < lo) & (c > lo)
    short = (h > hi) & (c < hi)
    if trend_only:
        long_ &= st["trend"] > 0
        short &= st["trend"] < 0
    return _sig(pd.Series(long_, index=df.index), pd.Series(short, index=df.index))


@strategy("smc_equal_hl_sweep", "smc", {"n": [3, 5], "tol_atr": [0.1, 0.25]},
          "Sweep d'equal highs / equal lows (double sommet/creux = pool de liquidité)")
def smc_equal_hl_sweep(df, n=3, tol_atr=0.2):
    ph, pl, _, _ = ind.pivots(df, n)
    a = ind.atr(df, 14).to_numpy()
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    out = np.zeros(len(df), dtype=np.int8)
    highs, lows = [], []
    eq_hi = eq_lo = np.nan
    for i in range(len(df)):
        if not np.isnan(ph[i]):
            highs = (highs + [ph[i]])[-2:]
            if len(highs) == 2 and abs(highs[0] - highs[1]) <= tol_atr * a[i]:
                eq_hi = max(highs)
        if not np.isnan(pl[i]):
            lows = (lows + [pl[i]])[-2:]
            if len(lows) == 2 and abs(lows[0] - lows[1]) <= tol_atr * a[i]:
                eq_lo = min(lows)
        if not np.isnan(eq_lo) and l[i] < eq_lo and c[i] > eq_lo:
            out[i], eq_lo = 1, np.nan
        elif not np.isnan(eq_hi) and h[i] > eq_hi and c[i] < eq_hi:
            out[i], eq_hi = -1, np.nan
    return pd.Series(out, index=df.index)


@strategy("smc_ote", "smc", {"n": [3, 5], "lo_fib": [0.5, 0.618], "hi_fib": [0.786],
                              "mode": ["touche", "rejet"]},
          "Optimal Trade Entry (ICT) : retracement Fibonacci 62-79 % après un BOS")
def smc_ote(df, n=3, lo_fib=0.618, hi_fib=0.786, mode="touche"):
    st = structure(df, n)
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    zones: dict = {}
    for i in np.flatnonzero(st["bos_up"] | st["choch_up"]):
        s = st["sl_idx"][i]
        if s < 0:
            continue
        lo, hi = l[s], h[s: i + 1].max()
        _add(zones, i, (1, hi - hi_fib * (hi - lo), hi - lo_fib * (hi - lo)))
    for i in np.flatnonzero(st["bos_dn"] | st["choch_dn"]):
        s = st["sh_idx"][i]
        if s < 0:
            continue
        hi, lo = h[s], l[s: i + 1].min()
        _add(zones, i, (-1, lo + lo_fib * (hi - lo), lo + hi_fib * (hi - lo)))
    return zone_signals(df, zones, mode, 80)


@strategy("smc_choch_ob", "smc", {"n": [3, 5], "mode": ["touche", "rejet"]},
          "CHoCH puis retour sur l'order block qui l'a créé (entrée de retournement SMC classique)")
def smc_choch_ob(df, n=3, mode="rejet"):
    st = structure(df, n)
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    zones: dict = {}
    for i in np.flatnonzero(st["choch_up"]):
        for j in range(i - 1, max(0, i - 40) - 1, -1):
            if c[j] < o[j]:
                _add(zones, i, (1, l[j], h[j]))
                break
    for i in np.flatnonzero(st["choch_dn"]):
        for j in range(i - 1, max(0, i - 40) - 1, -1):
            if c[j] > o[j]:
                _add(zones, i, (-1, l[j], h[j]))
                break
    return zone_signals(df, zones, mode, 100)


# ===================================================================== zones & retours
@strategy("supply_demand", "zones", {"base_k": [0.4, 0.6], "exp_k": [1.2, 1.8], "mode": MODES,
                                      "max_age": [100, 300]},
          "Zones d'offre et de demande (base + bougie explosive), jouées au premier retour")
def supply_demand(df, base_k=0.5, exp_k=1.5, mode="touche", max_age=200):
    return zone_signals(df, supply_demand_zones(df, base_k, exp_k), mode, max_age)


@strategy("sd_flip", "zones", {"base_k": [0.5], "exp_k": [1.5], "mode": ["touche", "rejet"]},
          "Zone offre/demande cassée qui s'inverse (demande -> offre) puis jouée au retour")
def sd_flip(df, base_k=0.5, exp_k=1.5, mode="touche"):
    return zone_signals(df, supply_demand_zones(df, base_k, exp_k), mode, 250, breaker=True)


@strategy("break_retest", "zones", {"n": [3, 5, 10], "width_atr": [0.2, 0.5], "mode": ["touche", "rejet", "sans_rejet"]},
          "Cassure d'un swing puis RETOUR sur le niveau cassé (retest), avec ou sans rejet")
def break_retest(df, n=5, width_atr=0.3, mode="rejet"):
    st = structure(df, n)
    a = ind.atr(df, 14).to_numpy()
    zones: dict = {}
    for i in np.flatnonzero(st["bos_up"] | st["choch_up"]):
        lvl = st["last_sh"][i]
        _add(zones, i, (1, lvl - width_atr * a[i], lvl))
    for i in np.flatnonzero(st["bos_dn"] | st["choch_dn"]):
        lvl = st["last_sl"][i]
        _add(zones, i, (-1, lvl, lvl + width_atr * a[i]))
    return zone_signals(df, zones, mode, 60)


@strategy("sr_zone", "zones", {"n": [3, 5], "tol_atr": [0.25, 0.5], "touches": [2, 3],
                                "mode": ["rejet", "sans_rejet", "cassure"]},
          "Zones support/résistance horizontales (plusieurs swings au même niveau)")
def sr_zone(df, n=5, tol_atr=0.3, touches=2, mode="rejet"):
    ph, pl, _, _ = ind.pivots(df, n)
    a = ind.atr(df, 14).to_numpy()
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    out = np.zeros(len(df), dtype=np.int8)
    levels: list[float] = []
    for i in range(len(df)):
        for v in (ph[i], pl[i]):
            if not np.isnan(v):
                levels = (levels + [v])[-40:]
        if np.isnan(a[i]) or not levels:
            continue
        lv = np.array(levels)
        tol = tol_atr * a[i]
        body = abs(c[i] - o[i])
        # niveaux touchés par cette bougie et suffisamment testés
        for x in lv[(lv >= l[i] - tol) & (lv <= h[i] + tol)]:
            if (np.abs(lv - x) <= tol).sum() < touches:
                continue
            lw, uw = min(o[i], c[i]) - l[i], h[i] - max(o[i], c[i])
            if mode == "cassure":
                if c[i] > x + tol and o[i] < x and body > uw:
                    out[i] = 1
                elif c[i] < x - tol and o[i] > x and body > lw:
                    out[i] = -1
            elif c[i] > x and l[i] <= x + tol:   # support
                rej = lw >= max(body, 1e-12)
                if (mode == "rejet") == rej:
                    out[i] = 1
            elif c[i] < x and h[i] >= x - tol:   # résistance
                rej = uw >= max(body, 1e-12)
                if (mode == "rejet") == rej:
                    out[i] = -1
            if out[i]:
                break
    return pd.Series(out, index=df.index)


@strategy("fib_pullback", "zones", {"n": [5, 10], "level": [0.382, 0.5, 0.618], "mode": ["touche", "rejet"]},
          "Retracement de Fibonacci dans la tendance (38.2 / 50 / 61.8 %)")
def fib_pullback(df, n=5, level=0.5, mode="rejet"):
    return smc_ote(df, n=n, lo_fib=level - 0.05, hi_fib=level + 0.05, mode=mode)


@strategy("double_top_bottom", "zones", {"n": [3, 5], "tol_atr": [0.3, 0.6]},
          "Double sommet / double creux avec cassure de la ligne de cou")
def double_top_bottom(df, n=5, tol_atr=0.5):
    ph, pl, _, _ = ind.pivots(df, n)
    a = ind.atr(df, 14).to_numpy()
    c = df["close"].to_numpy()
    out = np.zeros(len(df), dtype=np.int8)
    lows, highs = [], []
    last_ph = last_pl = np.nan
    neck_up = neck_dn = np.nan
    for i in range(len(df)):
        if not np.isnan(ph[i]):
            highs = (highs + [ph[i]])[-2:]
            if len(highs) == 2 and abs(highs[0] - highs[1]) <= tol_atr * a[i] and not np.isnan(last_pl):
                neck_dn = last_pl
            last_ph = ph[i]
        if not np.isnan(pl[i]):
            lows = (lows + [pl[i]])[-2:]
            if len(lows) == 2 and abs(lows[0] - lows[1]) <= tol_atr * a[i] and not np.isnan(last_ph):
                neck_up = last_ph
            last_pl = pl[i]
        if not np.isnan(neck_up) and c[i] > neck_up:
            out[i], neck_up = 1, np.nan
        elif not np.isnan(neck_dn) and c[i] < neck_dn:
            out[i], neck_dn = -1, np.nan
    return pd.Series(out, index=df.index)


# ============================================================== sessions, pivots, ICT
@strategy("ict_silver_bullet", "session", {"hour": [3, 10, 14], "min_atr": [0.1, 0.3]},
          "ICT Silver Bullet : FVG joué seulement dans une fenêtre d'1 h (heure du serveur MT5)")
def ict_silver_bullet(df, hour=10, min_atr=0.2):
    hrs = _hours(df)
    if hrs is None:
        return pd.Series(0, index=df.index, dtype=np.int8)
    return zone_signals(df, fvg_zones(df, min_atr), "touche", 20, allowed=(hrs == hour))


@strategy("asian_range_sweep", "session", {"end": [6, 7, 8]},
          "Judas swing : le prix balaie le haut/bas de la session asiatique puis réintègre le range")
def asian_range_sweep(df, end=7):
    hrs = _hours(df)
    if hrs is None:
        return pd.Series(0, index=df.index, dtype=np.int8)
    day = df.index.normalize()
    in_asia = hrs < end
    hi = df["high"].where(in_asia).groupby(day).transform("max").to_numpy()
    lo = df["low"].where(in_asia).groupby(day).transform("min").to_numpy()
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    after = (hrs >= end) & (hrs < end + 8)
    short = after & (h > hi) & (c < hi)
    long_ = after & (l < lo) & (c > lo)
    dser = pd.Series(day, index=df.index)
    s = pd.Series(short, index=df.index)
    lg = pd.Series(long_, index=df.index)
    first_s = s & (s.astype(int).groupby(dser).cumsum() == 1)
    first_l = lg & (lg.astype(int).groupby(dser).cumsum() == 1)
    return _sig(first_l, first_s)


@strategy("pdh_pdl", "session", {"mode": ["cassure", "sweep"]},
          "Plus haut / plus bas de la veille : cassure ou sweep (turtle soup)")
def pdh_pdl(df, mode="sweep"):
    pdh, pdl = _prev_day_levels(df)
    if pdh is None:
        return pd.Series(0, index=df.index, dtype=np.int8)
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    pc = np.r_[np.nan, c[:-1]]
    if mode == "cassure":
        long_, short = (c > pdh) & (pc <= pdh), (c < pdl) & (pc >= pdl)
    else:
        long_, short = (l < pdl) & (c > pdl), (h > pdh) & (c < pdh)
    return _sig(pd.Series(long_, index=df.index), pd.Series(short, index=df.index))


@strategy("pivot_points", "session", {"mode": ["rebond", "cassure"], "level": [1, 2]},
          "Points pivots journaliers classiques (P, S1/R1, S2/R2)")
def pivot_points(df, mode="rebond", level=1):
    pdh, pdl = _prev_day_levels(df)
    if pdh is None:
        return pd.Series(0, index=df.index, dtype=np.int8)
    day = df.index.normalize()
    pdc = df["close"].groupby(day).last().shift(1).reindex(day).to_numpy()
    p = (pdh + pdl + pdc) / 3
    r = 2 * p - pdl if level == 1 else p + (pdh - pdl)
    s = 2 * p - pdh if level == 1 else p - (pdh - pdl)
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    pc = np.r_[np.nan, c[:-1]]
    if mode == "rebond":
        long_, short = (l <= s) & (c > s), (h >= r) & (c < r)
    else:
        long_, short = (c > r) & (pc <= r), (c < s) & (pc >= s)
    return _sig(pd.Series(long_, index=df.index), pd.Series(short, index=df.index))


@strategy("opening_range", "session", {"start": [7, 8, 13, 14], "bars": [1, 2]},
          "Opening range breakout (ORB) de Londres / New York (heure du serveur)")
def opening_range(df, start=8, bars=1):
    hrs = _hours(df)
    if hrs is None:
        return pd.Series(0, index=df.index, dtype=np.int8)
    day = df.index.normalize()
    in_or = (hrs >= start) & (hrs < start + bars)
    hi = df["high"].where(in_or).groupby(day).transform("max")
    lo = df["low"].where(in_or).groupby(day).transform("min")
    c = df["close"]
    after = pd.Series((hrs >= start + bars) & (hrs < start + bars + 6), index=df.index)
    lg = after & (c > hi) & (c.shift(1) <= hi)
    sh = after & (c < lo) & (c.shift(1) >= lo)
    dser = pd.Series(day, index=df.index)
    return _sig(lg & (lg.astype(int).groupby(dser).cumsum() == 1), sh & (sh.astype(int).groupby(dser).cumsum() == 1))
