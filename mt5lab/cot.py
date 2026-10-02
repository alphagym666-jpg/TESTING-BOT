"""RAPPORT COT (Commitments of Traders) de la CFTC : les positions des gros spéculateurs (fonds) sur les contrats à
terme, publiées GRATUITEMENT chaque vendredi pour le mardi précédent (https://www.cftc.gov).

Pour chaque marché suivi : position nette des « non-commerciaux » (fonds spéculatifs) / intérêt ouvert, ramenée en
z-score sur 52 et 156 semaines (colonnes cot_z52 et cot_z156). Le sens est celui du marché tradé (ex. USDJPY : des
fonds acheteurs de YEN = signe inversé).

Pas de regard vers le futur : une donnée du mardi n'est utilisée qu'à partir du samedi suivant (publiée le vendredi).
Le DAX (GER40) n'a pas de contrat à la CFTC : pas de COT pour lui.
"""
from __future__ import annotations

import io
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

URL = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"   # « Legacy - Futures Only », un fichier par an
CACHE = Path(__file__).resolve().parent.parent / "data" / "cot"

# marché tradé -> (mots du nom du contrat CFTC, sens)
MARKETS = {
    "GOLD": (("GOLD - COMMODITY EXCHANGE",), 1),
    "EUR": (("EURO FX - CHICAGO MERCANTILE",), 1),
    "GBP": (("BRITISH POUND",), 1),
    "JPY": (("JAPANESE YEN",), -1),            # USDJPY monte quand le yen baisse
    "NASDAQ": (("NASDAQ-100", "NASDAQ MINI", "NASDAQ 100"), 1),
    "DOW": (("DJIA", "DOW JONES INDUSTRIAL"), 1),
}


def market_of(symbol: str) -> str | None:
    s = symbol.upper()
    if "XAU" in s or "GOLD" in s:
        return "GOLD"
    if s.startswith("EUR") and "USD" in s:
        return "EUR"
    if s.startswith("GBP") and "USD" in s:
        return "GBP"
    if "JPY" in s and s.startswith("USD"):
        return "JPY"
    if any(k in s for k in ("NAS", "US100", "USTEC", "NDX")):
        return "NASDAQ"
    if any(k in s for k in ("US30", "DJ", "DOW", "WS30")):
        return "DOW"
    return None


def _col(df: pd.DataFrame, *words) -> str | None:
    for c in df.columns:
        lc = str(c).lower()
        if all(w in lc for w in words):
            return c
    return None


def parse(raw: pd.DataFrame) -> pd.DataFrame:
    """Fichier annuel CFTC -> lignes (date, marché, net) pour les marchés suivis."""
    name = _col(raw, "market", "exchange") or raw.columns[0]
    date = _col(raw, "yyyy-mm-dd") or _col(raw, "as of date in form yymmdd") or _col(raw, "date")
    oi = _col(raw, "open interest")
    lo = _col(raw, "noncommercial", "long")
    sh = _col(raw, "noncommercial", "short")
    if not all((date, oi, lo, sh)):
        return pd.DataFrame(columns=["date", "marche", "net"])
    rows = []
    names = raw[name].astype(str).str.upper()
    for m, (words, sign) in MARKETS.items():
        mask = np.zeros(len(raw), dtype=bool)
        for w in words:
            mask |= names.str.contains(w, regex=False).to_numpy()
        if not mask.any():
            continue
        sub = raw[mask]
        d = pd.to_datetime(sub[date].astype(str), errors="coerce", format="mixed")
        net = (pd.to_numeric(sub[lo], errors="coerce") - pd.to_numeric(sub[sh], errors="coerce")) / \
            pd.to_numeric(sub[oi], errors="coerce").replace(0, np.nan)
        part = pd.DataFrame({"date": d, "marche": m, "net": sign * net}).dropna()
        rows.append(part.groupby("date", as_index=False)["net"].mean().assign(marche=m))  # plusieurs contrats : moyenne
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["date", "marche", "net"])


def download(years, cache: Path = CACHE, log=print) -> pd.DataFrame:
    """Télécharge (une fois) les fichiers annuels ; l'année en cours est retéléchargée s'il a plus de 3 jours."""
    cache.mkdir(parents=True, exist_ok=True)
    frames, offline = [], False
    for y in years:
        f = cache / f"cot_{y}.csv"
        fresh = f.exists() and (y < time.localtime().tm_year or time.time() - f.stat().st_mtime < 3 * 86400)
        if not fresh and not offline:
            try:
                req = urllib.request.Request(URL.format(year=y), headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    z = zipfile.ZipFile(io.BytesIO(r.read()))
                raw = pd.read_csv(z.open(z.namelist()[0]), low_memory=False)
                parse(raw).to_csv(f, index=False)
            except Exception as exc:
                offline = True  # pas d'internet / site bloqué : on n'insiste pas pour les autres années
                if not f.exists():
                    log(f"[COT] téléchargement impossible ({exc}) : on garde ce qui est déjà en cache")
                    continue
        elif not f.exists():
            continue
        frames.append(pd.read_csv(f, parse_dates=["date"]))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["date", "marche", "net"])


def table(start_year: int | None = None, cache: Path = CACHE, log=print) -> pd.DataFrame:
    y1 = time.localtime().tm_year
    return download(range(start_year or y1 - 8, y1 + 1), cache, log)


def add_cot(df: pd.DataFrame, symbol: str, cot: pd.DataFrame | None) -> pd.DataFrame:
    """Ajoute cot_z52 et cot_z156 (z-score de la position nette des fonds) alignés sur les bougies, sans futur."""
    m = market_of(symbol)
    if cot is None or not len(cot) or m is None or not isinstance(df.index, pd.DatetimeIndex):
        return df
    w = cot[cot["marche"] == m].sort_values("date").drop_duplicates("date")
    if len(w) < 30:
        return df
    s = pd.Series(w["net"].to_numpy(float), index=pd.DatetimeIndex(w["date"]) + pd.Timedelta(days=4))  # dispo samedi
    df = df.copy()
    for n in (52, 156):
        z = (s - s.rolling(n, min_periods=26).mean()) / s.rolling(n, min_periods=26).std().replace(0, np.nan)
        idx = df.index.tz_localize(None) if df.index.tz is not None else df.index
        df[f"cot_z{n}"] = z.reindex(idx, method="ffill").to_numpy(dtype=np.float32)
    return df


def uses_cot(obj) -> bool:
    """Une stratégie se sert-elle du COT ? (pour charger les données en paper trading seulement si besoin)"""
    import json
    return '"f": "cot"' in json.dumps(obj)
