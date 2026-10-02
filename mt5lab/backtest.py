"""Moteur de backtest orienté R-multiples.

- Entrée à l'ouverture de la bougie qui suit le signal (pas de look-ahead).
- Stop loss : ATR, swing (plus bas/haut récent) ou pourcentage.
- Take profit : SL x R:R (ex. 1:2 -> TP à 2 fois la distance du SL). rr=None -> sortie sur signal inverse.
- Gestion : aucune, break-even à +1R, trailing stop ATR, PALIERS (break-even à +1R, stop à +1R une fois à +2R,
  à +2R une fois à +3R...), INTELLIGENTE (paliers + sortie avant un retournement : signal inverse de la stratégie,
  ou le prix rend 1 ATR après avoir atteint +1R).
- Si SL et TP sont touchés dans la même bougie, on suppose le PIRE cas (SL touché).
- Coûts : spread + commission exprimés en prix.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from . import indicators as ind

RR_LEVELS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, None]
SL_MODES = {"atr": [1.0, 1.5, 2.0, 3.0], "swing": [5, 10, 20], "pct": [0.25, 0.5, 1.0]}
MANAGEMENT = ["none", "breakeven", "trailing", "paliers", "intelligente"]
MGMT_CODE = {"none": 0, "breakeven": 1, "trailing": 2, "paliers": 3, "intelligente": 4}


@dataclass(frozen=True)
class RiskConfig:
    sl_mode: str = "atr"          # atr | swing | pct
    sl_value: float = 1.5         # multiple d'ATR, nb de bougies du swing, ou % du prix
    rr: float | None = 2.0        # ratio risque:rendement ; None -> sortie sur signal opposé
    management: str = "none"      # none | breakeven | trailing | paliers | intelligente
    max_hold: int = 200           # nb max de bougies en position
    direction: str = "both"       # both | long | short

    def label(self) -> str:
        rr = f"1:{self.rr:g}" if self.rr else "signal"
        return f"SL {self.sl_mode}={self.sl_value:g} | R:R {rr} | {self.management} | {self.direction}"


@dataclass
class Result:
    trades: int
    win_rate: float
    avg_r: float            # espérance en R par trade
    total_r: float
    profit_factor: float
    max_dd_r: float         # drawdown max en R
    sharpe: float           # Sharpe des trades (annualisation non appliquée)
    return_pct: float       # rendement avec risque fixe de risk_pct par trade (composé)
    max_dd_pct: float
    avg_bars: float

    def to_dict(self):
        return asdict(self)


EMPTY = Result(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _stop_distance(df: pd.DataFrame, cfg: RiskConfig, atr_arr: np.ndarray, i: int, side: int, entry: float) -> float:
    if cfg.sl_mode == "atr":
        return cfg.sl_value * atr_arr[i]
    if cfg.sl_mode == "pct":
        return entry * cfg.sl_value / 100.0
    if cfg.sl_mode == "swing":
        n = int(cfg.sl_value)
        lo = df["low"].to_numpy()[max(0, i - n + 1): i + 1]
        hi = df["high"].to_numpy()[max(0, i - n + 1): i + 1]
        d = (entry - lo.min()) if side > 0 else (hi.max() - entry)
        # on évite les stops ridiculement serrés
        return max(d, 0.25 * atr_arr[i])
    raise ValueError(cfg.sl_mode)


def _core(o, h, l, c, sig, atr, sl_mode, sl_value, rr, mgmt, max_hold, cost, cost_mult,
          bar_cost, use_bar_cost, sw_long, sw_short, day_num, has_swap, news, has_news, wk_last, weekend_exit):
    """Boucle de simulation (compilée par numba quand il est installé : même logique, bien plus rapide).
    sl_mode : 0 atr, 1 pct, 2 swing ; rr <= 0 : sortie sur signal opposé ; mgmt : 0 aucune, 1 break-even, 2 trailing,
    3 paliers (stop remonté d'1R à chaque R gagné, à la clôture), 4 intelligente (paliers + signal inverse + le prix rend
    1 ATR depuis son meilleur cours après avoir atteint +1R).
    weekend_exit : position fermée à la clôture de la dernière bougie avant le week-end (wk_last), pas d'entrée
    sur cette bougie (compte FTMO Standard : pas de position pendant le week-end)."""
    n = len(o)
    m = 0
    for k in range(n):
        if sig[k] != 0:
            m += 1
    out = np.empty((m, 8))
    cnt = 0
    k = 0
    while k < n:
        i = k
        k += 1
        if sig[i] == 0:
            continue
        e = i + 1
        if e >= n or np.isnan(atr[i]):
            continue
        if has_news and news[e]:
            continue
        if weekend_exit and wk_last[e]:
            continue
        side = 1 if sig[i] > 0 else -1
        entry = o[e]
        if sl_mode == 0:
            risk = sl_value * atr[i]
        elif sl_mode == 1:
            risk = entry * sl_value / 100.0
        else:
            nb = int(sl_value)
            a = i - nb + 1
            if a < 0:
                a = 0
            lo = l[a]
            hi = h[a]
            for q in range(a, i + 1):
                if l[q] < lo:
                    lo = l[q]
                if h[q] > hi:
                    hi = h[q]
            d = (entry - lo) if side > 0 else (hi - entry)
            risk = d if d > 0.25 * atr[i] else 0.25 * atr[i]
        if not np.isfinite(risk) or risk <= 0:
            continue
        sl = entry - side * risk
        has_tp = rr > 0
        tp = entry + side * rr * risk if has_tp else 0.0
        be_done = False
        best = entry
        exit_px = np.nan
        exit_bar = -1
        last = e + max_hold
        if last > n - 1:
            last = n - 1
        for j in range(e, last + 1):
            hit_sl = (l[j] <= sl) if side > 0 else (h[j] >= sl)
            hit_tp = has_tp and ((h[j] >= tp) if side > 0 else (l[j] <= tp))
            if hit_sl:
                if side > 0:
                    exit_px = sl if sl < o[j] else o[j]
                else:
                    exit_px = sl if sl > o[j] else o[j]
                exit_bar = j
                break
            if hit_tp:
                exit_px = tp
                exit_bar = j
                break
            if (not has_tp) and j > e and sig[j] == -side:
                exit_px = c[j]
                exit_bar = j
                break
            if weekend_exit and wk_last[j]:  # vendredi soir : on ferme avant le week-end
                exit_px = c[j]
                exit_bar = j
                break
            if mgmt == 1 and not be_done:
                if (c[j] - entry) * side >= risk:
                    sl = entry
                    be_done = True
            elif mgmt == 2 and np.isfinite(atr[j]):
                trail = c[j] - side * sl_value * atr[j] if sl_mode == 0 else c[j] - side * risk
                if side > 0:
                    sl = sl if sl > trail else trail
                else:
                    sl = sl if sl < trail else trail
            elif mgmt >= 3:
                if mgmt == 4:  # sortie intelligente, à la clôture de la bougie
                    if (c[j] - best) * side > 0:
                        best = c[j]
                    if j > e and sig[j] == -side:  # la stratégie donne le signal inverse
                        exit_px = c[j]
                        exit_bar = j
                        break
                    if (best - entry) * side >= risk and np.isfinite(atr[j]) and (best - c[j]) * side >= atr[j]:
                        exit_px = c[j]  # retournement : le prix rend 1 ATR depuis son meilleur cours
                        exit_bar = j
                        break
                prog = (c[j] - entry) * side / risk
                if prog >= 1.0:  # paliers : +1R -> break-even, +2R -> stop à +1R, +3R -> stop à +2R...
                    lock = entry + side * (np.floor(prog) - 1.0) * risk
                    if side > 0:
                        sl = sl if sl > lock else lock
                    else:
                        sl = sl if sl < lock else lock
        if exit_bar < 0:
            exit_px = c[last]
            exit_bar = last
        tc = cost
        if use_bar_cost and np.isfinite(bar_cost[e]):
            tc = bar_cost[e]
        tc = tc * cost_mult
        swap = 0.0
        if has_swap:
            nights = day_num[exit_bar] - day_num[e]
            swap = (sw_long[e] if side > 0 else sw_short[e]) * nights
        out[cnt, 0] = i
        out[cnt, 1] = e
        out[cnt, 2] = exit_bar
        out[cnt, 3] = side
        out[cnt, 4] = entry
        out[cnt, 5] = exit_px
        out[cnt, 6] = risk
        out[cnt, 7] = ((exit_px - entry) * side - tc + swap) / risk
        cnt += 1
        # pas de positions simultanées : on saute les signaux pendant le trade
        if k < exit_bar:
            k = exit_bar
    return out[:cnt]


try:  # l'INGÉNIEUR DE VITESSE : boucle compilée si numba est installé (pip install numba)
    import numba as _numba
    _core_fast = _numba.njit(cache=True, nogil=True)(_core)
    NUMBA = True
except Exception:  # pragma: no cover - sans numba : même calcul en Python
    _core_fast = _core
    NUMBA = False

_ARR_CACHE: dict = {}


def _arrays(df: pd.DataFrame) -> dict:
    """Tableaux du DataFrame (prix, ATR, coûts, swaps, nouvelles) calculés une seule fois par jeu de données."""
    key = (id(df), len(df), df.index[0] if len(df) else None, df.index[-1] if len(df) else None)
    a = _ARR_CACHE.get(key)
    if a is not None:
        return a
    n = len(df)
    a = {"o": df["open"].to_numpy(dtype=float), "h": df["high"].to_numpy(dtype=float),
         "l": df["low"].to_numpy(dtype=float), "c": df["close"].to_numpy(dtype=float),
         "atr": ind.atr(df, 14).to_numpy(dtype=float)}
    a["use_bar_cost"] = "cost" in df.columns
    a["bar_cost"] = df["cost"].to_numpy(dtype=float) if a["use_bar_cost"] else np.zeros(1)
    a["has_swap"] = "swap_long" in df.columns and isinstance(df.index, pd.DatetimeIndex)
    if a["has_swap"]:
        a["sw_long"] = df["swap_long"].to_numpy(dtype=float)
        a["sw_short"] = df["swap_short"].to_numpy(dtype=float)
        a["day_num"] = (df.index.normalize().values.astype("datetime64[D]").astype(np.int64))
    else:
        a["sw_long"] = a["sw_short"] = np.zeros(1)
        a["day_num"] = np.zeros(1, dtype=np.int64)
    if isinstance(df.index, pd.DatetimeIndex) and n > 1:  # dernière bougie avant un trou de plus de 36 h
        ts = df.index.values.astype("datetime64[s]").astype(np.int64)  # en secondes, quelle que soit l'unité
        a["wk_last"] = np.concatenate([np.diff(ts) > 36 * 3600, [False]])
    else:
        a["wk_last"] = np.zeros(max(n, 1), dtype=np.bool_)
    a["has_news"] = "news_block" in df.columns
    a["news"] = df["news_block"].to_numpy(dtype=bool) if a["has_news"] else np.zeros(1, dtype=bool)
    if len(_ARR_CACHE) > 64:
        _ARR_CACHE.clear()
    _ARR_CACHE[key] = a
    return a


def run_backtest(
    df: pd.DataFrame,
    signals: pd.Series,
    cfg: RiskConfig,
    cost: float = 0.0,
    risk_pct: float = 1.0,
    return_trades: bool = False,
    cost_mult: float = 1.0,
    weekend_exit: bool = False,
):
    """Backtest un vecteur de signaux avec une config de risque.

    cost = coût aller-retour en prix (spread + commission), utilisé seulement si les données n'ont pas de colonne
    « cost » (spread historique de chaque bougie + commission, préparée par MT5Connector.enrich). Le spread
    « du moment » n'est PAS appliqué à l'historique : le samedi, MT5 affiche le spread très large de la fermeture
    du vendredi, ce qui aurait pénalisé tous les trades. cost_mult = 2 pour le test « coûts doublés ».
    Colonnes « swap_long » / « swap_short » (prix par nuit) : swaps comptés pour chaque nuit passée en position.
    Colonne « news_block » : aucune entrée sur une bougie qui s'ouvre près d'une annonce économique importante.
    weekend_exit = True : jamais de position pendant le week-end (fermeture le vendredi soir, compte Standard).
    """
    a = _arrays(df)
    sig = np.asarray(signals.to_numpy(), dtype=np.float64)
    if cfg.direction == "long":
        sig = np.where(sig > 0, sig, 0.0)
    elif cfg.direction == "short":
        sig = np.where(sig < 0, sig, 0.0)
    sl_mode = {"atr": 0, "pct": 1, "swing": 2}[cfg.sl_mode]
    mgmt = MGMT_CODE[cfg.management]
    t = _core_fast(a["o"], a["h"], a["l"], a["c"], sig, a["atr"], sl_mode, float(cfg.sl_value),
                   float(cfg.rr) if cfg.rr else 0.0, mgmt, int(cfg.max_hold), float(cost), float(cost_mult),
                   a["bar_cost"], a["use_bar_cost"], a["sw_long"], a["sw_short"], a["day_num"], a["has_swap"],
                   a["news"], a["has_news"], a["wk_last"], bool(weekend_exit))
    res = summarize(t[:, 7], (t[:, 2] - t[:, 1] + 1), risk_pct)
    if return_trades:
        tdf = pd.DataFrame(t, columns=["signal_bar", "entry_bar", "exit_bar", "side", "entry", "exit", "risk", "r"])
        for col in ("signal_bar", "entry_bar", "exit_bar", "side"):
            tdf[col] = tdf[col].astype(int)
        if len(tdf):
            tdf["entry_time"] = df.index[tdf["entry_bar"]]
            tdf["exit_time"] = df.index[tdf["exit_bar"]]
        return res, tdf
    return res


def summarize(r: np.ndarray, bars: np.ndarray, risk_pct: float = 1.0) -> Result:
    if len(r) == 0:
        return EMPTY
    wins, losses = r[r > 0], r[r <= 0]
    gross_loss = -losses.sum()
    pf = wins.sum() / gross_loss if gross_loss > 0 else (float("inf") if len(wins) else 0.0)
    cum = np.cumsum(r)
    dd_r = float(np.max(np.maximum.accumulate(np.concatenate([[0], cum]))[1:] - cum)) if len(cum) else 0.0
    eq = np.cumprod(1 + np.clip(r * risk_pct / 100.0, -0.99, None))
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))[1:]
    dd_pct = float(np.max((peak - eq) / peak) * 100)
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    return Result(
        trades=int(len(r)),
        win_rate=float(len(wins) / len(r) * 100),
        avg_r=float(r.mean()),
        total_r=float(r.sum()),
        profit_factor=float(min(pf, 99.0)),
        max_dd_r=dd_r,
        sharpe=float(r.mean() / sd * np.sqrt(len(r))) if sd > 0 else 0.0,
        return_pct=float((eq[-1] - 1) * 100),
        max_dd_pct=dd_pct,
        avg_bars=float(bars.mean()),
    )


def all_risk_configs(directions=("both",)) -> list[RiskConfig]:
    out = []
    for mode, vals in SL_MODES.items():
        for v in vals:
            for rr in RR_LEVELS:
                for m in MANAGEMENT:
                    if rr is None and m == "breakeven":
                        continue
                    for d in directions:
                        out.append(RiskConfig(mode, v, rr, m, 200, d))
    return out
