"""LES BOTS GAP FILL ET STOCH de la plateforme Railway (Trading Lab, nasdaq-bot), reproduits dans le labo MT5.

Règles copiées de nasdaq_news_bot_PRET.py (_sig_gapfill, _sig_stoch et la « grille » qui les suit) :

GAP FILL  : de 9 h 45 à 13 h (heure de New York), en semaine, UNE fois par jour. Le gap = clôture de la bougie moins la
            clôture de la veille (16 h, fin de la séance cash). S'il fait au moins 0,5 ATR et que la bougie repart déjà
            vers la clôture de la veille -> trade vers la clôture (gap haussier = vente, gap baissier = achat).
            Risque = max(0,8 ATR ; 0,35 x gap) x multiplicateur de stop.
STOCH     : stochastique 14 (brut, non lissé) qui SORT de 20 vers le haut (achat, bougie verte) ou de 80 vers le bas
            (vente, bougie rouge), seulement en range (moyennes 50 et 200 à moins de 0,4 % l'une de l'autre).
            Risque = max(distance au plus bas / plus haut des 4 dernières bougies ; 0,6 ATR) x multiplicateur.
GESTION   : cible = tp_r x le risque ; à +1R (mèche) stop au point d'entrée ; à 0,66 x la cible, stop à +0,33 x la cible ;
            sortie au bout de max_bars bougies. Entrée à la clôture de la bougie du signal. ATR = moyenne simple 14.

ATTENTION : la grille de Railway vérifie le stop et la cible DÈS la bougie d'entrée, avec son plus haut et son plus
bas... qui ont eu lieu AVANT l'entrée (à la clôture). Des stops ou des cibles « touchés » en 0 bougie sont donc
impossibles en vrai (ça fausse dans les deux sens). replay(..., meme_bougie=True) reproduit Railway tel quel ;
meme_bougie=False est la version honnête.

Dans le labo, les deux stratégies (gapfill_ny, stoch_range) entrent au catalogue : recherche, paper trading, heures,
combinées et bots, avec la gestion « verrou ». Heure de New York = heure du serveur MT5 - 7 h (FTMO et la plupart des
courtiers MT5 suivent l'heure de New York + 7 h toute l'année) : réglable avec ny_offset.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .strategies import _sig, strategy

NY_OFFSET = 7.0
# Les meilleures configurations de la grille Railway (classement du 2026-10-05) : marché, période, stop x, cible R, bougies
BEST = {
    "GAPFILL": [("NAS100", "M15", 1.7, 2.0, 80), ("NAS100", "M15", 1.3, 4.0, 80), ("NAS100", "H1", 1.7, 4.0, 30)],
    "STOCH": [("NAS100", "M15", 1.7, 4.0, 30), ("NAS100", "M15", 1.3, 2.0, 80), ("NAS100", "M15", 1.7, 1.5, 80)],
}
MARKETS = {"NAS100": "US100.cash", "GOLD": "XAUUSD"}


def _atr_simple(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """ATR comme Railway : moyenne SIMPLE du vrai range des 14 dernières bougies."""
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def _ny_clock(df: pd.DataFrame, ny_offset: float):
    ny = df.index - pd.Timedelta(hours=float(ny_offset))
    return ny, np.asarray(ny.hour * 60 + ny.minute), np.asarray(ny.weekday)


def gap_levels(df: pd.DataFrame, ny_offset: float = NY_OFFSET) -> pd.Series:
    """Clôture de la VEILLE (dernière bougie de la séance cash, avant 16 h à New York) pour chaque bougie."""
    ny, mins, _ = _ny_clock(df, ny_offset)
    day = pd.Series(ny.normalize(), index=df.index)
    cash = (mins >= 9 * 60 + 30) & (mins < 16 * 60)
    last = df["close"].where(cash).groupby(day.values).last().dropna()
    prev = last.shift(1)
    return pd.Series(day.map(prev).to_numpy(dtype=float), index=df.index)


def _gap_signal(df: pd.DataFrame, min_gap: float, ny_offset: float):
    """(signal +1/-1/0, risque de base en prix) du GAP FILL."""
    _, mins, wd = _ny_clock(df, ny_offset)
    ref = gap_levels(df, ny_offset)
    atr = _atr_simple(df)
    c, o = df["close"], df["open"]
    gap = c - ref
    window = (wd < 5) & (mins >= 9 * 60 + 45) & (mins <= 13 * 60)
    short = window & (gap >= min_gap * atr) & (c < o)
    long_ = window & (gap <= -min_gap * atr) & (c > o)
    day = pd.Series((df.index - pd.Timedelta(hours=float(ny_offset))).normalize(), index=df.index)
    take = (short | long_).fillna(False)
    first = take & (take.astype(int).groupby(day.values).cumsum() == 1)   # une seule fois par jour
    sig = _sig(first & long_.fillna(False), first & short.fillna(False))
    risk = np.maximum(atr * 0.8, gap.abs() * 0.35)
    return sig, risk


def _stoch_signal(df: pd.DataFrame, k: int, spread: float, low: float, high: float):
    c, o = df["close"], df["open"]
    hh, ll = df["high"].rolling(k).max(), df["low"].rolling(k).min()
    rng = hh - ll
    st = ((c - ll) / rng.replace(0, np.nan) * 100).fillna(50.0)
    prev = st.shift(1)
    ma50, ma200 = c.rolling(50).mean(), c.rolling(200).mean()
    ranging = ((ma50 - ma200).abs() / c) <= spread
    atr = _atr_simple(df)
    long_ = ranging & (prev <= low) & (st > low) & (c > o)
    short = ranging & (prev >= high) & (st < high) & (c < o)
    sig = _sig(long_, short)
    lo4, hi4 = df["low"].rolling(4).min(), df["high"].rolling(4).max()
    risk = pd.Series(np.where(sig > 0, np.maximum(c - lo4, atr * 0.6), np.maximum(hi4 - c, atr * 0.6)), index=df.index)
    return sig, risk


@strategy("gapfill_ny", "session", {"min_gap": [0.5, 0.75], "ny_offset": [7]},
          "GAP FILL (bot Railway) : de 9h45 à 13h (New York), retour vers la clôture de la veille si le gap fait "
          ">= 0,5 ATR et que la bougie repart déjà dans ce sens ; une fois par jour")
def gapfill_ny(df, min_gap=0.5, ny_offset=NY_OFFSET):
    if not isinstance(df.index, pd.DatetimeIndex) or len(df) < 30:
        return pd.Series(0, index=df.index, dtype=np.int8)
    return _gap_signal(df, float(min_gap), float(ny_offset))[0]


@strategy("stoch_range", "stochastic", {"k": [14], "spread": [0.004]},
          "STOCH (bot Railway) : stochastique 14 qui sort de 20 / 80 avec une bougie dans le même sens, seulement en "
          "range (moyennes 50 et 200 à moins de 0,4 % l'une de l'autre)")
def stoch_range(df, k=14, spread=0.004):
    if len(df) < 210:
        return pd.Series(0, index=df.index, dtype=np.int8)
    return _stoch_signal(df, int(k), float(spread), 20.0, 80.0)[0]


# ------------------------------------------------------------------------------- jumeau exact de la grille Railway
def replay(df: pd.DataFrame, name: str, sl_mult: float, tp_r: float, max_bars: int, meme_bougie: bool = False,
           ny_offset: float = NY_OFFSET, cost: float = 0.0) -> pd.DataFrame:
    """Rejoue la grille de Railway sur un historique (une position à la fois par configuration).
    meme_bougie=True : comme Railway (le plus haut / bas de la bougie d'entrée compte, alors qu'il a eu lieu avant
    l'entrée) ; False : version honnête (seulement les bougies APRÈS l'entrée). cost : spread + commission en prix."""
    if name == "GAPFILL":
        sig, base = _gap_signal(df, 0.5, ny_offset)
    elif name == "STOCH":
        sig, base = _stoch_signal(df, 14, 0.004, 20.0, 80.0)
    else:
        raise ValueError(name)
    o, h, l, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    s, b = sig.to_numpy(), base.to_numpy(float)
    bar_cost = df["cost"].to_numpy(float) if "cost" in df.columns else np.full(len(df), float(cost))
    rows, k, n = [], 0, len(df)
    while k < n - 1:
        if s[k] == 0 or not np.isfinite(b[k]) or b[k] <= 0:
            k += 1
            continue
        side, e = int(s[k]), c[k]
        r = max(b[k] * sl_mult, e * 0.0008)
        sl, res, age = -1.0, None, 0
        j = k if meme_bougie else k + 1
        while j < n:
            if j > k:
                age += 1
            pmax = ((h[j] - e) if side > 0 else (e - l[j])) / r
            pmin = ((l[j] - e) if side > 0 else (e - h[j])) / r
            if pmin <= sl:          # le stop d'abord (prudent), comme Railway
                res = sl
                break
            if pmax >= tp_r:
                res = tp_r
                break
            if pmax >= tp_r * 0.66:  # le stop monte pour la bougie suivante
                sl = max(sl, tp_r * 0.33)
            elif pmax >= 1.0:
                sl = max(sl, 0.0)
            if age >= max_bars:
                res = max(((c[j] - e) if side > 0 else (e - c[j])) / r, sl)
                break
            j += 1
        if res is None:
            break
        cst = bar_cost[k] if np.isfinite(bar_cost[k]) else float(cost)
        why = "cible" if res >= tp_r - 0.05 else "stop" if res <= -0.99 else "breakeven" if abs(res) < 0.06 else \
            "verrou" if res > 0 else "temps"
        rows.append({"entry_time": df.index[k], "exit_time": df.index[min(j, n - 1)], "side": side, "entry": e,
                     "risk": r, "r": res - cst / r, "r_brut": res, "bougies": j - k, "sortie": why})
        k = max(j, k + 1)
    return pd.DataFrame(rows)


def summary(t: pd.DataFrame) -> dict:
    if t is None or not len(t):
        return {"trades": 0}
    r = t["r"].to_numpy(float)
    eq = np.cumsum(r)
    days = max(1, (pd.Timestamp(t["exit_time"].max()) - pd.Timestamp(t["entry_time"].min())).days)
    return {"trades": int(len(r)), "reussite": round(float((r > 0).mean() * 100), 1), "r_total": round(float(r.sum()), 1),
            "r_moyen": round(float(r.mean()), 3), "dd_r": round(float(np.max(np.maximum.accumulate(np.concatenate([[0], eq]))[1:] - eq)), 1),
            "trades_mois": round(len(r) / days * 30.4, 1), "cibles_0_bougie": int(((t["bougies"] == 0) & (t["sortie"] == "cible")).sum()),
            "du": f"{pd.Timestamp(t['entry_time'].min()):%Y-%m-%d}", "au": f"{pd.Timestamp(t['exit_time'].max()):%Y-%m-%d}"}


def candidate(name: str, sl_mult: float, tp_r: float, max_bars: int) -> dict:
    """La stratégie au format du labo (recherche, paper, bots). Stop : ATR du labo (0,8 ATR x multiplicateur pour le
    GAP FILL, 0,6 ATR x multiplicateur pour le STOCH : la partie « gap » / « 4 dernières bougies » du stop de Railway
    est approchée par l'ATR) ; entrée à l'ouverture de la bougie suivante (= la clôture du signal)."""
    if name == "GAPFILL":
        sig, sl = {"type": "single", "name": "gapfill_ny", "params": {"min_gap": 0.5, "ny_offset": NY_OFFSET}}, 0.8
    else:
        sig, sl = {"type": "single", "name": "stoch_range", "params": {"k": 14, "spread": 0.004}}, 0.6
    return {"signal": sig, "filter": "none",
            "risk": {"sl_mode": "atr", "sl_value": round(sl * sl_mult, 3), "rr": float(tp_r), "management": "verrou",
                     "max_hold": int(max_bars), "direction": "both"}}


def run(conn, out_dir, rules=None, risk_pct: float = 1.0, years: float = 2.0, log=print, make_bots: bool = True) -> dict:
    """Backtest des meilleures configs GAP FILL / STOCH sur l'historique MT5 : jumeau exact (comme Railway, et version
    honnête) + moteur du labo, puis les bots (challenge FTMO et compte perso 5k) de chacune."""
    import json
    from pathlib import Path

    from .backtest import RiskConfig, run_backtest
    from .evaluator import compute_signal
    from .ftmo import FtmoRules
    from .pont import generate_strategy_bot, single_strategy
    rules = rules or FtmoRules()
    out = Path(out_dir) / "railway"
    out.mkdir(parents=True, exist_ok=True)
    data, report = {}, {"configs": [], "ny_offset": NY_OFFSET}
    for name, cfgs in BEST.items():
        for mk, tf, slm, tpr, mb in cfgs:
            sym = MARKETS.get(mk, mk)
            key = (sym, tf)
            if key not in data:
                try:
                    df = conn.rates_years(sym, tf, years)
                    df = conn.enrich(df, sym) if hasattr(conn, "enrich") else df
                    data[key] = df
                except Exception as exc:
                    log(f"[railway] {sym} {tf} : pas de données ({exc})")
                    data[key] = None
            df = data[key]
            row = {"bot": name, "marche": mk, "symbole": sym, "timeframe": tf, "sl_mult": slm, "tp_r": tpr, "max_bars": mb}
            if df is None or len(df) < 300:
                row["erreur"] = "pas assez d'historique MT5"
                report["configs"].append(row)
                continue
            row["comme_railway"] = summary(replay(df, name, slm, tpr, mb, meme_bougie=True))
            row["honnete"] = summary(replay(df, name, slm, tpr, mb, meme_bougie=False))
            cand = candidate(name, slm, tpr, mb)
            sig = compute_signal(df, cand["signal"])
            res, tr = run_backtest(df, sig, RiskConfig(**cand["risk"]), risk_pct=risk_pct, return_trades=True)
            row["labo"] = {"trades": res.trades, "reussite": round(res.win_rate, 1), "r_total": round(res.total_r, 1),
                           "r_moyen": round(res.avg_r, 3), "dd_r": round(res.max_dd_r, 1)}
            row["candidate"] = cand
            if make_bots:
                comb = single_strategy(cand, sym, tf, risk_pct, f"{name} (Railway) {sym} {tf} SL x{slm:g} cible {tpr:g}R")
                row["bot_ftmo"] = str(generate_strategy_bot(comb, out_dir, 100_000.0, rules, risk_pct))
                from .comptes import ftmo_like, profile
                p = profile("perso")
                cp = single_strategy(cand, sym, tf, float(p["risk_pct"]), comb["nom"] + " — Compte perso",
                                     float(p["day_budget"]), float(p["total_budget"]))
                cp.update(profil="perso", composer=True, capital=float(p["capital"]))
                row["bot_perso"] = str(generate_strategy_bot(cp, out_dir, float(p["capital"]), ftmo_like(p), float(p["risk_pct"])))
            h, r = row["comme_railway"], row["honnete"]
            log(f"[railway] {name} {sym} {tf} SL x{slm:g} {tpr:g}R {mb} bougies : comme Railway {h.get('trades', 0)} trades "
                f"{h.get('r_total', 0):+}R | honnête {r.get('trades', 0)} trades {r.get('r_total', 0):+}R | labo "
                f"{row['labo']['trades']} trades {row['labo']['r_total']:+}R")
            report["configs"].append(row)
    (out / "railway.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return report
