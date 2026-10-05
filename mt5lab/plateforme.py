"""Plateforme web locale : tous les trades fictifs en direct (http://localhost:8765).

Le serveur n'écoute que sur 127.0.0.1 (accessible uniquement depuis votre PC). La page interroge /api/etat
toutes les 3 secondes : positions ouvertes (entrée, SL, TP, prix, latent), historique complet des trades,
classement des stratégies, comparaison des R:R, suivi des challenges FTMO et journal des événements.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, engine):
        super().__init__(addr, handler)
        self.engine = engine
        self._data = b"{}"
        self._lock = threading.Lock()

    def publish(self, snapshot: dict):
        data = json.dumps(snapshot, default=str).encode("utf-8")
        with self._lock:
            self._data = data

    def data(self) -> bytes:
        with self._lock:
            return self._data


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # pas de bruit dans la console
        pass

    def _send(self, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/etat":
            self._send(self.server.data(), "application/json; charset=utf-8")
        elif path == "/trades.csv":
            f = self.server.engine.out / "trades.csv"
            body = f.read_bytes() if f.exists() else b""
            self._send(body, "text/csv; charset=utf-8", {"Content-Disposition": "attachment; filename=trades.csv"})
        elif path == "/api/analyse":
            try:
                body = json.dumps(analyse_live(self.server.engine), ensure_ascii=False, default=str)
            except Exception as exc:
                body = json.dumps({"message": f"Analyse impossible : {exc}", "classement": []}, ensure_ascii=False)
            self._send(body.encode("utf-8"), "application/json; charset=utf-8")
        elif path == "/api/marches":
            try:
                body = json.dumps(best_per_market(self.server.engine), ensure_ascii=False, default=str)
            except Exception as exc:
                body = json.dumps({"message": f"Classement impossible : {exc}", "marches": []}, ensure_ascii=False)
            self._send(body.encode("utf-8"), "application/json; charset=utf-8")
        elif path == "/api/top10":  # bouton « Compiler toutes les stratégies » : TOP 10 des combinaisons du direct
            try:
                body = json.dumps(top10_live(self.server.engine, start="lancer=1" in self.path),
                                  ensure_ascii=False, default=str)
            except Exception as exc:
                body = json.dumps({"etat": "erreur", "message": f"Compilation impossible : {exc}"}, ensure_ascii=False)
            self._send(body.encode("utf-8"), "application/json; charset=utf-8")
        elif path == "/api/bots":  # page « Mes bots »
            try:
                body = json.dumps(bots_live(self.server.engine), ensure_ascii=False, default=str)
            except Exception as exc:
                body = json.dumps({"bots": [], "message": f"Liste impossible : {exc}"}, ensure_ascii=False)
            self._send(body.encode("utf-8"), "application/json; charset=utf-8")
        elif path == "/api/ouvrir":  # « Ouvrir le dossier » d'un bot (seulement les dossiers de bots)
            try:
                body = json.dumps(open_bot_folder(self.server.engine, self.path), ensure_ascii=False)
            except Exception as exc:
                body = json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False)
            self._send(body.encode("utf-8"), "application/json; charset=utf-8")
        elif path == "/api/auto":  # calcul automatique de la nuit : état
            self._send(json.dumps(getattr(self.server.engine, "_auto", {}) or {}, ensure_ascii=False,
                                  default=str).encode("utf-8"), "application/json; charset=utf-8")
        elif path == "/api/analyse_trades":  # onglet « Analyse des trades » (excursions, contexte, fantômes)
            try:
                body = json.dumps(analyse_trades_live(self.server.engine, self.path), ensure_ascii=False, default=str)
            except Exception as exc:
                body = json.dumps({"message": f"Analyse impossible : {exc}"}, ensure_ascii=False)
            self._send(body.encode("utf-8"), "application/json; charset=utf-8")
        elif path == "/api/top2ans":  # TOP 10 backtest 2 ans des stratégies qui tradent en direct
            try:
                from urllib.parse import parse_qs, urlparse
                deb = (parse_qs(urlparse(self.path).query).get("debut") or [""])[0]
                body = json.dumps(top2ans_live(self.server.engine, start="lancer=1" in self.path, debut=deb or None),
                                  ensure_ascii=False, default=str)
            except Exception as exc:
                body = json.dumps({"etat": "erreur", "message": f"Calcul impossible : {exc}"}, ensure_ascii=False)
            self._send(body.encode("utf-8"), "application/json; charset=utf-8")
        elif path == "/api/backtest":  # bouton « Backtest » d'une combinaison du TOP 10 du direct
            try:
                body = json.dumps(backtest_live(self.server.engine, self.path), ensure_ascii=False, default=str)
            except Exception as exc:
                body = json.dumps({"etat": "erreur", "message": f"Backtest impossible : {exc}"}, ensure_ascii=False)
            self._send(body.encode("utf-8"), "application/json; charset=utf-8")
        elif path == "/api/botfichier":  # bouton « Bot MT5 » des rapports (comparaison, Directeur, fiches...)
            try:
                body = json.dumps(bot_from_report(self.server.engine, self.path), ensure_ascii=False)
                code = 200
            except PermissionError as exc:
                body, code = json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False), 403
            except Exception as exc:
                body, code = json.dumps({"ok": False, "message": f"Impossible de créer le bot : {exc}"},
                                        ensure_ascii=False), 200
            if code == 403:
                self.send_error(403)
            else:  # le rapport est un fichier (origine « null ») : il faut l'autoriser à lire la réponse
                self._send(body.encode("utf-8"), "application/json; charset=utf-8",
                           {"Access-Control-Allow-Origin": "*"})
        elif path == "/api/bot":
            try:
                body = json.dumps(make_bot(self.server.engine, self.path), ensure_ascii=False)
            except Exception as exc:
                body = json.dumps({"ok": False, "message": f"Impossible de créer le bot : {exc}"}, ensure_ascii=False)
            self._send(body.encode("utf-8"), "application/json; charset=utf-8")
        elif path in ("/", "/index.html"):
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self.send_error(404)


def analyse_live(engine) -> dict:
    """Onglet « Analyse du direct » : meilleurs setups de CE paper trading et meilleure combinaison."""
    from .direct import analyse
    strategies = {s.id: {"symbole": s.symbol, "timeframe": s.timeframe, "candidate": s.candidate}
                  for s in engine.slots.values()}
    import pandas as pd
    trades = pd.DataFrame(engine.recent)
    if len(trades):
        trades["r"] = pd.to_numeric(trades["r"], errors="coerce")
        trades = trades.dropna(subset=["r"])
    res = analyse(engine.out, engine.ftmo, engine.risk_pct, trades=trades, strategies=strategies, n_sim=800)
    engine.last_analysis = res
    return res


def top10_live(engine, start: bool = False) -> dict:
    """Onglet « TOP 10 combinées du direct » : le bouton lance (en arrière-plan) la compilation de TOUTES les
    stratégies de cette plateforme ; la page demande l'avancement toutes les 2 secondes."""
    import time as _time
    job = getattr(engine, "_top10_job", None)
    if start and not (job and job["etat"] == "en cours"):
        job = {"etat": "en cours", "fait": 0, "total": 0, "debut": _time.strftime("%H:%M:%S"), "resultat": None,
               "message": "Compilation de toutes les stratégies du direct…"}
        engine._top10_job = job
        threading.Thread(target=_run_top10, args=(engine, job), daemon=True).start()
    if job is None:  # pas encore lancé depuis le démarrage : le dernier TOP 10 enregistré
        f = engine.out / "top10_direct.json"
        try:
            res = json.loads(f.read_text(encoding="utf-8"))
            return {"etat": "fini", "fait": 1, "total": 1, "resultat": res,
                    "message": f"Dernier TOP 10 enregistré ({res.get('calcule_le', '')}). Recliquez pour le refaire."}
        except (OSError, ValueError):
            return {"etat": "jamais", "message": "Cliquez sur le bouton pour compiler toutes les stratégies du direct."}
    return job


def _run_top10(engine, job):
    import time as _time

    import pandas as pd

    from .direct import top_combinations
    from .ftmo import FtmoRules
    try:
        live = {k for k, s in engine.slots.items() if not getattr(s, "paused", False)}  # pas les stratégies en pause
        t = _live_trades(engine, live)
        strategies = {k: {"symbole": s.symbol, "timeframe": s.timeframe, "candidate": s.candidate}
                      for k, s in engine.slots.items() if k in live}
        # toujours les règles du CHALLENGE (même sur la plateforme d'un compte perso) et 1 % max par trade
        rules = FtmoRules() if getattr(engine, "profile", None) else engine.ftmo
        risk = min(float(engine.risk_pct or 1.0), 1.0)

        t, strategies = _live_hour_variants(t, strategies)

        def progress(done, total):
            job.update(fait=done, total=total)
        res = top_combinations(t, strategies, rules, risk, progress=progress)
        res["calcule_le"] = _time.strftime("%Y-%m-%d %H:%M")
        res["regles"] = rules.label()
        try:
            (engine.out / "top10_direct.json").write_text(json.dumps(res, ensure_ascii=False, default=str, indent=1),
                                                          encoding="utf-8")
        except OSError:
            pass
        job.update(etat="fini", resultat=res, message=res["message"])
    except Exception as exc:
        job.update(etat="erreur", message=f"Compilation impossible : {exc}")


def analyse_trades_live(engine, url: str) -> dict:
    """Ce que les trades du paper trading apprennent : stops et objectifs (excursions), quand ça marche (heure,
    jour, type de marché, annonces) et trades refusés par la stratégie combinée (fantômes)."""
    import time as _time
    from urllib.parse import parse_qs, urlparse

    import pandas as pd

    from .analyse_trades import analyse
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    gpath = engine.out / "fantomes.csv"
    gsize = gpath.stat().st_size if gpath.exists() else 0
    key = (engine.total_trades if hasattr(engine, "total_trades") else 0, gsize, q.get("id"), q.get("sym"), q.get("tf"))
    cache = getattr(engine, "_at_cache", None)
    if cache and cache[0] == key and _time.time() - cache[1] < 30:
        return cache[2]
    t = _live_trades(engine)
    ghosts = pd.read_csv(gpath) if gsize else None
    rr_of = {k: sl.cfg.rr for k, sl in engine.slots.items()} if engine.slots else {}
    res = analyse(t, ghosts, rr_of, q.get("id") or None, q.get("sym") or None, q.get("tf") or None)
    if len(t):
        n = t.groupby("strategie_id").agg(n=("r", "size"), sym=("symbole", "first"), tf=("timeframe", "first"),
                                          strat=("strategie", "first")).sort_values("n", ascending=False).head(400)
        res["liste"] = [{"id": k, "label": f"{r.sym} {r.tf} · {str(r.strat)[:70]} ({r.n} trades)"}
                        for k, r in n.iterrows()]
    engine._at_cache = (key, _time.time(), res)
    return res


def _bot_dirs(engine) -> list:
    from .boutons import REPO
    out = []
    for d in (engine.out.parent / "bots", REPO / "results" / "bots"):
        try:
            d = d.resolve()
        except OSError:
            continue
        if d.is_dir() and d not in out:
            out.append(d)
    return out


def bots_live(engine) -> dict:
    """PAGE « MES BOTS » : chaque bot créé (dossier results/bots/...) avec ses stratégies et leurs heures, son profil,
    et ce que fait son paper trading (LANCER_BOT.bat) : actif ou arrêté, solde, trades, statut du challenge,
    et le VRAI compte MT5 quand le bot y est branché."""
    import time as _time
    bots = []
    for root in _bot_dirs(engine):
        for d in sorted(root.iterdir()):
            f = d / "strategie.json"
            if not f.is_file():
                continue
            try:
                comb = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            row = {"dossier": str(d), "nom": comb.get("nom", d.name), "cree_le": comb.get("cree_le", ""),
                   "profil": comb.get("profil") or "challenge FTMO",
                   "regles": comb.get("regles") or {},
                   "composants": [{"symbole": c.get("symbole"), "timeframe": c.get("timeframe"),
                                   "strategie": c.get("strategie", ""), "risk_pct": c.get("risk_pct"),
                                   "horaire": (c.get("horaire") or {}).get("nom") or "24h/24"}
                                  for c in comb.get("composants", [])],
                   "etat": "jamais lancé", "paper": None}
            st = d / "paper" / "etat.json"
            if st.is_file():
                age = _time.time() - st.stat().st_mtime
                row["etat"] = "actif" if age < 15 * 60 else "arrêté"
                row["maj"] = _time.strftime("%Y-%m-%d %H:%M", _time.localtime(st.stat().st_mtime))
                try:
                    e = json.loads(st.read_text(encoding="utf-8"))
                    g = next(iter((e.get("groups") or {}).values()), None)
                    if g:
                        cap = float(comb.get("capital") or 100_000)
                        row["paper"] = {"solde": g.get("balance"), "trades": g.get("trades"), "pnl": g.get("pnl"),
                                        "profit_pct": round((float(g.get("balance") or cap) - cap) / cap * 100, 2),
                                        "statut": g.get("ftmo_status"), "jours": len(g.get("trade_days") or [])}
                    ra = e.get("real_acc") or {}
                    if ra.get("day_start") is not None:
                        row["reel"] = {"debut_jour": ra.get("day_start"), "meilleur_jour": ra.get("best_day")}
                except (OSError, ValueError):
                    pass
            try:
                from .pont import bot_diagnostic, common_files_dir
                row["diag"] = bot_diagnostic(d, common_files_dir(getattr(engine, "mt5", None)))
            except Exception:
                row["diag"] = None
            bots.append(row)
    bots.sort(key=lambda b: (b["etat"] != "actif", b.get("cree_le", "")), reverse=False)
    return {"bots": bots, "dossiers": [str(d) for d in _bot_dirs(engine)],
            "message": f"{len(bots)} bot(s) créés, {sum(b['etat'] == 'actif' for b in bots)} actif(s) en ce moment."}


def open_bot_folder(engine, url: str) -> dict:
    from urllib.parse import parse_qs, urlparse
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    target = Path(q.get("dossier", "")).resolve()
    if not any(target == r or r in target.parents for r in _bot_dirs(engine)) or not target.is_dir():
        return {"ok": False, "message": "Dossier refusé (seulement les dossiers de bots)."}
    try:
        import os
        os.startfile(str(target))  # Windows
        return {"ok": True, "message": f"Dossier ouvert : {target}"}
    except Exception:
        return {"ok": False, "message": f"Ouvrez ce dossier à la main : {target}"}


AUTO_HOUR = 2   # calcul automatique chaque nuit (heure locale du PC) : TOP 10 du direct puis backtest 2 ans


def _nightly(engine, hour: int | None = AUTO_HOUR, check_every: int = 60, now=None, wait=None):
    """CALCUL AUTOMATIQUE DE LA NUIT : chaque nuit à `hour` h (heure du PC), le TOP 10 du direct puis le TOP 10
    backtest 2 ans (avec heures et planning) sont recalculés tout seuls ; résumé sur Telegram si configuré.
    Variable d'environnement LABO_AUTO_HEURE (ex. 3) pour changer l'heure, vide ou -1 pour désactiver."""
    import os
    import time as _time
    from datetime import datetime
    env = os.environ.get("LABO_AUTO_HEURE")
    if env is not None:
        hour = int(env) if env.strip().lstrip("-").isdigit() and int(env) >= 0 else None
    engine._auto = {"heure": hour, "dernier": None, "etat": "désactivé" if hour is None else "en attente"}
    if hour is None:
        return
    now = now or datetime.now
    wait = wait or _time.sleep
    while True:
        t = now()
        if t.hour == hour and engine._auto.get("dernier") != t.strftime("%Y-%m-%d"):
            engine._auto.update(dernier=t.strftime("%Y-%m-%d"), etat="calcul du TOP 10 du direct", debut=t.strftime("%H:%M"))
            run_nightly_once(engine, wait)
            engine._auto["etat"] = "fini " + now().strftime("%Y-%m-%d %H:%M")
            if getattr(engine, "_auto_once", False):
                return
        wait(check_every)


def run_nightly_once(engine, wait=None) -> dict:
    """Une passe du calcul de la nuit (aussi utilisée par le bouton « Tout recalculer »)."""
    import time as _time
    wait = wait or _time.sleep
    out = {}
    for name, fn in (("top10", top10_live), ("top2ans", top2ans_live)):
        job = fn(engine, start=True)
        for _ in range(4 * 3600 // 5):
            if job.get("etat") != "en cours":
                break
            wait(5)
        out[name] = job
    try:
        gen = ((out["top2ans"].get("resultat") or {}).get("general") or [])
        n1 = gen[0] if gen else None
        txt = "🌙 Calcul de la nuit terminé."
        if n1:
            comps = ", ".join(f"{c['symbole']} {c['timeframe']}" + (f" {c['horaire']['nom'].split(' ')[0]}"
                              if c.get("horaire") else "") for c in n1["composants"])
            txt += f" N°1 du classement général : {n1['type']} ({comps})."
        notifier = getattr(engine, "notifier", None)
        if notifier is not None:
            notifier.send(txt)
    except Exception:
        pass
    return out


YEARS_BT = 2.0   # backtest des 2 dernières années


def top2ans_live(engine, start: bool = False, debut: str | None = None) -> dict:
    """Onglet « TOP 10 backtest 2 ans » : chaque stratégie qui a tradé en direct (et chaque stratégie combinée
    qui tourne) rejouée sur les 2 dernières années de MT5, TOP 10 seules et combinées, avec le paper trading."""
    import time as _time
    job = getattr(engine, "_top2_job", None)
    if start and not (job and job["etat"] == "en cours"):
        job = {"etat": "en cours", "fait": 0, "total": 0, "debut": _time.strftime("%H:%M:%S"), "resultat": None,
               "message": "Préparation…", "etape": "préparation"}
        engine._top2_job = job
        threading.Thread(target=_run_top2ans, args=(engine, job, debut), daemon=True).start()
    if job is None:
        try:
            res = json.loads((engine.out / "top_backtest_2ans.json").read_text(encoding="utf-8"))
            return {"etat": "fini", "fait": 1, "total": 1, "resultat": res,
                    "message": f"Dernier calcul enregistré ({res.get('calcule_le', '')}). Recliquez pour le refaire."}
        except (OSError, ValueError):
            return {"etat": "jamais", "message": "Cliquez sur le bouton pour backtester les stratégies du direct."}
    return job


def _run_top2ans(engine, job, debut: str | None = None):
    import time as _time

    from .ftmo import FtmoRules
    from .top_backtest import top_backtest
    try:
        t = _live_trades(engine)
        counts = t["strategie_id"].value_counts() if len(t) else {}
        traded = set(counts.keys()) if len(t) else set()
        extras = []
        for g in getattr(engine, "groups", {}).values():   # stratégies combinées qui tournent en paper
            members = [k for k, sl in engine.slots.items() if getattr(sl, "group", "") == g.name]
            if members and traded & set(members):
                extras.append({"nom": f"Stratégie combinée en paper : {g.name}", "keys": members,
                               "weights": {k: engine.slots[k].risk_pct or engine.risk_pct for k in members}})
        top = ((getattr(engine, "_top10_job", None) or {}).get("resultat") or {}).get("top") or []
        for e in top:   # le TOP 10 du direct : comment il s'en sort sur 2 ans
            keys = [c["strategie_id"] for c in e["composants"] if c["strategie_id"] in engine.slots]
            if keys:
                extras.append({"nom": f"N°{e['rang']} du TOP 10 du direct", "keys": keys,
                               "weights": {c["strategie_id"]: c["risk_pct"] for c in e["composants"]}})
        ids = traded | {k for x in extras for k in x["keys"]}
        if not ids:
            job.update(etat="erreur", message="Aucune stratégie n'a encore tradé en direct : laissez tourner la plateforme.")
            return
        text = t.drop_duplicates("strategie_id").set_index("strategie_id") if len(t) else None
        strategies = {}
        for k in ids:
            sl = engine.slots[k]
            row = text.loc[k] if text is not None and k in text.index else None
            txt = {c: (row.get(c) if row is not None and isinstance(row.get(c), str) else None)
                   for c in ("strategie", "risque")}
            strategies[k] = {"symbole": sl.symbol, "timeframe": sl.timeframe, "candidate": sl.candidate,
                             "strategie": txt["strategie"], "risque": txt["risque"],
                             "en_pause": bool(getattr(sl, "paused", False)), "trades_direct": int(counts.get(k, 0))}
        rules = FtmoRules() if getattr(engine, "profile", None) else engine.ftmo
        risk = min(float(engine.risk_pct or 1.0), 1.0)

        def progress(phase, done, total):
            job.update(etape=phase, fait=done, total=total, message=phase)
        import pandas as pd
        start = None
        if debut:   # début choisi (ex. 2024-01-01) au lieu des 2 dernières années
            try:
                start = pd.Timestamp(debut)
            except (ValueError, TypeError):
                start = None
        years = YEARS_BT if start is None else max(0.5, (pd.Timestamp.now() - start).days / 365.25)
        get = _platform_data(engine, years + 0.25)   # 3 mois de plus : indicateurs prêts au début
        res = top_backtest(strategies, get, rules, risk, years, live=t, extras=extras, progress=progress,
                           log=print, start=start)
        res["debut_choisi"] = debut or "" 
        res["calcule_le"] = _time.strftime("%Y-%m-%d %H:%M")
        try:
            (engine.out / "top_backtest_2ans.json").write_text(json.dumps(res, ensure_ascii=False, default=str),
                                                               encoding="utf-8")
        except OSError:
            pass
        job.update(etat="fini" if res.get("ok") else "erreur", resultat=res, message=res["message"])
    except Exception as exc:
        job.update(etat="erreur", message=f"Calcul impossible : {exc}")


def _live_hour_variants(t, strategies: dict):
    """MEILLEURES HEURES d'après le PAPER : une stratégie avec au moins 40 trades en direct et une plage horaire
    confirmée (choisie sur ses 60 % premiers trades, contrôlée sur les suivants) devient aussi une variante
    « id@début-fin » qui ne garde que ses trades dans la plage : le Chef des combinaisons peut la mélanger."""
    import pandas as pd

    from .horaires import MIN_TRADES, best_window, horaire, hours_of, in_window
    if not len(t) or "ouverture" not in t.columns:
        return t, strategies
    extra, strategies = [], dict(strategies)
    counts = t["strategie_id"].value_counts()
    for k in counts[counts >= MIN_TRADES].index:
        g = t[t["strategie_id"] == k]
        try:
            bw = best_window(g["ouverture"], pd.to_numeric(g["r"], errors="coerce").fillna(0.0))
        except Exception:
            continue
        if not bw or not bw["ok"] or k not in strategies:
            continue
        a, b = bw["debut"], bw["fin"]
        v = f"{k}@{a:g}-{b:g}"
        extra.append(g[in_window(hours_of(g["ouverture"]), a, b)].assign(strategie_id=v))
        strategies[v] = {**strategies[k], "horaire": horaire(a, b), "base_id": k}
    return (pd.concat([t, *extra], ignore_index=True) if extra else t), strategies


def _live_trades(engine, ids=None):
    """Tous les trades du paper trading (trades.csv) des stratégies encore suivies (ou de `ids`)."""
    import pandas as pd
    path = engine.out / "trades.csv"
    t = pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame(engine.recent)
    if len(t) and "strategie_id" in t.columns:
        t = t[t["strategie_id"].isin(ids if ids is not None else engine.slots.keys())].copy()
        t["r"] = pd.to_numeric(t["r"], errors="coerce")
        t = t.dropna(subset=["r"])
    return t


def _top_comb(engine, rank, source: str = "direct") -> dict | None:
    """Une combinaison d'un TOP 10 (« direct » : TOP 10 du direct ; « bt2 » : TOP 10 backtest 2 ans)."""
    if source in ("bt2", "bt2x"):
        top = (top2ans_live(engine).get("resultat") or {}).get("combinees" if source == "bt2" else "combinees_croisees") or []
    else:
        top = (top10_live(engine).get("resultat") or {}).get("top") or []
    found = [e for e in top if str(e.get("rang")) == str(rank)]
    if not found:
        return None
    comb = dict(found[0])
    comb["composants"] = [{**c, "candidate": c.get("candidate") or
                           engine.slots[c.get("base_id") or c["strategie_id"].split("@")[0]].candidate}
                          for c in comb["composants"]]
    return comb


def _platform_data(engine, years: float | None = None):
    """Historique MT5 de chaque marché, préparé comme pour la recherche (coûts réels, nouvelles, inter-marchés,
    COT). Les appels à MT5 se font à tour de rôle avec le paper trading (verrou)."""
    import contextlib

    from .cot import add_cot, uses_cot
    from .data import add_ext
    from .paper import ext_needed
    lock = getattr(engine, "mt5_lock", None) or contextlib.nullcontext()
    conn, raw = engine.c, {}

    def rates(sym, tf):
        if (sym, tf) not in raw:
            with lock:
                raw[(sym, tf)] = conn.rates_years(sym, tf, years)
        return raw[(sym, tf)]

    def get(sym, tf, cand=None):
        df = rates(sym, tf)
        others = {}
        for o in sorted(ext_needed(cand or {})):
            try:
                others[o] = rates(o, tf)["close"]
            except Exception as exc:
                print(f"[backtest] inter-marchés : {o} {tf} indisponible ({exc})")
        if others:
            df = add_ext(df, others)
        if cand and uses_cot(cand):
            df = add_cot(df, sym, engine._cot_table())
        comm = getattr(engine, "_comm", {}).get(sym, getattr(engine, "_comm_default", 0.0))
        with lock:
            df = conn.enrich(df, sym, 0.0, comm, getattr(engine, "news", None), getattr(engine, "news_window", 30))
            return df, conn.typical_cost(df, sym, 0.0, comm)
    get.clear = raw.clear   # libère la mémoire (historiques déjà utilisés)
    return get


def backtest_live(engine, url: str) -> dict:
    """Bouton « Backtest » : rejoue une combinaison du TOP 10 du direct sur tout l'historique MT5 (en
    arrière-plan) ; la page demande l'avancement toutes les 2 secondes."""
    import hashlib
    from urllib.parse import parse_qs, urlparse
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    comb = _top_comb(engine, q.get("top", "0"))
    if comb is None:
        return {"etat": "erreur", "message": "Recompilez d'abord le TOP 10 (bouton de l'onglet)."}
    key = hashlib.md5(json.dumps([[c["strategie_id"], c["risk_pct"]] for c in comb["composants"]],
                                 sort_keys=True).encode()).hexdigest()[:12]
    jobs = engine.__dict__.setdefault("_bt_jobs", {})
    job = jobs.get(key)
    if job is None:  # déjà fait avant un redémarrage ?
        try:
            res = json.loads((engine.out / "backtests" / f"{key}.json").read_text(encoding="utf-8"))
            job = jobs[key] = {"etat": "fini", "fait": 1, "total": 1, "resultat": res, "message": res.get("message", "")}
        except (OSError, ValueError):
            pass
    if q.get("lancer") and not (job and job["etat"] == "en cours"):
        import time as _time
        job = jobs[key] = {"etat": "en cours", "fait": 0, "total": len(comb["composants"]) + 1, "resultat": None,
                           "debut": _time.strftime("%H:%M:%S"), "message": "Téléchargement de l'historique MT5…"}
        threading.Thread(target=_run_backtest, args=(engine, comb, key, job), daemon=True).start()
    if job is None:
        return {"etat": "jamais", "message": "Cliquez sur « Backtest » pour rejouer cette combinaison sur l'historique."}
    return {**job, "rang": int(q.get("top", 0)), "cle": key}


def _run_backtest(engine, comb, key, job):
    import time as _time

    from .backtest_combinee import backtest_combination
    from .ftmo import FtmoRules
    try:
        get = _platform_data(engine)
        cands = {(c["symbole"], c["timeframe"]): c["candidate"] for c in comb["composants"]}

        def progress(done, total, what):
            job.update(fait=done, total=total, message=what)
        rules = FtmoRules() if getattr(engine, "profile", None) else engine.ftmo
        risk = max(float(c["risk_pct"]) for c in comb["composants"])
        res = backtest_combination(comb, lambda s, tf: get(s, tf, cands.get((s, tf))), rules, risk,
                                   progress=progress, log=print)
        res["calcule_le"] = _time.strftime("%Y-%m-%d %H:%M")
        res["nom"] = comb.get("nom", "")
        try:
            (engine.out / "backtests").mkdir(exist_ok=True)
            (engine.out / "backtests" / f"{key}.json").write_text(json.dumps(res, ensure_ascii=False, default=str),
                                                                  encoding="utf-8")
        except OSError:
            pass
        job.update(etat="fini" if res.get("ok") else "erreur", resultat=res, message=res["message"])
    except Exception as exc:
        job.update(etat="erreur", message=f"Backtest impossible : {exc}")


MIN_TRADES_MARKET = 10   # trades en direct avant de conseiller un bot pour un marché


def best_per_market(engine, min_trades: int = MIN_TRADES_MARKET) -> dict:
    """Onglet « Meilleur bot par marché » : pour chaque marché, la stratégie du paper trading qui a le mieux marché
    EN DIRECT (prix réels), à mettre sur ce marché dans MT5. Classement par solidité (t = R moyen / écart x racine
    du nombre de trades), puis R total. Exclus : stratégies en pause, perdantes ou avec trop peu de trades."""
    import time as _time

    import pandas as pd

    from .direct import strategy_table
    cache = getattr(engine, "_marches_cache", None)   # 60 000 comptes : on ne recalcule qu'une fois par minute
    if cache and cache[0] == engine.total_trades and _time.time() - cache[1] < 60:
        return cache[2]
    path = engine.out / "trades.csv"
    t = pd.read_csv(path) if path.exists() and path.stat().st_size else pd.DataFrame(engine.recent)
    slots = engine.slots
    markets: dict[str, dict] = {s.symbol: {"symbole": s.symbol, "bot": None, "reserve": None, "candidats": 0,
                                            "raison": "pas encore de trade en direct"} for s in slots.values()}
    if len(t) and "strategie_id" in t.columns:
        t = t[t["strategie_id"].isin(slots.keys())].copy()
        t["r"] = pd.to_numeric(t["r"], errors="coerce")
        t = t.dropna(subset=["r"])
    most = t.groupby("symbole")["strategie_id"].agg(lambda x: int(x.value_counts().max())) if len(t) else {}
    if len(t):
        n_by = t["strategie_id"].map(t["strategie_id"].value_counts())
        t = t[n_by >= min_trades]   # seulement les stratégies qui ont assez de trades : beaucoup plus rapide
    for sym, mx in dict(most).items():
        m = markets.setdefault(sym, {"symbole": sym, "bot": None, "reserve": None, "candidats": 0})
        if mx < min_trades:
            m["raison"] = f"pas encore {min_trades} trades en direct (max {mx})"
    if len(t):
        tab = strategy_table(t, min_trades)
        for sym, g in tab.groupby("symbole"):
            m = markets.setdefault(sym, {"symbole": sym, "bot": None, "reserve": None, "candidats": 0})
            ok = g[g["fiable"] & (g["r_total"] > 0) & (g["r_moyen"] > 0)
                   & ~g["strategie_id"].map(lambda k: bool(getattr(slots.get(k), "paused", False)))]
            ok = ok.sort_values(["t", "r_total"], ascending=False)
            m["candidats"] = int(len(g))
            rows = []
            for r in ok.head(2).to_dict("records"):
                sl = slots.get(r["strategie_id"])
                r.update(ftmo=getattr(sl, "ftmo_status", ""),
                         attendu_r=getattr(sl, "expected_avg_r", None),
                         meilleur_jour_ok=r["meilleur_jour_part"] is None or r["meilleur_jour_part"] <= engine.ftmo.best_day_pct)
                rows.append(r)
            m["bot"] = rows[0] if rows else None
            m["reserve"] = rows[1] if len(rows) > 1 else None
            if not rows:
                m["raison"] = f"aucune stratégie gagnante (et pas en pause) avec au moins {min_trades} trades en direct"
    out = sorted(markets.values(), key=lambda m: (m["bot"] is None, -(m["bot"] or {}).get("t", 0)))
    n = sum(1 for m in out if m["bot"])
    res = {"marches": out, "min_trades": min_trades,
           "message": f"{n} marché(s) sur {len(out)} ont un bot conseillé d'après le direct" if out else
                      "Aucun marché suivi par ce paper trading."}
    engine._marches_cache = (engine.total_trades, _time.time(), res)
    return res


def make_bot(engine, url: str) -> dict:
    """Bouton « Bot MT5 » : prépare le bot d'une stratégie (?id=...) ou de la stratégie combinée (?groupe=...)."""
    from urllib.parse import parse_qs, urlparse

    from .pont import single_strategy
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    root = engine.out.parent
    if q.get("top") or q.get("bt2") or q.get("bt2x"):  # une combinaison d'un TOP 10 (règles du challenge)
        src = "direct" if q.get("top") else "bt2" if q.get("bt2") else "bt2x"
        comb = _top_comb(engine, q.get("top") or q.get("bt2") or q["bt2x"], src)
        if comb is None:
            return {"ok": False, "message": "Recompilez d'abord le TOP 10 (bouton de l'onglet)."}
        comb.pop("resultat", None)
        for k in ("backtest", "direct", "comptes"):
            comb.pop(k, None)
        from .ftmo import FtmoRules
        ftmo = FtmoRules() if getattr(engine, "profile", None) else engine.ftmo
        capital = 100_000.0 if getattr(engine, "profile", None) else next(iter(engine.slots.values())).capital
        if q.get("profil") in ("perso", "finance"):
            return _profile_bot(engine, comb, root, q["profil"])
        return _build_bot(engine, comb, root, capital, ftmo, max(c["risk_pct"] for c in comb["composants"]))
    if q.get("analyse"):  # la meilleure combinaison trouvée par l'analyse du direct
        comb = dict(getattr(engine, "last_analysis", {}).get("combinaison") or {})
        if not comb:
            return {"ok": False, "message": "Lancez d'abord l'analyse du direct."}
        capital = next(iter(engine.slots.values())).capital
        risk = max(c["risk_pct"] for c in comb["composants"])
        comb["composants"] = [{**c, "symbole": engine.slots[c["strategie_id"]].symbol,
                               "candidate": engine.slots[c["strategie_id"]].candidate} for c in comb["composants"]]
    elif q.get("groupe"):
        g = engine.groups[q["groupe"]]
        members = [x for x in engine.slots.values() if x.group == g.name]
        comb = single_strategy(members[0].candidate, members[0].symbol, members[0].timeframe,
                               members[0].risk_pct or engine.risk_pct, g.name, g.day_budget or 2.5,
                               g.total_budget or 10.0)
        comb["composants"] = [single_strategy(x.candidate, x.symbol, x.timeframe, x.risk_pct or engine.risk_pct)
                              ["composants"][0] for x in members]
        comb["regles"].update({"day_stop": g.day_stop, "max_open": g.max_open, "max_correles": g.max_corr,
                               "pilote": g.pilot, "frein": g.day_lock, "volatilite": g.vol_target})
        comb["fermer_week_end"] = g.weekend_close
        if g.session:
            comb["horaire"] = {"nom": f"{g.session[0]:g}h-{g.session[1]:g}h", "debut": g.session[0],
                               "fin": g.session[1], "decalage_serveur": g.session[2]}
        capital, risk = g.capital, max(x.risk_pct or engine.risk_pct for x in members)
    else:
        sid, _, win = q["id"].partition("@")   # « id@8-12 » : la stratégie dans SES meilleures heures
        s = engine.slots[sid]
        risk = s.risk_pct or engine.risk_pct
        comb = single_strategy(s.candidate, s.symbol, s.timeframe, risk)
        capital = s.capital
        if win:
            from .horaires import horaire
            a, b = (float(x) for x in win.split("-"))
            comb["composants"][0]["horaire"] = horaire(a, b)
            comb["nom"] += f" | heures {a:g}h-{b:g}h"
        if q.get("profil") in ("perso", "finance"):
            return _profile_bot(engine, comb, root, q["profil"])
    prof = getattr(engine, "profile", None)
    if prof:  # compte perso / financé : le bot garde le même profil (pas d'objectif, intérêts composés...)
        comb.update(profil=prof["cle"], composer=bool(prof.get("compound")))
    return _build_bot(engine, comb, root, capital, engine.ftmo, risk)


def bot_from_report(engine, url: str) -> dict:
    """Bouton « Bot MT5 » d'un rapport : la stratégie (seule ou combinée) arrive dans la demande, avec le jeton
    secret du projet (sinon refus : un site web ne peut pas créer de bot à votre place)."""
    import hmac
    import re
    from urllib.parse import parse_qs, urlparse

    from .boutons import REPO, token
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    if not hmac.compare_digest(q.get("t", ""), token()):
        raise PermissionError("jeton invalide")
    comb = json.loads(q.get("c", "{}"))
    comps = comb.get("composants") or []
    if not comps or not all(isinstance(c, dict) and {"symbole", "timeframe", "candidate"} <= c.keys() for c in comps):
        return {"ok": False, "message": "Stratégie incomplète : impossible de créer ce bot."}
    res = q.get("r", "results")
    root = REPO / res if re.fullmatch(r"\w[\w.-]*", res) and (REPO / res).is_dir() else engine.out.parent
    risk = max(float(c.get("risk_pct") or 1.0) for c in comps)
    ftmo, capital = engine.ftmo, float(comb.get("capital") or 100_000)
    if comb.get("profil") in ("perso", "finance"):  # compte perso / financé : ses propres règles
        from .comptes import ftmo_like, profile
        p = profile(comb["profil"], capital=comb.get("capital"))
        ftmo, capital = ftmo_like(p), float(p["capital"])
        comb["composer"] = bool(p["compound"])
    elif getattr(engine, "profile", None) is not None:  # plateforme d'un compte perso : règles FTMO par défaut
        from .ftmo import FtmoRules
        ftmo = FtmoRules()
    out = _build_bot(engine, comb, root, capital, ftmo, risk)
    if comb.get("essai"):
        out["message"] = ("ATTENTION : stratégie À L'ESSAI (non validée) : testez-la en paper trading / compte démo "
                          "avant tout.\n\n" + out["message"])
    return out


def _profile_bot(engine, comb, root, name: str) -> dict:
    """Bot pour le COMPTE PERSO (5 000 $, 2 %/trade, 5 %/jour, intérêts composés) ou le compte financé : les
    risques des composants (calculés pour 1 %) sont mis à l'échelle du profil, avec ses propres limites."""
    from .comptes import ftmo_like, profile
    p = profile(name)
    base = min(float(engine.risk_pct or 1.0), 1.0)   # les TOP 10 sont calculés avec 1 % max par trade
    k = float(p["risk_pct"]) / base if name == "perso" else 1.0
    comb = {**comb, "profil": name, "composer": bool(p["compound"]), "capital": float(p["capital"]),
            "regles": {**(comb.get("regles") or {}), "day_budget": float(p["day_budget"]),
                       "total_budget": float(p["total_budget"])},
            "composants": [{**c, "risk_pct": round(min(float(c["risk_pct"]) * k, float(p["risk_pct"])), 3)}
                           for c in comb["composants"]]}
    comb["nom"] = f"{comb.get('nom', 'Stratégie')} — {p['nom']}"
    return _build_bot(engine, comb, root, float(p["capital"]), ftmo_like(p),
                      max(c["risk_pct"] for c in comb["composants"]))


def _build_bot(engine, comb, root, capital, ftmo, risk) -> dict:
    from .pont import generate_strategy_bot, install_in_mt5
    out = generate_strategy_bot(comb, root, capital, ftmo, risk)
    ok, inst = install_in_mt5(out / "LaboBot.mq5", engine.mt5, f"LaboBot_{out.name}.mq5")
    try:
        import os
        os.startfile(str(out))  # Windows : ouvre le dossier du bot
    except Exception:
        pass
    return {"ok": True, "dossier": str(out.resolve()),
            "message": f"{inst}\n\nDossier du bot : {out.resolve()}\n\n1) Double-cliquez LANCER_BOT.bat dans ce dossier "
                       f"(il suit cette stratégie en paper trading et envoie ses signaux au bot).\n2) Dans MT5, glissez "
                       f"LaboBot_{out.name} sur UN graphique, cochez « Autoriser le trading algorithmique », et activez "
                       "le bouton Algo Trading.\nTout est expliqué dans LISEZMOI_BOT.txt."}


def start_server(engine, port: int = 8765, open_browser: bool = True):
    try:
        srv = _Server(("127.0.0.1", port), _Handler, engine)
    except OSError as exc:
        print(f"[plateforme] port {port} indisponible ({exc}) : la plateforme web est désactivée")
        return None
    srv.publish(engine.snapshot())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=_nightly, args=(engine,), daemon=True).start()   # calcul automatique de la nuit
    if open_browser:
        try:
            webbrowser.open(f"http://localhost:{port}")
        except Exception:
            pass
    return srv


PAGE = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Plateforme paper trading</title>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--pos:#006300;--neg:#d03b3b;--accent:#2a78d6;
--track:#cde2fb;--fill:#2a78d6;--negbar:#e34948;--good:#0ca30c;--warn:#fab219;--crit:#d03b3b}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;
--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--pos:#0ca30c;
--neg:#e66767;--accent:#3987e5;--track:#184f95;--fill:#3987e5;--negbar:#e66767}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--pos:#0ca30c;--neg:#e66767;--accent:#3987e5;--track:#184f95;
--fill:#3987e5;--negbar:#e66767}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--surface);border-bottom:1px solid var(--border);padding:10px 16px}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
h1{font-size:18px;margin:0 12px 0 0}
.mut{color:var(--muted)}.ink2{color:var(--ink2)}
.live{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink2)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--good)}.dot.off{background:var(--crit)}
.px{font-variant-numeric:tabular-nums;font-size:12.5px;padding:3px 8px;border:1px solid var(--border);border-radius:999px;background:var(--page)}
main{max-width:none;margin:auto;padding:14px 16px 40px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-bottom:14px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px 12px}
.tile .v{font-size:22px;font-weight:600;margin-top:2px}
.tabs{display:flex;flex-wrap:wrap;gap:4px;border-bottom:1px solid var(--border);margin:6px 0 10px}
.tabs button{border:0;background:none;color:var(--ink2);font:inherit;padding:8px 12px;border-bottom:2px solid transparent;cursor:pointer}
.tabs button.on{color:var(--ink);border-bottom-color:var(--accent);font-weight:600}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}
select,input{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 8px}
input{min-width:220px}
.scroll{overflow:auto;max-height:68vh;border:1px solid var(--border);border-radius:10px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{padding:6px 8px;border-bottom:1px solid var(--grid);text-align:left;white-space:nowrap}
th{position:sticky;top:0;z-index:2;background:var(--surface);color:var(--ink2);font-weight:600;cursor:pointer;user-select:none}
td.n{text-align:right;font-variant-numeric:tabular-nums}
td.s{max-width:340px;overflow:hidden;text-overflow:ellipsis;cursor:help}
.scroll tr>*:first-child{position:sticky;left:0;z-index:1;background:var(--surface)}
.scroll thead tr>*:first-child{z-index:3}
tr:hover td{background:color-mix(in srgb,var(--accent) 7%,transparent)}
.pos{color:var(--pos);font-weight:600}.neg{color:var(--neg);font-weight:600}
.tag{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11.5px;border:1px solid var(--border)}
.botbtn{font:inherit;font-size:12px;padding:3px 9px;border-radius:8px;border:1px solid var(--border);background:transparent;color:inherit;cursor:pointer;white-space:nowrap}
.botbtn:hover{border-color:currentColor}
.cmpbtn{font:inherit;font-size:14px;font-weight:600;padding:8px 16px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
.cmpbtn:disabled{opacity:.6;cursor:wait}
.btbtn{font:inherit;font-size:12px;padding:3px 9px;border-radius:8px;border:1px solid var(--accent);background:transparent;color:var(--accent);cursor:pointer;white-space:nowrap}
.btbtn:disabled{opacity:.5;cursor:wait}
.botbtn.mini{font-size:11px;padding:0 6px;margin:1px 6px 1px 0;line-height:1.5}
.sec{margin:18px 0 8px;font-size:16px}.livebox{border:1px solid var(--border);border-radius:10px;padding:8px 12px;margin:6px 0 10px;background:var(--page)}
.panel{background:var(--surface);border:1px solid var(--accent);border-radius:12px;padding:12px 14px;margin:0 0 14px;scroll-margin-top:96px}
.eqwrap{position:relative;max-width:980px}.eq{width:100%;height:auto;display:block}
.eq text{fill:var(--muted);font-size:11px}.eq .gl{stroke:var(--grid);stroke-width:1}.eq .zl{stroke:var(--axis);stroke-width:1}
.eq .ln{fill:none;stroke:var(--accent);stroke-width:2;stroke-linejoin:round}.eq .rec{fill:var(--accent);opacity:.06}
.eqtip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:4px 8px;font-size:12px;white-space:nowrap;display:none;box-shadow:0 2px 8px rgba(0,0,0,.12)}
.cmp td,.cmp th{padding:4px 10px}.months td{text-align:right;font-variant-numeric:tabular-nums;padding:4px 6px}
.ok::before{content:"✔ ";color:var(--good)}.ko::before{content:"✖ ";color:var(--crit)}.run::before{content:"● ";color:var(--accent)}
.meter{position:relative;width:120px;height:8px;border-radius:4px;background:var(--track);display:inline-block;vertical-align:middle}
.meter i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:var(--fill)}
.meter.neg i{background:var(--negbar)}
.bars{display:grid;grid-template-columns:110px 1fr 90px;gap:6px 10px;align-items:center;max-width:860px}
.bar{height:14px;position:relative}.bar i{position:absolute;top:0;bottom:0;border-radius:0 4px 4px 0;background:var(--fill)}
.bar i.neg{background:var(--negbar);border-radius:4px 0 0 4px}.bar .zero{position:absolute;top:-3px;bottom:-3px;width:1px;background:var(--axis)}
.empty{padding:28px;text-align:center;color:var(--muted)}
.note{font-size:12.5px;color:var(--ink2);margin:6px 0 10px}
.secs{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 4px}
.secs button{font:inherit;font-weight:600;border:1px solid var(--border);background:var(--surface);color:var(--ink2);padding:8px 14px;border-radius:999px;cursor:pointer}
.secs button.on{background:var(--accent);border-color:var(--accent);color:#fff}
.homegrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:12px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:12px 14px}
.card h3{margin:0 0 8px;font-size:15px}
.card .tile .v{font-size:17px;white-space:nowrap}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid var(--border)}
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:50;display:flex;align-items:center;justify-content:center;padding:16px}
.modal{background:var(--surface);color:var(--ink);border-radius:14px;max-width:720px;width:100%;max-height:85vh;overflow:auto;padding:16px 18px;box-shadow:0 10px 40px rgba(0,0,0,.3)}
.modal pre{white-space:pre-wrap;font:inherit;font-size:13px;line-height:1.5;margin:8px 0}
body.simple .x{display:none}
th[title]{text-decoration:underline dotted;text-underline-offset:3px}
@media (max-width:760px){
 header .row{gap:6px} h1{font-size:16px} #info{display:none}
 .scroll{max-height:none;border:0;background:transparent}
 .scroll table,.scroll thead,.scroll tbody,.scroll tr,.scroll td{display:block;width:100%}
 .scroll thead{display:none}
 .scroll tr{background:var(--surface);border:1px solid var(--border);border-radius:10px;margin:0 0 10px;padding:6px 4px}
 .scroll td{border:0;padding:3px 8px;white-space:normal;max-width:none;text-align:left!important}
 .scroll td[data-l]::before{content:attr(data-l) " : ";color:var(--muted);font-weight:600}
 .scroll tr>*:first-child{position:static}
 .tiles{grid-template-columns:repeat(2,1fr)}
 #tiles{display:none}
}
</style></head>
<body>
<header><div class="row"><h1>Plateforme paper trading</h1><span class="live"><span class="dot" id="dot"></span><span id="maj">connexion…</span></span>
<span class="mut" id="info"></span><button class="botbtn" id="modeBtn" style="margin-left:auto" title="Mode simple : l'essentiel, avec des pastilles. Mode expert : toutes les colonnes techniques."></button></div><div class="row" id="prix" style="margin-top:6px"></div></header>
<main>
<div class="tiles" id="tiles"></div>
<div class="tabs" id="tabs"></div>
<div class="filters">
<select id="fSym"><option value="">Tous les marchés</option></select>
<select id="fTf"><option value="">Tous les timeframes</option></select>
<input id="fTxt" placeholder="Filtrer une stratégie (ex. INVENTION, rsi, 1:3)…">
<a class="tag" href="/trades.csv" style="align-self:center;color:var(--ink2);text-decoration:none">Télécharger tous les trades (Excel)</a>
</div>
<div id="view"></div>
</main>
<script>
const SECTIONS=[["home","🏠 Accueil",[["home","Aujourd'hui"]]],
 ["live","📡 En direct",[["comb","Stratégie combinée"],["pos","Positions ouvertes"],["hist","Historique des trades"],["ftmo","Challenges FTMO"],["log","Journal"]]],
 ["rank","🏆 Classements",[["gen","Général (backtest × paper)"],["bt2","Backtest 2 ans"],["top","TOP 10 du direct"],["strat","Toutes les stratégies"],["mk","Meilleur par marché"],["an","Meilleurs setups"]]],
 ["ana","🔬 Analyse",[["hours","Heures & planning"],["at","Analyse des trades"],["rr","Meilleur R:R"]]],
 ["bots","🤖 Mes bots",[["bots","Mes bots"]]]];
const TABS=SECTIONS.flatMap(x=>x[2]);
const secOf=k=>(SECTIONS.find(x=>x[2].some(t=>t[0]===k))||SECTIONS[0])[0];
let A=null,aTime=0,aBusy=false;  // une seule demande à la fois (sinon elles s'empilent et rien ne finit)
async function loadAnalyse(force){if(aBusy||(!force&&A&&Date.now()-aTime<60000))return;aBusy=true;aTime=Date.now();
 try{A=await (await fetch("/api/analyse",{cache:"no-store"})).json()}catch(e){A={message:"Analyse impossible : "+e,classement:[]}}
 aBusy=false;aTime=Date.now();render()}
function viewAn(){if(!A){loadAnalyse(true);return `<div class="empty">Analyse des trades du direct en cours…</div>`}loadAnalyse(false);
 const c=A.combinaison,r=c?c.resultat:null;
 const comb=c?`<div class="tiles"><div class="tile"><div class="mut">Gain en direct de la combinaison</div><div class="v">${fmt(r.rendement_pct,2,true)} %</div></div>
  <div class="tile"><div class="mut">Pire journée</div><div class="v">${fmt(r.pire_jour,2)} %</div></div>
  <div class="tile"><div class="mut">Challenges enchaînés</div><div class="v">${r.reussis} réussis / ${r.rates} ratés</div></div>
  <div class="tile"><div class="mut">Challenge réussi en</div><div class="v">${isFinite(r.jours_attendus)?fmt(r.jours_attendus,0)+" j":"—"}</div><div class="mut" style="font-size:12px">${isFinite(r.jours_attendus)?"jours attendus":"pas assez de jours pour simuler"}</div></div></div>`+
  table("anc",[["Marché","symbole"],["TF","timeframe"],["Stratégie","strategie"],["Réglage","risque_config"],["Risque/trade","risk_pct",v=>fmt(v,2)+" %",1],
   ["Trades en direct","trades_direct",null,1],["R total en direct","r_total_direct",rr,1]],c.composants)+
  `<p style="margin:10px 0"><button class="botbtn" data-analyse="1">Créer le bot MT5 de cette combinaison</button></p>`:`<p class="note">Pas encore de combinaison gagnante en direct.</p>`;
 return `<p class="note">${esc(A.message)} (${fmt(A.trades,0)} trades, ${fmt(A.strategies,0)} stratégies${A.periode?", "+esc(A.periode):""}) ·
  mise à jour chaque minute · <a href="#" onclick="loadAnalyse(true);return false">actualiser</a></p>
  <h3 style="margin:8px 0">Meilleure combinaison des stratégies qui tournent (1 % max par trade, 2,5 % max par jour)</h3>${comb}
  <h3 style="margin:14px 0 8px">Meilleurs setups en direct</h3>`+
  table("anr",[["Marché","symbole"],["TF","timeframe"],["Bot","strategie_id",botBtn],["Stratégie","strategie"],["Réglage","risque"],["Trades","trades",null,1],["Trades / mois","trades_mois",v=>v==null?"—":"~"+fmt(v,0),1],
   ["Réussite","reussite_pct",v=>fmt(v,0)+" %",1],["R moyen","r_moyen",rr,1],["R total","r_total",rr,1],["t (solidité)","t",v=>fmt(v,2),1],
   ["Jours","jours",null,1],["Meilleur jour (part du profit)","meilleur_jour_part",v=>v==null?"—":fmt(v,0)+" %",1]],filt(A.classement||[],"symbole","timeframe"))}
let T10=null,tBusy=false,tTimer=null;
async function loadTop(start){if(tBusy)return;tBusy=true;
 try{T10=await (await fetch("/api/top10"+(start?"?lancer=1":""),{cache:"no-store"})).json()}catch(e){T10={etat:"erreur",message:"Compilation impossible : "+e}}
 tBusy=false;clearTimeout(tTimer);if(T10.etat==="en cours")tTimer=setTimeout(()=>loadTop(false),2000);render()}
function viewTop(){if(!T10){loadTop(false);return `<div class="empty">Chargement…</div>`}
 const run=T10.etat==="en cours",R=T10.resultat||{},top=R.top||[];
 const pct=T10.total?Math.round(T10.fait/T10.total*100):0;
 const head=`<p style="margin:4px 0 10px"><button class="cmpbtn" ${run?"disabled":""} onclick="loadTop(true)">
  ${run?"Compilation en cours…":"Compiler toutes les stratégies du direct → TOP 10 des combinaisons"}</button>
  ${run?` <span class="meter" style="width:220px"><i style="width:${pct}%"></i></span> ${pct} % <span class="mut">(démarré à ${esc(T10.debut)}, environ 1 à 5 minutes)</span>`:""}</p>
  <p class="note">${esc(T10.message||"")}${R.periode?` · période ${esc(R.periode)} · ${fmt(R.jours,0)} jours de bourse · ${fmt(R.strategies,0)} stratégies dans le direct · ${esc(R.regles||"")}`:""}${R.calcule_le?" · calculé le "+esc(R.calcule_le):""}</p>
  <p class="note">Chaque combinaison tourne sur UN seul compte : 1 % max par trade, perte possible max 2,5 % par jour (si tous les stops sautent).
  Classement : d'abord celles qui ratent au plus 2 % des challenges, puis le moins de jours pour réussir, puis la réussite.
  Les chiffres viennent des trades EN DIRECT (prix jamais vus pendant la recherche).</p>`;
 if(!top.length)return head+(run?"":`<div class="empty">Pas encore de TOP 10.</div>`);
 const val=v=>v==null?"—":v;
 const rows=top.map(c=>{const r=c.resultat||{};
  const comps=c.composants.map(x=>`<div title="${esc(x.strategie)}">${miniBot(x.strategie_id)}${hTag(x.horaire)} ${esc(x.symbole)} ${esc(x.timeframe)} · ${esc(String(x.strategie).slice(0,55))} <span class="mut">(${fmt(x.risk_pct,1)} %/trade, ${x.trades_direct} trades, ${fmt(x.r_total_direct,1,true)}R)</span></div>`).join("");
  return `<tr><td class="n"><b>${c.rang}</b></td><td>${c.conforme?'<span class="tag ok">conforme</span>':'<span class="tag ko">trop risquée</span>'}</td>
   <td><button class="btbtn" data-bt="${c.rang}" title="Rejouer cette combinaison sur tout l'historique MT5">Backtest</button> <button class="botbtn" data-top="${c.rang}">Créer le bot MT5</button></td>
   <td style="font-size:12px;line-height:1.5">${comps}</td>
   <td class="n">${r.ftmo_pass==null?"—":fmt(r.ftmo_pass,0)+" %"}</td><td class="n">${r.ftmo_echec_p1==null?"—":fmt(r.ftmo_echec_p1,1)+" %"}</td>
   <td class="n">${r.jours_attendus==null?"—":"~"+fmt(r.jours_attendus,0)+" j"}</td>
   <td class="n">${`<span class="${cls(r.rendement_pct)}">${fmt(r.rendement_pct,2,true)} %</span>`}</td>
   <td class="n">${fmt(r.pire_jour,2)} %</td><td class="n">${fmt(r.dd_max,2)} %</td>
   <td class="n">~${fmt(r.trades_mois,0)}</td><td class="n">${val(r.trades)}</td><td class="n">${val(r.reussis)} / ${val(r.rates)}</td>
</tr>`}).join("");
 return head+viewBT()+`<div class="scroll"><table><thead><tr><th>#</th><th>État</th><th>Historique / bot</th><th>Stratégies de la combinaison</th><th>Réussite challenge</th><th>Échecs</th>
  <th>Réussi en</th><th>Gain en direct</th><th>Pire jour</th><th>DD max</th><th>Trades / mois</th><th>Trades</th><th>Challenges réussis / ratés (vrais jours)</th></tr></thead>
  <tbody>${rows}</tbody></table></div>`}
let BT=null,BTr=null,btBusy=false,btTimer=null;
async function loadBT(rang,start){if(btBusy)return;btBusy=true;BTr=rang;
 const get=async go=>(await fetch(`/api/backtest?top=${rang}`+(go?"&lancer=1":""),{cache:"no-store"})).json();
 try{BT=await get(start===true);if(start==="auto"&&BT.etat==="jamais")BT=await get(true)}catch(e){BT={etat:"erreur",message:"Backtest impossible : "+e}}
 btBusy=false;clearTimeout(btTimer);if(BT.etat==="en cours")btTimer=setTimeout(()=>loadBT(BTr,false),2000);render()}
let EQ=null;
function eqSvg(c,rec){if(!c||c.length<2)return "";EQ=c;const W=900,H=240,L=46,R=10,T=10,B=24,v=c.map(x=>x[1]);
 let lo=Math.min(0,...v),hi=Math.max(0,...v);if(hi-lo<1e-9)hi=lo+1;const pad=(hi-lo)*.06;lo-=pad;hi+=pad;
 const X=i=>L+i/(c.length-1)*(W-L-R),Y=y=>T+(hi-y)/(hi-lo)*(H-T-B);
 const st=Math.pow(10,Math.floor(Math.log10((hi-lo)/4)));const step=[1,2,5,10].map(k=>k*st).find(k=>(hi-lo)/k<=5);
 let g="";for(let y=Math.ceil(lo/step)*step;y<=hi;y+=step)g+=`<line class="${Math.abs(y)<1e-9?"zl":"gl"}" x1="${L}" x2="${W-R}" y1="${Y(y)}" y2="${Y(y)}"/><text x="${L-6}" y="${Y(y)+4}" text-anchor="end">${fmt(Math.abs(y)<1e-9?0:y,0,true)} %</text>`;
 const ri=c.findIndex(x=>x[0]>=rec);const recR=ri>0?`<rect class="rec" x="${X(ri)}" y="${T}" width="${W-R-X(ri)}" height="${H-T-B}"/><text x="${X(ri)+6}" y="${T+12}">période récente</text>`:"";
 const yrs=[];let last="";c.forEach((x,i)=>{const yy=x[0].slice(0,4);if(yy!==last){last=yy;yrs.push(i)}});
 const ticks=yrs.filter((_,k)=>yrs.length<=12||k%Math.ceil(yrs.length/12)===0).map(i=>`<text x="${X(i)}" y="${H-6}" text-anchor="middle">${c[i][0].slice(0,4)}</text>`).join("");
 const d=c.map((x,i)=>(i?"L":"M")+X(i).toFixed(1)+" "+Y(x[1]).toFixed(1)).join("");
 return `<div class="eqwrap"><svg class="eq" viewBox="0 0 ${W} ${H}" role="img" aria-label="Courbe du compte en % sur l'historique">${recR}${g}${ticks}<path class="ln" d="${d}"/>
  <line class="cur" x1="0" x2="0" y1="${T}" y2="${H-B}" stroke="var(--axis)" style="display:none"/><circle class="dotc" r="4.5" fill="var(--accent)" stroke="var(--surface)" stroke-width="2" style="display:none"/>
  <rect x="${L}" y="${T}" width="${W-L-R}" height="${H-T-B}" fill="transparent" class="hit" data-l="${L}" data-w="${W-L-R}" data-t="${T}" data-h="${H-T-B}" data-lo="${lo}" data-hi="${hi}"/></svg><div class="eqtip"></div></div>`}
document.getElementById("view").addEventListener("mousemove",e=>{const hit=e.target.closest(".eq .hit");const wrap=e.target.closest(".eqwrap");
 if(!wrap)return;const svg=wrap.querySelector("svg"),tip=wrap.querySelector(".eqtip"),cur=svg.querySelector(".cur"),dot=svg.querySelector(".dotc");
 if(!hit){tip.style.display=cur.style.display=dot.style.display="none";return}
 const c=EQ||[];if(!c.length)return;const r=svg.getBoundingClientRect(),k=900/r.width;
 const L=+hit.dataset.l,Wd=+hit.dataset.w,T=+hit.dataset.t,Hh=+hit.dataset.h,lo=+hit.dataset.lo,hi=+hit.dataset.hi;
 const x=(e.clientX-r.left)*k,i=Math.max(0,Math.min(c.length-1,Math.round((x-L)/Wd*(c.length-1))));
 const px=L+i/(c.length-1)*Wd,py=T+(hi-c[i][1])/(hi-lo)*Hh;
 cur.setAttribute("x1",px);cur.setAttribute("x2",px);cur.style.display="";dot.setAttribute("cx",px);dot.setAttribute("cy",py);dot.style.display="";
 tip.innerHTML=`<b>${esc(c[i][0])}</b> · compte ${fmt(c[i][1],2,true)} %`;tip.style.display="block";
 const left=px/k;tip.style.left=Math.min(left+12,r.width-170)+"px";tip.style.top=Math.max(0,py/k-34)+"px"});
document.getElementById("view").addEventListener("click",e=>{const b=e.target.closest(".btbtn[data-bt]");if(!b)return;e.stopPropagation();
 loadBT(+b.dataset.bt,"auto");setTimeout(()=>{const p=document.querySelector(".panel");if(p)p.scrollIntoView({behavior:"smooth",block:"start"})},300)});
function viewBT(){if(!BT||!BTr)return "";const run=BT.etat==="en cours",R=BT.resultat;
 const pct=BT.total?Math.round(BT.fait/BT.total*100):0;
 let h=`<div class="panel"><div class="row" style="justify-content:space-between"><h3 style="margin:0">Backtest de la combinaison n°${BTr} sur l'historique MT5</h3>
  <a href="#" onclick="BT=null;BTr=null;render();return false" class="mut">fermer</a></div>`;
 if(run)return h+`<p><span class="meter" style="width:220px"><i style="width:${pct}%"></i></span> ${pct} % · ${esc(BT.message)} <span class="mut">(démarré à ${esc(BT.debut||"")})</span></p></div>`;
 if(!R||!R.ok)return h+`<p class="neg">${esc(BT.message||"")}</p>${R?compTable(R):""}</div>`;
 return h+btBody(R,{cols:["Tout l'historique","Période récente"],redo:"loadBT(BTr,true)",
  honest:`<b>À lire honnêtement :</b> ces stratégies ont été trouvées en cherchant sur une partie de ce même historique, donc
  « tout l'historique » est optimiste. La colonne <b>période récente</b> (les 35 % les plus récents, la période de validation de la recherche) et le paper trading sont les chiffres qui comptent.`})+`</div>`}
function btBody(R,o){
 const A=R.tout,Rc=R.recent;
 const row=(lab,k,f)=>`<tr><td>${lab}</td><td class="n">${f(A[k])}</td><td class="n">${f(Rc[k])}</td></tr>`;
 const pc=(d,s)=>v=>v==null?"—":`<span class="${s?cls(v):""}">${fmt(v,d,s)} %</span>`,nb=d=>v=>v==null?"—":fmt(v,d),days=v=>v==null?"—":"~"+fmt(v,0)+" j";
 let h=`<p class="note">${esc(R.message)} · ${esc(R.regles||"")} · ${fmt(R.refuses,0)} signaux refusés par la perte possible max par jour · calculé le ${esc(R.calcule_le||"")}
  ${o.redo?` · <a href="#" onclick="${o.redo};return false">refaire</a>`:""}</p>
  <p class="note" style="color:var(--ink)">${o.honest}</p>${o.extra||""}
  ${eqSvg(R.courbe,R.debut_recent)}
  <div class="row" style="align-items:flex-start;gap:18px;margin-top:10px">
  <table class="cmp" style="width:auto"><thead><tr><th></th><th>${o.cols[0]}</th><th>${o.cols[1]}</th></tr></thead><tbody>
  <tr><td>Période</td><td class="n">${esc(A.periode)}</td><td class="n">${esc(Rc.periode)}</td></tr>
  ${row("Gain total (risque fixe)","rendement_pct",pc(1,true))}${row("Réussite du challenge (simulée)","ftmo_pass",pc(0))}
  ${row("Échecs (limite de perte touchée)","ftmo_echec_p1",pc(1))}${row("Challenge réussi en","jours_attendus",days)}
  <tr><td>Challenges enchaînés sur les vrais jours</td><td class="n">${A.reussis} réussis / ${A.rates} ratés</td><td class="n">${Rc.reussis} réussis / ${Rc.rates} ratés</td></tr>
  ${row("Pire journée","pire_jour",pc(2,true))}${row("Drawdown max","dd_max",pc(2))}${row("Trades","trades",nb(0))}
  ${row("Trades par mois","trades_mois",nb(0))}${row("Trades gagnants","reussite_trades",pc(0))}${row("R moyen par trade","r_moyen",v=>v==null?"—":rr(v))}
  ${row("Jours tradés perdants","jours_negatifs_pct",pc(0))}${row("Trades gardés pendant un week-end","week_end_pct",pc(0))}${row("Durée moyenne d'un trade","duree_moy_h",v=>v==null?"—":fmt(v,1)+" h")}
  </tbody></table>
  <table class="cmp" style="width:auto"><thead><tr><th>Année</th><th>Gain</th><th>Pire jour</th><th>Trades</th></tr></thead><tbody>
  ${(R.annees||[]).map(y=>`<tr><td>${y.annee}</td><td class="n"><span class="${cls(y.pct)}">${fmt(y.pct,1,true)} %</span></td><td class="n">${fmt(y.pire_jour,2)} %</td><td class="n">${y.trades}</td></tr>`).join("")}</tbody></table></div>`;
 const M={};(R.mois||[]).forEach(m=>{(M[m.annee]=M[m.annee]||{})[m.mois]=m.pct});
 const mn=["janv","févr","mars","avr","mai","juin","juil","août","sept","oct","nov","déc"];
 h+=`<h4 style="margin:14px 0 6px">Gain par mois (%)</h4><div class="scroll" style="max-height:none"><table class="months"><thead><tr><th>Année</th>${mn.map(x=>`<th>${x}</th>`).join("")}</tr></thead><tbody>`+
  Object.keys(M).sort().map(y=>`<tr><td style="text-align:left">${y}</td>${mn.map((_,i)=>{const v=M[y][i+1];return `<td>${v==null?"":`<span class="${cls(v)}">${fmt(v,1,true)}</span>`}</td>`}).join("")}</tr>`).join("")+`</tbody></table></div>`;
 return h+compTable(R)}
function compTable(R){return `<h4 style="margin:14px 0 6px">Chaque stratégie de la combinaison sur l'historique</h4>`+table("btc",[["Marché","symbole"],["TF","timeframe"],["Stratégie","strategie"],["Risque/trade","risk_pct",v=>fmt(v,1)+" %",1],
 ["Trades","trades",null,1],["R total","r_total",rr,1],["Trades gagnants","reussite",v=>v==null?"—":fmt(v,0)+" %",1],["Historique","debut",(v,r)=>v?esc(v)+" → "+esc(r.fin):"—"],["Problème","erreur",v=>v?`<span class="neg">${esc(v)}</span>`:""]],R.composants||[])}
function miniBot(id){return id?`<button class="botbtn mini" data-id="${esc(id)}" title="Créer le bot MT5 de CETTE stratégie seule">Bot</button>`:""}
let T2=null,t2Busy=false,t2Timer=null,SEL2=null,bt2Deb="";
async function loadTop2(start){if(t2Busy)return;t2Busy=true;
 try{T2=await (await fetch("/api/top2ans"+(start?"?lancer=1&debut="+encodeURIComponent(bt2Deb):""),{cache:"no-store"})).json()}catch(e){T2={etat:"erreur",message:"Calcul impossible : "+e}}
 t2Busy=false;clearTimeout(t2Timer);if(T2.etat==="en cours")t2Timer=setTimeout(()=>loadTop2(false),2000);render()}
document.getElementById("view").addEventListener("click",e=>{const b=e.target.closest(".v2btn");if(!b)return;e.stopPropagation();
 SEL2={k:b.dataset.k,r:+b.dataset.r,l:b.dataset.l};render();setTimeout(()=>{const p=document.querySelector(".panel");if(p)p.scrollIntoView({behavior:"smooth",block:"start"})},100)});
function liveBox(L,single){if(!L||!L.trades)return `<div class="livebox"><b>En paper trading :</b> <span class="mut">pas encore de trade en direct.</span></div>`;
 return `<div class="livebox"><b>En paper trading jusqu'à maintenant</b> (depuis le ${esc(L.depuis)}, ${fmt(L.jours,0)} jours de bourse) :
  ${fmt(L.trades,0)} trades · ${rr(L.r_total)} · ${fmt(L.reussite,0)} % gagnants · gain <span class="${cls(L.rendement_pct)}">${fmt(L.rendement_pct,2,true)} %</span>
  · pire jour ${fmt(L.pire_jour,2)} % · challenges enchaînés ${L.reussis} réussis / ${L.rates} ratés${single||L.composants_avec_trades===L.composants?"":` · <span class="mut">${L.composants_avec_trades} stratégies sur ${L.composants} ont déjà tradé</span>`}</div>`}
function paperCells(L){if(!L||!L.trades)return `<td class="n mut">—</td><td class="n mut">—</td><td class="n mut">—</td><td class="mut">pas encore</td>`;
 return `<td class="n">${fmt(L.trades,0)}</td><td class="n"><span class="${cls(L.rendement_pct)}">${fmt(L.rendement_pct,2,true)} %</span> <span class="mut">(${fmt(L.r_total,1,true)}R)</span></td>
  <td class="n">${L.reussis} / ${L.rates}</td><td>${esc(L.depuis)}</td>`}
function bt2Cells(B){const a=(B&&B.tout)||{};const pc=(v,d)=>v==null?"—":fmt(v,d)+" %";
 return `<td class="n"><span class="${cls(a.rendement_pct)}">${fmt(a.rendement_pct,1,true)} %</span></td><td class="n">${pc(a.ftmo_pass,0)}</td><td class="n">${pc(a.ftmo_echec_p1,1)}</td>
  <td class="n">${a.jours_attendus==null?"—":"~"+fmt(a.jours_attendus,0)+" j"}</td><td class="n">${a.reussis??"—"} / ${a.rates??"—"}</td>
  <td class="n">${fmt(a.pire_jour,2)} %</td><td class="n">${fmt(a.dd_max,2)} %</td><td class="n">${fmt(a.trades,0)}</td><td class="n">~${fmt(a.trades_mois,0)}</td>`}
const BT2H=`<th>Gain 2 ans</th><th>Réussite challenge</th><th>Échecs</th><th>Réussi en</th><th>Challenges réussis / ratés</th><th>Pire jour</th><th>DD max</th><th>Trades</th><th>Trades / mois</th>`;
const PAPH=`<th>Trades paper</th><th>Gain paper</th><th>Challenges paper</th><th>Paper depuis</th><th title="Le paper trading fait combien % du backtest (100 % = pareil)">Paper / backtest</th>`;
function ratioCell(v){if(v==null)return `<td class="n mut">—</td>`;const c=v>=70?"pos":v<30?"neg":"";return `<td class="n"><span class="${c}">${fmt(v,0)} %</span></td>`}
const XL={partout:"Bonnes partout (backtest ET paper)",paper:"Bonnes en paper, pas en backtest",backtest:"Bonnes en backtest, pas en paper"};
let XSEL="partout";
function crossView(X){if(!X)return "";const L=(X.listes||{})[XSEL]||[];
 const tabs=Object.keys(XL).map(k=>`<button class="${k===XSEL?"cmpbtn":"botbtn"}" style="${k===XSEL?"font-size:13px;padding:5px 12px":""}" onclick="XSEL='${k}';render()">${XL[k]} (${((X.listes||{})[k]||[]).length})</button>`).join(" ");
 const why={partout:"Les plus solides : bien classées dans le backtest des 2 ans ET en paper trading. Ce sont elles à mettre en premier sur un vrai compte.",
  paper:"Elles marchent en paper trading mais leur backtest est faible : peut-être une bonne période passagère. À surveiller, pas à miser gros.",
  backtest:"Elles brillaient dans le backtest mais pas en paper : le backtest ne se confirme pas (chance, marché qui a changé, coûts réels). Prudence."}[XSEL];
 let h=`<h3 class="sec">Classement croisé : backtest 2 ans × paper trading</h3>
  <p class="note">${esc(X.message||"")} Rang = position parmi toutes les stratégies comparées (100 % = la meilleure), d'après la solidité t (R moyen / écart-type × racine du nombre de trades).
  « Paper / backtest » = R moyen du paper ÷ R moyen du backtest (100 % = le paper fait comme le backtest).</p><p>${tabs}</p><p class="note"><b>${esc(XL[XSEL])} :</b> ${why}</p>`;
 if(!L.length)return h+`<p class="note">Aucune stratégie dans cette liste pour l'instant.</p>`;
 return h+`<div class="scroll"><table><thead><tr><th>#</th><th>Backtest / bot</th><th>Marché</th><th>TF</th><th>Stratégie</th>
  <th>Rang backtest</th><th>Rang paper</th><th>Trades 2 ans</th><th>R moyen 2 ans</th><th>Gain 2 ans</th><th>t backtest</th>
  <th>Trades paper</th><th>R moyen paper</th><th>Gain paper</th><th>t paper</th><th title="R moyen du paper ÷ R moyen du backtest">Paper / backtest</th></tr></thead><tbody>`+
  L.map(x=>`<tr><td class="n"><b>${x.rang}</b></td><td><button class="v2btn btbtn" data-k="x" data-l="${XSEL}" data-r="${x.rang}">Fiche complète</button> <button class="botbtn" data-id="${esc(x.strategie_id)}">Bot MT5</button>${x.en_pause?' <span class="tag ko">en pause</span>':""}</td>
   <td>${esc(x.symbole)}</td><td>${esc(x.timeframe)}</td><td class="s" title="${esc(x.strategie)} · ${esc(x.risque_config)}">${esc(x.strategie)}</td>
   <td class="n"><b>${fmt(x.rang_bt,0)} %</b></td><td class="n"><b>${fmt(x.rang_paper,0)} %</b></td>
   <td class="n">${fmt(x.bt.trades,0)}</td><td class="n">${rr(x.bt.r_moyen)}</td><td class="n"><span class="${cls(x.bt.gain_pct)}">${fmt(x.bt.gain_pct,1,true)} %</span></td><td class="n">${fmt(x.bt.t,2)}</td>
   <td class="n">${fmt(x.paper.trades,0)}</td><td class="n">${rr(x.paper.r_moyen)}</td><td class="n"><span class="${cls(x.paper.gain_pct)}">${fmt(x.paper.gain_pct,1,true)} %</span></td><td class="n">${fmt(x.paper.t,2)}</td>${ratioCell(x.ratio)}</tr>`).join("")+`</tbody></table></div>`}
function stateTag(e){return verdict(e)+" "+(e.conforme?'<span class="tag ok">conforme</span>':'<span class="tag ko">trop risquée</span>')+(e.hors_top?' <span class="tag">hors TOP 10</span>':"")}
function viewTop2(mode){mode=mode||"bt2";if(!T2){loadTop2(false);return `<div class="empty">Chargement…</div>`}
 const run=T2.etat==="en cours",R=T2.resultat||{},C=R.combinees||[],S=R.seules||[];
 const pct=T2.total?Math.round(T2.fait/T2.total*100):0;
 let h=`<p style="margin:4px 0 10px"><button class="cmpbtn" ${run?"disabled":""} onclick="loadTop2(true)">
  ${run?"Backtest en cours…":"Backtester toutes les stratégies du direct → TOP 10"}</button>
  <label class="mut" style="margin-left:10px">Début du backtest : <input id="bt2deb" type="date" style="min-width:0" value="${esc(bt2Deb)}" onchange="bt2Deb=this.value" title="Vide = les 2 dernières années (jusqu'à aujourd'hui)"></label>
  <span class="mut">(vide = les 2 dernières années jusqu'à aujourd'hui)</span>
  ${run?` <span class="meter" style="width:220px"><i style="width:${pct}%"></i></span> ${pct} % · ${esc(T2.etape||"")} <span class="mut">(démarré à ${esc(T2.debut||"")} ; de quelques minutes à une heure selon le nombre de stratégies)</span>`:""}</p>
  <p class="note">${esc(T2.message||"")}${R.periode?` · période ${esc(R.periode)} · ${esc(R.regles||"")}`:""}${R.calcule_le?" · calculé le "+esc(R.calcule_le):""}</p>
  <p class="note">Chaque stratégie qui a pris au moins un trade sur cette plateforme (et chaque stratégie combinée qui tourne) est rejouée sur les
  <b>2 dernières années</b> de MT5 (spread, commission, glissement, swaps, nouvelles), sur un compte avec 1 % max par trade et 2,5 % de perte possible max par jour.
  À côté : ce qu'elle a donné <b>en paper trading</b> jusqu'à maintenant. <b>À lire honnêtement :</b> le n°1 parmi des milliers de backtests est en partie chanceux ;
  celle qui est bonne sur 2 ans <b>ET</b> en paper trading est la plus solide.</p>`;
 const X=(R.croise&&R.croise.listes)||{};
 const PZ=R.perso||[];
 const CX=R.combinees_croisees||[],GN=R.general||[];
 if(SEL2){const L=SEL2.k==="c"?C:SEL2.k==="s"?S:SEL2.k==="p"?PZ:SEL2.k==="x2"?CX:SEL2.k==="g"?GN:(X[SEL2.l]||[]),e=L.find(x=>x.rang===SEL2.r);
  if(e&&e.backtest){if(SEL2.k==="x")e.composants=[{strategie_id:e.strategie_id,symbole:e.symbole,timeframe:e.timeframe}];
   const B=e.backtest,title=SEL2.k==="g"?`Classement général n°${e.rang} — ${esc(e.type)}`:SEL2.k==="c"||SEL2.k==="x2"?`${esc(e.nom)}${e.origine&&e.origine!=="Chef des combinaisons"?" · "+esc(e.origine):""}`:SEL2.k==="x"?`${esc(XL[SEL2.l])} n°${e.rang} : ${esc(e.symbole)} ${esc(e.timeframe)}`:`${esc(e.nom)} : ${esc(e.composants[0].symbole)} ${esc(e.composants[0].timeframe)}`;
   h+=`<div class="panel"><div class="row" style="justify-content:space-between"><h3 style="margin:0">Backtest 2 ans — ${title}</h3>
    <span>${botPair(SEL2.k==="p"||SEL2.k==="g"?e.k:SEL2.k==="x2"?"x":SEL2.k,SEL2.k==="p"||SEL2.k==="g"?e.rang_source:e.rang,e)}
    <a href="#" onclick="SEL2=null;render();return false" class="mut" style="margin-left:10px">fermer</a></span></div>`+
    ficheExtra(e,R)+(B.ok?btBody(B,{cols:["2 dernières années","Période récente (8 derniers mois)"],extra:compteBox(e.comptes)+liveBox(e.direct,SEL2.k!=="c"),
     honest:"Backtest des 2 dernières années. Une partie de cette période a pu servir à la recherche des stratégies, et le classement choisit les meilleures parmi beaucoup : comparez toujours avec le paper trading ci-dessous."}):`<p class="neg">${esc(B.message||"")}</p>`)+`</div>`}}
 if(!C.length&&!S.length)return h+(run?"":`<div class="empty">Pas encore de TOP 10 backtest.</div>`);
 const comps=e=>e.composants.map(x=>`<div title="${esc(x.strategie)}">${miniBot(x.strategie_id)}${esc(x.symbole)} ${esc(x.timeframe)} ${hTag(x.horaire)} · ${esc(String(x.strategie).split(" | heures")[0].slice(0,50))}
  <span class="mut">(${fmt(x.risk_pct,1)} %/trade · 2 ans : ${fmt(x.trades_bt,0)} trades ${fmt(x.r_total_bt,1,true)}R · paper : ${x.direct_trades?fmt(x.direct_trades,0)+" trades "+fmt(x.direct_r,1,true)+"R":"pas encore"})</span>${x.en_pause?' <span class="tag ko">en pause</span>':""}</div>`).join("");
 if(mode==="gen"){h+=generalView(GN,comps)+crossCombView(CX,comps)+crossView(R.croise);return h}
 if(mode==="hours"){h+=planView(R.planning)+heuresView(R.heures||[]);return h}
 h+=`<h3 class="sec">TOP 10 des stratégies COMBINÉES (backtest 2 ans)</h3>`+(C.length?`<div class="scroll"><table><thead><tr><th>#</th><th>État</th><th>Backtest / bot</th><th>Stratégies de la combinaison (bot de chacune)</th><th>Origine</th>${BT2H}${CPTH}${PAPH}</tr></thead><tbody>`+
  C.map(e=>`<tr><td class="n"><b>${e.rang}</b></td><td>${stateTag(e)}</td><td><button class="v2btn btbtn" data-k="c" data-r="${e.rang}">Fiche complète</button> <button class="botbtn" data-bt2="${e.rang}">Bot MT5 combinée</button></td>
   <td style="font-size:12px;line-height:1.5">${comps(e)}</td><td>${esc(e.origine||"")}</td>${bt2Cells(e.backtest)}${compteCells(e.comptes)}${paperCells(e.direct)}${ratioCell(e.ratio)}</tr>`).join("")+`</tbody></table></div>`:`<p class="note">Pas de combinaison.</p>`);
 h+=persoView(PZ);
 h+=`<h3 class="sec">TOP 10 des stratégies SEULES (backtest 2 ans)</h3>`+(S.length?`<div class="scroll"><table><thead><tr><th>#</th><th>État</th><th>Backtest / bot</th><th>Marché</th><th>TF</th><th>Heures</th><th>Stratégie</th><th>Réglage</th>${BT2H}${CPTH}${PAPH}</tr></thead><tbody>`+
  S.map(e=>{const x=e.composants[0];return `<tr><td class="n"><b>${e.rang}</b></td><td>${stateTag(e)}${x.en_pause?' <span class="tag ko">en pause</span>':""}</td>
   <td><button class="v2btn btbtn" data-k="s" data-r="${e.rang}">Fiche complète</button> <button class="botbtn" data-id="${esc(x.strategie_id)}">Bot MT5</button></td>
   <td>${esc(x.symbole)}</td><td>${esc(x.timeframe)}</td><td>${hTag(x.horaire)||'<span class="mut">24 h/24</span>'}</td><td class="s" title="${esc(x.strategie)}">${esc(String(x.strategie).split(" | heures")[0])}</td><td class="s" title="${esc(x.risque_config)}">${esc(x.risque_config)}</td>${bt2Cells(e.backtest)}${compteCells(e.comptes)}${paperCells(e.direct)}${ratioCell(e.ratio)}</tr>`}).join("")+`</tbody></table></div>`:`<p class="note">Pas de stratégie seule.</p>`);
 return h}
function strip(x){const P=x.profil||[];if(!P.length)return "";const m=Math.max(...P.map(c=>Math.abs(c.r_moyen||0)),0.3);
 const inW=h=>x.debut==null?false:(x.debut<x.fin?(h>=x.debut&&h<x.fin):(h>=x.debut||h<x.fin));
 return `<span style="display:inline-flex;gap:1px">`+P.map(c=>{const v=c.r_moyen,a=v==null||!c.trades?0:Math.min(1,Math.abs(v)/m)*0.85+0.15;
  const bg=v==null||!c.trades?"var(--grid)":`color-mix(in srgb,${v>0?"var(--pos)":"var(--neg)"} ${Math.round(a*100)}%,transparent)`;
  return `<i title="${c.h} h : ${c.trades} trades, ${c.gagnants} gagnants${v==null?"":", "+fmt(v,2,true)+"R en moyenne, "+fmt(c.r_total,1,true)+"R au total"}" style="display:inline-block;width:11px;height:18px;border-radius:2px;background:${bg};${inW(c.h)?"outline:2px solid var(--accent);outline-offset:-1px":""}"></i>`}).join("")+`</span>`}
const PLANC=["#2a78d6","#eb6834","#1baf7a","#eda100","#e87ba4","#008300","#4a3aa7","#e34948"];
function planView(P){if(!P)return "";const B=P.blocs||[],G=P.global||[];
 const idx={};B.forEach((b,i)=>{b.heures.forEach(hh=>{idx[hh]=i})});
 const gm=Math.max(...G.map(c=>Math.abs(c.r_moyen||0)),0.2);
 const cell=(i,inner,bg,tip,dim)=>`<div title="${esc(tip)}" style="flex:1;min-width:30px;text-align:center;padding:6px 0;border-radius:4px;background:${bg};color:${dim?"var(--muted)":"#fff"};font-weight:600;font-size:12px">${inner}</div>`;
 const line=`<div style="display:flex;gap:2px;margin:6px 0 2px">`+Array.from({length:24},(_,i)=>{const k=idx[i];const b=k==null?null:B[k];
   const hx=(P.heures||[])[i]||{};
   return k==null?cell(i,"·","var(--grid)",`${i} h : aucune stratégie nettement gagnante (pas de trade)`,true):
    cell(i,k+1,PLANC[k%8],`${i} h : n°${k+1} ${b.symbole} ${b.timeframe} (${b.nom})${b.ok?"":" — non confirmée"}${hx.trades_choix?` · à cette heure : ${hx.trades_choix} trades, ${fmt(hx.r_moyen_choix,2,true)}R`:""}`,false)}).join("")+`</div>
  <div style="display:flex;gap:2px;font-size:11px;color:var(--muted)">`+Array.from({length:24},(_,i)=>`<div style="flex:1;min-width:30px;text-align:center">${i}h</div>`).join("")+`</div>`;
 const gl=`<div style="display:flex;gap:2px;margin:6px 0 2px">`+G.map(c=>{const v=c.r_moyen,a=v==null?0:Math.min(1,Math.abs(v)/gm)*0.85+0.15;
   const bg=v==null||!c.trades?"var(--grid)":`color-mix(in srgb,${v>0?"var(--pos)":"var(--neg)"} ${Math.round(a*100)}%,transparent)`;
   return `<div title="${c.h} h : ${c.trades} trades de toutes les stratégies, ${c.gagnants} gagnants${v==null?"":", "+fmt(v,2,true)+"R en moyenne"}" style="flex:1;min-width:30px;height:22px;border-radius:4px;background:${bg}"></div>`}).join("")+`</div>`;
 const C=P.combinees||[];
 let h=`<h3 class="sec">Planning de la journée : la meilleure stratégie à chaque heure</h3>
  <p class="note">Pour chaque heure (heure du serveur MT5), la plus forte de TOUTES les stratégies ${P.mode==="plages confirmées"?"parmi celles dont la plage horaire confirmée couvre cette heure":"(choix heure par heure, seulement si elle y est nettement gagnante)"} ;
  les heures de suite d'une même stratégie forment une plage. Choisi sur les 60 % premiers trades, contrôlé sur les 40 % suivants : seules les plages confirmées (✔) entrent dans la combinée « planning ».
  ${P.controle&&P.controle.trades?`Contrôle du planning : ${P.controle.trades} trades jamais vus pendant le choix, ${rr(P.controle.r_moyen)} en moyenne (${P.controle.plages_confirmees} plages confirmées sur ${P.controle.plages}).`:""}</p>
  ${line}
  <p class="mut" style="font-size:12px;margin:4px 0 12px">Chaque case = une heure ; le numéro = la stratégie qui trade à cette heure (tableau ci-dessous) ; « · » = personne ne trade.</p>`;
 if(B.length)h+=`<div class="scroll" style="max-height:none"><table><thead><tr><th>N°</th><th>Bot</th><th>Heures</th><th>Marché</th><th>TF</th><th>Stratégie</th><th>Choix : trades</th><th>Choix : R moyen</th><th>Contrôle : trades</th><th>Contrôle : R moyen</th><th>Confirmée</th></tr></thead><tbody>`+
  B.map((b,i)=>`<tr><td><span style="display:inline-block;width:14px;height:14px;border-radius:3px;background:${PLANC[i%8]};vertical-align:middle"></span> <b>${i+1}</b></td>
   <td><button class="botbtn mini" data-id="${esc(b.strategie_id)}" title="Bot de cette stratégie dans ces heures">Bot FTMO</button><button class="botbtn mini" data-id="${esc(b.strategie_id)}" data-profil="perso">Bot 5k</button></td>
   <td><b>${esc(b.nom)}</b></td><td>${esc(b.symbole)}</td><td>${esc(b.timeframe)}</td><td class="s" title="${esc(b.strategie)}">${esc(b.strategie)}</td>
   <td class="n">${b.trades_choix}</td><td class="n">${b.r_moyen_choix==null?"—":rr(b.r_moyen_choix)}</td><td class="n">${b.trades_controle}</td><td class="n">${b.r_moyen_controle==null?"—":rr(b.r_moyen_controle)}</td>
   <td>${b.ok?'<span class="tag ok">oui</span>':'<span class="tag ko">non</span>'}</td></tr>`).join("")+`</tbody></table></div>`;
 else h+=`<p class="note">Pas encore de planning : il faut des stratégies avec une plage horaire nettement meilleure (au moins 40 trades).</p>`;
 if(C.length)h+=`<p style="margin:10px 0">`+C.map(c=>`<b>${esc(c.nom)}</b> : n°${c.rang} des combinées${c.hors_top?" (hors TOP 10)":""}
   <button class="v2btn btbtn" data-k="c" data-r="${c.rang}">Fiche complète</button> <button class="botbtn" data-bt2="${c.rang}">Bot challenge FTMO</button> <button class="botbtn" data-bt2="${c.rang}" data-profil="perso">Bot compte perso 5k</button>`).join("<br>")+`</p>`;
 h+=`<h4 style="margin:16px 0 4px">Les meilleures heures en général (toutes les stratégies réunies)</h4>${gl}
  <div style="display:flex;gap:2px;font-size:11px;color:var(--muted)">`+Array.from({length:24},(_,i)=>`<div style="flex:1;min-width:30px;text-align:center">${i}h</div>`).join("")+`</div>
  <p class="mut" style="font-size:12px">Vert = à cette heure, les trades de toutes les stratégies gagnent en moyenne ; rouge = ils perdent. Survolez pour le détail.</p>`;
 return h}
function generalView(L,comps){let h=`<h3 class="sec">CLASSEMENT GÉNÉRAL : le meilleur en backtest ET en paper trading (seules et combinées)</h3>
  <p class="note">Toutes les stratégies seules et combinées (24 h/24 ou dans leurs meilleures heures 🕘) qui ont déjà tradé en paper, dans UN seul classement.
  Rang backtest = position pour passer le challenge sur les 2 ans (échecs, jours pour réussir, réussite) ; rang paper = gain par jour en paper trading (mêmes risques).
  Le n°1 est celle dont le PLUS FAIBLE des deux rangs est le plus haut : bonne dans les deux, pas seulement dans un.</p>`;
 if(!L.length)return h+`<p class="note">Pas encore : il faut des stratégies avec des trades en paper trading.</p>`;
 return h+`<div class="scroll"><table><thead><tr><th>#</th><th>Type</th><th>Rang backtest</th><th>Rang paper</th><th>Paper : gain / jour</th><th>Voir / bots</th><th>Stratégies (bot de chacune)</th>${BT2H}${CPTH}${PAPH}</tr></thead><tbody>`+
  L.map(e=>`<tr><td class="n"><b>${e.rang}</b></td><td>${verdict(e)} ${esc(e.type)}${e.conforme?"":' <span class="tag ko">trop risquée</span>'}</td>
   <td class="n"><b>${fmt(e.rang_bt,0)} %</b></td><td class="n"><b>${fmt(e.rang_paper,0)} %</b></td>
   <td class="n"><span class="${cls(e.paper_par_jour)}">${fmt(e.paper_par_jour,2,true)} %</span></td>
   <td><button class="v2btn btbtn" data-k="g" data-r="${e.rang}">Fiche complète</button> ${botPair(e.k,e.rang_source,e)}</td>
   <td style="font-size:12px;line-height:1.5">${comps(e)}</td>
   ${bt2Cells(e.backtest)}${compteCells(e.comptes)}${paperCells(e.direct)}${ratioCell(e.ratio)}</tr>`).join("")+`</tbody></table></div>`}
function crossCombView(L,comps){let h=`<h3 class="sec">TOP 10 des stratégies COMBINÉES bonnes en backtest ET en paper trading</h3>
  <p class="note">Le Chef des combinaisons part seulement des stratégies « bonnes partout » (backtest 2 ans ET paper trading), en 24 h/24 ou dans leurs meilleures heures (🕘),
  puis chaque combinée est classée sur les DEUX : son rang dans le backtest et son rang en paper trading (gain en paper avec les mêmes risques). Classement : le plus petit des deux rangs
  (bonne partout), puis la somme. « Paper / backtest » = gain par jour en paper ÷ gain par jour du backtest.</p>`;
 if(!L.length)return h+`<p class="note">Pas encore : il faut au moins 2 stratégies bonnes partout (au moins 5 trades en paper et 10 dans le backtest) et des trades en paper pour les combinaisons.</p>`;
 return h+`<div class="scroll"><table><thead><tr><th>#</th><th>État</th><th>Rang backtest</th><th>Rang paper</th><th>Backtest / bots</th><th>Stratégies (bot de chacune)</th>${BT2H}${CPTH}${PAPH}</tr></thead><tbody>`+
  L.map(e=>`<tr><td class="n"><b>${e.rang}</b></td><td>${stateTag(e)}</td><td class="n"><b>${fmt(e.rang_bt,0)} %</b></td><td class="n"><b>${fmt(e.rang_paper,0)} %</b></td><td><button class="v2btn btbtn" data-k="x2" data-r="${e.rang}">Fiche complète</button> ${botPair("x",e.rang,e)}</td>
   <td style="font-size:12px;line-height:1.5">${comps(e)}</td>
   ${bt2Cells(e.backtest)}${compteCells(e.comptes)}${paperCells(e.direct)}${ratioCell(e.ratio)}</tr>`).join("")+`</tbody></table></div>`}
function ficheExtra(e,R){const H=R.heures||[],comps=e.composants||[];
 const rows=comps.map(c=>{const base=c.base_id||String(c.strategie_id||"").split("@")[0],x=H.find(h=>h.strategie_id===base);
  return `<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:4px 0"><b style="min-width:120px">${esc(c.symbole)} ${esc(c.timeframe)}</b>${hTag(c.horaire)||'<span class="mut">24 h/24</span>'}
   ${x?strip({...x,debut:c.horaire?c.horaire.debut:null,fin:c.horaire?c.horaire.fin:null}):'<span class="mut" style="font-size:12px">profil heure par heure : moins de 40 trades</span>'}
   <button class="botbtn mini" onclick="atId='${esc(base)}';AT=null;go('at')">Analyse de ses trades</button>${miniBot(c.strategie_id)}</div>`}).join("");
 return `<div class="livebox"><b>Chaque stratégie, heure par heure</b> <span class="mut" style="font-size:12px">(vert = bonnes heures, rouge = mauvaises, encadré = ses heures)</span>${rows}</div>`}
function hTag(hz){return hz&&hz.debut!=null?`<span class="tag" title="Entrées seulement dans cette plage (heure du serveur MT5)">🕘 ${fmt(hz.debut,0)}h-${fmt(hz.fin,0)}h</span>`:""}
const CPTH=`<th title="Compte financé FTMO 100 000 $, 1 %/trade : gain moyen par jour de bourse sur 1 an (médiane)">Financé 100k $/jour</th><th title="Compte perso 5 000 $, 2 %/trade, intérêts composés : gain moyen par jour sur 1 an (médiane)">Perso 5k $/jour</th>`;
function compteCells(K){const f=(K||{}).finance,p=(K||{}).perso;const c=x=>x?`<td class="n"><span class="${cls(x.gain_jour_usd)}">${fmt(x.gain_jour_usd,0,true)} $</span> <span class="mut">(${fmt(x.rendement_an_median,0,true)} %/an)</span></td>`:`<td class="n mut">—</td>`;return c(f)+c(p)}
function compteBox(K){if(!K||(!K.finance&&!K.perso))return "";const one=(x,t)=>x?`<div class="tile"><div class="mut">${t}</div>
  <div class="v">${fmt(x.gain_jour_usd,0,true)} $ / jour</div><div class="mut" style="font-size:12px">sur 1 an (médiane de 1 000 années possibles) : ${fmt(x.gain_an_usd,0,true)} $ (${fmt(x.rendement_an_median,0,true)} %) ·
  ~${fmt(x.gain_mois_usd,0,true)} $ le 1er mois · mauvaise année (1 sur 10) : ${fmt(x.gain_an_p10_usd,0,true)} $ · baisse typique ${fmt(x.dd_median,0)} % · risque de problème ${fmt(x.p_probleme,1)} %</div></div>`:"";
 return `<div class="tiles" style="grid-template-columns:repeat(auto-fit,minmax(380px,1fr));margin-top:8px">${one(K.finance,"Compte financé FTMO 100 000 $ (1 %/trade, 2,5 %/jour ; avant le partage des profits)")}${one(K.perso,"Compte perso 5 000 $ (2 %/trade, 5 %/jour, intérêts composés)")}</div>`}
function botPair(k,r,e){const id=e.composants&&e.composants[0]?e.composants[0].strategie_id:"";
 if(k==="x")return `<button class="botbtn" data-bt2x="${r}">Bot challenge FTMO</button> <button class="botbtn" data-bt2x="${r}" data-profil="perso">Bot compte perso 5k</button>`;
 return k==="c"?`<button class="botbtn" data-bt2="${r}">Bot challenge FTMO</button> <button class="botbtn" data-bt2="${r}" data-profil="perso">Bot compte perso 5k</button>`:
  `<button class="botbtn" data-id="${esc(id)}">Bot challenge FTMO</button> <button class="botbtn" data-id="${esc(id)}" data-profil="perso">Bot compte perso 5k</button>`}
function persoView(L){let h=`<h3 class="sec">Le meilleur pour le COMPTE PERSO 5 000 $ (sur 1 an)</h3>
  <p class="note">2 % de risque par trade, 5 % de perte possible max par jour, intérêts composés (le risque suit le solde). Classement : d'abord celles avec au plus 5 % de risque de problème
  (baisse de 25 % ou -30 % au total), puis le plus gros gain médian sur 1 an. « $/jour » = gain de l'année ÷ 252 jours de bourse (une moyenne : certains jours perdent).
  Projections tirées du backtest : à confirmer en paper trading.</p>`;
 if(!L.length)return h+`<p class="note">Pas encore de projection.</p>`;
 return h+`<div class="scroll"><table><thead><tr><th>#</th><th>Type</th><th>Voir / bots</th><th>Stratégies (et leurs heures)</th><th>$/jour</th><th>$/mois (1er mois)</th><th>Gain 1 an (médiane)</th><th>Mauvaise année (1 sur 10)</th><th>Baisse typique</th><th>Risque de problème</th><th>Financé 100k $/jour</th>${PAPH}</tr></thead><tbody>`+
  L.map(x=>{const p=x.comptes.perso,f=x.comptes.finance;return `<tr><td class="n"><b>${x.rang}</b></td><td>${esc(x.type)}${x.sur?"":' <span class="tag ko">risquée</span>'}</td>
   <td><button class="v2btn btbtn" data-k="p" data-r="${x.rang}">Fiche complète</button> ${botPair(x.k,x.rang_source,x)}</td>
   <td style="font-size:12px;line-height:1.5">${x.composants.map(c=>`<div>${miniBot(c.strategie_id)}${esc(c.symbole)} ${esc(c.timeframe)} ${hTag(c.horaire)} · ${esc(String(c.strategie).split(" | heures")[0].slice(0,45))}</div>`).join("")}</td>
   <td class="n"><b class="${cls(p.gain_jour_usd)}">${fmt(p.gain_jour_usd,0,true)} $</b></td><td class="n">${fmt(p.gain_mois_usd,0,true)} $</td>
   <td class="n">${fmt(p.gain_an_usd,0,true)} $ <span class="mut">(${fmt(p.rendement_an_median,0,true)} %)</span></td><td class="n">${fmt(p.gain_an_p10_usd,0,true)} $</td>
   <td class="n">${fmt(p.dd_median,0)} %</td><td class="n">${fmt(p.p_probleme,1)} %</td><td class="n">${f?fmt(f.gain_jour_usd,0,true)+" $":"—"}</td>${paperCells(x.direct)}</tr>`}).join("")+`</tbody></table></div>`}
function heuresView(L){let h=`<h3 class="sec">Meilleures heures de chaque stratégie</h3>
  <p class="note">Pour chaque stratégie : la plage horaire (heure du serveur MT5) où elle trade le mieux. Elle est CHOISIE sur les 60 % premiers trades du backtest, puis CONTRÔLÉE sur les 40 % suivants
  (au moins 10 trades, gagnante, nettement mieux que 24 h/24). Les plages confirmées deviennent des stratégies « horaires » (🕘) que le Chef des combinaisons mélange avec d'autres qui tradent à d'autres heures.
  Colonne paper : ce que la stratégie fait en direct dans sa plage et en dehors.</p>`;
 if(!L.length)return h+`<p class="note">Aucune plage nettement meilleure trouvée (ou pas assez de trades : 40 minimum).</p>`;
 return h+`<p class="note"><b>Heure par heure</b> : chaque case = une heure d'ouverture (0 h à 23 h, heure du serveur MT5). Vert = trades gagnants en moyenne à cette heure,
  rouge = perdants, gris = pas de trade ; plus c'est foncé, plus c'est net. La plage choisie est encadrée. Survolez une case pour le détail.</p>
  <div class="scroll"><table><thead><tr><th>Bot</th><th>Marché</th><th>TF</th><th>Stratégie</th><th>Heure par heure (0 h → 23 h)</th><th>Meilleures heures</th><th>Contrôle</th><th>R moyen dans la plage</th><th>R moyen 24 h/24</th><th>R moyen hors plage</th>
  <th>Trades dans la plage</th><th>R total plage / 24 h</th><th>Contrôle : plage / 24 h</th><th>Paper : dans la plage</th><th>Paper : hors plage</th></tr></thead><tbody>`+
  L.map(x=>{const pp=x.paper_plage,ph=x.paper_hors,pc=v=>v&&v.trades?`${rr(v.r_moyen)} <span class="mut">(${v.trades})</span>`:'<span class="mut">—</span>';
   return `<tr><td>${x.ok?`<button class="botbtn mini" data-id="${esc(x.strategie_id)}@${x.debut}-${x.fin}" title="Bot de cette stratégie dans SES heures">Bot ${esc(x.nom)}</button>`:miniBot(x.strategie_id)}</td>
   <td>${esc(x.symbole)}</td><td>${esc(x.timeframe)}</td><td class="s" title="${esc(x.strategie)}">${esc(x.strategie)}</td><td>${strip(x)}</td><td><b>${esc(x.nom)}</b></td>
   <td>${x.ok===true?'<span class="tag ok">confirmée</span>':x.ok===false?'<span class="tag ko">pas confirmée</span>':'<span class="mut">aucune plage nettement meilleure</span>'}</td><td class="n">${x.r_moyen_plage==null?"—":rr(x.r_moyen_plage)}</td><td class="n">${rr(x.r_moyen_24h)}</td><td class="n">${x.r_moyen_hors==null?"—":rr(x.r_moyen_hors)}</td>
   <td class="n">${x.trades_plage??"—"} / ${x.trades_total}</td><td class="n">${rTxt(x.r_total_plage)} / ${rTxt(x.r_total_24h)}</td>
   <td class="n">${x.r_moyen_controle_plage==null?"—":rr(x.r_moyen_controle_plage)} / ${x.r_moyen_controle_24h==null?"—":rr(x.r_moyen_controle_24h)}</td><td class="n">${pc(pp)}</td><td class="n">${pc(ph)}</td></tr>`}).join("")+`</tbody></table></div>`}
let AT=null,atKey="",atBusy=false,atSec="exc",atId="";
async function loadAT(force){const k=[atId,fSym.value,fTf.value].join("|");if(atBusy||(!force&&AT&&k===atKey))return;atBusy=true;atKey=k;
 try{AT=await (await fetch(`/api/analyse_trades?id=${encodeURIComponent(atId)}&sym=${encodeURIComponent(fSym.value)}&tf=${encodeURIComponent(fTf.value)}`,{cache:"no-store"})).json()}catch(e){AT={message:"Analyse impossible : "+e}}
 atBusy=false;render()}
function rTxt(v){return v==null?"—":`<span class="${cls(v)}">${fmt(v,1,true)}R</span>`}
function viewAT(){loadAT(false);if(!AT)return `<div class="empty">Analyse des trades en cours…</div>`;
 const secs=[["exc","Stops & objectifs (MAE / MFE)"],["ctx","Quand ça marche"],["gh","Trades refusés (fantômes)"]];
 let h=`<p class="note">Ce que chaque trade du paper trading apprend. Filtres : marché et timeframe en haut de la page, et une stratégie :
  <select onchange="atId=this.value;loadAT(true)"><option value="">Toutes les stratégies</option>${(AT.liste||[]).map(x=>`<option value="${esc(x.id)}"${x.id===atId?" selected":""}>${esc(x.label)}</option>`).join("")}</select>
  · <a href="#" onclick="loadAT(true);return false">actualiser</a></p><p>`+secs.map(([k,l])=>`<button class="${k===atSec?"cmpbtn":"botbtn"}" style="${k===atSec?"font-size:13px;padding:5px 12px":""}" onclick="atSec='${k}';render()">${l}</button>`).join(" ")+`</p>`;
 if(atSec==="exc"){const E=AT.excursions||{},G=E.global;
  h+=`<p class="note">${esc(E.message||"")}</p><p class="note"><b>Comment lire :</b> MAE = jusqu'où le prix est allé CONTRE le trade, MFE = jusqu'où il est allé EN SA FAVEUR (en R).
   Les variantes sont calculées sur les prix réellement vus : « stop au point d'entrée à +1R », « objectif plus proche », « stop plus serré » (même risque en argent, donc plus de lots).
   Les frais ne sont pas recomptés : avec un stop plus serré, le spread pèse plus lourd. Un objectif plus loin ou un stop plus large se testent dans le backtest.</p>`;
  if(G)h+=`<div class="tiles"><div class="tile"><div class="mut">Trades avec excursions</div><div class="v">${fmt(G.trades,0)}</div></div>
   <div class="tile"><div class="mut">Perdants qui étaient passés à +1R</div><div class="v">${fmt(G.perdants_passes_1r,0)} %</div><div class="mut" style="font-size:12px">un stop au point d'entrée à +1R les aurait sortis à 0</div></div>
   <div class="tile"><div class="mut">Tous les trades : R réel → avec BE à +1R</div><div class="v">${rTxt(G.actuel)} → ${rTxt(G.be_1r)}</div></div>
   <div class="tile"><div class="mut">Gagnants : recul médian avant de gagner</div><div class="v">${G.gagnants_mae_med==null?"—":fmt(G.gagnants_mae_med,2)+"R"}</div><div class="mut" style="font-size:12px">petit = le stop pourrait être plus serré</div></div>
   <div class="tile"><div class="mut">Perdants : avance médiane avant de perdre</div><div class="v">${G.perdants_mfe_med==null?"—":fmt(G.perdants_mfe_med,2)+"R"}</div><div class="mut" style="font-size:12px">grand = l'objectif est peut-être trop loin</div></div></div>`;
  const S=E.strategies||[];if(S.length){const tpk=[...new Set(S.flatMap(x=>Object.keys(x.tp||{})))].sort((a,b)=>a-b);
   h+=`<div class="scroll"><table><thead><tr><th>Bot</th><th>Marché</th><th>TF</th><th>Stratégie</th><th>Trades</th><th>Conseil (prix réels)</th><th>R réel</th><th>BE à +1R</th>
    ${tpk.map(k=>`<th>Objectif ${k}R</th>`).join("")}<th>Stop ×0,75</th><th>Stop ×0,5</th><th>Perdants passés à +1R</th><th>Recul médian des gagnants</th><th>Avance médiane des perdants</th></tr></thead><tbody>`+
    S.map(x=>`<tr><td>${miniBot(x.strategie_id)}</td><td>${esc(x.symbole)}</td><td>${esc(x.timeframe)}</td><td class="s" title="${esc(x.strategie)} · ${esc(x.risque)}">${esc(x.strategie)}</td><td class="n">${x.trades}</td>
     <td>${x.conseil==="garder le réglage actuel"?'<span class="mut">garder le réglage actuel</span>':`<b>${esc(x.conseil)}</b>`}</td><td class="n">${rTxt(x.r_actuel)}</td><td class="n">${rTxt(x.r_be)}</td>
     ${tpk.map(k=>`<td class="n">${x.tp&&x.tp[k]!=null?rTxt(x.tp[k]):'<span class="mut">—</span>'}</td>`).join("")}<td class="n">${rTxt(x.sl&&x.sl["0.75"])}</td><td class="n">${rTxt(x.sl&&x.sl["0.5"])}</td>
     <td class="n">${fmt(x.perdants_passes_1r,0)} %</td><td class="n">${x.gagnants_mae_med==null?"—":fmt(x.gagnants_mae_med,2)+"R"}</td><td class="n">${x.perdants_mfe_med==null?"—":fmt(x.perdants_mfe_med,2)+"R"}</td></tr>`).join("")+`</tbody></table></div>`}
  return h}
 if(atSec==="ctx"){const C=AT.contexte||{};h+=`<p class="note">${esc(C.message||"")}</p>`;
  const lst=(L,title,good)=>L&&L.length?`<div class="tile" style="border-color:${good?"var(--good)":"var(--crit)"}"><div class="mut">${title}</div>${L.slice(0,8).map(x=>`<div style="margin-top:4px"><b>${esc(x.quoi)}</b> : ${x.trades} trades, ${rr(x.r_moyen)} en moyenne, ${fmt(x.reussite,0)} % gagnants (t ${fmt(x.t,1)})</div>`).join("")}</div>`:"";
  h+=`<div class="tiles" style="grid-template-columns:repeat(auto-fit,minmax(360px,1fr))">${lst(C.a_eviter,"Moments à éviter (à vérifier dans le backtest avant de filtrer)",false)}${lst(C.points_forts,"Points forts",true)}</div>`;
  Object.values(C.tables||{}).forEach(T=>{const m=Math.max(...T.lignes.map(x=>Math.abs(x.r_total)),1e-9);
   h+=`<h4 style="margin:14px 0 6px">${esc(T.titre)}</h4><div class="bars" style="grid-template-columns:200px 1fr 400px;max-width:1150px">`+T.lignes.map(x=>{const w=Math.abs(x.r_total)/m*50;
    return `<div>${esc(x.valeur)}</div><div class="bar" title="${esc(x.valeur)} : ${x.trades} trades, R total ${fmt(x.r_total,1,true)}"><span class="zero" style="left:50%"></span><i class="${x.r_total<0?"neg":""}" style="${x.r_total<0?"right:50%":"left:50%"};width:${w}%"></i></div>
     <div class="mut" style="font-size:12px">${rTxt(x.r_total)} · ${x.trades} trades · ${rr(x.r_moyen)}/trade · ${fmt(x.reussite,0)} % · t ${fmt(x.t,1)}</div>`}).join("")+`</div>`});
  return h}
 const F=AT.fantomes||{};h+=`<p class="note">${esc(F.message||"")}</p>`;
 if(F.total)h+=`<div class="tiles"><div class="tile"><div class="mut">Signaux refusés suivis</div><div class="v">${fmt(F.trades,0)}</div></div><div class="tile"><div class="mut">Ce qu'ils auraient fait</div><div class="v">${rTxt(F.total.r_total)}</div><div class="mut" style="font-size:12px">${fmt(F.total.reussite,0)} % gagnants · ${rr(F.total.r_moyen)}/trade</div></div></div>`;
 if((F.par_raison||[]).length)h+=`<h4 style="margin:12px 0 6px">Par raison du refus</h4>`+table("ghr",[["Raison","raison"],["Trades","trades",null,1],["Gagnants","reussite",v=>v==null?"—":fmt(v,0)+" %",1],["R moyen","r_moyen",rr,1],["R total","r_total",rTxt,1],
  ["Verdict","r_total",v=>v<0?'<span class="tag ok">la règle protège</span>':'<span class="tag ko">la règle coûte des gains</span>']],F.par_raison);
 if((F.par_strategie||[]).length)h+=`<h4 style="margin:12px 0 6px">Par stratégie</h4>`+table("ghs",[["Bot","strategie_id",v=>miniBot(v)],["Marché","symbole"],["TF","timeframe"],["Stratégie","strategie"],["Combinée","groupe"],["Trades","trades",null,1],["R total","r_total",rTxt,1],["Gagnants","reussite",v=>v==null?"—":fmt(v,0)+" %",1]],F.par_strategie);
 return h}
let M=null,mTime=0,mBusy=false;
async function loadMarches(force){if(mBusy||(!force&&M&&Date.now()-mTime<60000))return;mBusy=true;mTime=Date.now();
 try{M=await (await fetch("/api/marches",{cache:"no-store"})).json()}catch(e){M={message:"Classement impossible : "+e,marches:[]}}
 mBusy=false;mTime=Date.now();render()}
function viewMk(){if(!M){loadMarches(true);return `<div class="empty">Classement des marchés en cours…</div>`}loadMarches(false);
 const card=m=>{const b=m.bot,r=m.reserve;
  if(!b)return `<div class="tile"><div class="v" style="font-size:18px">${esc(m.symbole)}</div><div class="mut">Aucun bot conseillé : ${esc(m.raison||"")}</div></div>`;
  return `<div class="tile" style="border-color:var(--accent)"><div class="v" style="font-size:18px">${esc(m.symbole)} <span class="mut" style="font-size:13px">graphique ${esc(b.timeframe)}</span></div>
  <div style="margin:6px 0"><b title="${esc(b.strategie)}">${esc(String(b.strategie).slice(0,90))}</b><div class="mut" style="font-size:12px">${esc(b.risque)}</div></div>
  <div class="mut" style="font-size:13px">${b.trades} trades en direct (~${fmt(b.trades_mois,0)}/mois) · réussite ${fmt(b.reussite_pct,0)} % · R total ${rr(b.r_total)} · R moyen ${rr(b.r_moyen)} · solidité t ${fmt(b.t,2)} · ${b.jours} jours</div>
  ${b.meilleur_jour_ok?"":`<div class="neg" style="font-size:12px">Attention : une seule journée fait ${fmt(b.meilleur_jour_part,0)} % du profit (règle FTMO du meilleur jour)</div>`}
  <p style="margin:8px 0 0"><button class="botbtn" data-id="${esc(b.strategie_id)}">Créer le bot MT5 pour ${esc(m.symbole)}</button></p>
  ${r?`<div class="mut" style="font-size:12px;margin-top:8px">Remplaçant : ${esc(r.timeframe)} · ${esc(String(r.strategie).slice(0,60))} (${r.trades} trades, ${fmt(r.r_total,2,true)}R) <button class="botbtn" data-id="${esc(r.strategie_id)}">Bot</button></div>`:""}</div>`};
 return `<p class="note">${esc(M.message)}. Un bot par marché : dans MT5, posez chaque bot sur un graphique de SON marché et de SON timeframe.
  Classement d'après les trades EN DIRECT de cette plateforme (au moins ${M.min_trades} trades, gagnant, pas en pause), puis la solidité t.
  Les chiffres bougent tant qu'il y a peu de trades · <a href="#" onclick="loadMarches(true);return false">actualiser</a></p>
  <div class="tiles" style="grid-template-columns:repeat(auto-fill,minmax(300px,1fr))">${(M.marches||[]).filter(m=>!fSym.value||m.symbole===fSym.value).map(card).join("")}</div>`}
let tab=localStorageGet("tab")||"home",D=null,sortState={};if(!TABS.some(t=>t[0]===tab))tab="home";
let expert=localStorageGet("expert")==="1";
function localStorageGet(k){try{return localStorage.getItem(k)}catch(e){return null}}
function localStorageSet(k,v){try{localStorage.setItem(k,v)}catch(e){}}
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt=(v,d=2,sign=false)=>v==null||v===""||isNaN(v)?"—":(sign&&v>0?"+":"")+Number(v).toLocaleString("fr-FR",{minimumFractionDigits:d,maximumFractionDigits:d});
const cls=v=>v>0?"pos":v<0?"neg":"";
function px(v,r){if(v==null||v==="")return "sur signal";const d=(D.prix[r.symbole]||{}).digits;
 return d==null?esc(v):Number(v).toFixed(d)}
function money(v){return `<span class="${cls(v)}">${fmt(v,2,true)} $</span>`}
function rr(v){return `<span class="${cls(v)}">${fmt(v,2,true)}R</span>`}
function drawTabs(){const sec=secOf(tab),S=SECTIONS.find(x=>x[0]===sec);
 document.getElementById("tabs").innerHTML=`<div class="secs">${SECTIONS.map(([k,l,sub])=>`<button data-s="${k}" class="${k===sec?"on":""}">${l}</button>`).join("")}</div>`+
  (S[2].length>1?S[2].map(([k,l])=>`<button data-k="${k}" class="${k===tab?"on":""}">${l}</button>`).join(""):"")}
function go(k){tab=k;localStorageSet("tab",k);render();window.scrollTo(0,0)}
document.getElementById("tabs").onclick=e=>{const s=e.target.dataset.s,k=e.target.dataset.k;
 if(s){const S=SECTIONS.find(x=>x[0]===s);go(S[2][0][0])}else if(k)go(k)};
function setMode(){document.body.classList.toggle("simple",!expert);const b=document.getElementById("modeBtn");
 if(b)b.textContent=expert?"🔧 Mode expert (cliquer pour simple)":"✨ Mode simple (cliquer pour expert)"}
document.getElementById("modeBtn").onclick=()=>{expert=!expert;localStorageSet("expert",expert?"1":"0");setMode();lastHtml=null;render()};
setMode();
["fSym","fTf","fTxt"].forEach(id=>document.getElementById(id).addEventListener("input",render));
function filt(rows,symKey="symbole",tfKey="tf"){const s=fSym.value,t=fTf.value,q=fTxt.value.toLowerCase();
 return rows.filter(r=>(!s||r[symKey]===s)&&(!t||r[tfKey]===t)&&(!q||JSON.stringify(r).toLowerCase().includes(q)))}
function table(id,cols,rows){ // cols: [label,key,render,numeric]
 const st=sortState[id];if(st){const c=cols[st.i];rows=[...rows].sort((a,b)=>{const x=a[c[1]],y=b[c[1]];
  return (x>y?1:x<y?-1:0)*(st.d)})}
 if(!rows.length)return `<div class="scroll"><div class="empty">Rien pour l'instant</div></div>`;
 return `<div class="scroll"><table data-id="${id}"><thead><tr>${cols.map((c,i)=>`<th data-i="${i}">${c[0]}</th>`).join("")}</tr></thead><tbody>`+
 rows.slice(0,1500).map(r=>"<tr>"+cols.map(c=>{const long=["strategie","texte","risque"].includes(c[1]);
  const att=c[3]?' class="n"':long?` class="s" title="${esc(r[c[1]])}"`:"";
  return `<td${att}>${c[2]?c[2](r[c[1]],r):esc(r[c[1]])}</td>`}).join("")+"</tr>").join("")+"</tbody></table></div>"}
document.getElementById("view").addEventListener("click",async e=>{const b=e.target.closest(".botbtn");if(!b)return;
 e.stopPropagation();b.disabled=true;const q=b.dataset.bt2x?`bt2x=${encodeURIComponent(b.dataset.bt2x)}`:b.dataset.bt2?`bt2=${encodeURIComponent(b.dataset.bt2)}`:b.dataset.top?`top=${encodeURIComponent(b.dataset.top)}`:b.dataset.analyse?"analyse=1":b.dataset.groupe?`groupe=${encodeURIComponent(b.dataset.groupe)}`:`id=${encodeURIComponent(b.dataset.id)}`;const qq=q+(b.dataset.profil?`&profil=${b.dataset.profil}`:"");
 try{const r=await (await fetch("/api/bot?"+qq,{cache:"no-store"})).json();showModal(r.ok===false?"Bot non créé":"✅ Bot créé et installé dans MT5",r.message,r.dossier)}catch(err){showModal("Erreur",String(err))}b.disabled=false});
function showModal(title,text,dossier){const bg=document.createElement("div");bg.className="modal-bg";
 bg.innerHTML=`<div class="modal" role="dialog" aria-modal="true"><h3 style="margin:0">${esc(title)}</h3><pre>${esc(text||"")}</pre>
  <p style="display:flex;gap:8px;flex-wrap:wrap">${dossier?`<button class="botbtn" data-open="${esc(dossier)}">Ouvrir le dossier du bot</button>`:""}<button class="botbtn" data-k2="bots">Voir mes bots</button><button class="cmpbtn" data-close="1">Fermer</button></p></div>`;
 bg.onclick=async e=>{if(e.target===bg||e.target.dataset.close){bg.remove();return}
  if(e.target.dataset.k2){bg.remove();go(e.target.dataset.k2);return}
  if(e.target.dataset.open){try{const r=await (await fetch("/api/ouvrir?dossier="+encodeURIComponent(e.target.dataset.open))).json();e.target.textContent=r.ok?"Dossier ouvert":"Chemin : "+e.target.dataset.open}catch(err){}}};
 document.body.appendChild(bg)}
document.getElementById("view").addEventListener("click",e=>{const th=e.target.closest("th");if(!th)return;
 const id=th.closest("table").dataset.id,i=+th.dataset.i;const s=sortState[id];
 sortState[id]={i,d:s&&s.i===i?-s.d:-1};render()});
function tiles(){const c=D.comptes,P=D.positions;const closed=c.reduce((a,x)=>a+x.trades,0),wins=c.reduce((a,x)=>a+x.gagnants,0);
 const pnl=c.reduce((a,x)=>a+x.pnl,0),lat=P.reduce((a,x)=>a+x.latent,0);
 const ok=c.filter(x=>x.ftmo==="RÉUSSI").length,ko=c.filter(x=>x.ftmo.startsWith("ÉCHOUÉ")).length;
 const T=[["Comptes fictifs",fmt(D.n_comptes,0),`${fmt(D.n_actifs,0)} ont déjà tradé`],["Positions ouvertes",fmt(P.length,0),`latent ${fmt(lat,0,true)} $`],
 ["Trades clôturés",fmt(closed,0),closed?`${fmt(wins/closed*100,0)} % gagnants`:""],["P&L réalisé (tous comptes)",fmt(pnl,0,true)+" $",""],
 D.profil?["Compte",esc(D.profil.nom),esc(D.profil.but)]:["Challenges FTMO",`${ok} réussis`,`${ko} échoués · ${fmt(D.ftmo.target1,0)} % / ${fmt(D.ftmo.max_daily,0)} % jour / ${fmt(D.ftmo.max_total,0)} % total`]];
 tiles_.innerHTML=T.map(([k,v,s])=>`<div class="tile"><div class="mut">${k}</div><div class="v">${v}</div><div class="mut" style="font-size:12px">${s}</div></div>`).join("")}
const tiles_=document.getElementById("tiles");
function viewPos(){return `<p class="note">Chaque ligne est un trade fictif en cours, calculé sur les vrais prix de MT5. Le SL et le TP sont vérifiés tick par tick.</p>`+
 table("pos",[["Marché","symbole"],["TF","tf"],["Bot","id",botBtn],["Sens","sens",v=>`<b>${v}</b>`],["Lots","lots",v=>fmt(v,2),1],["Ouverture","ouverture"],
 ["Entrée","entree",px,1],["SL initial","sl_initial",px,1],["SL actuel","sl",px,1],["TP","tp",px,1],["Prix actuel","prix",px,1],
 ["Pips → SL","pips_sl",v=>fmt(v,1),1],["Pips → TP","pips_tp",v=>fmt(v,1),1],["Latent","latent",money,1],["Latent R","latent_r",rr,1],
 ["Bougies","bougies",null,1],["Stratégie","strategie"],["Risque","risque"]],filt(D.positions))}
function viewHist(){const n=D.n_trades_total||D.trades.length;return `<p class="note">${fmt(n,0)} trades pris depuis le début, tous gardés pour toujours
 (fichier trades.csv). Ici : les ${fmt(Math.min(n,D.trades.length),0)} plus récents. Tout l'historique : bouton « Télécharger tous les trades (Excel) ».</p>`+table("hist",[["Fermeture","fermeture"],["Bot","strategie_id",botBtn],["Ouverture","ouverture"],["Durée","duree_min",v=>v==null?"—":v<60?fmt(v,0)+" min":fmt(v/60,1)+" h",1],
 ["Marché","symbole"],["TF","timeframe"],["Sens","sens",v=>`<b>${v}</b>`],["Lots","lots",v=>fmt(v,2),1],["Entrée","prix_entree",px,1],
 ["SL initial","sl_initial",px,1],["SL final","sl_final",px,1],["TP","tp",px,1],["Sortie","prix_sortie",px,1],
 ["Raison","raison",v=>`<span class="tag">${esc(v)}</span>`],["Pips","pips",v=>`<span class="${cls(v)}">${fmt(v,1,true)}</span>`,1],
 ["R","r",rr,1],["P&L","pnl",money,1],["Solde","solde",v=>fmt(v,2),1],["Spread entrée","spread_entree_pts",v=>fmt(v,1)+" pts",1],
 ["Stratégie","strategie"],["Risque","risque"]],filt(D.trades,"symbole","timeframe"))}
function ftmoCell(v,x){if(v==="RÉUSSI")return `<span class="tag ok">réussi</span>`;if(v.startsWith("ÉCHOUÉ"))return `<span class="tag ko">${esc(v.toLowerCase())}</span>`;
 const p=x&&x.profit_pct!=null?x.profit_pct:null,j=x?x.jours_trades:null,m=D.ftmo.min_days;
 if(p!=null&&p>=((x&&x.objectif_requis_pct)||D.ftmo.target1))return j!=null&&j<m?`<span class="tag ok" title="FTMO exige au moins ${m} jours avec un trade : l'objectif est atteint, il manque ${m-j} jour(s) de trading">objectif atteint · jours ${j}/${m}</span>`:`<span class="tag ok" title="Objectif atteint : le challenge sera validé à la fermeture des positions">objectif atteint · positions à fermer</span>`;
 return `<span class="tag run">en cours</span>`}
function botBtn(v){if(!v)return "";return `<button class="botbtn" data-id="${esc(v)}" title="Créer le bot MT5 de cette stratégie">Bot MT5</button>`}
function prog(p,x){const t=(x&&x.objectif_requis_pct)||D.ftmo.target1,w=Math.max(0,Math.min(100,Math.abs(p)/t*100));
 const more=t>D.ftmo.target1+1e-9?` <span class="mut" title="Règle du meilleur jour : une journée ne peut pas faire plus de ${D.ftmo.best_day_pct} % du profit total">(objectif ${fmt(t,2)} %)</span>`:"";
 return `<span class="meter${p<0?" neg":""}" title="${fmt(p,2,true)} % sur ${fmt(t,2)} %"><i style="width:${w}%"></i></span> ${fmt(p,2,true)} %${more}`}
function viewStrat(){return `<p class="note">Un compte fictif par stratégie × marché × timeframe × R:R. Triez en cliquant sur les colonnes.</p>`+
 table("strat",[["Marché","symbole"],["TF","tf"],["Bot","id",botBtn],["Stratégie","strategie"],["Risque","risque"],["Origine","origine"],["Trades","trades",null,1],["Trades / mois","trades_mois",v=>v==null?"—":"~"+fmt(v,0),1],
 ["Réussite","gagnants",(v,r)=>r.trades?fmt(v/r.trades*100,0)+" %":"—",1],["R moyen","r_moyen",rr,1],["R total","r_total",rr,1],["P&L","pnl",money,1],
 ["Objectif FTMO","profit_pct",prog],["Pire jour","pire_jour_pct",v=>`<span class="${cls(v)}">${fmt(v,2)} %</span>`,1],["DD max","dd_max",v=>fmt(v,2)+" %",1],
 ["Challenge","ftmo",ftmoCell],["Jours tradés","jours_trades",null,1],["Attendu (recherche)","attendu_r",v=>v==null?"—":rr(v),1],["Contrôle","en_pause",pauseCell],["","en_position",v=>v?'<span class="tag run">en position</span>':""]],filt(D.comptes))}
function pauseCell(v,x){return v?`<span class="tag ko" title="${esc(x.pause_raison||"")}">en pause</span>`:(x.trades>=20&&x.attendu_r!=null?'<span class="tag ok">conforme</span>':"")}
function viewRR(){const c=filt(D.comptes),by={};c.forEach(x=>{const k=x.rr==null?"signal":"1:"+x.rr;(by[k]=by[k]||{k,rr:x.rr??99,t:0,w:0,R:0,p:0,n:0});
 const b=by[k];b.t+=x.trades;b.w+=x.gagnants;b.R+=x.r_total;b.p+=x.pnl;b.n++});
 const rows=Object.values(by).sort((a,b)=>a.rr-b.rr);if(!rows.length)return `<div class="empty">Pas encore de trade clôturé</div>`;
 const m=Math.max(...rows.map(r=>Math.abs(r.R)),1e-9);
 const bars=`<div class="bars">`+rows.map(r=>{const w=Math.abs(r.R)/m*50;
  return `<div><b>R:R ${esc(r.k)}</b></div><div class="bar" title="R:R ${esc(r.k)} : ${fmt(r.R,1,true)}R sur ${r.t} trades"><span class="zero" style="left:50%"></span><i class="${r.R<0?"neg":""}" style="${r.R<0?`right:50%`:`left:50%`};width:${w}%"></i></div><div class="n">${rr(r.R)}</div>`}).join("")+`</div>`;
 const strat={};c.forEach(x=>{const k=x.base;(strat[k]=strat[k]||{base:k,best:null,R:-1e9,t:0});const s=strat[k];s.t+=x.trades;if(x.trades&&x.r_total>s.R){s.R=x.r_total;s.best=x.rr==null?"signal":"1:"+x.rr}});
 return `<p class="note">R total cumulé de toutes les stratégies pour chaque R:R (marchés et timeframes filtrés ci-dessus).</p>`+bars+
 `<h3 style="margin:18px 0 8px;font-size:15px">Par R:R</h3>`+table("rrt",[["R:R","k"],["Comptes","n",null,1],["Trades","t",null,1],["Réussite","w",(v,r)=>r.t?fmt(v/r.t*100,0)+" %":"—",1],
 ["R total","R",rr,1],["R moyen / trade","R",(v,r)=>r.t?rr(v/r.t):"—",1],["P&L","p",money,1]],rows)+
 `<h3 style="margin:18px 0 8px;font-size:15px">Meilleur R:R pour chaque stratégie</h3>`+
 table("rrs",[["Stratégie","base"],["Meilleur R:R","best"],["R total (meilleur)","R",rr,1],["Trades (tous R:R)","t",null,1]],Object.values(strat).filter(s=>s.best).sort((a,b)=>b.R-a.R))}
function viewFtmo(){const c=filt(D.comptes);const ok=c.filter(x=>x.ftmo==="RÉUSSI"),ko=c.filter(x=>x.ftmo.startsWith("ÉCHOUÉ"));
 return `<p class="note">Chaque compte fictif est suivi comme un challenge FTMO (${esc(D.ftmo_label)}). La perte du jour compte le latent des positions ouvertes.</p>`+
 table("ftmo",[["Challenge","ftmo",ftmoCell],["Bot","id",botBtn],["Quand","ftmo_quand"],["Marché","symbole"],["TF","tf"],["Stratégie","strategie"],["Risque","risque"],
 ["Progression","profit_pct",prog],["Pire jour","pire_jour_pct",v=>`<span class="${cls(v)}">${fmt(v,2)} %</span>`,1],["Jours tradés","jours_trades",null,1],
 ["Trades","trades",null,1],["DD max","dd_max",v=>fmt(v,2)+" %",1]],[...ok,...c.filter(x=>x.ftmo==="en cours").sort((a,b)=>b.profit_pct-a.profit_pct),...ko])}
function gauge(val,limit,label,good){if(Math.abs(val)<1e-9)val=0;const w=Math.max(0,Math.min(100,Math.abs(val)/limit*100));
 return `<div class="tile"><div class="mut">${label}</div><div class="v ${good?cls(val):""}">${fmt(val,2,true)} %</div>
 <span class="meter${val<0?" neg":""}" style="width:100%"><i style="width:${w}%"></i></span>
 <div class="mut" style="font-size:12px">limite ${fmt(limit,1)} %</div></div>`}
function viewComb(){const G=D.groupes||[];if(!G.length)return `<div class="empty">Pas de stratégie combinée dans ce paper trading.<br>
 Lancez le Directeur (menu, option D) puis le paper trading de la stratégie combinée (option C).</div>`;
 const P=D.profil;
 return G.map(g=>{const st=P?(g.ftmo.startsWith("ÉCHOUÉ")?`<span class="tag ko">limite touchée : ${esc(g.ftmo.toLowerCase())}</span>`:'<span class="tag ok">compte actif</span>'):g.ftmo==="RÉUSSI"?'<span class="tag ok">challenge réussi</span>':g.ftmo.startsWith("ÉCHOUÉ")?`<span class="tag ko">${esc(g.ftmo.toLowerCase())}</span>`:'<span class="tag run">challenge en cours</span>';
  const r=g.regles||{};
  return `<h3 style="margin:6px 0 8px;font-size:16px">${esc(g.nom)} ${st} <button class="botbtn" data-groupe="${esc(g.nom)}">Créer le bot MT5 de cette stratégie combinée</button></h3>
  <p class="note">Un seul compte de ${fmt(g.capital,0)} $ partagé par ${g.composants.length} composants · perte possible max
  ${r.budget_jour==null?"—":fmt(r.budget_jour,1)+" %"} par jour · positions max ${r.max_positions??"illimité"} · marchés corrélés dans le même sens max ${r.max_correles??"illimité"} ·
  ${r.frein?`frein de bonne journée : ${esc(r.frein)}${r.frein_actif?' <b>(ACTIF aujourd\'hui)</b>':""} ·`:""}
  ${g.refuses} signaux refusés par les règles de risque</p>
  <div class="tiles">
   <div class="tile"><div class="mut">Équité</div><div class="v">${fmt(g.equite,2)} $</div><div class="mut" style="font-size:12px">solde ${fmt(g.solde,2)} · latent ${fmt(g.latent,2,true)}</div></div>
   ${g.reel?`<div class="tile" style="border-color:var(--accent)"><div class="mut">VRAI compte MT5 (bot) — c'est lui qui compte</div><div class="v">${fmt(g.reel.profit_pct,2,true)} %</div><div class="mut" style="font-size:12px">solde ${fmt(g.reel.solde,2)} $ · aujourd'hui ${fmt(g.reel.jour_pct,2,true)} % · écart avec le paper ${fmt(g.reel.ecart_paper,2,true)} $ · objectif ${fmt(g.objectif_requis_pct,2)} %</div></div>`:""}
   ${P?`<div class="tile"><div class="mut">Rendement depuis le départ</div><div class="v ${cls(g.profit_pct)}">${fmt(g.profit_pct,2,true)} %</div><div class="mut" style="font-size:12px">${esc(P.nom)} · ${P.composer?"intérêts composés":"risque sur le capital de départ"}</div></div>`:gauge(g.profit_pct,D.ftmo.target1,"Objectif FTMO",true)}
   ${gauge(g.jour_pct,r.budget_jour??D.ftmo.max_daily,"Aujourd'hui",true)}
   ${gauge(-g.risque_ouvert_pct,r.budget_jour??D.ftmo.max_daily,"Risque ouvert (si tous les stops sautent)",false)}
   ${gauge(g.pire_jour_pct,D.ftmo.max_daily,"Pire journée",true)}
   ${gauge(-g.dd_max,D.ftmo.max_total,"Drawdown max",false)}
  </div>
  <div class="tiles"><div class="tile"><div class="mut">Trades</div><div class="v">${g.trades}</div><div class="mut" style="font-size:12px">${g.trades?fmt(g.gagnants/g.trades*100,0)+" % gagnants":""}</div></div>
   <div class="tile"><div class="mut">Trades par mois</div><div class="v">${g.trades_mois_direct==null?"—":"~"+fmt(g.trades_mois_direct,0)}</div><div class="mut" style="font-size:12px">${g.trades_mois_direct==null?"rythme en direct après 3 jours":"rythme en direct"}${g.trades_mois_attendus!=null?" · attendu ~"+fmt(g.trades_mois_attendus,0)+" (recherche)":""}</div></div>
   <div class="tile"><div class="mut">R total</div><div class="v">${rr(g.r_total)}</div></div>
   <div class="tile"><div class="mut">P&L réalisé</div><div class="v">${money(g.pnl)}</div></div>
   <div class="tile"><div class="mut">Jours tradés</div><div class="v">${g.jours_trades}</div><div class="mut" style="font-size:12px">minimum ${D.ftmo.min_days}</div></div></div>`+
  table("comp",[["Marché","symbole"],["TF","tf"],["Heures","horaire"],["Stratégie","strategie"],["Réglage","risque"],["Risque/trade","risque_pct",v=>fmt(v,2)+" %",1],
   ["Trades","trades",null,1],["Trades / mois","trades_mois",v=>v==null?"—":"~"+fmt(v,0),1],["Réussite","gagnants",(v,x)=>x.trades?fmt(v/x.trades*100,0)+" %":"—",1],["R total","r_total",rr,1],
   ["R moyen","r_moyen",rr,1],["Attendu","attendu_r",v=>v==null?"—":rr(v),1],["Contrôle","en_pause",pauseCell],
   ["","en_position",v=>v?'<span class="tag run">en position</span>':""]],g.composants)}).join("<hr style='border:0;border-top:1px solid var(--border);margin:18px 0'>")}
function viewLog(){return table("log",[["Heure","t"],["Type","type",v=>`<span class="tag">${esc(v)}</span>`],["Marché","symbole"],["TF","tf"],["Détail","texte"]],filt(D.evenements))}
function render(){if(!D)return;tiles();if(D.profil){const t="Plateforme — "+D.profil.nom+" ("+fmt(D.profil.capital,0)+" $)";const h=document.querySelector("h1");if(h.textContent!==t){h.textContent=t;document.title=t}}document.querySelectorAll(".tabs button").forEach(b=>b.classList.toggle("on",b.dataset.k===tab));
 drawTabs();document.querySelector(".filters").style.display=["home","bots","gen","bt2","hours"].includes(tab)?"none":"";
 const v={home:viewHome,bots:viewBots,gen:()=>viewTop2("gen"),hours:()=>viewTop2("hours"),comb:viewComb,top:viewTop,bt2:()=>viewTop2("bt2"),at:viewAT,an:viewAn,mk:viewMk,pos:viewPos,hist:viewHist,strat:viewStrat,rr:viewRR,ftmo:viewFtmo,log:viewLog}[tab]||viewPos;
 const el=document.getElementById("view");
 // garde la position de défilement (haut/bas ET gauche/droite) de chaque tableau à chaque mise à jour
 const keep=[...el.querySelectorAll(".scroll")].map(x=>[x.scrollTop,x.scrollLeft]),wy=window.scrollY;
 const html=v();if(tab===lastTab&&html===lastHtml){return}lastHtml=html;
 el.innerHTML=html;postRender(el);
 if(tab===lastTab)el.querySelectorAll(".scroll").forEach((x,i)=>{if(keep[i]){x.scrollTop=keep[i][0];x.scrollLeft=keep[i][1]}});
 lastTab=tab;window.scrollTo(0,wy)}
let lastTab=null,lastHtml=null;
// AIDE (bulles sur les en-têtes) et colonnes TECHNIQUES (cachées en mode simple) ; étiquettes pour le téléphone
const HELP={"R moyen":"Gain moyen par trade, en multiples du risque (1R = la perte si le stop est touché)","R total":"Somme des R de tous les trades",
 "t":"Solidité : R moyen ÷ écart-type × racine du nombre de trades (au-dessus de 2 = résultat net, pas juste de la chance)","t (solidité)":"Solidité : au-dessus de 2 = résultat net",
 "Rang backtest":"Position dans le backtest des 2 ans (100 % = la meilleure)","Rang paper":"Position en paper trading (100 % = la meilleure)",
 "Échecs":"% des challenges simulés où une limite de perte est touchée","Réussite challenge":"% des challenges simulés réussis","Réussi en":"Jours de bourse attendus pour réussir (reprises comprises)",
 "DD max":"Plus grosse baisse depuis un plus haut","Pire jour":"Pire journée (positions ouvertes comptées au pire moment)","Paper / backtest":"Le paper fait combien % du backtest (100 % = pareil)",
 "Financé 100k $/jour":"Compte financé 100 000 $ à 1 %/trade : gain moyen par jour de bourse sur 1 an (médiane)","Perso 5k $/jour":"Compte perso 5 000 $ à 2 %/trade, intérêts composés : gain moyen par jour sur 1 an",
 "Trades / mois":"Nombre moyen de trades par mois","Challenges réussis / ratés":"Challenges enchaînés sur les vrais jours du backtest","Heures":"Heures où la stratégie a le droit d'entrer (heure du serveur MT5)"};
const TECH=["t","t (solidité)","t backtest","t paper","Échecs","Challenges réussis / ratés","Challenges réussis / ratés (vrais jours)","DD max","Trades","R moyen 2 ans","R moyen paper","Rang backtest","Rang paper",
 "Paper / backtest","Réglage","Origine","Challenges paper","Paper depuis","Attendu","Attendu (recherche)","Contrôle","Spread entrée","SL initial","SL final","Pips","Lots","Pips → SL","Pips → TP","Bougies","Latent R",
 "Trades gagnants","Stop ×0,75","Stop ×0,5","R moyen hors plage","Contrôle : plage / 24 h","R total plage / 24 h","Choix : trades","Contrôle : trades","Recul médian des gagnants","Avance médiane des perdants"];
function postRender(el){el.querySelectorAll("table").forEach(tb=>{const ths=[...tb.querySelectorAll("thead th, tr:first-child th")];if(!ths.length)return;
 const labs=ths.map(th=>th.textContent.trim());
 ths.forEach((th,i)=>{if(HELP[labs[i]]&&!th.title)th.title=HELP[labs[i]];if(TECH.includes(labs[i]))th.classList.add("x")});
 const techIdx=labs.map((l,i)=>TECH.includes(l)?i:-1).filter(i=>i>=0);
 tb.querySelectorAll("tbody tr").forEach(tr=>{[...tr.children].forEach((td,i)=>{if(labs[i])td.setAttribute("data-l",labs[i]);if(techIdx.includes(i))td.classList.add("x")})})})}
// PASTILLE : bonne en backtest ET en paper ?
function verdict(e){const d=e.direct||{},paperOk=(d.rendement_pct||0)>0,bt=(e.backtest&&e.backtest.tout)||{},btOk=e.conforme!==false&&(bt.rendement_pct==null||bt.rendement_pct>0);
 if(btOk&&paperOk&&(e.ratio==null||e.ratio>=50))return '<span class="badge" style="color:var(--pos)" title="Bonne en backtest ET en paper trading">🟢 solide</span>';
 if(d.trades&&!paperOk||(!btOk&&!paperOk))return '<span class="badge" style="color:var(--neg)" title="Perd en paper trading ou trop risquée en backtest">🔴 prudence</span>';
 return `<span class="badge" style="color:var(--warn)" title="${d.trades?"Bonne d'un côté seulement":"Pas encore de trade en paper"}">🟡 à surveiller</span>`}
// ---------------------------------------------------------------- ACCUEIL « Aujourd'hui »
let AUTO=null;async function loadAuto(){try{AUTO=await (await fetch("/api/auto",{cache:"no-store"})).json()}catch(e){AUTO={}}}
function recalcAll(){loadTop(true);loadTop2(true);setTimeout(render,500)}
function viewHome(){if(!T2)loadTop2(false);if(!T10)loadTop(false);if(!AUTO)loadAuto();
 const R=(T2&&T2.resultat)||{},G=R.general||[],n1=G[0],t10=((T10&&T10.resultat)||{}).top||[];
 let h=`<div class="homegrid">`;
 // 1. quel bot faire tourner
 h+=`<div class="card" style="border-color:var(--accent)"><h3>🎯 Quel bot faire tourner ?</h3>`;
 if(n1){h+=`<p class="mut" style="margin:0 0 6px">N°1 du classement général (bon en backtest 2 ans ET en paper trading)</p>
   <div style="font-size:15px;font-weight:600;margin-bottom:4px">${esc(n1.type)} ${verdict(n1)}</div>
   ${n1.composants.map(c=>`<div style="font-size:13px">${esc(c.symbole)} ${esc(c.timeframe)} ${hTag(c.horaire)||'<span class="mut">24 h/24</span>'} · <span class="mut">${esc(String(c.strategie).split(" | heures")[0].slice(0,60))}</span></div>`).join("")}
   <div class="tiles" style="margin:10px 0 6px;grid-template-columns:repeat(auto-fit,minmax(95px,1fr))">${n1.comptes&&n1.comptes.finance?`<div class="tile"><div class="mut">Financé 100k</div><div class="v">${fmt(n1.comptes.finance.gain_jour_usd,0,true)} $/j</div></div>`:""}
   ${n1.comptes&&n1.comptes.perso?`<div class="tile"><div class="mut">Perso 5k</div><div class="v">${fmt(n1.comptes.perso.gain_jour_usd,0,true)} $/j</div></div>`:""}
   <div class="tile"><div class="mut">Paper : gain / jour</div><div class="v ${cls(n1.paper_par_jour)}">${fmt(n1.paper_par_jour,2,true)} %</div></div></div>
   <p style="display:flex;gap:6px;flex-wrap:wrap;margin:6px 0 0">${botPair(n1.k,n1.rang_source,n1)} <button class="botbtn" onclick="go('gen')">Voir le classement</button></p>`}
 else if(t10.length){const c=t10[0];h+=`<p class="mut" style="margin:0 0 6px">Pas encore de classement général : voici le n°1 du TOP 10 du direct.</p>
   ${c.composants.map(x=>`<div style="font-size:13px">${esc(x.symbole)} ${esc(x.timeframe)} ${hTag(x.horaire)} · <span class="mut">${esc(String(x.strategie).slice(0,60))}</span></div>`).join("")}
   <p><button class="botbtn" data-top="${c.rang}">Créer le bot MT5</button> <button class="botbtn" onclick="go('top')">Voir le TOP 10</button></p>`}
 else h+=`<p class="note">Pas encore de classement. Cliquez sur « Tout recalculer » (ou attendez le calcul automatique de la nuit).</p>`;
 h+=`</div>`;
 // 2. mon challenge
 const g=(D.groupes||[])[0];h+=`<div class="card"><h3>📈 Mon challenge</h3>`;
 if(g){const r=g.regles||{};h+=`<p class="mut" style="margin:0 0 6px">${esc(g.nom)} · ${g.ftmo==="RÉUSSI"?'<span class="tag ok">réussi</span>':g.ftmo.startsWith("ÉCHOUÉ")?`<span class="tag ko">${esc(g.ftmo.toLowerCase())}</span>`:'<span class="tag run">en cours</span>'}</p>
   <div class="tiles">${D.profil?"":gauge(g.profit_pct,(g.objectif_requis_pct||D.ftmo.target1),"Vers l'objectif",true)}${gauge(g.jour_pct,r.budget_jour??D.ftmo.max_daily,"Aujourd'hui",true)}
   ${gauge(-g.dd_max,D.ftmo.max_total,"Baisse max",false)}<div class="tile"><div class="mut">Jours tradés</div><div class="v">${g.jours_trades}</div><div class="mut" style="font-size:12px">minimum ${D.ftmo.min_days}</div></div></div>
   ${g.reel?`<p class="note">VRAI compte MT5 : ${fmt(g.reel.profit_pct,2,true)} % (aujourd'hui ${fmt(g.reel.jour_pct,2,true)} %)</p>`:""}
   <p><button class="botbtn" onclick="go('comb')">Détails</button></p>`}
 else h+=`<p class="note">Aucune stratégie combinée ne tourne sur cette plateforme. Créez un bot depuis « Quel bot faire tourner ? » puis lancez son LANCER_BOT.bat.</p>`;
 if(D.bot)h+=`<p class="note">Bot MT5 : ${D.bot.vivant?'<span class="tag ok">actif</span>':'<span class="tag ko">SILENCIEUX</span>'} · ${D.bot.executes} ordres exécutés · ${D.bot.manques} manqués</p>`;
 h+=`</div>`;
 // 3. alertes
 const ev=(D.evenements||[]).filter(x=>["CONTRÔLE","SURVEILLANT","FTMO","REFUS"].includes(x.type)).slice(0,8);
 const al=[];if(D.bot&&!D.bot.vivant)al.push("⚠️ Le bot MT5 ne donne plus signe de vie : vérifiez MT5 et le bouton Algo Trading.");
 const paused=(D.comptes||[]).filter(x=>x.en_pause).length;if(paused)al.push(`⏸ ${paused} stratégie(s) mises en pause par le contrôleur de qualité (moins bonnes en direct que prévu).`);
 h+=`<div class="card"><h3>🔔 Alertes</h3>${al.map(a=>`<p style="margin:4px 0">${a}</p>`).join("")}${ev.length?ev.map(x=>`<div style="font-size:12.5px;margin:3px 0"><span class="mut">${esc(x.t)}</span> <span class="tag">${esc(x.type)}</span> ${esc(x.texte)}</div>`).join(""):(al.length?"":'<p class="note">Rien à signaler.</p>')}
  <p><button class="botbtn" onclick="go('log')">Tout le journal</button></p></div>`;
 // 4. calculs
 const run10=T10&&T10.etat==="en cours",run2=T2&&T2.etat==="en cours";
 h+=`<div class="card"><h3>🧮 Calculs</h3><p style="margin:3px 0">TOP 10 du direct : ${run10?"en cours…":esc(((T10&&T10.resultat)||{}).calcule_le||"jamais")}</p>
  <p style="margin:3px 0">Backtest 2 ans, heures, planning et classement général : ${run2?"en cours ("+(T2.total?Math.round(T2.fait/T2.total*100):0)+" %)…":esc(R.calcule_le||"jamais")}</p>
  <p class="note">${AUTO&&AUTO.heure!=null?`Calcul automatique chaque nuit à ${AUTO.heure} h (heure du PC)${AUTO.etat?" · "+esc(AUTO.etat):""}.`:"Calcul automatique de la nuit désactivé (variable LABO_AUTO_HEURE)."}</p>
  <p><button class="cmpbtn" ${run10||run2?"disabled":""} onclick="recalcAll()">Tout recalculer maintenant</button></p></div>`;
 return h+`</div>`}
// ---------------------------------------------------------------- MES BOTS
let BOTS=null,botsTime=0;async function loadBots(force){if(!force&&BOTS&&Date.now()-botsTime<20000)return;botsTime=Date.now();
 try{BOTS=await (await fetch("/api/bots",{cache:"no-store"})).json()}catch(e){BOTS={bots:[],message:"Liste impossible : "+e}}render()}
document.getElementById("view").addEventListener("click",async e=>{const b=e.target.closest("[data-open]");if(!b||e.target.closest(".modal"))return;
 try{const r=await (await fetch("/api/ouvrir?dossier="+encodeURIComponent(b.dataset.open))).json();showModal(r.ok?"Dossier ouvert":"Dossier",r.message)}catch(err){}});
function viewBots(){loadBots(false);if(!BOTS)return `<div class="empty">Chargement des bots…</div>`;const L=BOTS.bots||[];
 let h=`<p class="note">${esc(BOTS.message||"")} Un bot est « actif » quand son LANCER_BOT.bat tourne (son paper trading envoie les signaux au bot MT5). · <a href="#" onclick="loadBots(true);return false">actualiser</a></p>`;
 if(!L.length)return h+`<div class="empty">Pas encore de bot. Créez-en un depuis l'Accueil ou n'importe quel classement (boutons « Bot »).</div>`;
 return h+`<div class="homegrid">`+L.map(b=>{const p=b.paper;return `<div class="card" style="${b.etat==="actif"?"border-color:var(--good)":""}">
  <h3>${b.etat==="actif"?"🟢":b.etat==="arrêté"?"⚪":"⚫"} ${esc(b.nom)}</h3>
  <p class="mut" style="margin:0 0 6px">${esc(b.profil)} · créé le ${esc(b.cree_le)} · ${esc(b.etat)}${b.maj?" (dernière activité "+esc(b.maj)+")":""}</p>
  ${b.composants.map(c=>`<div style="font-size:13px">${esc(c.symbole)} ${esc(c.timeframe)} · <span class="tag">🕘 ${esc(c.horaire)}</span> · ${fmt(c.risk_pct,1)} %/trade <span class="mut">${esc(String(c.strategie).split(" | heures")[0].slice(0,50))}</span></div>`).join("")}
  ${p?`<div class="tiles" style="margin-top:8px"><div class="tile"><div class="mut">Paper : résultat</div><div class="v ${cls(p.profit_pct)}">${fmt(p.profit_pct,2,true)} %</div><div class="mut" style="font-size:12px">${fmt(p.trades,0)} trades · ${p.jours} jours · ${esc(p.statut||"")}</div></div></div>`:'<p class="note">Pas encore lancé : double-cliquez LANCER_BOT.bat dans son dossier.</p>'}
  ${b.diag?`<div class="note" style="margin-top:8px"><b>MT5 :</b> ${fmt(b.diag.signaux_24h,0)} signal(s) envoyé(s) en 24 h · ${fmt(b.diag.executes,0)} ordre(s) passé(s) · ${fmt(b.diag.refuses,0)} refusé(s) · bot ${b.diag.bot_vivant?"🟢 branché":"🔴 pas de signe de vie"}<br>${esc(b.diag.conseil)}${(b.diag.derniers_refus||[]).length?"<br><span class=mut>Derniers refus : "+b.diag.derniers_refus.map(r=>esc(r.t+" "+r.symbole+" : "+r.raison)).join(" · ")+"</span>":""}</div>`:""}
  <p style="margin:6px 0 0"><button class="botbtn" data-open="${esc(b.dossier)}">Ouvrir le dossier</button> <span class="mut" style="font-size:12px">${esc(b.dossier)}</span></p></div>`}).join("")+`</div>`}
function fillSelect(id,vals){const el=document.getElementById(id),cur=el.value,first=el.options[0].outerHTML;
 el.innerHTML=first+[...vals].sort().map(v=>`<option${v===cur?" selected":""}>${esc(v)}</option>`).join("")}
async function poll(){try{const r=await fetch("/api/etat",{cache:"no-store"});D=await r.json();
 dot.classList.remove("off");maj.textContent="en direct · "+D.maj;
 info.textContent=`${D.serveur} · capital fictif ${fmt(D.capital,0)} $ / compte · perte max ${D.risque_pct} % par trade · aucun ordre envoyé à MT5 par la plateforme`
  +(D.bot?` · Bot MT5 : ${D.bot.vivant?"actif (signe de vie "+D.bot.dernier_signe+")":"SILENCIEUX"}, ${D.bot.executes} ordres exécutés, ${D.bot.manques} manqués${D.bot.glissement_moyen_pts!=null?", glissement moyen "+D.bot.glissement_moyen_pts+" pts":""}`:"")
  +(D.telegram?" · alertes Telegram actives":"");
 prix.innerHTML=Object.entries(D.prix).map(([s,p])=>{const m=(D.meteo||{})[s]||{},tf=m.H1?"H1":Object.keys(m).find(k=>m[k]);
  const w=tf&&m[tf]?` · <span title="Météo du marché (${tf}) selon le Météorologue">${esc(m[tf])}</span>`:"";
  return `<span class="px"><b>${esc(s)}</b> ${Number(p.bid).toFixed(p.digits)} / ${Number(p.ask).toFixed(p.digits)} · spread ${p.spread}${w}</span>`}).join("");
 fillSelect("fSym",new Set([...D.comptes.map(x=>x.symbole),...Object.keys(D.prix)]));fillSelect("fTf",new Set(D.comptes.map(x=>x.tf).concat(D.positions.map(x=>x.tf))));
 render()}catch(e){dot.classList.add("off");maj.textContent="plateforme arrêtée (relancez le paper trading)"}}
const dot=document.getElementById("dot"),maj=document.getElementById("maj"),info=document.getElementById("info"),prix=document.getElementById("prix");
poll();setInterval(poll,3000);
</script></body></html>"""
