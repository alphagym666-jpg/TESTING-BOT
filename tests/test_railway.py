"""Bots GAP FILL et STOCH de la plateforme Railway reproduits dans le labo."""
import numpy as np
import pandas as pd

from mt5lab.backtest import RiskConfig, run_backtest
from mt5lab.evaluator import compute_signal
from mt5lab.railway import BEST, candidate, gap_levels, gapfill_ny, replay, stoch_range, summary


def _bars(days=3, start="2026-09-14 00:00"):
    """M15 en heure du serveur MT5 (New York + 7 h), 24 h/24, prix plat à 100."""
    idx = pd.date_range(start, periods=days * 96, freq="15min")
    df = pd.DataFrame({"open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 1}, index=idx)
    return df


def test_gapfill_trades_toward_yesterdays_close_once_a_day():
    df = _bars(3)
    # jour 2 : gap haussier de +3 à l'ouverture (9h30 NY = 16h30 serveur), bougie rouge à 9h45 NY (16h45 serveur)
    d2 = df.index.normalize().unique()[1]
    up = (df.index >= d2 + pd.Timedelta(hours=16, minutes=30)) & (df.index < d2 + pd.Timedelta(hours=23))
    df.loc[up, ["open", "high", "low", "close"]] = [103.0, 103.2, 102.8, 103.0]
    t945 = d2 + pd.Timedelta(hours=16, minutes=45)
    df.loc[t945, ["open", "close", "high", "low"]] = [103.2, 102.9, 103.3, 102.8]      # rouge
    df.loc[t945 + pd.Timedelta(minutes=15), ["open", "close"]] = [103.2, 102.9]         # encore rouge : pas 2 fois
    ref = gap_levels(df)
    assert ref.loc[t945] == 100.0                                                       # clôture de la veille (16 h NY)
    sig = gapfill_ny(df)
    assert sig.loc[t945] == -1 and int((sig != 0).sum()) == 1                          # vente, une seule fois
    # avant 9h45 NY : rien
    assert (sig[df.index < t945] == 0).all()


def test_stoch_range_needs_a_range_and_a_candle_in_the_same_direction():
    rng = np.random.default_rng(1)
    n = 400
    idx = pd.date_range("2026-09-01", periods=n, freq="15min")
    c = 100 + np.sin(np.arange(n) / 6) * 1.5 + rng.normal(0, 0.05, n)       # range qui oscille
    df = pd.DataFrame({"open": np.r_[c[0], c[:-1]], "close": c}, index=idx)
    df["high"] = df[["open", "close"]].max(axis=1) + 0.05
    df["low"] = df[["open", "close"]].min(axis=1) - 0.05
    sig = stoch_range(df)
    assert (sig == 1).sum() > 3 and (sig == -1).sum() > 3
    green = df["close"] > df["open"]
    assert green[sig == 1].all() and (~green[sig == -1]).all()
    trend = df.copy()
    trend[["open", "high", "low", "close"]] = trend[["open", "high", "low", "close"]].add(np.arange(n) * 0.2, axis=0)
    assert (stoch_range(trend) == 0).all()                                     # tendance : pas de signal


def test_replay_entry_bar_bias_and_lock():
    """Railway compte le plus haut / bas de la bougie d'entrée (qui a eu lieu AVANT l'entrée) : la version honnête non."""
    df = _bars(3)
    d2 = df.index.normalize().unique()[1]
    up = (df.index >= d2 + pd.Timedelta(hours=16, minutes=30)) & (df.index < d2 + pd.Timedelta(hours=23))
    df.loc[up, ["open", "high", "low", "close"]] = [103.0, 103.2, 102.8, 103.0]
    t945 = d2 + pd.Timedelta(hours=16, minutes=45)
    df.loc[df.index >= d2 + pd.Timedelta(hours=23), ["open", "high", "low", "close"]] = [103.0, 103.2, 102.8, 103.0]
    # bougie d'entrée rouge, avec un très gros plus bas AVANT la clôture
    df.loc[t945, ["open", "high", "low", "close"]] = [103.2, 103.3, 100.0, 102.9]
    biased = replay(df, "GAPFILL", 1.0, 2.0, 80, meme_bougie=True)
    honest = replay(df, "GAPFILL", 1.0, 2.0, 80, meme_bougie=False)
    assert biased["sortie"].iloc[0] == "cible" and biased["bougies"].iloc[0] == 0       # « gagné » en 0 bougie
    assert honest["r"].iloc[0] < 2.0                                                    # en vrai : pas de cible
    assert summary(biased)["cibles_0_bougie"] == 1


def test_lock_management_in_backtest():
    """Gestion « verrou » : à 0,66 x la cible, le stop monte à +0,33 x la cible (mèches comprises)."""
    n = 60
    idx = pd.date_range("2026-09-01", periods=n, freq="15min")
    df = pd.DataFrame({"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0}, index=idx)
    df.loc[idx[22], "high"] = 102.8             # +2,8R avec un risque de 1 (cible 4R : 0,66 x 4 = 2,64)
    df.loc[idx[23:25], ["open", "high", "close"]] = [102.5, 102.6, 102.5]
    df.loc[idx[25], ["open", "low", "close"]] = [102.5, 98.0, 98.5]   # le prix retombe
    sig = pd.Series(0, index=idx, dtype=np.int8)
    sig.iloc[19] = 1
    cfg = RiskConfig("pct", 1.0, 4.0, "verrou", 80)
    _, tr = run_backtest(df, sig, cfg, return_trades=True)
    assert abs(tr["r"].iloc[0] - 1.32) < 0.05                                          # sorti au verrou +1,32R
    _, tr2 = run_backtest(df, sig, RiskConfig("pct", 1.0, 4.0, "none", 80), return_trades=True)
    assert tr2["r"].iloc[0] <= -0.99                                                    # sans verrou : stop


def test_candidates_run_in_the_lab():
    df = _bars(5)
    for name, cfgs in BEST.items():
        mk, tf, slm, tpr, mb = cfgs[0]
        c = candidate(name, slm, tpr, mb)
        assert c["risk"]["management"] == "verrou" and c["risk"]["rr"] == tpr and c["risk"]["max_hold"] == mb
        sig = compute_signal(df.assign(close=df["close"] + np.arange(len(df)) * 1e-3), c["signal"])
        run_backtest(df, sig, RiskConfig(**c["risk"]))


def test_run_backtests_and_creates_both_bots(tmp_path):
    from types import SimpleNamespace
    from mt5lab.railway import run
    rng = np.random.default_rng(3)
    n = 96 * 60
    idx = pd.date_range("2026-07-01", periods=n, freq="15min")
    c = 20000 + np.cumsum(rng.normal(0, 8, n))
    df = pd.DataFrame({"open": np.r_[c[0], c[:-1]], "close": c, "volume": 1}, index=idx)
    df["high"] = df[["open", "close"]].max(axis=1) + 5
    df["low"] = df[["open", "close"]].min(axis=1) - 5
    conn = SimpleNamespace(rates_years=lambda sym, tf, years: df if tf == "M15" else df.resample("1h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna())
    rep = run(conn, tmp_path, log=lambda m: None)
    assert len(rep["configs"]) == 6 and all("honnete" in r and "labo" in r for r in rep["configs"])
    bots = sorted(p.name for p in (tmp_path / "bots").iterdir())
    assert len(bots) == 12 and (tmp_path / "railway" / "railway.json").exists()
    import json
    perso = [json.loads((tmp_path / "bots" / b / "strategie.json").read_text(encoding="utf-8")) for b in bots]
    assert sum(1 for x in perso if x.get("profil") == "perso") == 6
