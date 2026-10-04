"""Plateforme web locale : tous les trades fictifs en direct (http://localhost:8765).

Le serveur n'écoute que sur 127.0.0.1 (accessible uniquement depuis votre PC). La page interroge /api/etat
toutes les 3 secondes : positions ouvertes (entrée, SL, TP, prix, latent), historique complet des trades,
classement des stratégies, comparaison des R:R, suivi des challenges FTMO et journal des événements.
"""
from __future__ import annotations

import json
import threading
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
        elif path == "/api/top2ans":  # TOP 10 backtest 2 ans des stratégies qui tradent en direct
            try:
                body = json.dumps(top2ans_live(self.server.engine, start="lancer=1" in self.path),
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


YEARS_BT = 2.0   # backtest des 2 dernières années


def top2ans_live(engine, start: bool = False) -> dict:
    """Onglet « TOP 10 backtest 2 ans » : chaque stratégie qui a tradé en direct (et chaque stratégie combinée
    qui tourne) rejouée sur les 2 dernières années de MT5, TOP 10 seules et combinées, avec le paper trading."""
    import time as _time
    job = getattr(engine, "_top2_job", None)
    if start and not (job and job["etat"] == "en cours"):
        job = {"etat": "en cours", "fait": 0, "total": 0, "debut": _time.strftime("%H:%M:%S"), "resultat": None,
               "message": "Préparation…", "etape": "préparation"}
        engine._top2_job = job
        threading.Thread(target=_run_top2ans, args=(engine, job), daemon=True).start()
    if job is None:
        try:
            res = json.loads((engine.out / "top_backtest_2ans.json").read_text(encoding="utf-8"))
            return {"etat": "fini", "fait": 1, "total": 1, "resultat": res,
                    "message": f"Dernier calcul enregistré ({res.get('calcule_le', '')}). Recliquez pour le refaire."}
        except (OSError, ValueError):
            return {"etat": "jamais", "message": "Cliquez sur le bouton pour backtester les stratégies du direct."}
    return job


def _run_top2ans(engine, job):
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
        get = _platform_data(engine, YEARS_BT + 0.25)   # 3 mois de plus : indicateurs prêts au début
        res = top_backtest(strategies, get, rules, risk, YEARS_BT, live=t, extras=extras, progress=progress,
                           log=print)
        res["calcule_le"] = _time.strftime("%Y-%m-%d %H:%M")
        try:
            (engine.out / "top_backtest_2ans.json").write_text(json.dumps(res, ensure_ascii=False, default=str),
                                                               encoding="utf-8")
        except OSError:
            pass
        job.update(etat="fini" if res.get("ok") else "erreur", resultat=res, message=res["message"])
    except Exception as exc:
        job.update(etat="erreur", message=f"Calcul impossible : {exc}")


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
    if source == "bt2":
        top = (top2ans_live(engine).get("resultat") or {}).get("combinees") or []
    else:
        top = (top10_live(engine).get("resultat") or {}).get("top") or []
    found = [e for e in top if str(e.get("rang")) == str(rank)]
    if not found:
        return None
    comb = dict(found[0])
    comb["composants"] = [{**c, "candidate": c.get("candidate") or engine.slots[c["strategie_id"]].candidate}
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
    if q.get("top") or q.get("bt2"):  # une combinaison d'un TOP 10 (règles du challenge, même sur un compte perso)
        comb = _top_comb(engine, q.get("top") or q["bt2"], "direct" if q.get("top") else "bt2")
        if comb is None:
            return {"ok": False, "message": "Recompilez d'abord le TOP 10 (bouton de l'onglet)."}
        comb.pop("resultat", None)
        from .ftmo import FtmoRules
        ftmo = FtmoRules() if getattr(engine, "profile", None) else engine.ftmo
        capital = 100_000.0 if getattr(engine, "profile", None) else next(iter(engine.slots.values())).capital
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
        s = engine.slots[q["id"]]
        risk = s.risk_pct or engine.risk_pct
        comb = single_strategy(s.candidate, s.symbol, s.timeframe, risk)
        capital = s.capital
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
</style></head>
<body>
<header><div class="row"><h1>Plateforme paper trading</h1><span class="live"><span class="dot" id="dot"></span><span id="maj">connexion…</span></span>
<span class="mut" id="info"></span></div><div class="row" id="prix" style="margin-top:6px"></div></header>
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
const TABS=[["comb","Stratégie combinée"],["pos","Positions ouvertes"],["hist","Historique des trades"],["strat","Classement des stratégies"],
["top","TOP 10 combinées du direct"],["bt2","TOP 10 backtest 2 ans"],["an","Meilleurs setups du direct"],["mk","Meilleur bot par marché"],["rr","Meilleur R:R"],["ftmo","Challenges FTMO"],["log","Journal en direct"]];
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
  const comps=c.composants.map(x=>`<div title="${esc(x.strategie)}">${miniBot(x.strategie_id)}${esc(x.symbole)} ${esc(x.timeframe)} · ${esc(String(x.strategie).slice(0,55))} <span class="mut">(${fmt(x.risk_pct,1)} %/trade, ${x.trades_direct} trades, ${fmt(x.r_total_direct,1,true)}R)</span></div>`).join("");
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
let T2=null,t2Busy=false,t2Timer=null,SEL2=null;
async function loadTop2(start){if(t2Busy)return;t2Busy=true;
 try{T2=await (await fetch("/api/top2ans"+(start?"?lancer=1":""),{cache:"no-store"})).json()}catch(e){T2={etat:"erreur",message:"Calcul impossible : "+e}}
 t2Busy=false;clearTimeout(t2Timer);if(T2.etat==="en cours")t2Timer=setTimeout(()=>loadTop2(false),2000);render()}
document.getElementById("view").addEventListener("click",e=>{const b=e.target.closest(".v2btn");if(!b)return;e.stopPropagation();
 SEL2={k:b.dataset.k,r:+b.dataset.r};render();setTimeout(()=>{const p=document.querySelector(".panel");if(p)p.scrollIntoView({behavior:"smooth",block:"start"})},100)});
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
const PAPH=`<th>Trades paper</th><th>Gain paper</th><th>Challenges paper</th><th>Paper depuis</th>`;
function stateTag(e){return (e.conforme?'<span class="tag ok">conforme</span>':'<span class="tag ko">trop risquée</span>')+(e.hors_top?' <span class="tag">hors TOP 10</span>':"")}
function viewTop2(){if(!T2){loadTop2(false);return `<div class="empty">Chargement…</div>`}
 const run=T2.etat==="en cours",R=T2.resultat||{},C=R.combinees||[],S=R.seules||[];
 const pct=T2.total?Math.round(T2.fait/T2.total*100):0;
 let h=`<p style="margin:4px 0 10px"><button class="cmpbtn" ${run?"disabled":""} onclick="loadTop2(true)">
  ${run?"Backtest en cours…":"Backtester toutes les stratégies du direct sur les 2 dernières années → TOP 10"}</button>
  ${run?` <span class="meter" style="width:220px"><i style="width:${pct}%"></i></span> ${pct} % · ${esc(T2.etape||"")} <span class="mut">(démarré à ${esc(T2.debut||"")} ; de quelques minutes à une heure selon le nombre de stratégies)</span>`:""}</p>
  <p class="note">${esc(T2.message||"")}${R.periode?` · période ${esc(R.periode)} · ${esc(R.regles||"")}`:""}${R.calcule_le?" · calculé le "+esc(R.calcule_le):""}</p>
  <p class="note">Chaque stratégie qui a pris au moins un trade sur cette plateforme (et chaque stratégie combinée qui tourne) est rejouée sur les
  <b>2 dernières années</b> de MT5 (spread, commission, glissement, swaps, nouvelles), sur un compte avec 1 % max par trade et 2,5 % de perte possible max par jour.
  À côté : ce qu'elle a donné <b>en paper trading</b> jusqu'à maintenant. <b>À lire honnêtement :</b> le n°1 parmi des milliers de backtests est en partie chanceux ;
  celle qui est bonne sur 2 ans <b>ET</b> en paper trading est la plus solide.</p>`;
 if(SEL2){const L=SEL2.k==="c"?C:S,e=L.find(x=>x.rang===SEL2.r);
  if(e&&e.backtest){const B=e.backtest,title=SEL2.k==="c"?`${esc(e.nom)}${e.origine&&e.origine!=="Chef des combinaisons"?" · "+esc(e.origine):""}`:`${esc(e.nom)} : ${esc(e.composants[0].symbole)} ${esc(e.composants[0].timeframe)}`;
   h+=`<div class="panel"><div class="row" style="justify-content:space-between"><h3 style="margin:0">Backtest 2 ans — ${title}</h3>
    <span>${SEL2.k==="c"?`<button class="botbtn" data-bt2="${e.rang}">Créer le bot MT5 de cette combinaison</button>`:`<button class="botbtn" data-id="${esc(e.composants[0].strategie_id)}">Créer le bot MT5</button>`}
    <a href="#" onclick="SEL2=null;render();return false" class="mut" style="margin-left:10px">fermer</a></span></div>`+
    (B.ok?btBody(B,{cols:["2 dernières années","Période récente (8 derniers mois)"],extra:liveBox(e.direct,SEL2.k!=="c"),
     honest:"Backtest des 2 dernières années. Une partie de cette période a pu servir à la recherche des stratégies, et le classement choisit les meilleures parmi beaucoup : comparez toujours avec le paper trading ci-dessous."}):`<p class="neg">${esc(B.message||"")}</p>`)+`</div>`}}
 if(!C.length&&!S.length)return h+(run?"":`<div class="empty">Pas encore de TOP 10 backtest.</div>`);
 const comps=e=>e.composants.map(x=>`<div title="${esc(x.strategie)}">${miniBot(x.strategie_id)}${esc(x.symbole)} ${esc(x.timeframe)} · ${esc(String(x.strategie).slice(0,50))}
  <span class="mut">(${fmt(x.risk_pct,1)} %/trade · 2 ans : ${fmt(x.trades_bt,0)} trades ${fmt(x.r_total_bt,1,true)}R · paper : ${x.direct_trades?fmt(x.direct_trades,0)+" trades "+fmt(x.direct_r,1,true)+"R":"pas encore"})</span>${x.en_pause?' <span class="tag ko">en pause</span>':""}</div>`).join("");
 h+=`<h3 class="sec">TOP 10 des stratégies COMBINÉES (backtest 2 ans)</h3>`+(C.length?`<div class="scroll"><table><thead><tr><th>#</th><th>État</th><th>Backtest / bot</th><th>Stratégies de la combinaison (bot de chacune)</th><th>Origine</th>${BT2H}${PAPH}</tr></thead><tbody>`+
  C.map(e=>`<tr><td class="n"><b>${e.rang}</b></td><td>${stateTag(e)}</td><td><button class="v2btn btbtn" data-k="c" data-r="${e.rang}">Voir le backtest</button> <button class="botbtn" data-bt2="${e.rang}">Bot MT5 combinée</button></td>
   <td style="font-size:12px;line-height:1.5">${comps(e)}</td><td>${esc(e.origine||"")}</td>${bt2Cells(e.backtest)}${paperCells(e.direct)}</tr>`).join("")+`</tbody></table></div>`:`<p class="note">Pas de combinaison.</p>`);
 h+=`<h3 class="sec">TOP 10 des stratégies SEULES (backtest 2 ans)</h3>`+(S.length?`<div class="scroll"><table><thead><tr><th>#</th><th>État</th><th>Backtest / bot</th><th>Marché</th><th>TF</th><th>Stratégie</th><th>Réglage</th>${BT2H}${PAPH}</tr></thead><tbody>`+
  S.map(e=>{const x=e.composants[0];return `<tr><td class="n"><b>${e.rang}</b></td><td>${stateTag(e)}${x.en_pause?' <span class="tag ko">en pause</span>':""}</td>
   <td><button class="v2btn btbtn" data-k="s" data-r="${e.rang}">Voir le backtest</button> <button class="botbtn" data-id="${esc(x.strategie_id)}">Bot MT5</button></td>
   <td>${esc(x.symbole)}</td><td>${esc(x.timeframe)}</td><td class="s" title="${esc(x.strategie)}">${esc(x.strategie)}</td><td class="s" title="${esc(x.risque_config)}">${esc(x.risque_config)}</td>${bt2Cells(e.backtest)}${paperCells(e.direct)}</tr>`}).join("")+`</tbody></table></div>`:`<p class="note">Pas de stratégie seule.</p>`);
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
let tab=localStorageGet("tab")||"comb",D=null,sortState={};
function localStorageGet(k){try{return localStorage.getItem(k)}catch(e){return null}}
function localStorageSet(k,v){try{localStorage.setItem(k,v)}catch(e){}}
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt=(v,d=2,sign=false)=>v==null||v===""||isNaN(v)?"—":(sign&&v>0?"+":"")+Number(v).toLocaleString("fr-FR",{minimumFractionDigits:d,maximumFractionDigits:d});
const cls=v=>v>0?"pos":v<0?"neg":"";
function px(v,r){if(v==null||v==="")return "sur signal";const d=(D.prix[r.symbole]||{}).digits;
 return d==null?esc(v):Number(v).toFixed(d)}
function money(v){return `<span class="${cls(v)}">${fmt(v,2,true)} $</span>`}
function rr(v){return `<span class="${cls(v)}">${fmt(v,2,true)}R</span>`}
document.getElementById("tabs").innerHTML=TABS.map(([k,l])=>`<button data-k="${k}">${l}</button>`).join("");
document.getElementById("tabs").onclick=e=>{const k=e.target.dataset.k;if(k){tab=k;localStorageSet("tab",k);render()}};
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
 e.stopPropagation();b.disabled=true;const q=b.dataset.bt2?`bt2=${encodeURIComponent(b.dataset.bt2)}`:b.dataset.top?`top=${encodeURIComponent(b.dataset.top)}`:b.dataset.analyse?"analyse=1":b.dataset.groupe?`groupe=${encodeURIComponent(b.dataset.groupe)}`:`id=${encodeURIComponent(b.dataset.id)}`;
 try{const r=await (await fetch("/api/bot?"+q,{cache:"no-store"})).json();alert(r.message)}catch(err){alert("Erreur : "+err)}b.disabled=false});
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
  table("comp",[["Marché","symbole"],["TF","tf"],["Stratégie","strategie"],["Réglage","risque"],["Risque/trade","risque_pct",v=>fmt(v,2)+" %",1],
   ["Trades","trades",null,1],["Trades / mois","trades_mois",v=>v==null?"—":"~"+fmt(v,0),1],["Réussite","gagnants",(v,x)=>x.trades?fmt(v/x.trades*100,0)+" %":"—",1],["R total","r_total",rr,1],
   ["R moyen","r_moyen",rr,1],["Attendu","attendu_r",v=>v==null?"—":rr(v),1],["Contrôle","en_pause",pauseCell],
   ["","en_position",v=>v?'<span class="tag run">en position</span>':""]],g.composants)}).join("<hr style='border:0;border-top:1px solid var(--border);margin:18px 0'>")}
function viewLog(){return table("log",[["Heure","t"],["Type","type",v=>`<span class="tag">${esc(v)}</span>`],["Marché","symbole"],["TF","tf"],["Détail","texte"]],filt(D.evenements))}
function render(){if(!D)return;tiles();if(D.profil){const t="Plateforme — "+D.profil.nom+" ("+fmt(D.profil.capital,0)+" $)";const h=document.querySelector("h1");if(h.textContent!==t){h.textContent=t;document.title=t}}document.querySelectorAll(".tabs button").forEach(b=>b.classList.toggle("on",b.dataset.k===tab));
 const v={comb:viewComb,top:viewTop,bt2:viewTop2,an:viewAn,mk:viewMk,pos:viewPos,hist:viewHist,strat:viewStrat,rr:viewRR,ftmo:viewFtmo,log:viewLog}[tab]||viewPos;
 const el=document.getElementById("view");
 // garde la position de défilement (haut/bas ET gauche/droite) de chaque tableau à chaque mise à jour
 const keep=[...el.querySelectorAll(".scroll")].map(x=>[x.scrollTop,x.scrollLeft]),wy=window.scrollY;
 const html=v();if(tab===lastTab&&html===lastHtml){return}lastHtml=html;
 el.innerHTML=html;
 if(tab===lastTab)el.querySelectorAll(".scroll").forEach((x,i)=>{if(keep[i]){x.scrollTop=keep[i][0];x.scrollLeft=keep[i][1]}});
 lastTab=tab;window.scrollTo(0,wy)}
let lastTab=null,lastHtml=null;
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
