"""Sources de données : MetaTrader 5 (réel), CSV, ou données synthétiques (mode démo/hors-ligne)."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]


def _mt5():
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:  # pragma: no cover - dépend de Windows
        raise RuntimeError(
            "Le package MetaTrader5 n'est pas installé. Il fonctionne uniquement sous Windows avec "
            "le terminal MT5 installé : pip install MetaTrader5"
        ) from exc
    return mt5


class MT5Connector:
    """Connexion au terminal MetaTrader 5 local.

    Identifiants lus depuis les arguments ou les variables d'environnement
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH (chemin de terminal64.exe, optionnel).
    """

    def __init__(self, login=None, password=None, server=None, path=None):
        self.mt5 = _mt5()
        self.login = int(login or os.environ.get("MT5_LOGIN", 0)) or None
        self.password = password or os.environ.get("MT5_PASSWORD")
        self.server = server or os.environ.get("MT5_SERVER")
        self.path = path or os.environ.get("MT5_PATH")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.mt5.shutdown()

    def connect(self):
        kwargs = {}
        if self.path:
            kwargs["path"] = self.path
        if self.login:
            kwargs.update(login=self.login, password=self.password, server=self.server)
        if not self.mt5.initialize(**kwargs):
            raise RuntimeError(f"Échec de connexion à MT5 : {self.mt5.last_error()}")
        info = self.mt5.account_info()
        if info is not None:
            print(f"[MT5] Connecté : compte {info.login} ({info.server}) | balance {info.balance} {info.currency}"
                  f" | {'DÉMO' if info.trade_mode == 0 else 'RÉEL'}")
        return self

    def symbols(self, pattern: str = "*") -> list[str]:
        return [s.name for s in (self.mt5.symbols_get(pattern) or [])]

    def symbol_info(self, symbol: str):
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"Symbole inconnu : {symbol}")
        if not info.visible:
            self.mt5.symbol_select(symbol, True)
        return info

    def rates(self, symbol: str, timeframe: str = "H1", bars: int = 10000) -> pd.DataFrame:
        self.symbol_info(symbol)
        tf = getattr(self.mt5, f"TIMEFRAME_{timeframe}")
        raw = self.mt5.copy_rates_from_pos(symbol, tf, 0, bars)
        if raw is None or len(raw) == 0:
            raise RuntimeError(f"Aucune donnée pour {symbol} {timeframe} : {self.mt5.last_error()}")
        df = pd.DataFrame(raw)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.set_index("time").rename(columns={"tick_volume": "volume"})
        return df[["open", "high", "low", "close", "volume", "spread"]]

    def cost_in_price(self, symbol: str, commission_points: float = 0.0) -> float:
        """Coût aller-retour approximatif (spread courant + commission) en unités de prix."""
        info = self.symbol_info(symbol)
        return (info.spread + commission_points) * info.point


def load_csv(path: str | Path) -> pd.DataFrame:
    """Charge un CSV OHLC (export MT5 ou autre). Colonnes attendues : time/date, open, high, low, close[, volume]."""
    df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [c.strip("<>").lower() for c in df.columns]
    if "date" in df.columns and "time" in df.columns:
        df["time"] = pd.to_datetime(df["date"].astype(str) + " " + df["time"].astype(str))
    elif "date" in df.columns:
        df["time"] = pd.to_datetime(df["date"])
    else:
        df["time"] = pd.to_datetime(df["time"])
    for vol_col in ("tickvol", "tick_volume", "vol"):
        if "volume" not in df.columns and vol_col in df.columns:
            df["volume"] = df[vol_col]
    if "volume" not in df.columns:
        df["volume"] = 1.0
    return df.set_index("time")[["open", "high", "low", "close", "volume"]].sort_index()


def synthetic(bars: int = 8000, seed: int = 7, start_price: float = 1.10, freq: str = "h",
              momentum: float = 0.15) -> pd.DataFrame:
    """Marché synthétique à régimes (tendance / range / volatilité) pour tester la plateforme hors MT5.

    ATTENTION : les résultats sur données synthétiques ne disent RIEN sur un vrai marché.
    """
    rng = np.random.default_rng(seed)
    regime_len = rng.integers(150, 600, size=bars // 150 + 2)
    drift, vol = [], []
    for L in regime_len:
        kind = rng.choice(["trend_up", "trend_down", "range", "volatile"])
        d = {"trend_up": 6e-5, "trend_down": -6e-5, "range": 0.0, "volatile": 0.0}[kind]
        v = {"trend_up": 1.2e-3, "trend_down": 1.2e-3, "range": 8e-4, "volatile": 2.2e-3}[kind]
        drift += [d] * int(L)
        vol += [v] * int(L)
    drift, vol = np.array(drift[:bars]), np.array(vol[:bars])
    shocks = drift + vol * rng.standard_t(5, size=bars) / np.sqrt(5 / 3)
    # léger momentum (autocorrélation) : un edge réel que la plateforme doit être capable de retrouver
    rets = np.empty(bars)
    rets[0] = shocks[0]
    for i in range(1, bars):
        rets[i] = momentum * rets[i - 1] + shocks[i]
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start_price], close[:-1]])
    wick = np.abs(rng.normal(0, vol * 0.6, size=bars)) * close
    high = np.maximum(open_, close) + wick
    low = np.minimum(open_, close) - np.abs(rng.normal(0, vol * 0.6, size=bars)) * close
    idx = pd.date_range("2020-01-01", periods=bars, freq=freq)
    volume = rng.integers(100, 5000, size=bars).astype(float)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=idx)
