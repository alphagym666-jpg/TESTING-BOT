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
from .ftmo import FtmoRules, lock_text
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
    shadow: bool = False  # composant en pause : trade suivi pour le contrôle, mais hors du compte combiné

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
    eod_high: float = 0.0         # plus haut solde de fin de journée (perte max suiveuse du FTMO 1 étape)
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
    best_day: float = 0.0            # meilleure journée (argent) : règle « meilleur jour <= 50 % du profit »
    group: str = ""                  # composant d'une stratégie combinée (compte partagé)
    risk_pct: float | None = None    # risque propre à ce composant (sinon le risque général)
    paused: bool = False             # mis en pause par le contrôleur de qualité (sous-performance en direct)
    pause_reason: str = ""

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
    max_corr: int | None = None       # positions max sur des marchés corrélés dans le même sens (NASDAQ + US30...)
    session: tuple | None = None      # (début, fin, décalage serveur) : entrées seulement dans cet horaire local
    pilot: dict | None = None         # pilote de risque du challenge (ftmo.pilot_factor)
    day_lock: dict | None = None      # frein de bonne journée {"seuil": %, "facteur": 0 = stop, 0.5 = risque /2}
    compound: bool = False            # compte perso : le risque suit le SOLDE (intérêts composés)
    expected_tpm: float | None = None # trades par mois attendus (période de test de la recherche)
    weekend_close: bool = False       # compte Standard financé : tout fermer le vendredi soir, pas d'entrée le week-end
    best_day: float = 0.0             # meilleure journée (argent)
    prev_day_pnl: float = 0.0
    balance: float = 100_000.0
    peak: float = 100_000.0
    eod_high: float = 0.0             # plus haut solde de fin de journée (perte max suiveuse du FTMO 1 étape)
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
                                                                "total_budget", "max_corr", "session", "pilot",
                                                                "day_lock", "compound", "expected_tpm",
                                                                "weekend_close")]


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


def load_combined_slots(results_dir: Path, capital=100_000.0, horaire: str | None = None,
                        path: str | Path | None = None) -> tuple[list[Slot], dict]:
    """La stratégie combinée du Directeur (results/strategie_combinee.json) : un seul compte partagé.
    horaire = "24h24", "8h-17h", "8h-13h"... pour prendre la meilleure combinée de cet horaire."""
    path = Path(path) if path else \
        Path(results_dir) / (f"strategie_combinee_{horaire}.json" if horaire else "strategie_combinee.json")
    if not path.exists():
        return [], {}
    d = json.loads(path.read_text(encoding="utf-8"))
    if d.get("essai"):
        print("[paper] ATTENTION : stratégie combinée À L'ESSAI (faite de stratégies non validées) -> paper seulement")
    rules = d.get("regles", {})
    name = "Stratégie combinée"
    slots = []
    for i, c in enumerate(d.get("composants", []), 1):
        cand = c["candidate"]
        s = Slot(slot_id(c["symbole"], c["timeframe"], cand) + f"_comb{i}", c["symbole"], c["timeframe"], cand,
                 f"combinée n°{i}", capital=capital, balance=capital, peak=capital, day_start=capital,
                 group=name, risk_pct=float(c["risk_pct"]), expected_avg_r=c.get("r_moyen_attendu"),
                 expected_wr=c.get("wr_attendu"))
        slots.append(s)
    groups = {name: {"capital": capital, "day_budget": rules.get("day_budget"), "day_stop": rules.get("day_stop"),
                     "max_open": rules.get("max_open"), "total_budget": rules.get("total_budget", 10.0),
                     "max_corr": rules.get("max_correles"), "session": _session_of(d),
                     "pilot": rules.get("pilote"), "day_lock": rules.get("frein"),
                     "compound": bool(d.get("composer")), "weekend_close": bool(d.get("fermer_week_end")),
                     "expected_tpm": (d.get("resultat") or {}).get("trades_mois")
                     or (sum(float(c.get("trades_mois") or 0) for c in d.get("composants", [])) or None)}}
    print(f"[paper] stratégie combinée du Directeur : {len(slots)} composants sur un seul compte "
          f"(perte possible max {rules.get('day_budget')} %/jour)")
    return slots, groups


def _session_of(d: dict):
    h = d.get("horaire") or {}
    if h.get("debut") is None:
        return None
    return (float(h["debut"]), float(h["fin"]), float(h.get("decalage_serveur", 7.0)))


def load_slots(results_dir: Path, symbols, timeframe, source="tous", top=20, capital=100_000.0) -> list[Slot]:
    """Charge les stratégies à suivre depuis les classements produits par la recherche."""
    slots = []
    for sym in symbols:
        path = Path(results_dir) / f"{sym}_{timeframe}" / "classement.csv"
        if not path.exists():
            print(f"[paper] {path} introuvable : lancez d'abord la recherche pour {sym} {timeframe}")
            continue
        board = pd.read_csv(path)
        from .lab import mark_trials
        board = mark_trials(board)
        if source == "approuvees":
            board = board[board["verdict"] == "APPROUVÉ"]
        elif source == "essai":  # validées + à l'essai (non validées mais gagnantes hors-échantillon)
            board = board[board["verdict"].eq("APPROUVÉ") | board["verdict"].str.startswith("À L'ESSAI")]
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
                    elif sig["type"] in ("rule", "formula"):  # 2) inventions, failles des banques, lois des génies
                        nm = sig.get("name", "")
                        kind = ("loi d'un génie" if sig["type"] == "formula" else
                                "faille des banques" if nm.startswith("FAILLE") else
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
                 quiet: bool | None = None, save_every: float = 20.0, groups: dict | None = None,
                 news=None, news_window: int = 30, bridge=None):
        self.c = conn
        self.bridge = bridge  # pont vers le bot MT5 (pont.SignalBridge) : seulement pour la stratégie combinée
        # le surveillant : notifications (Telegram si configuré), contrôle du bot MT5, rapport du soir
        from .surveillant import BotWatcher, Notifier, exec_log_name
        self.notifier = Notifier()
        self.bot_watch = BotWatcher(Path(bridge.path).parent / exec_log_name(Path(bridge.path).name), self.alert,
                                    slip_log=Path(out_dir) / "glissements.csv") \
            if bridge is not None else None
        self._report_day = datetime.now().strftime("%Y-%m-%d")
        # compte MT5 RÉEL suivi par le bot : c'est LUI qui décide de l'objectif et des limites de perte
        self.real_acc = {"day": "", "day_start": None, "best_day": 0.0}
        self.news, self.news_window = news, news_window  # pas d'entrée autour des annonces importantes
        self._currencies: dict[str, set] = {}
        self._side: int | None = None  # sens du trade en cours d'ouverture (règle des marchés corrélés)
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
                                      g.get("total_budget"), g.get("max_corr"), g.get("session"), g.get("pilot"),
                                      g.get("day_lock"), bool(g.get("compound")), g.get("expected_tpm"),
                                      bool(g.get("weekend_close")), balance=cap, peak=cap, day_start=cap)
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
        self.total_trades = 0
        self.meteo: dict = {}  # symbole -> {timeframe: type de marché}  # tous les trades jamais clôturés (gardés pour toujours dans trades.csv)
        self.events: deque = deque(maxlen=400)
        self.started = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._load_state()
        self._load_history()  # tous les trades déjà pris restent visibles après un redémarrage
        try:  # définition de chaque stratégie suivie : sert à l'analyse du direct et aux bots
            (self.out / "strategies.json").write_text(json.dumps(
                {s.id: {"symbole": s.symbol, "timeframe": s.timeframe, "candidate": s.candidate}
                 for s in self.slots.values()}, ensure_ascii=False, default=str), encoding="utf-8")
        except OSError:
            pass

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
        self.real_acc.update(st.get("real_acc", {}))
        n_open = sum(s.position is not None for s in self.slots.values())
        print(f"[paper] reprise de l'état sauvegardé ({n_open} positions fictives ouvertes)")

    def _load_history(self, keep: int = 5000):
        """Recharge les derniers trades depuis trades.csv (l'historique complet n'est jamais effacé)."""
        path = self.out / "trades.csv"
        if not path.exists():
            return
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
        except (OSError, csv.Error):
            return
        self.total_trades = len(rows)
        num = {"lots", "prix_entree", "sl_initial", "sl_final", "tp", "prix_sortie", "duree_min", "pips", "r", "pnl",
               "solde", "spread_entree_pts"}
        for r in rows[-keep:]:
            for k in num & r.keys():
                try:
                    r[k] = float(r[k]) if r[k] not in ("", None) else None
                except ValueError:
                    pass
        self.recent = rows[-keep:]

    def save(self):
        active = {sid: s for sid, s in self.slots.items() if s.trades or s.position or s.ftmo_status != "en cours"}
        st = {"started": self.started, "last_bar": self.last_bar, "last_msc": self.last_msc, "real_acc": self.real_acc,
              "recent": self.recent[-1000:], "events": list(self.events),
              "groups": {n: {k: getattr(g, k) for k in GROUP_SAVED} for n, g in self.groups.items()},
              "slots": {sid: {**{k: getattr(s, k) for k in SAVED},
                              "position": asdict(s.position) if s.position else None}
                        for sid, s in active.items()}}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st), encoding="utf-8")
        tmp.replace(self.state_path)
        try:
            self.write_quality()
        except OSError:
            pass
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
            if g.pilot:  # pilote de risque : moins de risque quand le challenge va mal (ou près du but)
                from .ftmo import pilot_factor
                pl = {**g.pilot, "cible": self.ftmo.target1}
                pct *= float(pilot_factor(pl, (g.day_start - g.capital) / g.capital * 100,
                                          g.prev_day_pnl / g.capital * 100))
            if self._locked(g):  # frein de bonne journée : risque réduit jusqu'à demain
                pct *= float(g.day_lock.get("facteur", 0))
            return self._risk_base(g) * pct / 100
        return min(s.balance, s.capital) * pct / 100

    def _per_month(self, n: int) -> float | None:
        """Rythme en direct : trades ramenés à un mois depuis le début du paper trading (après 3 jours)."""
        try:
            days = (datetime.now() - datetime.strptime(self.started[:16], "%Y-%m-%d %H:%M")).total_seconds() / 86400
        except ValueError:
            return None
        return round(n / days * 30.44, 1) if days >= 3 else None

    @staticmethod
    def _risk_base(g: Group) -> float:
        """Base du risque : le solde (compte perso, intérêts composés) ou le capital de départ s'il est plus bas."""
        return g.balance if g.compound else min(g.balance, g.capital)

    def _locked(self, g: Group) -> bool:
        """Frein de bonne journée : +seuil % gagnés aujourd'hui (paper ou vrai compte, le plus haut des deux)."""
        if not g.day_lock:
            return False
        real = self.real_account()
        gain = max(g.day_realized, (real["solde"] - real["day_start"]) if real else g.day_realized)
        return gain >= float(g.day_lock["seuil"]) * g.capital / 100

    def group_allows(self, s: Slot, when: str) -> bool:
        """Règles de risque de la stratégie combinée, vérifiées avant chaque nouveau trade."""
        g = self.groups.get(s.group)
        if g is None:
            return True
        if g.session and not in_session(when, g.session):
            return False  # hors de l'horaire choisi : pas de nouvelle entrée (les positions ouvertes continuent)
        if g.weekend_close and near_weekend(when):
            return False  # compte Standard : pas de nouvelle position à l'approche du week-end
        if g.ftmo_status != "en cours" and self.bridge is None:
            return False  # avec le bot, le compte réel a ses propres garde-fous : on continue à donner les signaux
        if when[:10] != g.day:
            self._roll_day(g, g.balance, when)
        members = [x for x in self.slots.values() if x.group == g.name and x.position and not x.position.shadow]
        # pertes du jour et totale : la PIRE des deux entre le paper trading et le vrai compte du bot
        real = self.real_account()
        day_loss = max(0.0, -g.day_realized, (real["day_start"] - real["solde"]) if real else 0.0)
        total_loss = max(0.0, g.capital - g.balance, (g.capital - real["solde"]) if real else 0.0,
                         self._total_used(g, g.balance),
                         self._total_used(g, real["solde"], real.get("eod_high")) if real else 0.0)
        ok = True
        if g.max_open is not None and len(members) >= g.max_open:
            ok = False
        elif g.day_stop is not None and day_loss >= g.day_stop * g.capital / 100:
            ok = False
        elif g.day_lock and float(g.day_lock.get("facteur", 0)) <= 0 and self._locked(g):
            ok = False  # frein de bonne journée : la journée est déjà bonne, on garde le gain jusqu'à demain
        elif g.day_budget is not None:
            open_risk = sum(x.position.risk_money for x in members) * 1.1
            new_risk = self.risk_budget(s) * 1.1
            if (day_loss + open_risk + new_risk) / g.capital * 100 > g.day_budget + 1e-9:
                ok = False
        if ok and g.max_corr is not None and self._side is not None:
            from .data import correlation_of
            cl, sign = correlation_of(s.symbol)
            if cl:
                same = sum(1 for x in members if correlation_of(x.symbol)[0] == cl
                           and x.position.side * correlation_of(x.symbol)[1] == self._side * sign)
                ok = same < g.max_corr
        if ok and g.total_budget is not None:  # même si tous les stops sautent, la perte totale reste sous le plafond
            open_risk = sum(x.position.risk_money for x in members) * 1.1
            new_risk = self.risk_budget(s) * 1.1
            if (total_loss + open_risk + new_risk) / g.capital * 100 > g.total_budget + 1e-9:
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
            if acc.day:
                acc.best_day = max(acc.best_day, equity - acc.day_start)
                acc.eod_high = max(acc.eod_high, acc.balance)  # solde de clôture de la journée
            if isinstance(acc, Group) and acc.day:
                acc.prev_day_pnl = equity - acc.day_start
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
        elif equity <= self._floor(acc):
            acc.ftmo_status, acc.ftmo_when = "ÉCHOUÉ (perte max totale)", when
        elif flat and R.target1 > 0 and (acc.balance - acc.capital) / acc.capital * 100 >= self.target_needed(acc) \
                and len(acc.trade_days) >= R.min_days:
            acc.ftmo_status, acc.ftmo_when = "RÉUSSI", when
        return acc.ftmo_status != "en cours"

    def _floor(self, acc, eod_high: float | None = None) -> float:
        """Plancher de la perte max totale en argent : fixe (capital - 10 %) ou SUIVEUSE (FTMO 1 étape : plus haut
        solde de fin de journée - 10 % du capital, jamais au-dessus du capital de départ)."""
        R, cap = self.ftmo, acc.capital
        if not getattr(R, "trailing", False):
            return cap * (1 - R.max_total / 100)
        hi = max(cap, acc.eod_high if eod_high is None else eod_high)
        return min(hi - cap * R.max_total / 100, cap)

    def _total_used(self, acc, bal: float, eod_high: float | None = None) -> float:
        """Perte totale « consommée » en argent (0 = rien) : distance perdue vers le plancher, fixe ou suiveux."""
        return max(0.0, acc.capital * self.ftmo.max_total / 100 - (bal - self._floor(acc, eod_high)))

    def alert(self, text: str, when: str | None = None):
        """Alerte du surveillant : journal de la plateforme + téléphone (Telegram si configuré)."""
        when = when or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.events.append({"t": when, "type": "SURVEILLANT", "symbole": "", "tf": "", "texte": text})
        self._dirty = True
        print(f"[surveillant] {when} {text}")
        self.notifier.send(f"🛰 {text}")

    def update_ftmo(self, s: Slot, equity: float, when: str):
        if self._ftmo_check(s, equity, when, s.position is None):
            self.event(when, "FTMO", s, f"challenge {s.ftmo_status} : {describe(s.candidate)} [{s.cfg.label()}]")
            if s.ftmo_status == "RÉUSSI" or s.group:
                self.notifier.send(f"🏁 Challenge {s.ftmo_status} : {s.symbol} {s.timeframe} {describe(s.candidate)[:80]}")

    def update_groups(self):
        """Suivi FTMO des stratégies combinées, positions ouvertes comprises."""
        for g in self.groups.values():
            members = [x for x in self.slots.values() if x.group == g.name]
            if not members:
                continue
            fl, when = 0.0, None
            for x in members:
                t = self.mt5.symbol_info_tick(x.symbol)
                if t is not None and not (x.position and x.position.shadow):
                    fl += self.floating(x, t)
                    when = when or _now(t)
            if when and self._ftmo_check(g, g.balance + fl, when, not any(x.position and not x.position.shadow for x in members)):
                self.events.append({"t": when, "type": "FTMO", "symbole": "COMBINÉE", "tf": "",
                                    "texte": f"stratégie combinée « {g.name} » : challenge {g.ftmo_status}"})
                self.notifier.send(f"🏁 Stratégie combinée « {g.name} » : challenge {g.ftmo_status}")
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
        if self.news is not None and self._news_blackout(s, when):
            return
        shadow = bool(s.group and s.paused)
        self._side = side
        if s.group and not shadow and not self.group_allows(s, when):
            return
        lots = self._lots(s.symbol, self.risk_budget(s), dist)
        acc = self.groups.get(s.group) if s.group else s
        micro = acc is not None and self._target_reached(acc)
        if micro:
            if when[:10] in acc.trade_days:
                return  # objectif atteint et journée déjà comptée : on ne risque plus rien
            lots = info.volume_min  # objectif atteint : micro-trade pour compter les jours minimum FTMO
        if lots <= 0:
            if s.group:  # petit compte : le lot minimum dépasse le risque permis -> trade sauté (jamais plus de risque)
                self.event(when, "REFUS", s, f"trade sauté : le lot minimum ({info.volume_min}) risquerait plus que "
                                             f"{self.risk_budget(s):.2f} $ sur ce stop")
            return
        tp = price + side * s.cfg.rr * dist if s.cfg.rr else None
        s.position = Position(side, price, price - side * dist, tp, dist, lots,
                              self._money(s.symbol, dist, lots), when, (tick.ask - tick.bid) / info.point,
                              opened_msc=int(getattr(tick, "time_msc", 0)), shadow=shadow)
        if when[:10] not in s.trade_days:
            s.trade_days.append(when[:10])
        g = None if shadow else self.groups.get(s.group)
        if g is not None and when[:10] not in g.trade_days:
            g.trade_days.append(when[:10])
        if s.group and not shadow:
            self.notifier.send(f"📈 {'ACHAT' if side > 0 else 'VENTE'} {s.symbol} {s.timeframe} à {price:g} | "
                               f"{describe(s.candidate)[:70]}")
        if self._bot(s):
            from .pont import slot_key
            self.bot_watch.signal_open(slot_key(s.id), f"{s.symbol} {s.timeframe} {describe(s.candidate)[:60]}", price)
            g0 = self.groups.get(s.group)
            base = self._risk_base(g0) if g0 else min(s.balance, s.capital)
            self.bridge.open(s.id, s.symbol, side, dist, s.cfg.rr,
                             0.0 if micro else round(self.risk_budget(s) / base * 100, 4),  # 0 = micro-trade
                             self.commission(s.symbol))  # risque du pilote compris
        d = info.digits
        self.event(when, "OUVERTURE", s, f"{'ACHAT' if side > 0 else 'VENTE'} {lots} lots @ {price:.{d}f} | "
                                         f"SL {s.position.sl:.{d}f} | TP {'signal' if tp is None else f'{tp:.{d}f}'} | "
                                         f"{describe(s.candidate)} [{s.cfg.label()}]")

    def _bot(self, s: Slot) -> bool:
        """Les ordres du bot MT5 ne concernent que les composants actifs de la stratégie combinée."""
        return self.bridge is not None and bool(s.group) and not (s.position is not None and s.position.shadow)

    def real_account(self) -> dict | None:
        """Le VRAI compte MT5 où tourne le bot (solde, équité, début de journée, meilleure journée), ou None sans bot.
        Le paper trading et le vrai compte ne donnent jamais exactement le même résultat (spread, glissement,
        arrondi des lots, vitesse d'exécution) : l'objectif et les pertes max se décident sur le VRAI compte."""
        if self.bridge is None:
            return None
        try:
            a = self.mt5.account_info()
            bal, eq = float(a.balance), float(a.equity)
        except Exception:
            return None
        r = self.real_acc
        today = datetime.now().strftime("%Y-%m-%d")
        if r["day"] != today:
            if r["day_start"] is not None:
                r["best_day"] = max(r["best_day"], bal - r["day_start"])
                r["eod_high"] = max(r.get("eod_high") or 0.0, bal)  # solde de clôture (perte max suiveuse)
            r["day"], r["day_start"] = today, bal
            self._dirty = True
        return {"solde": bal, "equite": eq, "day_start": r["day_start"], "best_day": r["best_day"],
                "eod_high": r.get("eod_high") or 0.0}

    def target_needed(self, acc) -> float:
        """Objectif réel en % : +10 %, ou plus si la meilleure journée dépasse 50 % du profit (règle du meilleur jour).
        La journée en cours compte aussi. Avec le bot : calculé sur le VRAI compte MT5."""
        real = self.real_account() if isinstance(acc, Group) else None
        if real:
            best = max(real["best_day"], real["solde"] - real["day_start"])
        else:
            best = max(acc.best_day, acc.balance - acc.day_start if acc.day else 0.0)
        return self.ftmo.target_needed(self.ftmo.target1, best / acc.capital * 100)

    def _target_reached(self, acc) -> bool:
        """Objectif FTMO atteint mais challenge pas encore validé (jours de trading minimum pas atteints).
        Avec le bot : c'est le solde du VRAI compte qui compte (s'il est à +9 % quand le paper est à +10 %, on
        continue à trader normalement)."""
        if acc.ftmo_status != "en cours" and not (isinstance(acc, Group) and self.bridge is not None):
            return False
        if self.ftmo.target1 <= 0:  # compte perso / financé : pas d'objectif, on continue à trader normalement
            return False
        real = self.real_account() if isinstance(acc, Group) else None
        bal = real["solde"] if real else acc.balance
        return (bal - acc.capital) / acc.capital * 100 >= self.target_needed(acc)

    def _swap(self, symbol: str, p: Position, price: float, when: str) -> float:
        """Swaps (frais ou crédit de nuit) pour chaque nuit passée en position, comme chez le courtier."""
        try:
            nights = (pd.Timestamp(when[:10]) - pd.Timestamp(p.opened[:10])).days
            if nights <= 0:
                return 0.0
            sl, ss = self.c.swap_in_price(symbol, price)
            return self._money(symbol, (sl if p.side > 0 else ss) * nights, p.lots)
        except Exception:
            return 0.0

    def _news_blackout(self, s: Slot, when: str) -> bool:
        from .data import news_blocked
        if s.timeframe in ("H4", "D1", "W1"):  # même règle que la recherche : filtre seulement jusqu'à H1
            return False
        if s.symbol not in self._currencies:
            try:
                self._currencies[s.symbol] = self.c.currencies(s.symbol)
            except Exception:
                self._currencies[s.symbol] = {"USD"}
        if news_blocked(self.news, self._currencies[s.symbol], when, self.news_window):
            self.event(when, "NOUVELLE", s, "entrée annulée : annonce économique importante à moins de "
                                            f"{self.news_window} min")
            return True
        return False

    def _close(self, s: Slot, price: float, when: str, reason: str):
        p = s.position
        if self._bot(s):
            self.bridge.close(s.id, s.symbol)
        gross = self._money(s.symbol, (price - p.entry) * p.side, p.lots)
        gross += self._swap(s.symbol, p, price, when)
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
        self.total_trades += 1
        if len(self.recent) > 6000:
            self.recent = self.recent[-5000:]
        g = None if p.shadow else self.groups.get(s.group)
        if g is not None:
            self.notifier.send(f"{'✅' if pnl > 0 else '❌'} Fermeture {s.symbol} {s.timeframe} ({reason}) : {r:+.2f}R, "
                               f"{pnl:+,.2f} $".replace(",", " "))
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
                                         f"solde {s.balance:,.2f}" + (" (en pause : hors compte combiné)" if p.shadow else ""))
        self.update_ftmo(s, s.balance, when)
        self.quality_check(s, when)

    # ------------------------------------------------------------------ contrôleur de qualité
    QC_MIN_TRADES = 20
    QC_WINDOW = 50

    def quality_stats(self, s: Slot) -> dict:
        """Compare le R moyen EN DIRECT au R moyen attendu (hors-échantillon de la recherche)."""
        h = np.array(s.history_r[-self.QC_WINDOW:], dtype=float)
        out = {"trades": s.trades, "r_moyen_direct": round(float(h.mean()), 3) if len(h) else None,
               "r_moyen_attendu": s.expected_avg_r, "z": None}
        if len(h) >= 2 and s.expected_avg_r is not None:
            sd = float(h.std(ddof=1)) or 1.0
            out["z"] = round(float((h.mean() - s.expected_avg_r) / (sd / np.sqrt(len(h)))), 2)
        return out

    def quality_check(self, s: Slot, when: str):
        """Met en pause une stratégie qui fait nettement moins bien en direct que dans la recherche
        (au moins 20 trades, R moyen négatif ET écart statistique z < -2), et la réactive si ses 20 derniers
        trades (suivis même en pause) redeviennent bons. Un composant en pause ne touche plus le compte combiné."""
        if s.expected_avg_r is None or s.trades < self.QC_MIN_TRADES:
            return
        q = self.quality_stats(s)
        if not s.paused and q["z"] is not None and q["z"] < -2.0 and q["r_moyen_direct"] < 0:
            s.paused = True
            s.pause_reason = (f"{when[:16]} : R moyen en direct {q['r_moyen_direct']:+.2f} contre {s.expected_avg_r:+.2f} "
                              f"attendu sur {min(s.trades, self.QC_WINDOW)} trades (z = {q['z']})")
            self.event(when, "CONTRÔLE", s, f"MISE EN PAUSE — {s.pause_reason}. Le Directeur en sera informé.")
        elif s.paused:
            last = np.array(s.history_r[-self.QC_MIN_TRADES:], dtype=float)
            if len(last) >= self.QC_MIN_TRADES and last.mean() > 0 and last.mean() >= 0.5 * s.expected_avg_r:
                s.paused, s.pause_reason = False, ""
                self.event(when, "CONTRÔLE", s, f"RÉACTIVÉE — {self.QC_MIN_TRADES} derniers trades à "
                                                f"{last.mean():+.2f}R en moyenne")

    def write_quality(self):
        """controle_qualite.json : lu par le Directeur pour écarter les stratégies en pause."""
        rows = []
        for s in self.slots.values():
            if not s.trades:
                continue
            rows.append({"symbole": s.symbol, "timeframe": s.timeframe, "strategie": describe(s.candidate),
                         "candidate": s.candidate, "groupe": s.group, "en_pause": s.paused, "raison": s.pause_reason,
                         **self.quality_stats(s)})
        (self.out / "controle_qualite.json").write_text(json.dumps(
            {"maj": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "strategies": rows}, indent=1, ensure_ascii=False,
            default=str), encoding="utf-8")

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
        try:  # le météorologue : type de marché du moment, affiché dans la plateforme
            from .strategies import market_regime
            self.meteo.setdefault(symbol, {})[tf] = market_regime(closed).iloc[-1]
        except Exception:
            pass
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
                    if self._bot(s):
                        self.bridge.breakeven(s.id, s.symbol)
                elif cfg.management == "trailing" and np.isfinite(atr_last):
                    dist = cfg.sl_value * atr_last if cfg.sl_mode == "atr" else p.risk
                    trail = close - p.side * dist
                    new_sl = max(p.sl, trail) if p.side > 0 else min(p.sl, trail)
                    if new_sl != p.sl and self._bot(s):
                        self.bridge.move_sl(s.id, s.symbol, new_sl)
                    p.sl = new_sl
            if s.position is None and sig != 0 and (s.group or s.ftmo_status == "en cours"):
                self._open(s, sig, closed, tick, atr_arr)

    def _weekend_close(self):
        """Compte FTMO Standard financé : toutes les positions des stratégies combinées concernées sont fermées le
        vendredi soir (heure du serveur MT5), avant la fermeture des marchés."""
        for g in self.groups.values():
            if not g.weekend_close:
                continue
            for s in self.slots.values():
                if s.group != g.name or not s.position or s.position.shadow:
                    continue
                t = self.mt5.symbol_info_tick(s.symbol)
                if t is None:
                    continue
                when = _now(t)
                if near_weekend(when, close=True):
                    self._close(s, t.bid if s.position.side > 0 else t.ask, when, "fermeture avant le week-end")

    def step(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._report_day:  # rapport du soir de la journée qui vient de finir
            try:
                from .surveillant import daily_report, write_daily_report
                write_daily_report(self, self._report_day)
                self.notifier.send(daily_report(self, self._report_day))
            except Exception as exc:
                print(f"[surveillant] rapport du jour impossible : {exc}")
            self._report_day = today
        if self.bot_watch is not None:
            self.bot_watch.poll(lambda sym: getattr(self.c.symbol_info(sym), "point", None))
        for sym in sorted({s.symbol for s in self.slots.values()}):
            self.process_ticks(sym)
        self._weekend_close()
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
            ext = sorted({x for sl in self.by_bar[(sym, tf)] for x in ext_needed(sl.candidate)})
            if ext:  # inventions inter-marchés : il faut aussi les prix des autres marchés
                from .data import add_ext
                others = {}
                for o in ext:
                    try:
                        others[o] = self.c.rates(o, tf, self.bars)["close"]
                    except Exception as exc:
                        print(f"[paper] inter-marchés : {o} {tf} indisponible ({exc})")
                df = add_ext(df, others)
            self.on_bar(sym, tf, df.iloc[:-1])
        if self.groups:
            self.update_groups()
        if self._dirty or time.time() - self._last_save >= self.save_every:
            self.save()  # sauvegarde dès qu'un trade s'ouvre / se ferme (reprise sans perte après un arrêt)

    # ------------------------------------------------------------------ données pour la plateforme
    def snapshot(self, max_slots: int = 3000, max_trades: int = 3000) -> dict:
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
                    "trades_mois": self._per_month(s.trades),
                    "equite": round(eq, 2), "dd_max": round(s.max_dd_pct, 2),
                    "profit_pct": round((eq - s.capital) / s.capital * 100, 2),
                    "pire_jour_pct": round(s.worst_day_pct, 2), "jours_trades": len(s.trade_days),
                    "objectif_requis_pct": round(self.target_needed(s), 2),
                    "meilleur_jour_pct": round(max(s.best_day, s.balance - s.day_start if s.day else 0) / s.capital * 100, 2),
                    "ftmo": s.ftmo_status, "ftmo_quand": s.ftmo_when, "en_position": bool(p),
                    "attendu_r": s.expected_avg_r, "en_pause": s.paused, "pause_raison": s.pause_reason})
        slot_rows.sort(key=lambda r: r["r_total"], reverse=True)
        group_rows = []
        for g in self.groups.values():
            members = [x for x in self.slots.values() if x.group == g.name]
            fl = sum(self.floating(x, ticks.get(x.symbol)) for x in members if not (x.position and x.position.shadow))
            eq = g.balance + fl
            open_risk = sum(x.position.risk_money for x in members if x.position and not x.position.shadow)
            group_rows.append({
                "nom": g.name, "capital": g.capital, "solde": round(g.balance, 2), "equite": round(eq, 2),
                "latent": round(fl, 2), "profit_pct": round((eq - g.capital) / g.capital * 100, 2),
                "jour_pct": round((eq - g.day_start) / g.capital * 100, 2),
                "jour_realise_pct": round(g.day_realized / g.capital * 100, 2),
                "risque_ouvert_pct": round(open_risk / g.capital * 100, 2),
                "pire_jour_pct": round(g.worst_day_pct, 2), "dd_max": round(g.max_dd_pct, 2), "trades": g.trades,
                "objectif_requis_pct": round(self.target_needed(g), 2),
                "reel": (lambda r: None if not r else {
                    "solde": round(r["solde"], 2), "equite": round(r["equite"], 2),
                    "profit_pct": round((r["solde"] - g.capital) / g.capital * 100, 2),
                    "jour_pct": round((r["equite"] - r["day_start"]) / g.capital * 100, 2),
                    "ecart_paper": round(g.balance - r["solde"], 2)})(self.real_account()),
                "gagnants": g.wins, "r_total": round(g.sum_r, 2), "pnl": round(g.pnl, 2), "ftmo": g.ftmo_status,
                "ftmo_quand": g.ftmo_when, "jours_trades": len(g.trade_days), "refuses": g.skipped,
                "trades_mois_attendus": g.expected_tpm, "trades_mois_direct": self._per_month(g.trades),
                "regles": {"budget_jour": g.day_budget, "arret_jour": g.day_stop, "max_positions": g.max_open,
                           "budget_total": g.total_budget, "max_correles": g.max_corr,
                           "frein": lock_text(g.day_lock) if g.day_lock else None, "frein_actif": self._locked(g),
                           "horaire": None if not g.session else f"{g.session[0]:g}h-{g.session[1]:g}h"},
                "composants": [{"symbole": x.symbol, "tf": x.timeframe, "strategie": describe(x.candidate),
                                "risque": x.cfg.label(), "risque_pct": x.risk_pct, "trades": x.trades,
                                "trades_mois": self._per_month(x.trades),
                                "gagnants": x.wins, "r_total": round(x.sum_r, 2), "en_position": bool(x.position),
                                "en_pause": x.paused, "pause_raison": x.pause_reason,
                                "r_moyen": round(x.sum_r / x.trades, 3) if x.trades else 0.0,
                                "attendu_r": x.expected_avg_r}
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
            "profil": getattr(self, "profile", None),
            "meteo": {k: {t: (x if isinstance(x, str) else None) for t, x in v.items()} for k, v in self.meteo.items()},
            "n_comptes": len(self.slots), "n_actifs": len(slot_rows), "n_trades_total": self.total_trades,
            "bot": self.bot_watch.status() if self.bot_watch is not None else None,
            "telegram": self.notifier.active,
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
        self.alert("MT5 est déconnecté : le paper trading attend qu'il revienne.")
        while True:
            try:
                self.mt5.shutdown()
            except Exception:
                pass
            try:
                self.c.connect(verbose=False)
                print(f"[paper] {datetime.now():%H:%M} reconnecté à MT5, je reprends")
                self.alert("MT5 est reconnecté : le paper trading reprend.")
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
    if s["type"] in ("rule", "formula"):
        return s.get("name", "INVENTION")
    if s["type"] == "vote":
        return s.get("name", "CONSEIL")
    return f"{s['a']['name']}+{s['b']['name']}"


def _msc(v) -> str:
    return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def ext_needed(obj) -> set:
    """Autres marchés utilisés par une stratégie (conditions inter-marchés « s » des inventions)."""
    out = set()
    if isinstance(obj, dict):
        if obj.get("s") and str(obj.get("f", "")).startswith("ext_"):
            out.add(obj["s"])
        for v in obj.values():
            out |= ext_needed(v)
    elif isinstance(obj, list):
        for v in obj:
            out |= ext_needed(v)
    return out


def near_weekend(when: str, close: bool = False) -> bool:
    """Heure du serveur MT5 (FTMO : GMT+2/+3). Vendredi à partir de 21 h : plus de nouvelle entrée ; à partir de
    22 h : fermeture des positions (les indices ferment vers 23 h). Samedi et dimanche : marché fermé."""
    try:
        t = datetime.strptime(when[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return False
    if t.weekday() >= 5:
        return True
    return t.weekday() == 4 and t.hour >= (22 if close else 21)


def in_session(when: str, session) -> bool:
    """when = heure du serveur MT5 ; session = (début, fin, décalage) en heure locale."""
    start, end, offset = session
    t = pd.Timestamp(when) - pd.Timedelta(hours=offset)
    h = t.hour + t.minute / 60.0
    return start <= h < end


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
