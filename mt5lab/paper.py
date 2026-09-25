"""Paper trading : trades FICTIFS sur les prix RÉELS de MT5, sans jamais envoyer d'ordre.

- Les prix viennent du terminal MT5 en direct (bid / ask réels, donc spread réel).
- Entrée au prix ask (achat) ou bid (vente) du moment où la bougie se clôture avec un signal.
- SL et TP sont surveillés TICK PAR TICK (historique des ticks MT5 depuis le dernier passage) :
  le SL est rempli au prix du tick qui le touche (glissement réel inclus), le TP à son niveau.
- Taille de lot, valeur du pip et contraintes de lot (min / pas) = celles de votre courtier.
- Chaque stratégie a son propre compte virtuel ; tout est sauvegardé et reprend après un redémarrage.
- Aucune fonction d'envoi d'ordre (order_send) n'est appelée dans ce module.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import indicators as ind
from .backtest import RiskConfig, _stop_distance
from .evaluator import compute_signal, describe
from .strategies import apply_filter

TRADE_FIELDS = ["strategie_id", "symbole", "timeframe", "strategie", "risque", "sens", "lots", "ouverture",
                "prix_entree", "sl_initial", "tp", "fermeture", "prix_sortie", "raison", "r", "pnl", "solde",
                "spread_entree_pts"]


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


@dataclass
class Slot:
    """Une stratégie suivie en paper trading, avec son compte virtuel."""
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

    @property
    def cfg(self) -> RiskConfig:
        return RiskConfig(**self.candidate["risk"])


def slot_id(symbol, timeframe, candidate) -> str:
    h = hashlib.sha1(json.dumps(candidate, sort_keys=True).encode()).hexdigest()[:8]
    return f"{symbol}_{timeframe}_{h}"


def load_slots(results_dir: Path, symbols, timeframe, source="tous", top=20, capital=100_000.0) -> list[Slot]:
    """Charge les stratégies à suivre depuis les classements produits par la recherche."""
    slots = []
    for sym in symbols:
        path = results_dir / f"{sym}_{timeframe}" / "classement.csv"
        if not path.exists():
            print(f"[paper] {path} introuvable : lancez d'abord la recherche pour {sym} {timeframe}")
            continue
        board = pd.read_csv(path)
        if source == "approuvees":
            board = board[board["verdict"] == "APPROUVÉ"]
        board = board.head(top)
        for _, row in board.iterrows():
            cand = json.loads(row["candidate"])
            slots.append(Slot(slot_id(sym, timeframe, cand), sym, timeframe, cand, str(row["verdict"]),
                              _num(row.get("avgR_oos")), _num(row.get("wr_oos")), capital, capital, capital))
        print(f"[paper] {sym} {timeframe} : {min(len(board), top)} stratégies suivies")
    return slots


def _num(v):
    try:
        v = float(v)
        return None if math.isnan(v) else v
    except (TypeError, ValueError):
        return None


class PaperEngine:
    def __init__(self, conn, slots: list[Slot], out_dir: Path, risk_pct: float = 1.0,
                 commission_per_lot: float = 0.0, bars: int = 1500):
        self.c = conn
        self.mt5 = conn.mt5
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.risk_pct = risk_pct
        self.commission = commission_per_lot
        self.bars = bars
        self.slots = {s.id: s for s in slots}
        for s in self.slots.values():
            s.symbol = conn.resolve(s.symbol)
        self.last_bar: dict[str, str] = {}   # "SYM|TF" -> heure de la dernière bougie clôturée traitée
        self.last_msc: dict[str, int] = {}   # symbole -> dernier tick traité (ms)
        self.recent: list[dict] = []
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
                for k in ("balance", "peak", "max_dd_pct", "trades", "wins", "sum_r", "pnl", "history_r"):
                    setattr(s, k, d[k])
                s.position = Position(**d["position"]) if d.get("position") else None
        self.last_bar.update(st.get("last_bar", {}))
        self.last_msc.update({k: int(v) for k, v in st.get("last_msc", {}).items()})
        self.recent = st.get("recent", [])
        self.started = st.get("started", self.started)
        n_open = sum(s.position is not None for s in self.slots.values())
        print(f"[paper] reprise de l'état sauvegardé ({n_open} positions fictives ouvertes)")

    def save(self):
        st = {"started": self.started, "last_bar": self.last_bar, "last_msc": self.last_msc,
              "recent": self.recent[-200:],
              "slots": {sid: {**{k: getattr(s, k) for k in ("balance", "peak", "max_dd_pct", "trades", "wins",
                                                             "sum_r", "pnl", "history_r")},
                              "position": asdict(s.position) if s.position else None}
                        for sid, s in self.slots.items()}}
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    # ------------------------------------------------------------------ calculs argent
    def _money(self, symbol: str, price_move: float, lots: float) -> float:
        info = self.c.symbol_info(symbol)
        tick_size = info.trade_tick_size or info.point
        return price_move / tick_size * (info.trade_tick_value or 0.0) * lots

    def _lots(self, symbol: str, budget: float, dist: float) -> float:
        """Plus grand lot dont la perte au stop (+ commission) reste <= budget. 0 si même le lot minimum dépasse."""
        info = self.c.symbol_info(symbol)
        per_lot = self._money(symbol, dist, 1.0) + self.commission
        if per_lot <= 0:
            return 0.0
        step = info.volume_step or 0.01
        lots = math.floor(budget / per_lot / step + 1e-9) * step
        lots = min(lots, info.volume_max)
        return round(lots, 8) if lots >= info.volume_min else 0.0

    def risk_budget(self, s: "Slot") -> float:
        """Perte max autorisée par trade : risk_pct du capital de départ (ou du solde s'il a baissé)."""
        return min(s.balance, s.capital) * self.risk_pct / 100

    # ------------------------------------------------------------------ positions
    def _open(self, s: Slot, side: int, closed: pd.DataFrame, tick):
        price = tick.ask if side > 0 else tick.bid
        info = self.c.symbol_info(s.symbol)
        atr_arr = ind.atr(closed, 14).to_numpy()
        dist = _stop_distance(closed, s.cfg, atr_arr, len(closed) - 1, side, price)
        if not np.isfinite(dist) or dist <= 0:
            return
        lots = self._lots(s.symbol, self.risk_budget(s), dist)
        if lots <= 0:
            print(f"[paper] trade ignoré sur {s.symbol} : même le lot minimum dépasserait le risque max "
                  f"de {self.risk_budget(s):.2f} ({describe(s.candidate)})")
            return
        tp = price + side * s.cfg.rr * dist if s.cfg.rr else None
        s.position = Position(side, price, price - side * dist, tp, dist, lots,
                              self._money(s.symbol, dist, lots), _now(tick), (tick.ask - tick.bid) / info.point,
                              opened_msc=int(getattr(tick, "time_msc", 0)))
        print(f"[paper] OUVERTURE {'ACHAT' if side > 0 else 'VENTE'} {s.symbol} {lots} lots @ {price} "
              f"| SL {s.position.sl:.{info.digits}f} TP {tp and round(tp, info.digits)} | {describe(s.candidate)}")

    def _close(self, s: Slot, price: float, when: str, reason: str):
        p = s.position
        gross = self._money(s.symbol, (price - p.entry) * p.side, p.lots)
        pnl = gross - self.commission * p.lots
        r = pnl / p.risk_money if p.risk_money > 0 else 0.0
        s.balance += pnl
        s.pnl += pnl
        s.trades += 1
        s.wins += pnl > 0
        s.sum_r += r
        s.history_r.append(round(r, 3))
        s.peak = max(s.peak, s.balance)
        s.max_dd_pct = max(s.max_dd_pct, (s.peak - s.balance) / s.peak * 100 if s.peak > 0 else 0)
        row = {"strategie_id": s.id, "symbole": s.symbol, "timeframe": s.timeframe, "strategie": describe(s.candidate),
               "risque": s.cfg.label(), "sens": "ACHAT" if p.side > 0 else "VENTE", "lots": p.lots,
               "ouverture": p.opened, "prix_entree": p.entry, "sl_initial": round(p.entry - p.side * p.risk, 6),
               "tp": p.tp, "fermeture": when, "prix_sortie": price, "raison": reason, "r": round(r, 3),
               "pnl": round(pnl, 2), "solde": round(s.balance, 2), "spread_entree_pts": round(p.spread_pts, 1)}
        new = not (self.out / "trades.csv").exists()
        with open(self.out / "trades.csv", "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=TRADE_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
        self.recent.append(row)
        s.position = None
        print(f"[paper] FERMETURE {row['sens']} {s.symbol} ({reason}) @ {price} -> {r:+.2f}R | {pnl:+.2f} "
              f"| solde {s.balance:.2f}")

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
                be = p.be_done and abs(p.sl - p.entry) < 1e-12
                self._close(s, float(px[i_sl]), _msc(t["time_msc"][i_sl]), "break-even" if be else "stop loss")
            elif i_tp is not None:
                self._close(s, float(p.tp), _msc(t["time_msc"][i_tp]), "take profit")

    # ------------------------------------------------------------------ bougies
    def on_bar(self, symbol: str, tf: str, closed: pd.DataFrame):
        tick = self.mt5.symbol_info_tick(symbol)
        close = float(closed["close"].iloc[-1])
        atr_last = float(ind.atr(closed, 14).iloc[-1])
        for s in [s for s in self.slots.values() if s.symbol == symbol and s.timeframe == tf]:
            cfg = s.cfg
            try:
                sig = int(apply_filter(closed, compute_signal(closed, s.candidate["signal"]), s.candidate["filter"]).iloc[-1])
            except Exception as exc:
                print(f"[paper] {s.id} : erreur de calcul du signal ({exc})")
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
                elif cfg.management == "trailing" and np.isfinite(atr_last):
                    dist = cfg.sl_value * atr_last if cfg.sl_mode == "atr" else p.risk
                    trail = close - p.side * dist
                    p.sl = max(p.sl, trail) if p.side > 0 else min(p.sl, trail)
            if s.position is None and sig != 0:
                self._open(s, sig, closed, tick)

    def step(self):
        for sym in sorted({s.symbol for s in self.slots.values()}):
            self.process_ticks(sym)
        for sym, tf in sorted({(s.symbol, s.timeframe) for s in self.slots.values()}):
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
        self.save()

    def run(self, poll: int = 5, dashboard_every: int = 30):
        print(f"[paper] {len(self.slots)} stratégies en paper trading sur "
              f"{', '.join(sorted({s.symbol for s in self.slots.values()}))} — AUCUN ordre n'est envoyé à MT5.")
        print(f"[paper] Tableau de bord : {self.out / 'tableau_de_bord.html'}  (Ctrl+C pour arrêter)")
        last_dash = 0.0
        while True:
            try:
                self.step()
                if time.time() - last_dash >= dashboard_every:
                    write_dashboard(self)
                    last_dash = time.time()
            except KeyboardInterrupt:
                self.save()
                write_dashboard(self)
                print("[paper] arrêté, état sauvegardé.")
                return
            except Exception as exc:  # une coupure réseau ne doit pas tout arrêter
                print(f"[paper] erreur : {exc}")
            try:
                time.sleep(poll)
            except KeyboardInterrupt:
                self.save()
                write_dashboard(self)
                print("[paper] arrêté, état sauvegardé.")
                return


def _msc(v) -> str:
    return datetime.fromtimestamp(int(v) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _now(tick) -> str:
    return _msc(getattr(tick, "time_msc", int(time.time() * 1000)))


# ====================================================================== tableau de bord
CSS = """
:root{--bg:#f7f8fa;--card:#fff;--fg:#1c2230;--mut:#667085;--line:#e4e7ec;--ok:#12805c;--bad:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e6e8ee;--mut:#98a2b3;--line:#2a2f3a;--ok:#3ccb7f;--bad:#f97066}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:1250px;margin:auto;padding:20px 16px}h1{font-size:22px;margin:0}h2{font-size:17px;margin:28px 0 10px}
.mut{color:var(--mut)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-top:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.card b{display:block;font-size:21px}
.scroll{overflow:auto;border:1px solid var(--line);border-radius:10px;max-height:560px}
table{width:100%;border-collapse:collapse;background:var(--card);font-size:12.5px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{position:sticky;top:0;background:var(--card)}
.pos{color:var(--ok);font-weight:600}.neg{color:var(--bad);font-weight:600}
"""


def _cls(v):
    return "pos" if v > 0 else ("neg" if v < 0 else "")


def write_dashboard(engine: PaperEngine):
    esc = html.escape
    slots = sorted(engine.slots.values(), key=lambda s: s.sum_r, reverse=True)
    ticks = {}
    for sym in {s.symbol for s in slots}:
        t = engine.mt5.symbol_info_tick(sym)
        if t is not None:
            ticks[sym] = t
    floating = 0.0
    open_rows = ""
    for s in slots:
        p = s.position
        if not p or s.symbol not in ticks:
            continue
        cur = ticks[s.symbol].bid if p.side > 0 else ticks[s.symbol].ask
        fl = engine._money(s.symbol, (cur - p.entry) * p.side, p.lots)
        floating += fl
        open_rows += (f"<tr><td>{esc(s.symbol)}</td><td>{'ACHAT' if p.side > 0 else 'VENTE'}</td><td>{p.lots}</td>"
                      f"<td>{p.entry}</td><td>{p.sl:.5f}</td><td>{'' if p.tp is None else f'{p.tp:.5f}'}</td>"
                      f"<td>{cur}</td><td class='{_cls(fl)}'>{fl:+.2f}</td><td>{esc(p.opened)}</td>"
                      f"<td>{esc(describe(s.candidate))}</td></tr>")
    closed = sum(s.trades for s in slots)
    wins = sum(s.wins for s in slots)
    pnl = sum(s.pnl for s in slots)
    cards = [("Stratégies suivies", len(slots)), ("Positions fictives ouvertes", sum(bool(s.position) for s in slots)),
             ("Trades fictifs clôturés", closed), ("Taux de réussite", f"{wins / closed * 100:.0f} %" if closed else "—"),
             ("P&L réalisé (toutes stratégies)", f"{pnl:+.2f}"), ("P&L latent", f"{floating:+.2f}")]
    cards_html = "".join(f'<div class="card"><span class="mut">{esc(k)}</span><b>{esc(str(v))}</b></div>' for k, v in cards)
    rows = ""
    for i, s in enumerate(slots, 1):
        avg = s.sum_r / s.trades if s.trades else 0.0
        wr = s.wins / s.trades * 100 if s.trades else 0.0
        exp = "" if s.expected_avg_r is None else f"{s.expected_avg_r:+.2f}R / {s.expected_wr or 0:.0f}%"
        rows += (f"<tr><td>{i}</td><td>{esc(s.symbol)} {esc(s.timeframe)}</td><td>{esc(describe(s.candidate))}</td>"
                 f"<td>{esc(s.cfg.label())}</td><td>{esc(s.verdict)}</td><td>{s.trades}</td><td>{wr:.0f}%</td>"
                 f"<td class='{_cls(avg)}'>{avg:+.2f}</td><td class='{_cls(s.sum_r)}'>{s.sum_r:+.1f}</td>"
                 f"<td class='{_cls(s.pnl)}'>{s.pnl:+.2f}</td><td>{s.balance:.2f}</td><td>{s.max_dd_pct:.1f}%</td>"
                 f"<td class='mut'>{exp}</td><td>{'en position' if s.position else ''}</td></tr>")
    last = ""
    for t in reversed(engine.recent[-60:]):
        last += (f"<tr><td>{esc(t['fermeture'])}</td><td>{esc(t['symbole'])}</td><td>{esc(t['sens'])}</td>"
                 f"<td>{t['lots']}</td><td>{t['prix_entree']}</td><td>{t['prix_sortie']}</td><td>{esc(t['raison'])}</td>"
                 f"<td class='{_cls(t['r'])}'>{t['r']:+.2f}</td><td class='{_cls(t['pnl'])}'>{t['pnl']:+.2f}</td>"
                 f"<td>{esc(t['strategie'])}</td></tr>")
    acc = engine.mt5.account_info()
    doc = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta http-equiv="refresh" content="30">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Paper trading MT5</title><style>{CSS}</style></head>
<body><main><h1>Paper trading — trades fictifs sur prix réels</h1>
<p class="mut">Prix en direct de {esc(str(getattr(acc, 'server', 'MT5')))} · aucun ordre envoyé à MetaTrader ·
chaque stratégie a son compte virtuel de {next(iter(engine.slots.values())).capital:,.0f}
(perte max {engine.risk_pct:g} % par trade) · démarré le {esc(engine.started)} ·
mis à jour le {datetime.now():%Y-%m-%d %H:%M:%S} (rafraîchissement auto 30 s)</p>
<div class="cards">{cards_html}</div>
<h2>Classement des stratégies (en direct)</h2>
<p class="mut">« Attendu » = espérance et taux de réussite mesurés hors-échantillon pendant la recherche : comparez-les au réel.</p>
<div class="scroll"><table><thead><tr><th>#</th><th>Marché</th><th>Stratégie</th><th>Risque</th><th>Verdict recherche</th>
<th>Trades</th><th>Réussite</th><th>R moyen</th><th>R total</th><th>P&amp;L</th><th>Solde</th><th>DD max</th><th>Attendu</th><th></th>
</tr></thead><tbody>{rows}</tbody></table></div>
<h2>Positions fictives ouvertes</h2>
<div class="scroll"><table><thead><tr><th>Symbole</th><th>Sens</th><th>Lots</th><th>Entrée</th><th>SL</th><th>TP</th>
<th>Prix actuel</th><th>Latent</th><th>Ouverture</th><th>Stratégie</th></tr></thead><tbody>{open_rows or '<tr><td colspan=10 class=mut>Aucune</td></tr>'}</tbody></table></div>
<h2>Derniers trades fictifs</h2>
<div class="scroll"><table><thead><tr><th>Fermeture</th><th>Symbole</th><th>Sens</th><th>Lots</th><th>Entrée</th><th>Sortie</th>
<th>Raison</th><th>R</th><th>P&amp;L</th><th>Stratégie</th></tr></thead><tbody>{last or '<tr><td colspan=10 class=mut>Pas encore de trade clôturé</td></tr>'}</tbody></table></div>
<p class="mut">Historique complet : trades.csv (ouvrable dans Excel).</p>
</main></body></html>"""
    tmp = engine.out / "tableau_de_bord.tmp"
    tmp.write_text(doc, encoding="utf-8")
    tmp.replace(engine.out / "tableau_de_bord.html")
