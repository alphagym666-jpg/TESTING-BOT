"""Pont Python -> bot MT5 (mql5/LaboBot.mq5).

Le cerveau reste en Python : c'est EXACTEMENT le même code que celui qui a été testé (recherche, Directeur, paper
trading) qui décide des entrées, des stops déplacés et des sorties. À chaque décision de la stratégie combinée,
une commande est écrite dans le dossier commun de MT5 (Common\\Files\\labo_signaux.csv). Le bot LaboBot.mq5,
posé sur un graphique, lit ces commandes et passe les ordres sur le compte, avec ses propres garde-fous
(perte max du jour et totale, risque max par trade, refus d'un compte réel sauf autorisation).

Format d'une ligne (séparateur « ; ») :
    seq;utc;action;cle;symbole;sens;distance_sl;rr;risque_pct;prix;commission_lot
    action = OPEN | MOVE | BE | CLOSE
"""
from __future__ import annotations

import os
import time
import zlib
from pathlib import Path

SIGNAL_FILE = "labo_signaux.csv"


def common_files_dir(mt5=None) -> Path:
    """Dossier « Common\\Files » de MT5 (partagé par tous les terminaux et lisible par les bots)."""
    try:
        p = getattr(mt5.terminal_info(), "commondata_path", None) if mt5 is not None else None
        if p:
            return Path(p) / "Files"
    except Exception:
        pass
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"
    return Path("results") / "bot"


def slot_key(slot_id: str) -> str:
    """Identifiant court et stable d'un composant (sert de commentaire d'ordre dans MT5, 31 caractères max)."""
    return f"{zlib.crc32(slot_id.encode()) % 100_000_000:08d}"


class SignalBridge:
    def __init__(self, path: str | Path, keep: int = 300):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.keep = keep
        self.lines: list[str] = []
        self.seq = int(time.time() * 1000)
        if self.path.exists():  # reprise : on garde les dernières commandes et on continue la numérotation
            try:
                self.lines = [x for x in self.path.read_text(encoding="ascii", errors="ignore").splitlines()
                              if x and x[0].isdigit()][-keep:]
                if self.lines:
                    self.seq = max(self.seq, int(self.lines[-1].split(";")[0]))
            except (OSError, ValueError):
                self.lines = []

    def _emit(self, action: str, key: str, symbol: str = "", side: int = 0, dist: float = 0.0, rr=None,
              risk_pct: float = 0.0, price: float = 0.0, commission: float = 0.0):
        self.seq = max(self.seq + 1, int(time.time() * 1000))
        self.lines.append(f"{self.seq};{int(time.time())};{action};{key};{symbol};{side};{dist:.10g};"
                          f"{0 if rr is None else rr:g};{risk_pct:g};{price:.10g};{commission:g}")
        self.lines = self.lines[-self.keep:]
        body = "seq;utc;action;cle;symbole;sens;distance_sl;rr;risque_pct;prix;commission_lot\n" + \
               "\n".join(self.lines) + "\n"
        tmp = self.path.with_suffix(".tmp")
        for _ in range(5):  # le bot peut lire le fichier au même moment : on réessaie
            try:
                tmp.write_text(body, encoding="ascii")
                os.replace(tmp, self.path)
                return
            except OSError:
                time.sleep(0.2)
        print(f"[bot] impossible d'écrire {self.path}")

    def open(self, slot_id, symbol, side, dist, rr, risk_pct, commission=0.0):
        self._emit("OPEN", slot_key(slot_id), symbol, side, dist, rr, risk_pct, 0.0, commission)

    def move_sl(self, slot_id, symbol, price):
        self._emit("MOVE", slot_key(slot_id), symbol, price=price)

    def breakeven(self, slot_id, symbol):
        self._emit("BE", slot_key(slot_id), symbol)

    def close(self, slot_id, symbol):
        self._emit("CLOSE", slot_key(slot_id), symbol)


def generate_bot(results_dir: str | Path, capital: float = 100_000.0, ftmo=None, template: str | Path | None = None,
                 out_dir: str | Path | None = None, horaire: str | None = None) -> Path:
    """Prépare results/bot/ : LaboBot.mq5 réglé pour VOTRE stratégie combinée + LISEZMOI_BOT.txt."""
    import json
    results_dir = Path(results_dir)
    comb_path = results_dir / (f"strategie_combinee_{horaire}.json" if horaire else "strategie_combinee.json")
    if not comb_path.exists():
        raise SystemExit("Pas encore de stratégie combinée : lancez d'abord le Directeur (option D).")
    comb = json.loads(comb_path.read_text(encoding="utf-8"))
    rules = comb.get("regles", {})
    target = getattr(ftmo, "target1", 10.0)
    daily = getattr(ftmo, "max_daily", 3.0)
    total = getattr(ftmo, "max_total", 10.0)
    risk_max = max([float(c["risk_pct"]) for c in comb.get("composants", [])] or [1.0])
    template = Path(template or Path(__file__).resolve().parent.parent / "mql5" / "LaboBot.mq5")
    src = template.read_text(encoding="utf-8-sig")
    values = {"InpCapital": f"{capital:g}", "InpRisqueMax": f"{risk_max:g}",
              "InpPerteJourMax": f"{max(0.5, daily - 0.2):g}", "InpPerteTotaleMax": f"{max(1.0, total - 0.5):g}",
              "InpObjectif": f"{target:g}"}
    import re
    for name, v in values.items():
        src = re.sub(rf"(input\s+\w+\s+{name}\s*=\s*)[^;]+;", rf"\g<1>{v};", src)
    lines = ["//+------------------------------------------------------------------+",
             f"//| Stratégie combinée du Directeur ({comb.get('cree_le', '')})",
             f"//| Règles : perte possible max {rules.get('day_budget')} %/jour, perte totale max "
             f"{rules.get('total_budget')} %, positions max {rules.get('max_open') or 'illimité'}",
             f"//| Horaire des entrées : {(comb.get('horaire') or {}).get('nom', '24h/24')} (heure locale)",
             "//| Composants :"]
    for i, c in enumerate(comb.get("composants", []), 1):
        lines.append(f"//|  {i}. {c['symbole']} {c['timeframe']} | {c['strategie'][:90]} | {c['risque_config']} | "
                     f"{c['risk_pct']:g} %/trade")
    lines.append("//+------------------------------------------------------------------+")
    src = "\n".join(lines) + "\n" + src
    out = Path(out_dir or results_dir / "bot")
    out.mkdir(parents=True, exist_ok=True)
    (out / "LaboBot.mq5").write_text(src, encoding="utf-8-sig")  # BOM : MetaEditor lit bien les accents
    res = comb.get("resultat", {})
    readme = f"""BOT MT5 DE LA STRATÉGIE COMBINÉE
================================

Ce que fait le bot
------------------
La plateforme Python (paper trading de la stratégie combinée, option C du menu) décide des trades avec EXACTEMENT
le code qui a été testé. Elle écrit chaque décision dans le dossier commun de MT5 (labo_signaux.csv).
Le bot LaboBot, posé sur un graphique, lit ces décisions chaque seconde et passe les VRAIS ordres sur le compte.
=> Le PC (ou un VPS Windows) doit rester allumé avec MT5 ET l'option C du menu en marche.

Horaire des entrées : {(comb.get('horaire') or {}).get('nom', '24h/24')} (heure locale ; c'est la plateforme Python
qui applique l'horaire, le bot garde ses SL/TP en dehors).
Stratégie combinée : {len(comb.get('composants', []))} composants, réussite estimée {res.get('ftmo_pass')} %,
~{res.get('ftmo_jours_p1')} jours de bourse pour l'objectif (estimation sur le passé, rien n'est garanti).

Installation (une seule fois)
-----------------------------
1. Dans MT5 : Fichier > Ouvrir le dossier des données > MQL5 > Experts
2. Copiez-y le fichier LaboBot.mq5 de ce dossier ({out.resolve()})
3. Dans MT5 : Navigateur (Ctrl+N) > clic droit sur « Experts » > Actualiser
4. Double-cliquez LaboBot : MetaEditor s'ouvre, appuyez sur F7 (compiler). « 0 errors » = OK. Fermez MetaEditor.
5. Activez le bouton « Algo Trading » en haut de MT5 (il doit être vert).

Démarrer
--------
1. Menu lancer.bat : option C (paper trading de la stratégie combinée). Laissez la fenêtre ouverte.
2. Dans MT5, ouvrez UN graphique (n'importe lequel, par ex. EURUSD M1) et glissez-y LaboBot.
   Onglet « Dépendances » : cochez « Autoriser le trading algorithmique ». Onglet « Paramètres » : vérifiez :
     InpCapital        = {values['InpCapital']}   (taille du compte / challenge)
     InpRisqueMax      = {values['InpRisqueMax']} % par trade maximum
     InpPerteJourMax   = {values['InpPerteJourMax']} % : au-delà, tout est fermé jusqu'au lendemain
     InpPerteTotaleMax = {values['InpPerteTotaleMax']} % : au-delà, tout est fermé et le bot s'arrête
     InpObjectif       = {values['InpObjectif']} % : objectif atteint = plus de nouveau trade
3. Le coin du graphique affiche l'état du bot, la perte du jour et le dernier signal reçu.
   Onglet « Experts » (en bas) : chaque ordre passé ou refusé est expliqué.

Sécurité
--------
- Le bot REFUSE un compte réel, sauf si vous mettez InpAutoriserReel = true. Les comptes démo et challenge FTMO
  fonctionnent.
- Un seul graphique avec LaboBot par compte (sinon les ordres seraient passés deux fois).
- Au premier lancement, le bot ignore les anciens signaux ; un signal d'ouverture de plus de 90 s est ignoré.
- Le SL et le TP sont posés chez le courtier dès l'ouverture : si le PC s'éteint, les positions restent protégées.
- Testez-le d'abord plusieurs semaines sur un compte DÉMO.
"""
    (out / "LISEZMOI_BOT.txt").write_text(readme, encoding="utf-8-sig")
    return out
