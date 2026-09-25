"""Exécution d'une stratégie validée sur MetaTrader 5.

Sécurités par défaut :
  - mode simulation (--execute requis pour envoyer de vrais ordres) ;
  - refuse un compte RÉEL sauf --allow-real ;
  - une seule position à la fois par symbole (identifiée par le magic number) ;
  - taille de lot calculée pour risquer exactement risk_pct % du solde au stop loss.
"""
from __future__ import annotations

import json
import math
import time

import numpy as np

from . import indicators as ind
from .backtest import RiskConfig, _stop_distance
from .data import MT5Connector
from .evaluator import compute_signal, describe
from .strategies import apply_filter

MAGIC = 26092026


def lot_size(info, balance: float, risk_pct: float, sl_distance: float) -> float:
    tick_value = info.trade_tick_value or 0.0
    tick_size = info.trade_tick_size or info.point
    if tick_value <= 0 or sl_distance <= 0:
        return 0.0
    loss_per_lot = sl_distance / tick_size * tick_value
    lots = balance * risk_pct / 100.0 / loss_per_lot
    step = info.volume_step or 0.01
    lots = math.floor(lots / step) * step
    return float(min(max(lots, 0.0), info.volume_max)) if lots >= info.volume_min else 0.0


class LiveTrader:
    def __init__(self, conn: MT5Connector, symbol: str, timeframe: str, candidate: dict,
                 risk_pct: float = 0.5, execute: bool = False, allow_real: bool = False, bars: int = 1500):
        self.c = conn
        self.mt5 = conn.mt5
        self.symbol, self.tf, self.cand = conn.resolve(symbol), timeframe, candidate
        self.cfg = RiskConfig(**candidate["risk"])
        self.risk_pct, self.execute, self.bars = risk_pct, execute, bars
        acc = self.mt5.account_info()
        if acc is None:
            raise RuntimeError("Compte MT5 introuvable")
        if acc.trade_mode != self.mt5.ACCOUNT_TRADE_MODE_DEMO and not allow_real:
            raise RuntimeError("Compte RÉEL détecté : ajoutez --allow-real si vous êtes VRAIMENT sûr(e).")
        self.last_bar = None
        term = self.mt5.terminal_info()
        if execute and term is not None and not term.trade_allowed:
            raise RuntimeError("« Algo Trading » est désactivé dans le terminal MT5 : cliquez sur le bouton pour l'activer.")

    def log(self, msg):
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [{self.symbol} {self.tf}] {msg}", flush=True)

    def my_position(self):
        pos = self.mt5.positions_get(symbol=self.symbol) or []
        return next((p for p in pos if p.magic == MAGIC), None)

    def step(self):
        df = self.c.rates(self.symbol, self.tf, self.bars)
        closed = df.iloc[:-1]  # la dernière bougie est en cours de formation
        bar_time = closed.index[-1]
        pos = self.my_position()
        if pos is not None:
            self.manage(pos, closed)
        if bar_time == self.last_bar:
            return
        self.last_bar = bar_time
        sig = apply_filter(closed, compute_signal(closed, self.cand["signal"]), self.cand["filter"])
        s = int(sig.iloc[-1])
        if self.cfg.direction == "long" and s < 0 or self.cfg.direction == "short" and s > 0:
            s = 0
        if s == 0:
            return
        if pos is not None:
            side = 1 if pos.type == self.mt5.POSITION_TYPE_BUY else -1
            if self.cfg.rr is None and side == -s:
                self.close(pos, "signal opposé")
                pos = None
            else:
                return
        self.open(s, closed)

    def open(self, side: int, closed):
        info = self.c.symbol_info(self.symbol)
        tick = self.mt5.symbol_info_tick(self.symbol)
        price = tick.ask if side > 0 else tick.bid
        atr_arr = ind.atr(closed, 14).to_numpy()
        dist = _stop_distance(closed, self.cfg, atr_arr, len(closed) - 1, side, price)
        if not np.isfinite(dist) or dist <= 0:
            self.log("distance de stop invalide, ordre ignoré")
            return
        sl = round(price - side * dist, info.digits)
        tp = round(price + side * self.cfg.rr * dist, info.digits) if self.cfg.rr else 0.0
        balance = self.mt5.account_info().balance
        vol = lot_size(info, balance, self.risk_pct, dist)
        what = f"{'ACHAT' if side > 0 else 'VENTE'} {vol} lots @ {price} SL {sl} TP {tp or '-'} ({describe(self.cand)})"
        if vol <= 0:
            self.log(f"lot trop petit pour le risque demandé, ignoré : {what}")
            return
        if not self.execute:
            self.log(f"[SIMULATION] {what}")
            return
        req = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": self.symbol, "volume": vol,
               "type": self.mt5.ORDER_TYPE_BUY if side > 0 else self.mt5.ORDER_TYPE_SELL,
               "price": price, "sl": sl, "tp": tp, "deviation": 20, "magic": MAGIC,
               "comment": "mt5lab", "type_time": self.mt5.ORDER_TIME_GTC,
               "type_filling": self.c.filling_mode(self.symbol)}
        res = self.mt5.order_send(req)
        self.log(f"{what} -> retcode {getattr(res, 'retcode', None)} {getattr(res, 'comment', '')}")

    def close(self, pos, why: str):
        tick = self.mt5.symbol_info_tick(self.symbol)
        buy = pos.type == self.mt5.POSITION_TYPE_BUY
        if not self.execute:
            self.log(f"[SIMULATION] fermeture position {pos.ticket} ({why})")
            return
        req = {"action": self.mt5.TRADE_ACTION_DEAL, "symbol": self.symbol, "volume": pos.volume,
               "type": self.mt5.ORDER_TYPE_SELL if buy else self.mt5.ORDER_TYPE_BUY, "position": pos.ticket,
               "price": tick.bid if buy else tick.ask, "deviation": 20, "magic": MAGIC, "comment": why,
               "type_filling": self.c.filling_mode(self.symbol)}
        res = self.mt5.order_send(req)
        self.log(f"fermeture {pos.ticket} ({why}) -> retcode {getattr(res, 'retcode', None)}")

    def manage(self, pos, closed):
        """Break-even à +1R ou trailing stop, comme dans le backtest."""
        if self.cfg.management == "none" or not self.execute:
            return
        side = 1 if pos.type == self.mt5.POSITION_TYPE_BUY else -1
        entry, sl = pos.price_open, pos.sl
        if not sl:
            return
        close = float(closed["close"].iloc[-1])
        new_sl = sl
        if self.cfg.management == "breakeven":
            risk = abs(entry - sl)
            if (close - entry) * side >= risk and (sl - entry) * side < 0:
                new_sl = entry
        else:
            a = float(ind.atr(closed, 14).iloc[-1])
            dist = self.cfg.sl_value * a if self.cfg.sl_mode == "atr" else abs(entry - sl)
            trail = close - side * dist
            new_sl = max(sl, trail) if side > 0 else min(sl, trail)
        if abs(new_sl - sl) > 1e-12:
            info = self.c.symbol_info(self.symbol)
            res = self.mt5.order_send({"action": self.mt5.TRADE_ACTION_SLTP, "symbol": self.symbol,
                                       "position": pos.ticket, "sl": round(new_sl, info.digits), "tp": pos.tp})
            self.log(f"SL déplacé {sl} -> {new_sl:.5f} ({self.cfg.management}) retcode {getattr(res, 'retcode', None)}")

    def run(self, poll_seconds: int = 10):
        self.log(f"Démarrage {'EXÉCUTION RÉELLE' if self.execute else 'SIMULATION'} : {describe(self.cand)} | "
                 f"{self.cfg.label()} | risque {self.risk_pct}%/trade")
        while True:
            try:
                self.step()
            except Exception as exc:  # on ne crashe pas pour une erreur réseau ponctuelle
                self.log(f"erreur : {exc}")
            time.sleep(poll_seconds)


def load_candidate(path: str, index: int = 0) -> dict:
    with open(path, encoding="utf-8") as fh:
        items = json.load(fh)
    if not items:
        raise RuntimeError("Aucune stratégie approuvée dans ce fichier.")
    return items[index]
