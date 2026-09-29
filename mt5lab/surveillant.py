"""Le SURVEILLANT des opérations : il veille pendant que le paper trading et le bot tournent.

- Notifications sur le téléphone (Telegram) : trades de la stratégie combinée / des bots, challenge réussi ou raté,
  MT5 déconnecté ou reconnecté, bot MT5 silencieux, ordre non exécuté, rapport du soir.
  Réglage dans le fichier .env (jamais envoyé ailleurs) :
      TELEGRAM_TOKEN=123456:ABC...      (donné par @BotFather sur Telegram)
      TELEGRAM_CHAT_ID=123456789        (votre numéro de conversation, voir LISEZMOI / README)
  Sans ces deux lignes, rien n'est envoyé : les alertes restent dans le journal de la plateforme.
- Contrôle du bot MT5 : le bot écrit chaque exécution et un signe de vie dans le dossier commun de MT5 ; le
  surveillant compare avec le paper trading (glissement en points, ordres manqués, bot arrêté).
- Rapport du soir : un résumé de la journée (trades, P&L, challenges, meilleures stratégies) dans
  <dossier du paper>/rapports/ et sur Telegram.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path


class Notifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None, min_gap: float = 1.0):
        self.token = token if token is not None else os.environ.get("TELEGRAM_TOKEN", "")
        self.chat_id = chat_id if chat_id is not None else os.environ.get("TELEGRAM_CHAT_ID", "")
        self.min_gap = min_gap
        self.sent: deque = deque(maxlen=200)   # historique (aussi pour les tests)
        self._last = 0.0
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str):
        """Envoie en arrière-plan (ne bloque jamais le paper trading, n'échoue jamais)."""
        self.sent.append(text)
        if not self.active:
            return
        threading.Thread(target=self._post, args=(text,), daemon=True).start()

    def _post(self, text: str):
        with self._lock:
            wait = self.min_gap - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
        try:
            data = urllib.parse.urlencode({"chat_id": self.chat_id, "text": text[:4000]}).encode()
            urllib.request.urlopen(f"https://api.telegram.org/bot{self.token}/sendMessage", data=data, timeout=10)
        except Exception as exc:
            print(f"[surveillant] notification Telegram impossible : {exc}")


class BotWatcher:
    """Lit le journal d'exécution du bot MT5 (exec_<fichier des signaux>) et le compare au paper trading."""

    def __init__(self, path: Path, alert, silent_after: float = 180.0, missed_after: float = 120.0):
        self.path = Path(path)
        self.alert = alert
        self.silent_after, self.missed_after = silent_after, missed_after
        self.pos = 0
        self.last_seen: float | None = None
        self.pending: dict[str, tuple[float, str, float]] = {}   # clé -> (heure du signal, stratégie, prix paper)
        self.slippages: deque = deque(maxlen=200)
        self.missed = 0
        self.executed = 0
        self._silent_alerted = False
        if self.path.exists():  # on ne relit pas l'historique au démarrage
            self.pos = self.path.stat().st_size

    def signal_open(self, key: str, label: str, paper_price: float):
        self.pending[key] = (time.time(), label, paper_price)

    def poll(self, point_of=None):
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="ascii", errors="ignore") as fh:
                    fh.seek(self.pos)
                    lines = fh.read().splitlines()
                    self.pos = fh.tell()
            except OSError:
                lines = []
            for ln in lines:
                f = ln.split(";")
                if len(f) < 3:
                    continue
                self.last_seen = time.time()
                self._silent_alerted = False
                if f[1] == "OPEN" and len(f) >= 7:
                    key, symbol, price, ok = f[2], f[3], float(f[4] or 0), f[6] == "1"
                    p = self.pending.pop(key, None)
                    if ok:
                        self.executed += 1
                        if p and price > 0:
                            pt = (point_of(symbol) if point_of else None) or 1.0
                            self.slippages.append(abs(price - p[2]) / pt)
                    else:
                        self.missed += 1
                        self.alert(f"Le bot MT5 a REFUSÉ un ordre ({symbol}) : {f[7] if len(f) > 7 else ''}")
        now = time.time()
        for key, (t0, label, _px) in list(self.pending.items()):
            if now - t0 > self.missed_after:
                self.pending.pop(key)
                self.missed += 1
                self.alert(f"Ordre NON exécuté par le bot MT5 : {label}. Vérifiez que MT5 est ouvert, que le bot est "
                           "sur un graphique et que le bouton Algo Trading est vert.")
        if self.last_seen and now - self.last_seen > self.silent_after and not self._silent_alerted:
            self._silent_alerted = True
            self.alert("Le bot MT5 ne donne plus signe de vie depuis 3 minutes (MT5 fermé ? bot retiré du graphique ?).")

    def status(self) -> dict:
        sl = list(self.slippages)
        return {"vivant": bool(self.last_seen and time.time() - self.last_seen < self.silent_after),
                "dernier_signe": datetime.fromtimestamp(self.last_seen).strftime("%H:%M:%S") if self.last_seen else None,
                "executes": self.executed, "manques": self.missed,
                "glissement_moyen_pts": round(sum(sl) / len(sl), 1) if sl else None}


def daily_report(engine, day: str) -> str:
    """Résumé d'une journée de paper trading (texte simple, aussi envoyé sur Telegram)."""
    rows = [r for r in engine.recent if str(r.get("fermeture", ""))[:10] == day]
    pnl = sum(float(r.get("pnl") or 0) for r in rows)
    wins = sum(1 for r in rows if float(r.get("r") or 0) > 0)
    by: dict = {}
    for r in rows:
        k = f"{r.get('symbole')} {r.get('timeframe')} | {str(r.get('strategie'))[:60]} | {r.get('risque')}"
        by[k] = by.get(k, 0.0) + float(r.get("r") or 0)
    best = sorted(by.items(), key=lambda x: -x[1])[:5]
    lines = [f"RAPPORT DU {day}", f"{len(rows)} trades clôturés, {wins} gagnants, P&L {pnl:+,.2f} $".replace(",", " ")]
    for g in engine.groups.values():
        lines.append(f"Stratégie combinée « {g.name} » : solde {g.balance:,.2f} $ ({(g.balance - g.capital) / g.capital * 100:+.2f} %), "
                     f"challenge {g.ftmo_status}".replace(",", " "))
    done = [s for s in engine.slots.values() if s.ftmo_when[:10] == day]
    if done:
        ok = sum(1 for s in done if s.ftmo_status == "RÉUSSI")
        lines.append(f"Challenges terminés aujourd'hui : {ok} réussis, {len(done) - ok} ratés")
    if best:
        lines.append("Meilleures stratégies du jour :")
        lines += [f"  {v:+.2f}R  {k}" for k, v in best]
    if getattr(engine, "bot_watch", None):
        st = engine.bot_watch.status()
        lines.append(f"Bot MT5 : {'actif' if st['vivant'] else 'SILENCIEUX'} ; ordres exécutés {st['executes']}, "
                     f"manqués {st['manques']}, glissement moyen {st['glissement_moyen_pts'] or 0} points")
    return "\n".join(lines)


def write_daily_report(engine, day: str) -> Path:
    text = daily_report(engine, day)
    d = engine.out / "rapports"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"rapport_{day}.txt"
    path.write_text(text, encoding="utf-8")
    return path


def exec_log_name(signal_file: str) -> str:
    return "exec_" + signal_file


def telegram_help() -> str:
    return json.dumps({"TELEGRAM_TOKEN": "donné par @BotFather", "TELEGRAM_CHAT_ID": "voir README"})
