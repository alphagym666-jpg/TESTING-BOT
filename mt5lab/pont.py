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
                 out_dir: str | Path | None = None, horaire: str | None = None, forcer: bool = False) -> Path:
    """Prépare results/bot/ : LaboBot.mq5 réglé pour VOTRE stratégie combinée + LISEZMOI_BOT.txt."""
    import json
    results_dir = Path(results_dir)
    comb_path = results_dir / (f"strategie_combinee_{horaire}.json" if horaire else "strategie_combinee.json")
    if not comb_path.exists():
        raise SystemExit("Pas encore de stratégie combinée : lancez d'abord le Directeur (option D).")
    comb = json.loads(comb_path.read_text(encoding="utf-8"))
    if comb.get("essai") and not forcer:
        raise SystemExit("La stratégie combinée actuelle est « À L'ESSAI » : faite de stratégies NON validées. "
                         "Suivez-la d'abord en paper trading (option C). Le bot n'est généré que pour une stratégie "
                         "validée (ou avec --forcer, à vos risques, sur un compte démo).")
    return write_bot(comb, Path(out_dir or results_dir / "bot"), capital, ftmo, template=template)


def write_bot(comb: dict, out: Path, capital: float = 100_000.0, ftmo=None, signal_file: str = SIGNAL_FILE,
              magic: int = 260926, launcher: str | None = None, template: str | Path | None = None) -> Path:
    """Écrit LaboBot.mq5 réglé + LISEZMOI_BOT.txt pour une stratégie (combinée ou seule) au format du Directeur."""
    rules = comb.get("regles", {})
    target = getattr(ftmo, "target1", 10.0)
    daily = getattr(ftmo, "max_daily", 3.0)
    if comb.get("profil") == "perso" and rules.get("day_budget"):  # compte perso : tout fermer à -X % du jour
        daily = float(rules["day_budget"]) + 0.2                      # (reprise le lendemain)
    total = getattr(ftmo, "max_total", 10.0)
    risk_max = max([float(c["risk_pct"]) for c in comb.get("composants", [])] or [1.0])
    template = Path(template or Path(__file__).resolve().parent.parent / "mql5" / "LaboBot.mq5")
    src = template.read_text(encoding="utf-8-sig")
    values = {"InpCapital": f"{capital:g}", "InpRisqueMax": f"{risk_max:g}",
              "InpPerteJourMax": f"{max(0.5, daily - 0.2):g}", "InpPerteTotaleMax": f"{max(1.0, total - 0.5):g}",
              "InpObjectif": f"{target:g}", "InpMagic": str(int(magic)), "InpFichier": f'"{signal_file}"',
              "InpMeilleurJour": f"{getattr(ftmo, 'best_day_pct', 50.0):g}",
              "InpComposer": "true" if comb.get("composer") else "false",
              "InpPerteSuiveuse": "true" if getattr(ftmo, "trailing", False) else "false",
              "InpFermerVendredi": "22" if comb.get("fermer_week_end") else "0"}
    import re
    for name, v in values.items():
        src = re.sub(rf"(input\s+\w+\s+{name}\s*=\s*)[^;]+;", rf"\g<1>{v};", src)
    lines = ["//+------------------------------------------------------------------+",
             (f"//| Stratégie combinée du Directeur ({comb.get('cree_le', '')})" if len(comb.get("composants", [])) != 1
              else f"//| Bot d'UNE stratégie (créé le {comb.get('cree_le', '')}) : signaux envoyés par LANCER_BOT.bat"),
             f"//| Règles : perte possible max {rules.get('day_budget')} %/jour, perte totale max "
             f"{rules.get('total_budget')} %, positions max {rules.get('max_open') or 'illimité'}",
             f"//| Horaire des entrées : {(comb.get('horaire') or {}).get('nom', '24h/24')} (heure locale)",
             "//| Composants :"]
    for i, c in enumerate(comb.get("composants", []), 1):
        h = (c.get("horaire") or {}).get("nom") or "24h/24"
        lines.append(f"//|  {i}. {c['symbole']} {c['timeframe']} | {c['strategie'][:90]} | {c['risque_config']} | "
                     f"{c['risk_pct']:g} %/trade | entrées : {h}")
    lines.append("//+------------------------------------------------------------------+")
    src = "\n".join(lines) + "\n" + src
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
Heures de CHAQUE stratégie (entrées seulement dans sa plage, heure du serveur MT5 ; ses positions continuent après) :
{chr(10).join(f"  - {c['symbole']} {c['timeframe']} : {(c.get('horaire') or {}).get('nom') or '24h/24'}" for c in comb.get('composants', []))}
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
1. {launcher or "Menu lancer.bat : option C (paper trading de la stratégie combinée)"}. Laissez la fenêtre ouverte.
2. Dans MT5, ouvrez UN graphique (n'importe lequel, par ex. EURUSD M1) et glissez-y LaboBot.
   Onglet « Dépendances » : cochez « Autoriser le trading algorithmique ». Onglet « Paramètres » : vérifiez :
     InpCapital        = {values['InpCapital']}   (taille du compte / challenge)
     InpRisqueMax      = {values['InpRisqueMax']} % par trade maximum
     InpPerteJourMax   = {values['InpPerteJourMax']} % : au-delà, tout est fermé jusqu'au lendemain
     InpPerteTotaleMax = {values['InpPerteTotaleMax']} % : au-delà, tout est fermé et le bot s'arrête
     InpObjectif       = {values['InpObjectif']} % : objectif atteint = plus de nouveau trade
     InpMeilleurJour   = {values['InpMeilleurJour']} % : une journée ne peut pas faire plus que ça du profit total
                         (l'objectif monte si besoin : une journée à +6 % -> il faut +12 %)
     InpComposer       = {values['InpComposer']} : true = le risque suit le solde (compte perso, intérêts composés)
     InpFermerVendredi = {values['InpFermerVendredi']} : heure du serveur MT5 le vendredi où tout est fermé (0 = jamais ;
                         22 pour un compte FTMO Standard financé : pas de position pendant le week-end)
     InpPerteSuiveuse  = {values['InpPerteSuiveuse']} : true = perte max SUIVEUSE comme le FTMO 1 étape (le plancher
                         monte avec le plus haut solde de fin de journée, jusqu'au capital de départ)
     (InpObjectif = 0 : pas d'objectif, le bot continue à trader — compte perso ou compte financé)
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


def single_strategy(candidate: dict, symbol: str, tf: str, risk_pct: float, name: str | None = None,
                    day_budget: float = 2.5, total_budget: float = 10.0) -> dict:
    """Une stratégie seule, mise au format « stratégie combinée » (un seul composant) pour le paper et le bot."""
    from .backtest import RiskConfig
    from .evaluator import describe
    return {"nom": name or f"{symbol} {tf} | {describe(candidate)[:80]}", "cree_le": time.strftime("%Y-%m-%d %H:%M"),
            "regles": {"day_budget": day_budget, "total_budget": total_budget, "day_stop": None, "max_open": None},
            "resultat": {}, "composants": [{"symbole": symbol, "timeframe": tf, "candidate": candidate,
                                            "strategie": describe(candidate), "risk_pct": float(risk_pct),
                                            "risque_config": RiskConfig(**candidate["risk"]).label()}]}


def generate_strategy_bot(comb: dict, root: str | Path, capital: float = 100_000.0, ftmo=None,
                          risk_pct: float = 1.0) -> Path:
    """Bot pour N'IMPORTE QUELLE stratégie (ou combinée) : dossier results/bots/<nom>/ avec
    strategie.json, LaboBot.mq5 réglé (son propre fichier de signaux et numéro magique, pour pouvoir faire
    tourner plusieurs bots sur le même compte), LISEZMOI_BOT.txt et LANCER_BOT.bat (paper + signaux du bot)."""
    import json
    import re
    key = f"{zlib.crc32(json.dumps(comb.get('composants', []), sort_keys=True, default=str).encode()) % 100_000_000:08d}"
    first = (comb.get("composants") or [{}])[0]
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", f"{first.get('symbole', 'X')}_{first.get('timeframe', '')}_{key}")
    out = Path(root) / "bots" / slug
    out.mkdir(parents=True, exist_ok=True)
    comb_path = out / "strategie.json"
    comb_path.write_text(json.dumps(comb, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    signal_file = f"labo_signaux_{key}.csv"
    port = 8800 + int(key) % 150
    repo = Path(__file__).resolve().parent.parent
    target = getattr(ftmo, "target1", 10.0)
    def rel(p: Path) -> str:  # chemins relatifs au dossier du projet : pas de guillemets (espaces dans le chemin)
        try:
            return str(p.resolve().relative_to(repo)).replace("/", "\\")
        except ValueError:
            return f'"{p.resolve()}"'
    # le .bat se place dans le dossier du bot : on remonte au projet par %~dp0, puis tout est relatif
    up = "\\".join([".."] * len(out.resolve().relative_to(repo).parts)) if out.resolve().is_relative_to(repo) else None
    cd = f'cd /d "%~dp0{up}"' if up else f'cd /d "{repo}"'
    bat = (f'@echo off\r\n{cd}\r\n'
           f'start "Bot {slug}" paper_24h.bat --source combinee --combinee-fichier {rel(comb_path)} '
           f'--signaux {signal_file} --capital {capital:g} --risk {risk_pct:g} '
           + (f'--profil {comb["profil"]} ' if comb.get("profil") else f'--ftmo-target {target:g} ') +
           f'--out {rel(out / "paper")} --port {port}\r\n'
           f'echo Paper trading + signaux du bot lances (plateforme : http://localhost:{port})\r\npause\r\n')
    (out / "LANCER_BOT.bat").write_text(bat, encoding="ascii", errors="replace")
    write_bot(comb, out, capital, ftmo, signal_file, 260000 + int(key) % 9999,
              launcher=f"Double-cliquez LANCER_BOT.bat dans ce dossier (paper trading de CETTE stratégie qui envoie "
                        f"ses signaux au bot ; plateforme http://localhost:{port})")
    return out


def install_in_mt5(mq5: str | Path, mt5, name: str | None = None) -> tuple[bool, str]:
    """Installe le bot DIRECTEMENT dans MT5 : copie dans <données MT5>\\MQL5\\Experts\\LaboBot\\ puis compile avec
    MetaEditor (livré avec MT5). Le bot apparaît ensuite dans le Navigateur : Expert Advisors > LaboBot."""
    import shutil
    import subprocess
    try:
        info = mt5.terminal_info()
        data, prog = Path(info.data_path), Path(info.path)
    except Exception as exc:
        return False, f"MT5 introuvable ({exc}) : copiez LaboBot.mq5 à la main (voir LISEZMOI_BOT.txt)."
    dest_dir = data / "MQL5" / "Experts" / "LaboBot"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / (name or Path(mq5).name)
    shutil.copyfile(mq5, dest)
    editor = next((p for p in (prog / "metaeditor64.exe", prog / "MetaEditor64.exe", prog / "metaeditor.exe")
                   if p.exists()), None)
    where = f"Navigateur de MT5 (Ctrl+N) > Expert Advisors (Experts) > LaboBot > {dest.stem}"
    if editor is None:
        return False, (f"Copié dans {dest}, mais MetaEditor est introuvable : ouvrez ce fichier dans MetaEditor et "
                       f"appuyez sur F7. Ensuite : {where}.")
    log = dest.with_suffix(".log")
    try:
        subprocess.run([str(editor), f"/compile:{dest}", f"/log:{log}"], timeout=180, check=False)
    except Exception as exc:
        return False, f"Copié dans {dest}, compilation impossible ({exc}) : ouvrez-le dans MetaEditor et F7."
    text = ""
    for enc in ("utf-16", "utf-8", "cp1252"):
        try:
            text = log.read_text(encoding=enc)
            if "result" in text.lower() or "error" in text.lower():
                break
        except (OSError, UnicodeError):
            continue
    ex5 = dest.with_suffix(".ex5")
    errors = [ln.strip() for ln in text.splitlines() if " error " in f" {ln.lower()} " and "0 error" not in ln.lower()]
    if ex5.exists() and not errors:
        return True, (f"Bot installé et compilé dans MT5 : {where}. S'il n'apparaît pas tout de suite : clic droit sur "
                      "« Expert Advisors » > Actualiser.")
    return False, ("Compilation avec erreurs (envoyez ce message) :\n" + "\n".join(errors[:8] or [text[-600:]]))
