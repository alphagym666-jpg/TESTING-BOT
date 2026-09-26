"""Paper trading : trades FICTIFS sur les prix RÉELS de MT5, sans jamais envoyer d'ordre.

- Les prix viennent du terminal MT5 en direct (bid / ask réels, donc spread réel).
- Entrée au prix ask (achat) ou bid (vente) du moment où la bougie se clôture avec un signal.
- SL et TP sont surveillés TICK PAR TICK (historique des ticks MT5 depuis le dernier passage) :
  le SL est rempli au prix du tick qui le touche (glissement réel inclus), le TP à son niveau.
- Taille de lot, valeur du pip et contraintes de lot (min / pas) = celles de votre courtier.
- Chaque stratégie a son propre compte virtuel, suivi comme un challenge FTMO ;
  tout est sauvegardé et reprend après un redémarrage.
- Aucune fonction d'envoi d'ordre (order_send) n'est appelée dans ce module.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import time
from collections import deque
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import indicators as ind
from .backtest import RR_LEVELS, RiskConfig, _stop_distance
from .evaluator import compute_signal, describe, signal_key
from .ftmo import FtmoRules
from .strategies import REGISTRY, apply_filter, expand_grid

TRADE_FIELDS = ["strategie_id", "symbole", "timeframe", "strategie", "risque", "sens", "lots", "ouverture",
                "prix_entree", "sl_initial", "sl_final", "tp", "fermeture", "prix_sortie", "raison", "duree_min",
                "pips", "r", "pnl", "solde", "spread_entree_pts"]


@dataclass
class Position:
    side: int
    entry: float
    sl: float
    tp: float | None
    risk: float           # distance du stop initial, en prix
    lots: float
    risk_money: float     # perte en devise du compte si le SL initial est touché
    opened: str
    spread_pts: float
    bars_held: int = 0
    be_done: bool = False
    opened_msc: int = 0

    @property
    def sl_initial(self) -> float:
        return self.entry - self.side * self.risk


@dataclass
class Slot:
    """Une stratégie suivie en paper trading, avec son compte virtuel (suivi comme un challenge FTMO)."""
    id: str
    symbol: str
    timeframe: str
    candidate: dict
    verdict: str = ""
    expected_avg_r: float | None = None
    expected_wr: float | None = None
    balance: float = 100_000.0
    peak: float = 100_000.0
    capital: float = 100_000.0    # capital de départ : le risque par trade est plafonné sur cette base
    max_dd_pct: float = 0.0
    trades: int = 0
    wins: int = 0
    sum_r: float = 0.0
    pnl: float = 0.0
    position: Position | None = None
    history_r: list = field(default_factory=list)
    # suivi du challenge FTMO de ce compte fictif
    ftmo_status: str = "en cours"
    ftmo_when: str = ""
    day: str = ""
    day_start: float = 100_000.0
    worst_day_pct: float = 0.0
    trade_days: list = field(default_factory=list)
    group: str = ""                  # composant d'une stratégie combinée (compte partagé)
    risk_pct: float | None = None    # risque propre à ce composant (sinon le risque général)

    @property
    def cfg(self) -> RiskConfig:
        return RiskConfig(**self.candidate["risk"])


SAVED = [f.name for f in fields(Slot) if f.name not in ("id", "symbol", "timeframe", "candidate", "verdict",
                                                          "expected_avg_r", "expected_wr", "capital", "position",
                                                          "group", "risk_pct")]


@dataclass
class Group:
    """Compte UNIQUE partagé par les composants d'une stratégie combinée, suivi comme un challenge FTMO."""
    name: str
    capital: float = 100_000.0
    day_budget: float | None = None   # perte possible max par jour, en % (réalisé + ouvert + nouveau trade)
    day_stop: float | None = None     # plus de nouveau trade après -X % réalisés dans la journée
    max_open: int | None = None       # positions ouvertes max en même temps
    total_budget: float | None = None # perte totale max depuis le départ, en % (jamais dépassée par un nouveau trade)
    balance: float = 100_000.0
    peak: float = 100_000.0
    max_dd_pct: float = 0.0
    trades: int = 0
    wins: int = 0
    pnl: float = 0.0
    sum_r: float = 0.0
    ftmo_status: str = "en cours"
    ftmo_when: str = ""
    day: str = ""
    day_start: float = 100_000.0
    worst_day_pct: float = 0.0
    trade_days: list = field(default_factory=list)
    day_realized: float = 0.0
    skipped: int = 0                  # signaux refusés par les règles de risque


GROUP_SAVED = [f.name for f in fields(Group) if f.name not in ("name", "capital", "day_budget", "day_stop", "max_open",
                                                                "total_budget")]


def slot_id(symbol, timeframe, candidate) -> str:
    h = hashlib.sha1(json.dumps(candidate, sort_keys=True).encode()).hexdigest()[:8]
    return f"{symbol}_{timeframe}_{h}"


def _num(v):
    try:
        v = float(v)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


def _slot_from_row(sym, tf, row, capital) -> Slot:
    cand = json.loads(row["candidate"])
    return Slot(slot_id(sym, tf, cand), sym, tf, cand, str(row["verdict"]), _num(row.get("avgR_oos")),
                _num(row.get("wr_oos")), capital, capital, capital, day_start=capital)


def load_best_slots(results_dir: Path, symbols=None, top=30, capital=100_000.0) -> list[Slot]:
    """Top N global de la comparaison (tous marchés et timeframes) : validées d'abord, puis meilleur gain/mois."""
    path = Path(results_dir) / "comparaison.csv"
    if not path.exists():
        from .compare import build_comparison
        build_comparison(Path(results_dir), capital)
    if not path.exists():
        return []
    board = pd.read_csv(path)
    if symbols:
        board = board[board["symbole"].str.upper().isin([x.upper() for x in symbols])]
    board = board[board["trades_oos"].notna()].head(top)
    slots = [_slot_from_row(r["symbole"], r["timeframe"], r, capital) for _, r in board.iterrows()]
    print(f"[paper] {len(slots)} meilleures stratégies (tous marchés et timeframes) suivies")
    return slots


def load_portfolio_slots(results_dir: Path, capital=100_000.0) -> list[Slot]:
    """Les stratégies choisies ensemble par le Chef FTMO (results/portefeuille_ftmo.csv)."""
    path = Path(results_dir) / "portefeuille_ftmo.csv"
    if not path.exists():
        return []
    board = pd.read_csv(path)
    slots = [_slot_from_row(r["symbole"], r["timeframe"], r, capital) for _, r in board.iterrows()]
    print(f"[paper] portefeuille du Chef FTMO : {len(slots)} stratégies")
    return slots


def load_combined_slots(results_dir: Path, capital=100_000.0) -> tuple[list[Slot], dict]:
    """La stratégie combinée du Directeur (results/strategie_combinee.json) : un seul compte partagé."""
    path = Path(results_dir) / "strategie_combinee.json"
    if not path.exists():
        return [], {}
    d = json.loads(path.read_text(encoding="utf-8"))
    rules = d.get("regles", {})
    name = "Stratégie combinée"
    slots = []
    for i, c in enumerate(d.get("composants", []), 1):
        cand = c["candidate"]
        s = Slot(slot_id(c["symbole"], c["timeframe"], cand) + f"_comb{i}", c["symbole"], c["timeframe"], cand,
                 f"combinée n°{i}", capital=capital, balance=capital, peak=capital, day_start=capital,
                 group=name, risk_pct=float(c["risk_pct"]))
        slots.append(s)
    groups = {name: {"capital": capital, "day_budget": rules.get("day_budget"), "day_stop": rules.get("day_stop"),
                     "max_open": rules.get("max_open"), "total_budget": rules.get("total_budget", 10.0)}}
    print(f"[paper] stratégie combinée du Directeur : {len(slots)} composants sur un seul compte "
          f"(perte possible max {rules.get('day_budget')} %/jour)")
    return slots, groups


def load_slots(results_dir: Path, symbols, timeframe, source="tous", top=20, capital=100_000.0) -> list[Slot]:
    """Charge les stratégies à suivre depuis les classements produits par la recherche."""
    slots = []
    for sym in symbols:
        path = Path(results_dir) / f"{sym}_{timeframe}" / "classement.csv"
        if not path.exists():
            print(f"[paper] {path} introuvable : lancez d'abord la recherche pour {sym} {timeframe}")
            continue
        board = pd.read_csv(path)
        if source == "approuvees":
            board = board[board["verdict"] == "APPROUVÉ"]
        board = board.head(top)
        slots += [_slot_from_row(sym, timeframe, r, capital) for _, r in board.iterrows()]
        print(f"[paper] {sym} {timeframe} : {min(len(board), top)} stratégies suivies")
    return slots


def load_exploration_slots(results_dir: Path, symbols, timeframes, capital=100_000.0,
                           rr_levels=tuple(RR_LEVELS), sl_atr=1.5) -> list[Slot]:
    """TOUTES les stratégies du catalogue + les inventions des agents + les stratégies validées, × TOUS les R:R.

    Pour chaque stratégie on prend les meilleurs réglages trouvés par la recherche sur ce marché/timeframe,
    sinon un réglage médian de sa grille. Stop à sl_atr ATR, sans gestion (pour comparer les R:R à armes égales).
    """
    slots: dict[str, Slot] = {}
    # stratégies du portefeuille du Chef FTMO : repérées par leur numéro dans la plateforme
    port_path = Path(results_dir) / "portefeuille_ftmo.csv"
    in_port: dict[tuple, str] = {}
    if port_path.exists():
        pf = pd.read_csv(port_path)
        for i, r in enumerate(pf.itertuples(), 1):
            num = getattr(r, "ordre", i)
            in_port[(str(r.symbole), str(r.timeframe), signal_key(json.loads(r.candidate)))] = \
                f"portefeuille FTMO n°{num}"
    for sym in symbols:
        for tf in timeframes:
            path = Path(results_dir) / f"{sym}_{tf}" / "classement.csv"
            board = pd.read_csv(path) if path.exists() else pd.DataFrame()
            signals: dict[str, tuple] = {}
            # 1) chaque stratégie du catalogue : meilleurs réglages trouvés, sinon réglage médian
            for name, sd in REGISTRY.items():
                if name.startswith("_"):
                    continue
                grid = expand_grid(sd.grid)
                signals[name] = ({"type": "single", "name": name, "params": grid[len(grid) // 2]}, "none", "")
            if len(board):
                for _, r in board.sort_values("score_is", ascending=False).iterrows():
                    c = json.loads(r["candidate"])
                    sig = c["signal"]
                    if sig["type"] == "single" and sig["name"] in signals and not signals[sig["name"]][2]:
                        signals[sig["name"]] = (sig, "none", "réglages de la recherche")
                    elif sig["type"] == "rule":  # 2) inventions des agents et failles des banques
                        nm = sig.get("name", "")
                        kind = ("faille des banques" if nm.startswith("FAILLE") else
                                "invention institutionnelle" if str(r.get("equipe", "")) == "D" else "invention")
                        signals[nm or signal_key(sig)] = (sig, "none", kind)
                    best_of = r.get("meilleure_version_de", "")
                    if isinstance(best_of, str) and best_of and r["verdict"] != "APPROUVÉ":
                        # 4) meilleure version de chaque stratégie du catalogue, avec son réglage exact
                        s = Slot(slot_id(sym, tf, c), sym, tf, c, "catalogue : meilleure version",
                                 _num(r.get("avgR_oos")), _num(r.get("wr_oos")), capital, capital, capital,
                                 day_start=capital)
                        slots.setdefault(s.id, s)
                    if r["verdict"] == "APPROUVÉ":  # 3) stratégies validées, telles quelles (filtre compris)
                        signals["validée " + signal_key(sig) + c["filter"]] = (sig, c["filter"], "validée")
                        # ... et aussi avec leur réglage EXACT (stop, R:R, gestion, sens) trouvé par la recherche
                        origin = in_port.get((sym, tf, signal_key(c)), "validée (réglage exact)")
                        s = Slot(slot_id(sym, tf, c), sym, tf, c, origin, _num(r.get("avgR_oos")),
                                 _num(r.get("wr_oos")), capital, capital, capital, day_start=capital)
                        slots[s.id] = s
            for sig, flt, origin in signals.values():
                for rr in rr_levels:
                    cand = {"signal": sig, "filter": flt,
                            "risk": asdict(RiskConfig("atr", sl_atr, rr, "none", 200, "both"))}
                    s = Slot(slot_id(sym, tf, cand), sym, tf, cand, origin or "catalogue", capital=capital,
                             balance=capital, peak=capital, day_start=capital)
                    slots.setdefault(s.id, s)  # une étiquette précise (portefeuille, validée...) est conservée
    out = list(slots.values())
    print(f"[paper] EXPLORATION : {len(out)} comptes fictifs "
          f"({len(symbols)} marchés × {len(timeframes)} timeframes × stratégies × {len(rr_levels)} R:R)")
    return out


class PaperEngine:
    def __init__(self, conn, slots: list[Slot], out_dir: Path, risk_pct: float = 1.0,
                 commission_per_lot: float | dict = 0.0, bars: int = 1000, ftmo: FtmoRules | None = None,
                 quiet: bool | None = None, save_every: float = 20.0, groups: dict | None = None):
        self.c = conn
        self.mt5 = conn.mt5
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.risk_pct = risk_pct
        self.bars = bars
        self.ftmo = ftmo or FtmoRules()
        self.quiet = len(slots) > 60 if quiet is None else quiet
        self.save_every = save_every
        self._last_save = 0.0
        self._dirty = False
        self.slots = {s.id: s for s in slots}
        for s in self.slots.values():
            s.symbol = conn.resolve(s.symbol)
        self.groups: dict[str, Group] = {}
        for name, g in (groups or {}).items():
            cap = g.get("capital", 100_000.0)
            self.groups[name] = Group(name, cap, g.get("day_budget"), g.get("day_stop"), g.get("max_open"),
                                      g.get("total_budget"), balance=cap, peak=cap, day_start=cap)
        self.by_bar: dict[tuple, list[Slot]] = {}
        for s in self.slots.values():
            self.by_bar.setdefault((s.symbol, s.timeframe), []).append(s)
        # commission aller-retour par lot : un nombre pour tous, ou {symbole: montant}
        if isinstance(commission_per_lot, dict):
            self._comm = {conn.resolve(k): float(v) for k, v in commission_per_lot.items()}
            self._comm_default = 0.0
        else:
            self._comm, self._comm_default = {}, float(commission_per_lot)
        self.last_bar: dict[str, str] = {}   # "SYM|TF" -> heure de la dernière bougie clôturée traitée
        self.last_msc: dict[str, int] = {}   # symbole -> dernier tick traité (ms)
        self.recent: list[dict] = []
        self.events: deque = deque(maxlen=400)
        self.started = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._load_state()

    # ------------------------------------------------------------------ persistance
    @property
    def state_path(self):
        return self.out / "etat.json"

    def _load_state(self):
        if not self.state_path.exists():
            return
        st = json.loads(self.state_path.read_text(encoding="utf-8"))
        for sid, d in st.get("slots", {}).items():
            if sid in self.slots:
                s = self.slots[sid]
                for k in SAVED:
                    if k in d:
                        setattr(s, k, d[k])
                s.position = Position(**d["position"]) if d.get("position") else None
        self.last_bar.update(st.get("last_bar", {}))
        self.last_msc.update({k: int(v) for k, v in st.get("last_msc", {}).items()})
        for name, d in st.get("groups", {}).items():
            if name in self.groups:
                for k in GROUP_SAVED:
                    if k in d:
                        setattr(self.groups[name], k, d[k])
        self.recent = st.get("recent", [])
        self.events.extend(st.get("events", []))
        self.started = st.get("started", self.started)
        n_open = sum(s.position is not None for s in self.slots.values())
        print(f"[paper] reprise de l'état sauvegardé ({n_open} positions fictives ouvertes)")

    def save(self):
        active = {sid: s for sid, s in self.slots.items() if s.trades or s.position or s.ftmo_status != "en cours"}
        st = {"started": self.started, "last_bar": self.last_bar, "last_msc": self.last_msc,
              "recent": self.recent[-1000:], "events": list(self.events),
              "groups": {n: {k: getattr(g, k) for k in GROUP_SAVED} for n, g in self.groups.items()},
              "slots": {sid: {**{k: getattr(s, k) for k in SAVED},
                              "position": asdict(s.position) if s.position else None}
                        for sid, s in active.items()}}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st), encoding="utf-8")
        tmp.replace(self.state_path)
        self._last_save = time.time()
        self._dirty = False

    def event(self, when: str, kind: str, s: Slot, text: str):
        self.events.append({"t": when, "type": kind, "symbole": s.symbol, "tf": s.timeframe, "texte": text})
        self._dirty = True
        if not self.quiet:
            print(f"[paper] {when} {kind} {s.symbol} {s.timeframe} | {text}")

    # ------------------------------------------------------------------ calculs argent
    def _money(self, symbol: str, price_move: float, lots: float) -> float:
        info = self.c.symbol_info(symbol)
        tick_size = info.trade_tick_size or info.point
        return price_move / tick_size * (info.trade_tick_value or 0.0) * lots

    def commission(self, symbol: str) -> float:
        return self._comm.get(symbol, self._comm_default)

    def _lots(self, symbol: str, budget: float, dist: float) -> float:
        """Plus grand lot dont la perte au stop (+ commission) reste <= budget. 0 si même le lot minimum dépasse."""
        info = self.c.symbol_info(symbol)
        per_lot = self._money(symbol, dist, 1.0) + self.commission(symbol)
        if per_lot <= 0:
            return 0.0
        step = info.volume_step or 0.01
        lots = math.floor(budget / per_lot / step + 1e-9) * step
        lots = min(lots, info.volume_max)
        return round(lots, 8) if lots >= info.volume_min else 0.0

    def risk_budget(self, s: Slot) -> float:
        """Perte max autorisée par trade : risk_pct du capital de départ (ou du solde s'il a baissé).

        Pour un composant de stratégie combinée : son propre risque, calculé sur le compte partagé."""
        pct = s.risk_pct if s.risk_pct is not None else self.risk_pct
        if s.group and s.group in self.groups:
            g = self.groups[s.group]
            return min(g.balance, g.capital) * pct / 100
        return min(s.balance, s.capital) * pct / 100

    def group_allows(self, s: Slot, when: str) -> bool:
        """Règles de risque de la stratégie combinée, vérifiées avant chaque nouveau trade."""
        g = self.groups.get(s.group)
        if g is None:
            return True
        if g.ftmo_status != "en cours":
            return False
        if when[:10] != g.day:
            self._roll_day(g, g.balance, when)
        members = [x for x in self.slots.values() if x.group == g.name and x.position]
        ok = True
        if g.max_open is not None and len(members) >= g.max_open:
            ok = False
        elif g.day_stop is not None and g.day_realized <= -g.day_stop * g.capital / 100:
            ok = False
        elif g.day_budget is not None:
            open_risk = sum(x.position.risk_money for x in members) * 1.1
            new_risk = self.risk_budget(s) * 1.1
            if (max(0.0, -g.day_realized) + open_risk + new_risk) / g.capital * 100 > g.day_budget + 1e-9:
                ok = False
        if ok and g.total_budget is not None:  # même si tous les stops sautent, la perte totale reste sous le plafond
            open_risk = sum(x.position.risk_money for x in members) * 1.1
            new_risk = self.risk_budget(s) * 1.1
            if (max(0.0, g.capital - g.balance) + open_risk + new_risk) / g.capital * 100 > g.total_budget + 1e-9:
                ok = False
        if not ok:
            g.skipped += 1
        return ok

    def pips(self, symbol: str, move: float) -> float:
        info = self.c.symbol_info(symbol)
        pip = info.point * (10 if info.digits in (3, 5) else 1)
        return move / pip

    # ------------------------------------------------------------------ challenge FTMO
    def _roll_day(self, acc, equity: float, when: str):
        if when[:10] != acc.day:
            acc.day, acc.day_start = when[:10], (equity if acc.day else acc.capital)
            if isinstance(acc, Group):
                acc.day_realized = 0.0

    def _ftmo_check(self, acc, equity: float, when: str, flat: bool) -> bool:
        """Met à jour le suivi FTMO d'un compte (stratégie seule ou combinée). True si le statut vient de changer."""
        self._roll_day(acc, equity, when)
        if acc.ftmo_status != "en cours":
            return False
        R = self.ftmo
        daily = (equity - acc.day_start) / acc.capital * 100
        acc.worst_day_pct = min(acc.worst_day_pct, daily)
        if daily <= -R.max_daily:
            acc.ftmo_status, acc.ftmo_when = f"ÉCHOUÉ (perte du jour {daily:.2f} %)", when
        elif (equity - acc.capital) / acc.capital * 100 <= -R.max_total:
            acc.ftmo_status, acc.ftmo_when = "ÉCHOUÉ (perte max totale)", when
        elif flat and (acc.balance - acc.capital) / acc.capital * 100 >= R.target1 and len(acc.trade_days) >= R.min_days:
            acc.ftmo_status, acc.ftmo_when = "RÉUSSI", when
        return acc.ftmo_status != "en cours"

    def update_ftmo(self, s: Slot, equity: float, when: str):
        if self._ftmo_check(s, equity, when, s.position is None):
            self.event(when, "FTMO", s, f"challenge {s.ftmo_status} : {describe(s.candidate)} [{s.cfg.label()}]")

    def update_groups(self):
        """Suivi FTMO des stratégies combinées, positions ouvertes comprises."""
        for g in self.groups.values():
            members = [x for x in self.slots.values() if x.group == g.name]
            if not members:
                continue
            fl, when = 0.0, None
            for x in members:
                t = self.mt5.symbol_info_tick(x.symbol)
                if t is not None:
                    fl += self.floating(x, t)
                    when = when or _now(t)
            if when and self._ftmo_check(g, g.balance + fl, when, not any(x.position for x in members)):
                self.events.append({"t": when, "type": "FTMO", "symbole": "COMBINÉE", "tf": "",
                                    "texte": f"stratégie combinée « {g.name} » : challenge {g.ftmo_status}"})
                self._dirty = True

    def floating(self, s: Slot, tick) -> float:
        p = s.position
        if not p or tick is None:
            return 0.0
        cur = tick.bid if p.side > 0 else tick.ask
        return self._money(s.symbol, (cur - p.entry) * p.side, p.lots)

    # ------------------------------------------------------------------ positions
    def _open(self, s: Slot, side: int, closed: pd.DataFrame, tick, atr_arr=None):
        price = tick.ask if side > 0 else tick.bid
        info = self.c.symbol_info(s.symbol)
        atr_arr = ind.atr(closed, 14).to_numpy() if atr_arr is None else atr_arr
        dist = _stop_distance(closed, s.cfg, atr_arr, len(closed) - 1, side, price)
        if not np.isfinite(dist) or dist <= 0:
            return
        when = _now(tick)
        if s.group and not self.group_allows(s, when):
            return
        lots = self._lots(s.symbol, self.risk_budget(s), dist)
        if lots <= 0:
            return
        tp = price + side * s.cfg.rr * dist if s.cfg.rr else None
        s.position = Position(side, price, price - side * dist, tp, dist, lots,
                              self._money(s.symbol, dist, lots), when, (tick.ask - tick.bid) / info.point,
                              opened_msc=int(getattr(tick, "time_msc", 0)))
        if when[:10] not in s.trade_days:
            s.trade_days.append(when[:10])
        g = self.groups.get(s.group)
        if g is not None and when[:10] not in g.trade_days:
            g.trade_days.append(when[:10])
        d = info.digits
        self.event(when, "OUVERTURE", s, f"{'ACHAT' if side > 0 else 'VENTE'} {lots} lots @ {price:.{d}f} | "
                                         f"SL {s.position.sl:.{d}f} | TP {'signal' if tp is None else f'{tp:.{d}f}'} | "
                                         f"{describe(s.candidate)} [{s.cfg.label()}]")

    def _close(self, s: Slot, price: float, when: str, reason: str):
        p = s.position
        gross = self._money(s.symbol, (price - p.entry) * p.side, p.lots)
        pnl = gross - self.commission(s.symbol) * p.lots
        r = pnl / p.risk_money if p.risk_money > 0 else 0.0
        s.balance += pnl
        s.pnl += pnl
        s.trades += 1
        s.wins += pnl > 0
        s.sum_r += r
        s.history_r.append(round(r, 3))
        s.peak = max(s.peak, s.balance)
        s.max_dd_pct = max(s.max_dd_pct, (s.peak - s.balance) / s.peak * 100 if s.peak > 0 else 0)
        try:
            dur = (datetime.strptime(when, "%Y-%m-%d %H:%M:%S") - datetime.strptime(p.opened, "%Y-%m-%d %H:%M:%S"))
            dur_min = round(dur.total_seconds() / 60, 1)
        except ValueError:
            dur_min = None
        dg = self.c.symbol_info(s.symbol).digits
        price = round(price, dg)
        row = {"strategie_id": s.id, "symbole": s.symbol, "timeframe": s.timeframe, "strategie": describe(s.candidate),
               "risque": s.cfg.label(), "sens": "ACHAT" if p.side > 0 else "VENTE", "lots": p.lots,
               "ouverture": p.opened, "prix_entree": round(p.entry, dg), "sl_initial": round(p.sl_initial, dg),
               "sl_final": round(p.sl, dg), "tp": None if p.tp is None else round(p.tp, dg), "fermeture": when,
               "prix_sortie": price, "raison": reason, "duree_min": dur_min,
               "pips": round(self.pips(s.symbol, (price - p.entry) * p.side), 1), "r": round(r, 3),
               "pnl": round(pnl, 2), "solde": round(s.balance, 2), "spread_entree_pts": round(p.spread_pts, 1)}
        new = not (self.out / "trades.csv").exists()
        with open(self.out / "trades.csv", "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=TRADE_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
        self.recent.append(row)
        if len(self.recent) > 3000:
            self.recent = self.recent[-2000:]
        g = self.groups.get(s.group)
        if g is not None:  # le compte partagé de la stratégie combinée encaisse aussi le trade
            self._roll_day(g, g.balance, when)
            g.balance += pnl
            g.pnl += pnl
            g.trades += 1
            g.wins += pnl > 0
            g.sum_r += r
            g.day_realized += pnl
            g.peak = max(g.peak, g.balance)
            g.max_dd_pct = max(g.max_dd_pct, (g.peak - g.balance) / g.peak * 100 if g.peak > 0 else 0)
        s.position = None
        self.event(when, "FERMETURE", s, f"{row['sens']} {reason} @ {price} -> {r:+.2f}R | {pnl:+.2f} | "
                                         f"solde {s.balance:,.2f}")
        self.update_ftmo(s, s.balance, when)

    # ------------------------------------------------------------------ ticks
    def process_ticks(self, symbol: str):
        """Parcourt tous les ticks reçus depuis le dernier passage pour détecter SL / TP dans le bon ordre."""
        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            return
        last = self.last_msc.get(symbol)
        self.last_msc[symbol] = int(tick.time_msc)
        open_slots = [s for s in self.slots.values() if s.symbol == symbol and s.position]
        if not open_slots:
            return
        ticks = None
        if last is not None:
            ticks = self.mt5.copy_ticks_from(symbol, datetime.fromtimestamp(last / 1000, tz=timezone.utc),
                                             200_000, self.mt5.COPY_TICKS_ALL)
        if ticks is not None and len(ticks):
            ticks = ticks[(ticks["time_msc"] > last) & (ticks["bid"] > 0) & (ticks["ask"] > 0)]
        if ticks is None or not len(ticks):
            ticks = np.array([(tick.time_msc, tick.bid, tick.ask)],
                             dtype=[("time_msc", "i8"), ("bid", "f8"), ("ask", "f8")])
        for s in open_slots:
            p = s.position
            t = ticks[ticks["time_msc"] > p.opened_msc]
            if not len(t):
                continue
            px = t["bid"] if p.side > 0 else t["ask"]   # un achat se clôture au bid, une vente à l'ask
            hit_sl = (px <= p.sl) if p.side > 0 else (px >= p.sl)
            hit_tp = np.zeros(len(px), bool) if p.tp is None else ((px >= p.tp) if p.side > 0 else (px <= p.tp))
            i_sl = int(np.argmax(hit_sl)) if hit_sl.any() else None
            i_tp = int(np.argmax(hit_tp)) if hit_tp.any() else None
            if i_sl is not None and (i_tp is None or i_sl <= i_tp):
                if p.be_done and abs(p.sl - p.entry) < 1e-12:
                    why = "break-even"
                elif (p.sl - p.entry) * p.side > 0:
                    why = "stop suiveur"
                else:
                    why = "stop loss"
                self._close(s, float(px[i_sl]), _msc(t["time_msc"][i_sl]), why)
            elif i_tp is not None:
                self._close(s, float(p.tp), _msc(t["time_msc"][i_tp]), "take profit")
            else:
                self.update_ftmo(s, s.balance + self.floating(s, tick), _now(tick))

    # ------------------------------------------------------------------ bougies
    def on_bar(self, symbol: str, tf: str, closed: pd.DataFrame):
        tick = self.mt5.symbol_info_tick(symbol)
        close = float(closed["close"].iloc[-1])
        atr_arr = ind.atr(closed, 14).to_numpy()
        atr_last = float(atr_arr[-1])
        cache: dict[str, int | None] = {}  # un signal n'est calculé qu'une fois pour toutes ses variantes de R:R
        for s in self.by_bar.get((symbol, tf), []):
            cfg = s.cfg
            key = signal_key(s.candidate["signal"]) + "|" + s.candidate["filter"]
            if key not in cache:
                try:
                    cache[key] = int(apply_filter(closed, compute_signal(closed, s.candidate["signal"]),
                                                  s.candidate["filter"]).iloc[-1])
                except Exception as exc:
                    print(f"[paper] {s.id} : erreur de calcul du signal ({exc})")
                    cache[key] = None
            sig = cache[key]
            if sig is None:
                continue
            if cfg.direction == "long" and sig < 0 or cfg.direction == "short" and sig > 0:
                sig = 0
            p = s.position
            if p:
                p.bars_held += 1
                exit_px = tick.bid if p.side > 0 else tick.ask
                if cfg.rr is None and sig == -p.side:
                    self._close(s, exit_px, _now(tick), "signal opposé")
                elif p.bars_held >= cfg.max_hold:
                    self._close(s, exit_px, _now(tick), "durée max")
                elif cfg.management == "breakeven" and not p.be_done and (close - p.entry) * p.side >= p.risk:
                    p.sl, p.be_done = p.entry, True
                    self.event(_now(tick), "SL DÉPLACÉ", s, f"break-even à {p.entry}")
                elif cfg.management == "trailing" and np.isfinite(atr_last):
                    dist = cfg.sl_value * atr_last if cfg.sl_mode == "atr" else p.risk
                    trail = close - p.side * dist
                    p.sl = max(p.sl, trail) if p.side > 0 else min(p.sl, trail)
            if s.position is None and sig != 0 and (s.group or s.ftmo_status == "en cours"):
                self._open(s, sig, closed, tick, atr_arr)

    def step(self):
        for sym in sorted({s.symbol for s in self.slots.values()}):
            self.process_ticks(sym)
        for (sym, tf) in sorted(self.by_bar):
            last2 = self.mt5.copy_rates_from_pos(sym, getattr(self.mt5, f"TIMEFRAME_{tf}"), 0, 2)
            if last2 is None or len(last2) < 2:
                continue
            bar_key, closed_time = f"{sym}|{tf}", str(int(last2["time"][0]))
            if self.last_bar.get(bar_key) == closed_time:
                continue
            first = bar_key not in self.last_bar
            self.last_bar[bar_key] = closed_time
            if first:  # au démarrage on n'entre pas sur une bougie déjà clôturée
                continue
            df = self.c.rates(sym, tf, self.bars)
            self.on_bar(sym, tf, df.iloc[:-1])
        if self.groups:
            self.update_groups()
        if self._dirty or time.time() - self._last_save >= self.save_every:
            self.save()  # sauvegarde dès qu'un trade s'ouvre / se ferme (reprise sans perte après un arrêt)

    # ------------------------------------------------------------------ données pour la plateforme
    def snapshot(self, max_slots: int = 3000, max_trades: int = 1500) -> dict:
        ticks = {sym: self.mt5.symbol_info_tick(sym) for sym in {s.symbol for s in self.slots.values()}}
        open_rows, slot_rows = [], []
        for s in self.slots.values():
            t = ticks.get(s.symbol)
            fl = self.floating(s, t)
            p = s.position
            if p and t is not None:
                info = self.c.symbol_info(s.symbol)
                cur = t.bid if p.side > 0 else t.ask
                open_rows.append({
                    "id": s.id, "symbole": s.symbol, "tf": s.timeframe, "strategie": describe(s.candidate),
                    "risque": s.cfg.label(), "sens": "ACHAT" if p.side > 0 else "VENTE", "lots": p.lots,
                    "ouverture": p.opened, "entree": round(p.entry, info.digits),
                    "sl_initial": round(p.sl_initial, info.digits), "sl": round(p.sl, info.digits),
                    "tp": None if p.tp is None else round(p.tp, info.digits), "prix": round(cur, info.digits),
                    "pips_sl": round(self.pips(s.symbol, abs(cur - p.sl)), 1),
                    "pips_tp": None if p.tp is None else round(self.pips(s.symbol, abs(p.tp - cur)), 1),
                    "latent": round(fl, 2), "latent_r": round(fl / p.risk_money, 2) if p.risk_money else 0,
                    "bougies": p.bars_held})
            if s.trades or p or s.ftmo_status != "en cours":
                eq = s.balance + fl
                slot_rows.append({
                    "id": s.id, "symbole": s.symbol, "tf": s.timeframe, "strategie": describe(s.candidate),
                    "base": _base_name(s.candidate), "rr": s.cfg.rr, "risque": s.cfg.label(), "origine": s.verdict,
                    "trades": s.trades, "gagnants": s.wins, "r_total": round(s.sum_r, 2),
                    "r_moyen": round(s.sum_r / s.trades, 3) if s.trades else 0.0, "pnl": round(s.pnl, 2),
                    "equite": round(eq, 2), "dd_max": round(s.max_dd_pct, 2),
                    "profit_pct": round((eq - s.capital) / s.capital * 100, 2),
                    "pire_jour_pct": round(s.worst_day_pct, 2), "jours_trades": len(s.trade_days),
                    "ftmo": s.ftmo_status, "ftmo_quand": s.ftmo_when, "en_position": bool(p),
                    "attendu_r": s.expected_avg_r})
        slot_rows.sort(key=lambda r: r["r_total"], reverse=True)
        group_rows = []
        for g in self.groups.values():
            members = [x for x in self.slots.values() if x.group == g.name]
            fl = sum(self.floating(x, ticks.get(x.symbol)) for x in members)
            eq = g.balance + fl
            open_risk = sum(x.position.risk_money for x in members if x.position)
            group_rows.append({
                "nom": g.name, "capital": g.capital, "solde": round(g.balance, 2), "equite": round(eq, 2),
                "latent": round(fl, 2), "profit_pct": round((eq - g.capital) / g.capital * 100, 2),
                "jour_pct": round((eq - g.day_start) / g.capital * 100, 2),
                "jour_realise_pct": round(g.day_realized / g.capital * 100, 2),
                "risque_ouvert_pct": round(open_risk / g.capital * 100, 2),
                "pire_jour_pct": round(g.worst_day_pct, 2), "dd_max": round(g.max_dd_pct, 2), "trades": g.trades,
                "gagnants": g.wins, "r_total": round(g.sum_r, 2), "pnl": round(g.pnl, 2), "ftmo": g.ftmo_status,
                "ftmo_quand": g.ftmo_when, "jours_trades": len(g.trade_days), "refuses": g.skipped,
                "regles": {"budget_jour": g.day_budget, "arret_jour": g.day_stop, "max_positions": g.max_open,
                           "budget_total": g.total_budget},
                "composants": [{"symbole": x.symbol, "tf": x.timeframe, "strategie": describe(x.candidate),
                                "risque": x.cfg.label(), "risque_pct": x.risk_pct, "trades": x.trades,
                                "gagnants": x.wins, "r_total": round(x.sum_r, 2), "en_position": bool(x.position)}
                               for x in members]})
        acc = self.mt5.account_info()
        prices = {}
        for sym, t in ticks.items():
            if t is not None:
                info = self.c.symbol_info(sym)
                prices[sym] = {"bid": t.bid, "ask": t.ask, "spread": round((t.ask - t.bid) / info.point, 1),
                               "digits": info.digits}
        return {
            "maj": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "demarre": self.started,
            "serveur": getattr(acc, "server", ""), "prix": prices,
            "capital": next(iter(self.slots.values())).capital if self.slots else 0,
            "risque_pct": self.risk_pct, "ftmo": asdict(self.ftmo), "ftmo_label": self.ftmo.label(),
            "n_comptes": len(self.slots), "n_actifs": len(slot_rows),
            "n_marches": len({(s.symbol, s.timeframe) for s in self.slots.values()}),
            "comptes": slot_rows[:max_slots], "positions": open_rows, "groupes": group_rows,
            "trades": self.recent[-max_trades:][::-1], "evenements": list(self.events)[::-1][:200],
        }

    def run(self, poll: int = 5, dashboard_every: int = 30, server_port: int | None = 8765, open_browser=True):
        server = None
        if server_port:
            from .plateforme import start_server
            server = start_server(self, server_port, open_browser)
        print(f"[paper] {len(self.slots)} comptes fictifs sur "
              f"{', '.join(sorted({s.symbol for s in self.slots.values()}))} — AUCUN ordre n'est envoyé à MT5.")
        if server:
            print(f"[paper] PLATEFORME EN DIRECT : http://localhost:{server_port}   (Ctrl+C pour arrêter)")
        last_dash = 0.0
        errors = 0
        last_beat = time.time()
        _keep_awake(True)
        try:
            while True:
                try:
                    self.step()
                    errors = 0
                    if server:
                        server.publish(self.snapshot())
                    if time.time() - last_dash >= dashboard_every:
                        write_dashboard(self)
                        last_dash = time.time()
                    if time.time() - last_beat >= 600:  # signe de vie toutes les 10 min
                        n_open = sum(bool(x.position) for x in self.slots.values())
                        n_tr = sum(x.trades for x in self.slots.values())
                        print(f"[paper] {datetime.now():%H:%M} toujours en marche : {n_open} positions ouvertes, "
                              f"{n_tr} trades clôturés")
                        last_beat = time.time()
                except KeyboardInterrupt:
                    raise
                except Exception as exc:  # une coupure réseau ou MT5 fermé ne doit pas tout arrêter
                    errors += 1
                    print(f"[paper] erreur : {exc}")
                    if errors >= 3:
                        self._reconnect()
                        errors = 0
                time.sleep(poll)
        except KeyboardInterrupt:
            self.save()
            write_dashboard(self)
            print("[paper] arrêté, état sauvegardé.")
        finally:
            _keep_awake(False)
            if server:
                server.shutdown()

    def _reconnect(self):
        """MT5 fermé ou déconnecté : on attend qu'il revienne, sans perdre l'état."""
        self.save()
        while True:
            try:
                self.mt5.shutdown()
            except Exception:
                pass
            try:
                self.c.connect(verbose=False)
                print(f"[paper] {datetime.now():%H:%M} reconnecté à MT5, je reprends")
                return
            except Exception as exc:
                print(f"[paper] {datetime.now():%H:%M} MT5 injoignable ({str(exc).splitlines()[0]}), "
                      f"nouvel essai dans 30 s. Vérifiez que MetaTrader 5 est ouvert et connecté.")
                time.sleep(30)


def _keep_awake(on: bool):
    """Empêche Windows de se mettre en veille pendant le paper trading (sans effet ailleurs)."""
    try:
        import ctypes
        es_continuous, es_system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | (es_system_required if on else 0))
    except Exception:
        pass


def _base_name(cand: dict) -> str:
    s = cand["signal"]
    if s["type"] == "single":
        return s["name"]
    if s["type"] == "rule":
        return s.get("name", "INVENTION")
    return f"{s['a']['name']}+{s['b']['name']}"


def _msc(v) -> str:
    return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _now(tick) -> str:
    return _msc(getattr(tick, "time_msc", int(time.time() * 1000)))


# ================================================== tableau de bord statique (secours, sans serveur)
def write_dashboard(engine: PaperEngine):
    snap = engine.snapshot(max_slots=300, max_trades=300)
    esc = html.escape
    rows = "".join(f"<tr><td>{esc(c['symbole'])} {esc(c['tf'])}</td><td>{esc(c['strategie'])}</td>"
                   f"<td>{esc(c['risque'])}</td><td>{c['trades']}</td><td>{c['r_total']:+.2f}</td>"
                   f"<td>{c['pnl']:+.2f}</td><td>{esc(c['ftmo'])}</td></tr>" for c in snap["comptes"])
    pos = "".join(f"<tr><td>{esc(p['symbole'])} {esc(p['tf'])}</td><td>{p['sens']}</td><td>{p['lots']}</td>"
                  f"<td>{p['entree']}</td><td>{p['sl']}</td><td>{p['tp'] if p['tp'] is not None else 'signal'}</td>"
                  f"<td>{p['prix']}</td><td>{p['latent']:+.2f}</td><td>{esc(p['strategie'])}</td></tr>"
                  for p in snap["positions"][:300])
    trs = "".join(f"<tr><td>{esc(t['fermeture'])}</td><td>{esc(t['symbole'])} {esc(t['timeframe'])}</td>"
                  f"<td>{t['sens']}</td><td>{t['prix_entree']}</td><td>{t['sl_initial']}</td><td>{t['tp']}</td>"
                  f"<td>{t['prix_sortie']}</td><td>{esc(t['raison'])}</td><td>{t['r']:+.2f}</td><td>{t['pnl']:+.2f}</td>"
                  f"<td>{esc(t['strategie'])}</td></tr>" for t in snap["trades"][:300])
    doc = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Paper trading MT5</title>
<style>body{{font:13px system-ui,sans-serif;margin:16px;background:#f7f8fa;color:#1c2230}}
@media (prefers-color-scheme:dark){{body{{background:#0f1115;color:#e6e8ee}}}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:4px 6px;border-bottom:1px solid #8884;text-align:left;white-space:nowrap}}
div{{overflow:auto;max-height:480px}}</style></head><body>
<h1>Paper trading — trades fictifs sur prix réels</h1>
<p>Version de secours (se rafraîchit toutes les 30 s). La plateforme complète est sur http://localhost:8765 pendant que
le paper trading tourne. Mis à jour {snap['maj']} · {snap['n_comptes']} comptes fictifs · aucun ordre envoyé.</p>
<h2>Positions ouvertes</h2><div><table><tr><th>Marché</th><th>Sens</th><th>Lots</th><th>Entrée</th><th>SL</th><th>TP</th>
<th>Prix</th><th>Latent</th><th>Stratégie</th></tr>{pos}</table></div>
<h2>Derniers trades</h2><div><table><tr><th>Fermeture</th><th>Marché</th><th>Sens</th><th>Entrée</th><th>SL</th><th>TP</th>
<th>Sortie</th><th>Raison</th><th>R</th><th>P&amp;L</th><th>Stratégie</th></tr>{trs}</table></div>
<h2>Comptes fictifs</h2><div><table><tr><th>Marché</th><th>Stratégie</th><th>Risque</th><th>Trades</th><th>R total</th>
<th>P&amp;L</th><th>Challenge FTMO</th></tr>{rows}</table></div></body></html>"""
    tmp = engine.out / "tableau_de_bord.tmp"
    tmp.write_text(doc, encoding="utf-8")
    tmp.replace(engine.out / "tableau_de_bord.html")
