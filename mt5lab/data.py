"""Sources de données : MetaTrader 5 (réel), CSV, ou données synthétiques (mode démo/hors-ligne)."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]


def load_env(path: str | Path = ".env") -> None:
    """Charge un fichier .env (CLE=valeur) dans les variables d'environnement, sans écraser l'existant."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _mt5():
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:  # pragma: no cover - dépend de Windows
        raise RuntimeError(
            "Le package MetaTrader5 n'est pas installé. Il fonctionne uniquement sous Windows (Python 64 bits) "
            "avec le terminal MT5 installé. Lancez install.bat ou : pip install MetaTrader5"
        ) from exc
    return mt5


class MT5Connector:
    """Connexion au terminal MetaTrader 5 local.

    Identifiants lus depuis les arguments, les variables d'environnement ou le fichier .env :
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH (chemin de terminal64.exe, optionnel).
    Sans identifiants, on se branche sur le compte déjà connecté dans le terminal.
    """

    def __init__(self, login=None, password=None, server=None, path=None):
        load_env()
        self.mt5 = _mt5()
        self.login = int(login or os.environ.get("MT5_LOGIN") or 0) or None
        self.password = password or os.environ.get("MT5_PASSWORD")
        self.server = server or os.environ.get("MT5_SERVER")
        self.path = path or os.environ.get("MT5_PATH")
        self._resolved: dict[str, str] = {}

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.mt5.shutdown()

    def connect(self, verbose: bool = True):
        kwargs = {"timeout": 60_000}
        if self.path:
            kwargs["path"] = self.path
        if self.login:
            kwargs.update(login=self.login, password=self.password or "", server=self.server or "")
        if not self.mt5.initialize(**kwargs):
            err = self.mt5.last_error()
            raise RuntimeError(
                f"Échec de connexion à MT5 : {err}\n"
                "  - Le terminal MT5 est-il installé et ouvert ?\n"
                "  - Plusieurs MT5 installés ? Indiquez MT5_PATH=C:\\...\\terminal64.exe dans .env\n"
                "  - Identifiants : vérifiez MT5_LOGIN / MT5_PASSWORD / MT5_SERVER (nom exact du serveur)"
            )
        info = self.mt5.account_info()
        if info is None:
            raise RuntimeError("Terminal ouvert mais aucun compte connecté : connectez-vous dans MT5 ou remplissez .env")
        if verbose:
            print(f"[MT5] Connecté : compte {info.login} ({info.server}) | balance {info.balance} {info.currency}"
                  f" | {self.account_kind()}")
        return self

    def account_kind(self) -> str:
        mode = self.mt5.account_info().trade_mode
        return {0: "DÉMO", 1: "CONCOURS", 2: "RÉEL"}.get(mode, str(mode))

    def is_demo(self) -> bool:
        return self.mt5.account_info().trade_mode == self.mt5.ACCOUNT_TRADE_MODE_DEMO

    def symbols(self, pattern: str = "*") -> list[str]:
        return [s.name for s in (self.mt5.symbols_get(pattern) or [])]

    def resolve(self, symbol: str) -> str:
        """Trouve le nom exact chez le courtier (EURUSD -> EURUSD.m, EURUSDm, EURUSD.raw...)."""
        if symbol in self._resolved:
            return self._resolved[symbol]
        name = symbol
        if self.mt5.symbol_info(symbol) is None:
            cands = [s for s in self.symbols(f"*{symbol}*") if s.upper().startswith(symbol.upper())]
            if not cands:
                raise RuntimeError(f"Symbole introuvable chez ce courtier : {symbol}")
            name = min(cands, key=len)
            print(f"[MT5] {symbol} -> {name}")
        self._resolved[symbol] = name
        return name

    def symbol_info(self, symbol: str):
        symbol = self.resolve(symbol)
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"Symbole inconnu : {symbol}")
        if not info.visible:
            self.mt5.symbol_select(symbol, True)
        return info

    def filling_mode(self, symbol: str) -> int:
        """Mode de remplissage accepté par le courtier (cause fréquente du retcode 10030)."""
        flags = self.symbol_info(symbol).filling_mode
        if flags & 1:
            return self.mt5.ORDER_FILLING_FOK
        if flags & 2:
            return self.mt5.ORDER_FILLING_IOC
        return self.mt5.ORDER_FILLING_RETURN

    def rates(self, symbol: str, timeframe: str = "H1", bars: int = 10000) -> pd.DataFrame:
        symbol = self.resolve(symbol)
        self.symbol_info(symbol)
        tf = getattr(self.mt5, f"TIMEFRAME_{timeframe}")
        raw = None
        n = bars
        while n >= 500:  # MT5 refuse si on demande plus que « Max bougies dans le graphique »
            raw = self.mt5.copy_rates_from_pos(symbol, tf, 0, n)
            if raw is not None and len(raw):
                break
            n //= 2
        if raw is None or len(raw) == 0:
            raise RuntimeError(f"Aucune donnée pour {symbol} {timeframe} : {self.mt5.last_error()}")
        if len(raw) < bars:
            print(f"[MT5] {symbol} {timeframe} : {len(raw)} bougies disponibles (demandé {bars}). Pour plus "
                  "d'historique : Outils > Options > Graphiques > « Barres max. dans le graphique » = Unlimited")
        df = pd.DataFrame(raw)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df = df.set_index("time").rename(columns={"tick_volume": "volume"})
        return df[["open", "high", "low", "close", "volume", "spread"]]

    def cost_in_price(self, symbol: str, commission_points: float = 0.0, commission_per_lot: float = 0.0) -> float:
        """Coût aller-retour approximatif en unités de prix : spread courant + commission.

        commission_per_lot : commission aller-retour en devise du compte pour 1 lot (ex. 5 $ chez FTMO sur le forex),
        convertie en prix grâce à la valeur du tick du symbole.
        """
        info = self.symbol_info(symbol)
        cost = (info.spread + commission_points) * info.point
        tick_value, tick_size = info.trade_tick_value or 0.0, info.trade_tick_size or info.point
        if commission_per_lot and tick_value > 0:
            cost += commission_per_lot / tick_value * tick_size
        return cost

    def diagnose(self, symbols=("EURUSD",), timeframe: str = "H1") -> bool:
        """Vérifie tout ce qu'il faut pour la recherche et le trading. Renvoie True si tout est OK."""
        ok = True

        def line(good, msg):
            nonlocal ok
            ok &= bool(good)
            print(f"  [{'OK' if good else '!!'}] {msg}")

        term = self.mt5.terminal_info()
        acc = self.mt5.account_info()
        print("Diagnostic MT5")
        line(True, f"Package MetaTrader5 {getattr(self.mt5, '__version__', '?')}")
        line(term is not None and term.connected, f"Terminal connecté au serveur ({getattr(term, 'company', '?')})")
        line(True, f"Compte {acc.login} sur {acc.server} | {self.account_kind()} | "
                   f"{acc.balance} {acc.currency} | levier 1:{acc.leverage}")
        print(f"  [--] Algo Trading {'activé' if term is not None and term.trade_allowed else 'désactivé'} "
              "(inutile pour la recherche et le paper trading : aucun ordre n'est envoyé)")
        for sym in symbols:
            try:
                name = self.resolve(sym)
                info = self.symbol_info(name)
                df = self.rates(name, timeframe, 5000)
                line(True, f"{name} : {len(df)} bougies {timeframe} du {df.index[0]} au {df.index[-1]} | spread "
                           f"{info.spread} pts | lot min {info.volume_min} | heure serveur dernière bougie "
                           f"{df.index[-1]:%H:%M}")
            except Exception as exc:
                line(False, f"{sym} : {exc}")
        print("Tout est prêt." if ok else "Corrigez les points [!!] ci-dessus.")
        return ok


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
