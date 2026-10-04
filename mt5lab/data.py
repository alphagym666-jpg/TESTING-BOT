"""Sources de données : MetaTrader 5 (réel), CSV, ou données synthétiques (mode démo/hors-ligne)."""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1"]

# historique utilisé par défaut pour les tests des agents (en années)
DEFAULT_YEARS = {"M1": 2, "M5": 2, "M15": 5, "M30": 5, "H1": 5, "H4": 5, "D1": 5, "W1": 10}
# bougies par an (marché ouvert ~24 h/24, 5 jours/7) pour estimer les quantités
BARS_PER_YEAR = {"M1": 374_400, "M5": 74_880, "M15": 24_960, "M30": 12_480, "H1": 6_240, "H4": 1_560, "D1": 260,
                 "W1": 52}

# noms courants -> noms utilisés par les courtiers (FTMO : US100.cash, XAUUSD...)
SYMBOL_ALIASES = {
    "NASDAQ": ["US100", "USTEC", "NAS100", "NDX100", "NQ100", "USTECH", "NSDQ"],
    "NAS100": ["US100", "USTEC", "NDX100"],
    "US100": ["USTEC", "NAS100", "NDX100"],
    "GOLD": ["XAUUSD", "GOLD"],
    "OR": ["XAUUSD", "GOLD"],
    "XAUUSD": ["GOLD"],
    "SP500": ["US500", "SPX500", "US500.cash"],
    "DOW": ["US30", "DJ30", "WS30"],
    "DAX": ["GER40", "DE40", "GER30"],
    "GER40": ["DE40", "GER30", "DAX40"],
    "US30": ["DJ30", "WS30", "DJI30"],
    # marchés macro (ingrédients inter-marchés, pas tradés)
    "DXY": ["USDX", "DX", "USDIDX", "DOLLARINDEX", "DXY.cash"],
    "VIX": ["VIX.cash", "VOLX", "VIXX", "UVXY"],
    "US10Y": ["UST10Y", "US10YR", "USTN10", "TNOTE", "T10Y", "USTNOTE"],
    "USOIL": ["WTI", "XTIUSD", "USOIL.cash", "CRUDE", "OILUSD"],
    "US500": ["SP500", "SPX500", "US500.cash", "SPX"],
}

# marchés qui bougent ensemble : (groupe, sens). Deux positions du même groupe et de même « exposition »
# (sens du trade x sens du marché) s'additionnent : ACHAT NASDAQ + ACHAT US30 = double pari sur les actions US ;
# ACHAT EURUSD + VENTE USDJPY = double pari contre le dollar.
CORRELATION_GROUPS = {
    "INDICES": (["NASDAQ", "NAS100", "US100", "USTEC", "US30", "DOW", "DJ30", "WS30", "SP500", "US500", "SPX500",
                 "GER40", "DE40", "DAX", "GER30"], 1),
    "DOLLAR": (["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"], -1),
    "DOLLAR+": (["USDJPY", "USDCHF", "USDCAD"], 1),
}


def correlation_of(symbol: str) -> tuple[str, int]:
    """(groupe de corrélation, sens) d'un symbole ; ("", 0) si indépendant (ex. or)."""
    up = symbol.upper()
    for name, (members, sign) in CORRELATION_GROUPS.items():
        if any(up.startswith(m) for m in members):
            return ("DOLLAR" if name.startswith("DOLLAR") else name), sign
    return "", 0


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
            name = None
            for base in [symbol] + SYMBOL_ALIASES.get(symbol.upper(), []):
                if self.mt5.symbol_info(base) is not None:
                    name = base
                    break
                cands = [s for s in self.symbols(f"*{base}*") if s.upper().startswith(base.upper())]
                if cands:
                    name = min(cands, key=len)
                    break
            if name is None:
                raise RuntimeError(f"Symbole introuvable chez ce courtier : {symbol}")
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

    def rates_years(self, symbol: str, timeframe: str = "H1", years: float | None = None) -> pd.DataFrame:
        """Historique sur une DURÉE (par défaut : 2 ans en M1/M5, 5 ans de M15 à D1), avec la période réellement
        couverte affichée. Si le serveur du courtier en fournit moins, on le dit clairement."""
        years = years or DEFAULT_YEARS.get(timeframe, 5)
        symbol = self.resolve(symbol)
        self.symbol_info(symbol)
        tf = getattr(self.mt5, f"TIMEFRAME_{timeframe}")
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc) + timedelta(days=1)
        start = now - timedelta(days=365.25 * years + 1)
        raw = None
        get_range = getattr(self.mt5, "copy_rates_range", None)
        if get_range is not None:
            for _ in range(3):  # le terminal télécharge l'historique à la demande : on réessaie
                raw = get_range(symbol, tf, start, now)
                if raw is not None and len(raw) and pd.to_datetime(raw["time"][0], unit="s") <= \
                        pd.Timestamp(start.replace(tzinfo=None)) + pd.Timedelta(days=30):
                    break
                time.sleep(2)
        if raw is None or not len(raw):  # repli : par nombre de bougies
            df = self.rates(symbol, timeframe, int(years * BARS_PER_YEAR.get(timeframe, 6240) * 1.05))
        else:
            df = pd.DataFrame(raw)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            df = df.set_index("time").rename(columns={"tick_volume": "volume"})[
                ["open", "high", "low", "close", "volume", "spread"]]
        covered = (df.index[-1] - df.index[0]).days / 365.25 if len(df) else 0
        msg = (f"[MT5] {symbol} {timeframe} : {len(df):,} bougies du {df.index[0]:%Y-%m-%d} au {df.index[-1]:%Y-%m-%d} "
               f"({covered:.1f} ans)").replace(",", " ")
        if covered < years * 0.9:
            msg += (f" -- ATTENTION : {years:g} ans demandés, le serveur n'en fournit que {covered:.1f}. "
                    "Dans MT5 : Outils > Options > Graphiques > « Barres max. dans l'historique » et "
                    "« dans le graphique » = Unlimited, puis ouvrez un graphique de ce timeframe et faites défiler "
                    "vers le passé (touche Début) pour télécharger plus d'historique.")
        print(msg)
        return df

    def typical_cost(self, df: pd.DataFrame, symbol: str, commission_points: float = 0.0,
                     commission_per_lot: float = 0.0) -> float:
        """Coût aller-retour typique (spread médian de l'historique + commission). Prévient si le spread du
        moment est anormalement large (week-end, nuit) : il n'est pas utilisé pour les tests."""
        now = self.cost_in_price(symbol, commission_points, commission_per_lot)
        if "cost" not in df.columns:
            return now
        typ = float(df["cost"].median())
        if now > 2 * typ:
            print(f"[MT5] {symbol} : spread du moment {now / typ:.1f}x plus large que d'habitude (marché fermé ou "
                  "nuit) -> ignoré, les tests utilisent le spread historique de chaque bougie")
        return typ

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

    def swap_in_price(self, symbol: str, price: float) -> tuple[float, float]:
        """Swaps par nuit (achat, vente) convertis en PRIX (positif = crédit, négatif = frais).
        Gère les modes MT5 les plus courants : points, devise du compte, pourcentage annuel. Sinon 0."""
        info = self.symbol_info(symbol)
        mode = int(getattr(info, "swap_mode", 0) or 0)
        sl, ss = float(getattr(info, "swap_long", 0) or 0), float(getattr(info, "swap_short", 0) or 0)
        if mode == 1:  # en points
            return sl * info.point, ss * info.point
        tick_value, tick_size = info.trade_tick_value or 0.0, info.trade_tick_size or info.point
        if mode in (2, 3, 4) and tick_value > 0:  # en argent pour 1 lot (devise du compte ~ approximation)
            return sl / tick_value * tick_size, ss / tick_value * tick_size
        if mode in (5, 6):  # pourcentage annuel du prix
            return price * sl / 100 / 360, price * ss / 100 / 360
        return 0.0, 0.0

    def currencies(self, symbol: str) -> set[str]:
        """Devises qui font bouger le symbole (pour le filtre des nouvelles). L'or et les indices US -> USD."""
        info = self.symbol_info(symbol)
        cur = {getattr(info, "currency_base", "") or "", getattr(info, "currency_profit", "") or ""}
        cur = {c.upper() for c in cur if c}
        cur = {"USD" if c in ("XAU", "XAG") else c for c in cur}
        return cur or {"USD"}

    def enrich(self, df: pd.DataFrame, symbol: str, commission_points: float = 0.0, commission_per_lot: float = 0.0,
               news: pd.DataFrame | None = None, news_window: int = 30) -> pd.DataFrame:
        """Ajoute les coûts RÉELS aux données : spread de chaque bougie + commission (colonne « cost »),
        swaps par nuit (« swap_long » / « swap_short ») et blocage autour des nouvelles (« news_block »)."""
        info = self.symbol_info(symbol)
        df = df.copy()
        comm = commission_points * info.point
        tick_value, tick_size = info.trade_tick_value or 0.0, info.trade_tick_size or info.point
        if commission_per_lot and tick_value > 0:
            comm += commission_per_lot / tick_value * tick_size
        if "spread" in df.columns:
            # le spread enregistré dans une bougie MT5 est souvent le plus bas de la bougie : on prend au moins
            # le spread médian de l'historique (jamais le spread du moment, faussé le week-end)
            sp = df["spread"].astype(float)
            med = float(sp[sp > 0].median()) if (sp > 0).any() else 0.0
            df["cost"] = np.maximum(sp, med) * info.point + comm
            # coûts RÉELS mesurés en direct (paper trading + bot MT5) : ajoutés s'ils sont plus élevés
            from .couts_reels import extra_points
            calib = getattr(self, "calib", None)
            if getattr(self, "slippage", True):  # glissement : mesuré en direct, ou estimé tant qu'il ne l'est pas
                extra, why = extra_points(calib, self.resolve(symbol) if calib else symbol, med)
                if extra > 0:
                    df["cost"] = df["cost"] + extra * info.point
                    if why != getattr(self, "_last_why", {}).get(symbol):
                        print(f"[coûts réels] {symbol} : +{extra:g} points par trade ({why})")
                        self.__dict__.setdefault("_last_why", {})[symbol] = why
        sl, ss = self.swap_in_price(symbol, float(df["close"].iloc[-1]))
        if sl or ss:
            df["swap_long"], df["swap_short"] = sl, ss
        if news is not None and len(news):
            df["news_block"] = news_mask(df.index, news, self.currencies(symbol), news_window)
            df = add_news_cols(df, news, self.currencies(symbol))  # ingrédients « avant / après une annonce »
        return df

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


def shuffle_prices(df: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    """L'AVOCAT DU DIABLE : mêmes bougies (tailles, mèches, volatilité, spreads, heures), mais dans un ORDRE AU HASARD.
    Toute tendance, tout cycle et tout motif réel disparaissent : une stratégie qui « marche » ici gagne par hasard."""
    rng = np.random.default_rng(seed)
    c = df["close"].to_numpy(float)
    prev = np.concatenate([[c[0]], c[:-1]])
    rel = {k: df[k].to_numpy(float) / prev for k in ("open", "high", "low", "close")}
    perm = rng.permutation(len(df))
    out = df.copy()
    closes = c[0] * np.cumprod(rel["close"][perm])
    prev_new = np.concatenate([[c[0]], closes[:-1]])
    for k in ("open", "high", "low"):
        out[k] = prev_new * rel[k][perm]
    out["close"] = closes
    for k in ("volume", "tick_volume", "real_volume"):
        if k in out.columns:
            out[k] = df[k].to_numpy()[perm]
    # les colonnes « ext: » (autres marchés) restent dans le vrai ordre : leur lien avec ces prix est cassé
    return out


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


# ---------------------------------------------------------------------------------------------- nouvelles économiques
NEWS_FILE = "news.csv"


def news_paths() -> list[Path]:
    """Endroits où chercher le calendrier exporté par mql5/ExportNews.mq5 (dossier data/ ou dossier commun MT5)."""
    paths = [Path("data") / NEWS_FILE, Path(NEWS_FILE)]
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files" / NEWS_FILE)
    return paths


def load_news(path: str | Path | None = None, min_importance: int = 3) -> pd.DataFrame | None:
    """Charge les nouvelles à fort impact (time, currency, importance, event). None si aucun fichier."""
    for p in ([Path(path)] if path else news_paths()):
        if p.exists():
            df = pd.read_csv(p, sep=None, engine="python")
            df.columns = [c.strip().lower() for c in df.columns]
            df["time"] = pd.to_datetime(df["time"], format="mixed")
            df["currency"] = df["currency"].astype(str).str.upper()
            if "importance" in df.columns:
                df = df[df["importance"].astype(float) >= min_importance]
            print(f"[nouvelles] {len(df)} annonces à fort impact chargées ({p})")
            return df.sort_values("time").reset_index(drop=True)
    return None


def news_mask(index: pd.DatetimeIndex, news: pd.DataFrame, currencies: set[str], window: int = 30) -> np.ndarray:
    """True pour les bougies dont l'OUVERTURE (= moment d'entrée) tombe à moins de `window` minutes d'une annonce
    à fort impact sur une devise du symbole. Appliqué seulement aux timeframes <= H1 (au-delà, une bougie
    contient presque toujours une annonce et le filtre bloquerait tout)."""
    out = np.zeros(len(index), dtype=bool)
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        return out
    step = pd.Series(index[1:] - index[:-1]).median()
    if step > pd.Timedelta(minutes=60):
        return out
    ev = news.loc[news["currency"].isin(currencies), "time"].to_numpy(dtype="datetime64[ns]")
    if not len(ev):
        return out
    t = index.to_numpy(dtype="datetime64[ns]")
    w = np.timedelta64(window, "m")
    lo = np.searchsorted(ev, t - w, side="left")
    hi = np.searchsorted(ev, t + w, side="right")
    return hi > lo


def news_blocked(news: pd.DataFrame | None, currencies: set[str], when, window: int = 30) -> bool:
    """Vrai si `when` est à moins de `window` minutes d'une annonce importante (utilisé par le paper trading)."""
    if news is None or not len(news):
        return False
    when = pd.Timestamp(when)
    ev = news.loc[news["currency"].isin(currencies), "time"]
    return bool(((ev - when).abs() <= pd.Timedelta(minutes=window)).any())


NEWS_CAP = 1440.0   # minutes : au-delà d'une journée, « pas d'annonce proche »


def _events(news: pd.DataFrame | None, currencies: set[str]) -> np.ndarray:
    if news is None or not len(news):
        return np.array([], dtype="datetime64[ns]")
    return np.sort(news.loc[news["currency"].isin(currencies), "time"].to_numpy(dtype="datetime64[ns]"))


def add_news_cols(df: pd.DataFrame, news: pd.DataFrame | None, currencies: set[str]) -> pd.DataFrame:
    """Colonnes « news_prev_min » (minutes depuis la dernière annonce importante d'une devise du marché) et
    « news_next_min » (minutes jusqu'à la prochaine, l'heure des annonces est connue à l'avance : pas de futur),
    plafonnées à 1 440. Ingrédients des inventeurs (ex. « entrer 30 à 90 min APRÈS une annonce »)."""
    ev = _events(news, currencies)
    if not len(ev) or not isinstance(df.index, pd.DatetimeIndex):
        return df
    t = df.index.to_numpy(dtype="datetime64[ns]")
    i = np.searchsorted(ev, t, side="right")
    prev = np.where(i > 0, (t - ev[np.maximum(i - 1, 0)]) / np.timedelta64(1, "m"), NEWS_CAP)
    j = np.searchsorted(ev, t, side="left")
    nxt = np.where(j < len(ev), (ev[np.minimum(j, len(ev) - 1)] - t) / np.timedelta64(1, "m"), NEWS_CAP)
    df = df.copy()
    df["news_prev_min"] = np.minimum(prev, NEWS_CAP).astype(np.float32)
    df["news_next_min"] = np.minimum(nxt, NEWS_CAP).astype(np.float32)
    return df


def news_distance(news: pd.DataFrame | None, currencies: set[str], when) -> tuple[float | None, float | None]:
    """(minutes depuis la dernière annonce importante, minutes jusqu'à la prochaine) à l'instant `when`."""
    ev = _events(news, currencies)
    if not len(ev):
        return None, None
    t = np.datetime64(pd.Timestamp(when).to_datetime64(), "ns")
    i = np.searchsorted(ev, t, side="right")
    j = np.searchsorted(ev, t, side="left")
    prev = float((t - ev[i - 1]) / np.timedelta64(1, "m")) if i > 0 else NEWS_CAP
    nxt = float((ev[j] - t) / np.timedelta64(1, "m")) if j < len(ev) else NEWS_CAP
    return min(prev, NEWS_CAP), min(nxt, NEWS_CAP)


def uses_news(obj) -> bool:
    """Une stratégie se sert-elle des annonces comme ingrédient ? (paper trading : colonnes à ajouter)"""
    import json
    return '"f": "news_' in json.dumps(obj)


# ---------------------------------------------------------------------------------------------- marchés macro
# Marchés qui influencent les marchés tradés : dollar, volatilité, taux, pétrole, actions US. Ils ne sont pas
# tradés : leurs clôtures servent d'ingrédients aux inventeurs (inter-marchés) quand le courtier les propose.
MACRO = {"DXY": "indice du dollar", "VIX": "volatilité (VIX)", "US10Y": "taux US 10 ans", "USOIL": "pétrole (WTI)",
         "US500": "S&P 500"}


def macro_symbols(conn, exclude=(), log=print) -> list[str]:
    """Les marchés macro que ce courtier propose (noms trouvés automatiquement), hors marchés déjà tradés."""
    out = []
    taken = {conn.resolve(x) for x in exclude if _safe_resolve(conn, x)}
    for name in MACRO:
        real = _safe_resolve(conn, name)
        if real and real not in taken:
            out.append(name)
    log(f"[macro] marchés macro disponibles chez ce courtier : {', '.join(out) if out else 'aucun'}"
        + (f" (absents : {', '.join(m for m in MACRO if m not in out)})" if len(out) < len(MACRO) else ""))
    return out


def _safe_resolve(conn, name):
    try:
        return conn.resolve(name)
    except Exception:
        return None


# ---------------------------------------------------------------------------------------------- inter-marchés
def add_ext(df: pd.DataFrame, others: dict) -> pd.DataFrame:
    """Ajoute la clôture d'autres marchés (colonnes « ext:SYMBOLE ») alignée sur les bougies de df.
    À chaque bougie, on prend la DERNIÈRE clôture connue de l'autre marché à cette heure-là (jamais le futur)."""
    df = df.copy()
    for name, close in others.items():
        if close is None or not len(close):
            continue
        close = close[~close.index.duplicated()].sort_index()
        df[f"ext:{name}"] = close.reindex(df.index, method="ffill").to_numpy(dtype=np.float32)
    return df
