"""Moteur de backtest orienté R-multiples.

- Entrée à l'ouverture de la bougie qui suit le signal (pas de look-ahead).
- Stop loss : ATR, swing (plus bas/haut récent) ou pourcentage.
- Take profit : SL x R:R (ex. 1:2 -> TP à 2 fois la distance du SL). rr=None -> sortie sur signal inverse.
- Gestion : aucune, break-even à +1R, trailing stop ATR.
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
MANAGEMENT = ["none", "breakeven", "trailing"]


@dataclass(frozen=True)
class RiskConfig:
    sl_mode: str = "atr"          # atr | swing | pct
    sl_value: float = 1.5         # multiple d'ATR, nb de bougies du swing, ou % du prix
    rr: float | None = 2.0        # ratio risque:rendement ; None -> sortie sur signal opposé
    management: str = "none"      # none | breakeven | trailing
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


def run_backtest(
    df: pd.DataFrame,
    signals: pd.Series,
    cfg: RiskConfig,
    cost: float = 0.0,
    risk_pct: float = 1.0,
    return_trades: bool = False,
):
    """Backtest un vecteur de signaux avec une config de risque.

    cost = coût aller-retour en prix (spread + commission). Si les données contiennent une colonne « cost »
    (spread réel de chaque bougie + commission), c'est le plus grand des deux qui est retenu.
    Colonnes « swap_long » / « swap_short » (prix par nuit) : swaps comptés pour chaque nuit passée en position.
    Colonne « news_block » : aucune entrée sur une bougie qui s'ouvre près d'une annonce économique importante.
    """
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    sig = signals.to_numpy()
    atr_arr = ind.atr(df, 14).to_numpy()
    n = len(df)
    # coûts réels quand ils sont connus (données MT5) : spread de la bougie d'entrée + commission, et swaps par nuit
    bar_cost = df["cost"].to_numpy(dtype=float) if "cost" in df.columns else None
    has_swap = "swap_long" in df.columns and isinstance(df.index, pd.DatetimeIndex)
    if has_swap:
        sw_long = df["swap_long"].to_numpy(dtype=float)
        sw_short = df["swap_short"].to_numpy(dtype=float)
        day_num = (df.index.normalize().asi8 // 86_400_000_000_000)
    # filtre des nouvelles : pas d'entrée dans la fenêtre autour d'une annonce à fort impact
    news_block = df["news_block"].to_numpy(dtype=bool) if "news_block" in df.columns else None

    if cfg.direction == "long":
        sig = np.where(sig > 0, sig, 0)
    elif cfg.direction == "short":
        sig = np.where(sig < 0, sig, 0)

    trades = []
    idx = np.flatnonzero(sig)
    ptr = 0
    while ptr < len(idx):
        i = idx[ptr]
        ptr += 1
        e = i + 1
        if e >= n or np.isnan(atr_arr[i]):
            continue
        if news_block is not None and news_block[e]:
            continue
        side = int(np.sign(sig[i]))
        entry = o[e]
        risk = _stop_distance(df, cfg, atr_arr, i, side, entry)
        if not np.isfinite(risk) or risk <= 0:
            continue
        sl = entry - side * risk
        tp = entry + side * cfg.rr * risk if cfg.rr else None
        be_done = False
        exit_px, exit_bar = None, None
        last = min(n - 1, e + cfg.max_hold)
        for j in range(e, last + 1):
            hit_sl = (l[j] <= sl) if side > 0 else (h[j] >= sl)
            hit_tp = tp is not None and ((h[j] >= tp) if side > 0 else (l[j] <= tp))
            if hit_sl:  # pire cas si les deux sont touchés
                # gap au-delà du stop : on sort à l'ouverture
                exit_px = min(sl, o[j]) if side > 0 else max(sl, o[j])
                exit_bar = j
                break
            if hit_tp:
                exit_px, exit_bar = tp, j
                break
            if tp is None and j > e and sig[j] == -side:
                exit_px, exit_bar = c[j], j
                break
            # gestion de position à la clôture de la bougie
            if cfg.management == "breakeven" and not be_done:
                if (c[j] - entry) * side >= risk:
                    sl, be_done = entry, True
            elif cfg.management == "trailing" and np.isfinite(atr_arr[j]):
                trail = c[j] - side * cfg.sl_value * atr_arr[j] if cfg.sl_mode == "atr" else c[j] - side * risk
                sl = max(sl, trail) if side > 0 else min(sl, trail)
        if exit_px is None:
            exit_px, exit_bar = c[last], last
        trade_cost = max(cost, bar_cost[e]) if bar_cost is not None and np.isfinite(bar_cost[e]) else cost
        swap = 0.0
        if has_swap:  # swap positif = crédit, négatif = frais (convention MT5), une fois par nuit passée
            nights = int(day_num[exit_bar] - day_num[e])
            swap = (sw_long[e] if side > 0 else sw_short[e]) * nights
        r = ((exit_px - entry) * side - trade_cost + swap) / risk
        trades.append((i, e, exit_bar, side, entry, exit_px, risk, r))
        # pas de positions simultanées : on saute les signaux pendant le trade
        while ptr < len(idx) and idx[ptr] < exit_bar:
            ptr += 1

    res = summarize(np.array([t[-1] for t in trades]), np.array([t[2] - t[1] + 1 for t in trades]), risk_pct)
    if return_trades:
        tdf = pd.DataFrame(trades, columns=["signal_bar", "entry_bar", "exit_bar", "side", "entry", "exit", "risk", "r"])
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
