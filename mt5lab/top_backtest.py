"""TOP 10 « BACKTEST 2 ANS » des stratégies qui tradent sur la plateforme de paper trading.

1. Chaque stratégie qui a pris au moins un trade en direct (et chaque composant d'une stratégie combinée qui
   tourne) est rejouée sur les 2 DERNIÈRES ANNÉES de l'historique MT5 (mêmes coûts que la recherche).
2. TOP 10 des stratégies SEULES : classées pour passer le challenge (au plus 2 % d'échecs, puis le moins de jours
   pour réussir, puis la réussite), sur un compte avec 1 % max par trade.
3. TOP 10 des stratégies COMBINÉES : le Chef des combinaisons assemble les meilleures (un seul compte, 1 % max par
   trade, perte possible max 2,5 % par jour) ; les combinaisons déjà en place (stratégie combinée en paper,
   TOP 10 du direct) sont classées avec elles.
4. À côté de chaque backtest : ce que la stratégie (ou la combinaison) a donné EN PAPER TRADING jusqu'à maintenant.

ATTENTION : choisir le n°1 parmi des milliers de backtests favorise la chance. Une stratégie qui brille sur 2 ans
mais pas en paper trading est suspecte ; celle qui fait les deux est la plus solide.
"""
from __future__ import annotations

import pandas as pd

from .backtest import RiskConfig
from .backtest_combinee import account_report, component_trades
from .direct import _evaluator, expected_days, rank_key, strategy_table, top_combinations
from .evaluator import compute_signal, describe, signal_key
from .fiabilite import check, hurdle
from .ftmo import FtmoRules, count_challenges, daily_table, simulate, to_dt
from .horaires import MIN_TRADES as MIN_HOUR_TRADES
from .horaires import best_window, day_plan, horaire, hour_profile, hours_of, in_window
from .strategies import apply_filter

DAY_BUDGET = 2.5


def _quick(tr: pd.DataFrame, w: float, lo, hi, rules: FtmoRules, n: int = 200) -> dict:
    d = daily_table(tr.assign(w=w), w, lo, hi)
    res = simulate(d, rules, n, seed=0) if len(d) >= 15 else {}
    c = count_challenges(d, rules)
    res.update(rendement_pct=float(d["pnl"].sum()), pire_jour=float(d["worst"].min()) if len(d) else 0.0,
               par_jour=float(d["pnl"].sum()) / max(1, len(d)), reussis=c["reussis"], rates=c["rates"])
    res["jours_attendus"] = expected_days(res)
    return res


MIN_DAYS_PROJ = 120   # au moins ~6 mois de bourse dans le backtest pour projeter sur 1 an


def projections(parts: list, lo, hi, risk_pct: float = 1.0, n: int = 1000) -> dict:
    """COMBIEN ÇA FERAIT sur 1 an : compte financé FTMO (100 000 $, 1 %/trade, 2,5 %/jour, jamais -3 %/jour ni -10 %)
    et compte perso (5 000 $, 2 %/trade, 5 %/jour, intérêts composés). 1 000 années possibles tirées des journées du
    backtest (blocs de 5 jours) : gain médian, mauvais cas (1 année sur 10), risque de problème, gain moyen par jour.
    parts : [(trades, risque de ce composant pour un risque de base de risk_pct)]."""
    from .comptes import DAYS_YEAR, profile, simulate_long
    from .ftmo import apply_risk_rules
    out = {}
    for name in ("finance", "perso"):
        p = profile(name)
        k = float(p["risk_pct"]) / max(risk_pct, 1e-9)
        t = pd.concat([tr.assign(w=min(float(w) * k, float(p["risk_pct"]))) for tr, w in parts if len(tr)],
                      ignore_index=True) if parts else pd.DataFrame()
        if not len(t):
            continue
        t = t[(to_dt(t["entry_time"]) >= lo) & (to_dt(t["exit_time"]) <= hi)]
        t = apply_risk_rules(t, None, None, float(p["risk_pct"]), day_budget=float(p["day_budget"]))
        t = t[t["r"].notna()]
        d = daily_table(t, float(p["risk_pct"]), lo, hi)
        sl = simulate_long(d, p, n=n, min_days=MIN_DAYS_PROJ)
        cap = float(p["capital"])
        med = sl.get("rendement_an_median")
        if med is None or med != med:
            if len(d) < MIN_DAYS_PROJ:  # quelques semaines répétées sur 1 an = des chiffres sans aucun sens
                out["trop_court"] = {"jours": int(len(d)), "minimum": MIN_DAYS_PROJ}
            continue
        out[name] = {"capital": cap, "risque_trade": p["risk_pct"], "perte_jour_max": p["day_budget"],
                     "rendement_an_median": round(med, 1), "rendement_an_p10": round(sl["rendement_an_p10"], 1),
                     "rendement_mois_median": round(sl["rendement_mois_median"], 1),
                     "p_probleme": round(sl["p_probleme"], 1), "p_perte_an": round(sl["p_perte_an"], 1),
                     "dd_median": round(sl["dd_median"], 1),
                     "gain_an_usd": round(cap * med / 100), "gain_an_p10_usd": round(cap * sl["rendement_an_p10"] / 100),
                     "gain_mois_usd": round(cap * sl["rendement_mois_median"] / 100),
                     "gain_jour_usd": round(cap * med / 100 / DAYS_YEAR, 2)}
    return out


def live_summary(live: pd.DataFrame | None, keys: list, weights: dict, rules: FtmoRules, risk_pct: float) -> dict:
    """Ce que la stratégie (ou la combinaison) a donné EN PAPER TRADING jusqu'à maintenant."""
    if live is None or not len(live):
        return {"trades": 0}
    t = live[live["strategie_id"].isin(keys)]
    if not len(t):
        return {"trades": 0}
    t = t.assign(entry_time=to_dt(t["ouverture"]), exit_time=to_dt(t["fermeture"]))
    lo, hi = t["entry_time"].min().normalize(), t["exit_time"].max().normalize()
    n_days = len(pd.bdate_range(lo, hi))
    used = [k for k in keys if k in set(t["strategie_id"])]
    ev = _evaluator(t, rules, risk_pct, DAY_BUDGET, 10.0, lo, hi, n_days, 300)
    res = ev(used, {k: weights.get(k, risk_pct) for k in used})
    r = t["r"].to_numpy(float)
    return {"trades": int(len(t)), "r_total": round(float(r.sum()), 1), "reussite": round(float((r > 0).mean() * 100), 0),
            "rendement_pct": round(float(res["rendement_pct"]), 2), "pire_jour": round(float(res["pire_jour"]), 2),
            "reussis": int(res["reussis"]), "rates": int(res["rates"]), "jours": n_days,
            "depuis": f"{lo:%Y-%m-%d}", "composants_avec_trades": len(used), "composants": len(keys)}


def top_backtest(strategies: dict, get_data, rules: FtmoRules = FtmoRules(), risk_pct: float = 1.0,
                 years: float = 2.0, live: pd.DataFrame | None = None, extras: list | None = None,
                 max_strategies: int = 6000, n_top: int = 10, n_sim: int = 3000, min_trades: int = 10,
                 progress=None, log=print, start=None) -> dict:
    """strategies : {id: {symbole, timeframe, candidate, strategie?, en_pause?, trades_direct?}}
    get_data(symbole, timeframe, candidates) -> (df, coût), avec au moins `years` ans d'historique (un peu plus
    pour que les indicateurs soient prêts au début)."""
    extras = list(extras or [])
    strategies = dict(strategies)   # on y ajoute les variantes horaires sans toucher au dictionnaire reçu
    must = {k for x in extras for k in x.get("keys", [])}
    ids = sorted(strategies, key=lambda k: (k not in must, -(strategies[k].get("trades_direct") or 0)))
    capped = len(ids) > max_strategies
    ids = ids[:max(max_strategies, len(must))]
    groups: dict[tuple, list] = {}
    for k in ids:
        groups.setdefault((strategies[k]["symbole"], strategies[k]["timeframe"]), []).append(k)
    trades, window, errors = {}, {}, []
    total, done = len(ids), 0

    def say(phase, d, n):
        if progress:
            progress(phase, d, n)
    last_tf = None
    for gi, ((sym, tf), keys) in enumerate(sorted(groups.items(), key=lambda x: (x[0][1], x[0][0])), 1):
        if tf != last_tf and hasattr(get_data, "clear"):   # timeframe suivant : on libère les historiques d'avant
            get_data.clear()
        last_tf = tf
        say(f"historique MT5 et backtest : {sym} {tf} ({gi}/{len(groups)})", done, total)
        try:
            df, cost = get_data(sym, tf, [strategies[k]["candidate"] for k in keys])
        except Exception as exc:
            errors.append(f"{sym} {tf} : {str(exc)[:120]}")
            log(f"[backtest 2 ans] {sym} {tf} : pas de données ({exc})")
            done += len(keys)
            continue
        first = df.index[-1] - pd.DateOffset(years=years) if start is None else pd.Timestamp(start)
        mask = df.index >= first
        dfs = df[mask]
        sigs: dict = {}
        for k in keys:
            c = strategies[k]["candidate"]
            try:
                sk = (signal_key(c["signal"]), c.get("filter", "none"))
                if sk not in sigs:   # même signal avec d'autres R:R : calculé une seule fois
                    sigs[sk] = apply_filter(df, compute_signal(df, c["signal"]), c.get("filter", "none"))[mask]
                trades[k] = component_trades(dfs, cost, c, risk_pct, sig=sigs[sk])
                window[k] = (dfs.index[0], dfs.index[-1])
            except Exception as exc:
                log(f"[backtest 2 ans] {sym} {tf} {k} : {exc}")
            done += 1
            if done % 50 == 0:
                say(f"backtest des stratégies : {sym} {tf} ({gi}/{len(groups)})", done, total)
        del df, dfs, sigs
    if not trades:
        return {"ok": False, "seules": [], "combinees": [], "erreurs": errors,
                "message": "Aucun backtest possible (pas de données MT5 pour ces marchés ?)."}
    lo_all = max(w[0] for w in window.values())
    hi_all = min(w[1] for w in window.values())

    def label(k):
        s = strategies[k]
        return s.get("strategie") or describe(s["candidate"])

    def rconf(k):
        return strategies[k].get("risque") or RiskConfig(**strategies[k]["candidate"]["risk"]).label()

    # ---- MEILLEURES HEURES : chaque stratégie dans sa meilleure plage horaire devient une variante « horaire »
    say("meilleures heures de chaque stratégie", 0, 1)
    hours, live_extra = [], []
    for k in list(trades):
        tr = trades[k]
        if len(tr) < MIN_HOUR_TRADES:
            continue
        try:
            bw = best_window(tr["entry_time"], tr["r"])
        except Exception:
            continue
        if not bw:   # pas de plage nettement meilleure : on montre quand même son profil heure par heure
            hours.append({"strategie_id": k, "symbole": strategies[k]["symbole"],
                          "timeframe": strategies[k]["timeframe"], "strategie": label(k), "risque": rconf(k),
                          "ok": None, "nom": "24 h/24", "debut": None, "fin": None,
                          "trades_total": int(len(tr)), "r_moyen_24h": round(float(tr["r"].mean()), 3),
                          "r_total_24h": round(float(tr["r"].sum()), 1),
                          "profil": hour_profile(tr["entry_time"], tr["r"])})
            continue
        a, b = bw["debut"], bw["fin"]
        row = {"strategie_id": k, "symbole": strategies[k]["symbole"], "timeframe": strategies[k]["timeframe"],
               "strategie": label(k), "risque": rconf(k), **bw}
        lt = live[live["strategie_id"] == k] if live is not None and len(live) else None
        if lt is not None and len(lt):
            m = in_window(hours_of(lt["ouverture"]), a, b)
            rr_ = pd.to_numeric(lt["r"], errors="coerce").to_numpy(float)
            row["paper_plage"] = {"trades": int(m.sum()), "r_moyen": round(float(rr_[m].mean()), 3) if m.any() else None}
            row["paper_hors"] = {"trades": int((~m).sum()),
                                 "r_moyen": round(float(rr_[~m].mean()), 3) if (~m).any() else None}
            if bw["ok"] and m.any():
                live_extra.append(lt[m].assign(strategie_id=f"{k}@{a:g}-{b:g}"))
        hours.append(row)
        if not bw["ok"]:
            continue
        v = f"{k}@{a:g}-{b:g}"
        trades[v] = tr[in_window(hours_of(tr["entry_time"]), a, b)].reset_index(drop=True)
        window[v] = window[k]
        strategies[v] = {**strategies[k], "horaire": horaire(a, b), "base_id": k,
                         "strategie": f"{label(k)} | heures {bw['nom']}"}
    if live_extra:
        live = pd.concat([live, *live_extra], ignore_index=True)
    # ---- PLANNING DE LA JOURNÉE : la meilleure de toutes les stratégies à chaque heure -> une combinée « planning »
    say("planning de la journée (la meilleure stratégie à chaque heure)", 0, 1)
    wins_ok = {h["strategie_id"]: (h["debut"], h["fin"], h["t_choix"]) for h in hours if h.get("ok") is True}
    items = {k: (tr["entry_time"], tr["r"]) for k, tr in trades.items() if "@" not in k and len(tr) >= 20}
    plan = day_plan(items, wins_ok or None) if items else None
    plan_keys = []
    if plan:
        for b in plan["blocs"]:
            k = b["cle"]
            b.update(symbole=strategies[k]["symbole"], timeframe=strategies[k]["timeframe"], strategie=label(k))
            v = f"{k}@{b['debut']:g}-{b['fin']:g}"
            b["strategie_id"] = v
            if v not in trades:
                trades[v] = trades[k][in_window(hours_of(trades[k]["entry_time"]), b["debut"], b["fin"])].reset_index(drop=True)
                window[v] = window[k]
                strategies[v] = {**strategies[k], "horaire": horaire(b["debut"], b["fin"]), "base_id": k,
                                 "strategie": f"{label(k)} | heures {b['nom']}"}
                lt = live[live["strategie_id"] == k] if live is not None and len(live) else None
                if lt is not None and len(lt):
                    m = in_window(hours_of(lt["ouverture"]), b["debut"], b["fin"])
                    if m.any():
                        live = pd.concat([live, lt[m].assign(strategie_id=v)], ignore_index=True)
            if b["ok"] and len(trades[v]):
                plan_keys.append(v)
        for x in plan["heures"]:
            if x.get("cle"):
                x["etiquette"] = f"{strategies[x['cle']]['symbole']} {strategies[x['cle']]['timeframe']}"
        if plan_keys:
            for w in sorted({risk_pct, min(0.5, risk_pct)}, reverse=True):
                extras.append({"nom": f"Planning de la journée ({w:g} %/trade)".replace(".", ","), "keys": plan_keys,
                               "weights": {k: w for k in plan_keys}})
    hours.sort(key=lambda x: (x["ok"] is not True, x["ok"] is None,
                              -((x.get("r_moyen_plage") or 0) - (x.get("r_moyen_24h") or 0)), -x.get("r_total_24h", 0)))

    # ---- stratégies seules : tri rapide, puis rapport complet des meilleures
    say("classement des stratégies seules", 0, 1)
    quick = []
    for k, tr in trades.items():
        if len(tr) >= min_trades and tr["r"].sum() > 0:
            quick.append((k, _quick(tr, risk_pct, *window[k], rules)))
    quick.sort(key=lambda x: rank_key(x[1], DAY_BUDGET))
    singles = []
    for k, _ in quick[:2 * n_top]:
        rows = [{"symbole": strategies[k]["symbole"], "timeframe": strategies[k]["timeframe"], "strategie": label(k),
                 "risk_pct": risk_pct, "trades": 0, "r_total": 0.0, "erreur": None,
                 "debut": f"{window[k][0]:%Y-%m-%d}", "fin": f"{window[k][1]:%Y-%m-%d}"}]
        rep = account_report([trades[k].assign(w=risk_pct, comp=0)], rows, *window[k], rules,
                             {"day_budget": DAY_BUDGET}, risk_pct, n_sim)
        if rep.get("ok"):
            singles.append((k, rep))
    singles.sort(key=lambda x: rank_key(x[1]["tout"], DAY_BUDGET))
    out_s = []
    for i, (k, rep) in enumerate(singles[:n_top], 1):
        out_s.append({"rang": i, "nom": f"N°{i} des stratégies seules (backtest {years:g} ans)",
                      "conforme": not rank_key(rep["tout"], DAY_BUDGET)[0],
                      "composants": [{"strategie_id": k, "symbole": strategies[k]["symbole"],
                                      "timeframe": strategies[k]["timeframe"], "strategie": label(k),
                                      "risque_config": rconf(k), "risk_pct": risk_pct,
                                      "en_pause": bool(strategies[k].get("en_pause")),
                                      "horaire": strategies[k].get("horaire"),
                                      "base_id": strategies[k].get("base_id", k)}],
                      "backtest": rep, "direct": live_summary(live, [k], {k: risk_pct}, rules, risk_pct),
                      "comptes": projections([(trades[k], risk_pct)], *window[k], risk_pct)})

    # ---- stratégies combinées : le Chef des combinaisons sur les trades du backtest
    # le Chef des combinaisons part des meilleures de TOUTES les stratégies testées : les 40 meilleures toutes
    # catégories + les 25 meilleures variantes « horaires » (chacune dans ses heures), pour qu'elles soient toujours
    # en lice, + les combinaisons déjà en place
    pool = ([k for k, _ in quick[:40]] + [k for k, _ in quick if "@" in k][:25]
            + [k for k in must if k in trades] + plan_keys)
    rows = []
    for k in dict.fromkeys(pool):
        tr = trades[k]
        if not len(tr):
            continue
        rows.append(pd.DataFrame({"strategie_id": k, "symbole": strategies[k]["symbole"],
                                  "timeframe": strategies[k]["timeframe"], "strategie": label(k), "risque": rconf(k),
                                  "ouverture": tr["entry_time"].to_numpy(), "fermeture": tr["exit_time"].to_numpy(),
                                  "r": tr["r"].to_numpy(float)}))
    out_c, msg_c = [], ""

    def finish(e):
        """Une combinaison du Chef : backtest détaillé, projections sur 1 an, paper trading de chaque composant."""
        keys = [c["strategie_id"] for c in e["composants"]]
        weights = {c["strategie_id"]: c["risk_pct"] for c in e["composants"]}
        parts, crow = [], []
        for c in e["composants"]:
            k = c["strategie_id"]
            c.pop("candidate", None)
            c["trades_bt"], c["r_total_bt"] = c.pop("trades_direct", 0), c.pop("r_total_direct", 0.0)
            c["en_pause"] = bool(strategies[k].get("en_pause"))
            lv = live_summary(live, [k], {k: c["risk_pct"]}, rules, risk_pct)
            c["direct_trades"], c["direct_r"] = lv.get("trades", 0), lv.get("r_total")
            parts.append(trades[k].assign(w=c["risk_pct"], comp=len(crow)))
            crow.append({"symbole": c["symbole"], "timeframe": c["timeframe"], "strategie": c["strategie"],
                         "risk_pct": c["risk_pct"], "trades": 0, "r_total": 0.0, "erreur": None,
                         "debut": f"{window[k][0]:%Y-%m-%d}", "fin": f"{window[k][1]:%Y-%m-%d}"})
        lo = max(window[k][0] for k in keys)
        hi = min(window[k][1] for k in keys)
        e["backtest"] = account_report(parts, crow, lo, hi, rules, e["regles"], risk_pct, n_sim)
        e["comptes"] = projections([(trades[k], weights[k]) for k in keys], lo, hi, risk_pct)
        e["direct"] = live_summary(live, keys, weights, rules, risk_pct)
        e["ratio"] = _ratio(e["direct"].get("rendement_pct"), e["direct"].get("jours"),
                            (e["backtest"].get("tout") or {}).get("rendement_pct"), 252 * years)
        e.pop("resultat", None)
        return e
    if rows:
        frame = pd.concat(rows, ignore_index=True)
        frame = frame[(to_dt(frame["ouverture"]) >= lo_all) & (to_dt(frame["fermeture"]) <= hi_all)]
        ex = [x for x in extras if all(k in trades for k in x["keys"])]
        res = top_combinations(frame, strategies, rules, risk_pct, DAY_BUDGET, min_trades=min_trades, n_top=n_top,
                               n_seeds=8, n_cand=25, n_sim=1500, n_quick=150, extras=ex,
                               label=f"backtest {years:g} ans", live=False,
                               progress=lambda d, n: say("Chef des combinaisons", d, n))
        msg_c = res.get("message", "")
        for e in res.get("top", []):
            out_c.append(finish(e))
        say("Chef des combinaisons", 1, 1)
    say("classement croisé backtest × paper trading", 0, 1)
    cross = cross_ranking(trades, window, live, strategies, risk_pct, rules, label, rconf, n=n_top,
                          min_bt=min_trades, n_sim=min(n_sim, 1500))
    # ---- TOP 10 des COMBINÉES « backtest × paper » : faites avec les stratégies bonnes PARTOUT (24 h/24 ou dans
    # leurs meilleures heures), puis classées sur les DEUX : rang du backtest et rang du paper trading
    say("combinées backtest × paper trading", 0, 1)
    out_x = []
    ids = [k for k in cross.get("partout_ids", []) if k in trades and len(trades[k])]
    if len(ids) >= 2:
        rows_x = [pd.DataFrame({"strategie_id": k, "symbole": strategies[k]["symbole"],
                                "timeframe": strategies[k]["timeframe"], "strategie": label(k), "risque": rconf(k),
                                "ouverture": trades[k]["entry_time"].to_numpy(), "fermeture": trades[k]["exit_time"].to_numpy(),
                                "r": trades[k]["r"].to_numpy(float)}) for k in ids]
        fx = pd.concat(rows_x, ignore_index=True)
        fx = fx[(to_dt(fx["ouverture"]) >= lo_all) & (to_dt(fx["fermeture"]) <= hi_all)]
        rx = top_combinations(fx, strategies, rules, risk_pct, DAY_BUDGET, min_trades=min_trades, n_top=3 * n_top,
                              n_seeds=8, n_cand=min(25, len(ids)), n_sim=1500, n_quick=150,
                              label="backtest × paper", live=False,
                              progress=lambda d, n: say("combinées backtest × paper trading", d, n))
        cands = [e for e in rx.get("top", []) if not e.get("hors_top")]
        for e in cands:
            keys = [c["strategie_id"] for c in e["composants"]]
            w = {c["strategie_id"]: c["risk_pct"] for c in e["composants"]}
            e["_paper"] = live_summary(live, keys, w, rules, risk_pct)
        ok = [e for e in cands if e["_paper"].get("trades")]
        if ok:
            pb = pd.Series([-e["rang"] for e in ok]).rank(pct=True).to_numpy()          # rang du backtest
            pp = pd.Series([e["_paper"].get("rendement_pct") or 0.0 for e in ok]).rank(pct=True).to_numpy()
            for e, a, b in zip(ok, pb, pp):
                e["rang_bt"], e["rang_paper"] = round(float(a) * 100), round(float(b) * 100)
            ok.sort(key=lambda e: (-min(e["rang_bt"], e["rang_paper"]), -(e["rang_bt"] + e["rang_paper"])))
            for i, e in enumerate(ok[:n_top], 1):
                e.pop("_paper", None)
                e["rang_chef"], e["rang"] = e["rang"], i
                e["nom"] = f"N°{i} des combinées backtest × paper"
                out_x.append(finish(e))
    say("combinées backtest × paper trading", 1, 1)
    for e in out_s:
        k = e["composants"][0]["strategie_id"]
        e["ratio"] = next((x["ratio"] for lst in cross["listes"].values() for x in lst
                           if x["strategie_id"] == k), cross["ratios"].get(k))
    if plan:
        plan["combinees"] = [{"nom": e["origine"], "rang": e["rang"], "hors_top": e.get("hors_top", False)}
                             for e in out_c if str(e.get("origine", "")).startswith("Planning de la journée")]
    # ---- le meilleur pour le COMPTE PERSO 5 000 $ (et le compte financé) : gain sur 1 an avec peu de risque
    say("projections sur 1 an (compte perso 5 000 $, compte financé)", 0, 1)
    pool_p = [("c", e) for e in out_c] + [("x", e) for e in out_x]
    for k, rep in singles:
        e = next((x for x in out_s if x["composants"][0]["strategie_id"] == k), None)
        if e is None:
            e = {"rang": None, "nom": label(k), "composants": [{"strategie_id": k, "symbole": strategies[k]["symbole"],
                 "timeframe": strategies[k]["timeframe"], "strategie": label(k), "risque_config": rconf(k),
                 "risk_pct": risk_pct, "horaire": strategies[k].get("horaire"),
                 "base_id": strategies[k].get("base_id", k)}],
                 "backtest": rep, "direct": live_summary(live, [k], {k: risk_pct}, rules, risk_pct),
                 "comptes": projections([(trades[k], risk_pct)], *window[k], risk_pct)}
        pool_p.append(("s", e))
    perso = []
    for kind, e in pool_p:
        pr = (e.get("comptes") or {}).get("perso")
        if not pr:
            continue
        perso.append({"type": {"c": "combinée", "x": "combinée backtest × paper"}.get(kind, "seule"), "k": kind, "rang_source": e.get("rang"),
                      "nom": e.get("nom"), "origine": e.get("origine", ""), "composants": e["composants"],
                      "comptes": e["comptes"], "direct": e.get("direct"), "backtest": e.get("backtest"),
                      "sur": pr["p_probleme"] <= 5.0})
    # ---- CLASSEMENT GÉNÉRAL : seules ET combinées (24 h/24 ou dans leurs heures), classées sur le backtest ET le
    # paper trading : rang du backtest (challenge : échecs, jours pour réussir, réussite) et rang du paper (gain par
    # jour en direct) ; le meilleur des deux = le plus petit des deux rangs le plus haut
    general, seen = [], set()
    for kind, e in pool_p:
        sig = frozenset(c["strategie_id"] for c in e["composants"])
        bt = (e.get("backtest") or {}).get("tout")
        d = e.get("direct") or {}
        if sig in seen or not bt or not d.get("trades"):
            continue
        seen.add(sig)
        general.append({"type": {"c": "combinée", "x": "combinée backtest × paper"}.get(kind, "seule"), "k": kind,
                        "rang_source": e.get("rang"), "nom": e.get("nom"), "origine": e.get("origine", ""),
                        "composants": e["composants"], "backtest": e["backtest"], "direct": d,
                        "comptes": e.get("comptes"), "ratio": e.get("ratio"),
                        "conforme": not rank_key(bt, DAY_BUDGET)[0],
                        "_bt": rank_key(bt, DAY_BUDGET), "_pp": (d.get("rendement_pct") or 0.0) / max(1, d.get("jours") or 1)})
    if general:
        order = sorted(range(len(general)), key=lambda i: general[i]["_bt"])
        n = len(general)
        for pos, i in enumerate(order):
            general[i]["rang_bt"] = round(100 * (n - pos) / n)
        pp = pd.Series([g["_pp"] for g in general]).rank(pct=True).to_numpy()
        for g, v in zip(general, pp):
            g["rang_paper"] = round(float(v) * 100)
            g["paper_par_jour"] = round(g.pop("_pp"), 3)
            g.pop("_bt")
        general.sort(key=lambda g: (-min(g["rang_bt"], g["rang_paper"]), -(g["rang_bt"] + g["rang_paper"])))
        for i, g in enumerate(general, 1):
            g["rang"] = i
        general = general[:20]
    # ---- FIABILITÉ : on ne garde des chiffres de gain que pour ce qu'on peut croire (voir fiabilite.py)
    say("contrôle de fiabilité (plus que la chance, période récente, paper)", 0, 1)
    n_tested = len(trades)

    def entry_trades(e):
        keys = [c["strategie_id"] for c in e["composants"] if c["strategie_id"] in trades]
        if not keys:
            return None, None, None
        parts = [trades[k].assign(w=float(c["risk_pct"])) for c in e["composants"] for k in [c["strategie_id"]]
                 if k in trades]
        lo = max(window[k][0] for k in keys)
        hi = min(window[k][1] for k in keys)
        t = pd.concat(parts, ignore_index=True)
        t = t[(to_dt(t["entry_time"]) >= lo) & (to_dt(t["entry_time"]) <= hi)]
        return t, lo, hi

    def judge(e):
        t, lo, hi = entry_trades(e)
        fia = check(t, lo, hi, n_tested, risk_pct, e.get("direct")) if t is not None else \
            {"fiable": False, "raison": "pas de trades", "controles": []}
        e["fiabilite"] = fia
        e["comptes"] = realistic_accounts(fia)
        return e
    for lst in (out_s, out_c, out_x, general):
        for e in lst:
            judge(e)
    perso = [judge(x) for x in perso]
    perso = [x for x in perso if (x.get("comptes") or {}).get("perso")]
    for x in perso:
        x["sur"] = (x["fiabilite"]["realiste"]["baisse_typique_pct"] or 0) <= 25
    perso.sort(key=lambda x: (not x["sur"], -x["comptes"]["perso"]["gain_mois_usd"]))
    for i, x in enumerate(perso[:n_top], 1):
        x["rang"] = i
    perso = perso[:n_top]
    # LES RÉALISTES : chaque stratégie testée (24 h/24 ou dans ses heures) passée au contrôle complet
    judged = []
    for k, tr in trades.items():
        if len(tr) < 30:
            continue
        lv = live_summary(live, [k], {k: risk_pct}, rules, risk_pct)
        fia = check(tr.assign(w=risk_pct), *window[k], n_tested, risk_pct, lv)
        judged.append((k, fia, lv))
    good = sorted([x for x in judged if x[1]["fiable"]], key=lambda x: -x[1]["realiste"]["pct_mois"])
    near = sorted([x for x in judged if not x[1]["fiable"] and x[1].get("echecs") == 1], key=lambda x: -x[1].get("t", 0))

    def single_entry(i, k, fia, lv, tag):
        rows = [{"symbole": strategies[k]["symbole"], "timeframe": strategies[k]["timeframe"], "strategie": label(k),
                 "risk_pct": risk_pct, "trades": 0, "r_total": 0.0, "erreur": None,
                 "debut": f"{window[k][0]:%Y-%m-%d}", "fin": f"{window[k][1]:%Y-%m-%d}"}]
        rep = account_report([trades[k].assign(w=risk_pct, comp=0)], rows, *window[k], rules,
                             {"day_budget": DAY_BUDGET}, risk_pct, min(n_sim, 1500))
        return {"rang": i, "nom": f"{tag} n°{i}", "conforme": True,
                "composants": [{"strategie_id": k, "symbole": strategies[k]["symbole"],
                                "timeframe": strategies[k]["timeframe"], "strategie": label(k),
                                "risque_config": rconf(k), "risk_pct": risk_pct,
                                "en_pause": bool(strategies[k].get("en_pause")),
                                "horaire": strategies[k].get("horaire"), "base_id": strategies[k].get("base_id", k)}],
                "backtest": rep, "direct": lv, "fiabilite": fia, "comptes": realistic_accounts(fia)}
    real_s = [single_entry(i, k, f, lv, "Réaliste") for i, (k, f, lv) in enumerate(good[:n_top], 1)]
    near_s = [single_entry(i, k, f, lv, "Presque") for i, (k, f, lv) in enumerate(near[:n_top], 1)]
    real_c = []
    ids = [k for k, _, _ in good[:30]]
    if len(ids) >= 2:
        say("combinées réalistes (seulement des stratégies fiables)", 0, 1)
        fr = pd.concat([pd.DataFrame({"strategie_id": k, "symbole": strategies[k]["symbole"],
                                      "timeframe": strategies[k]["timeframe"], "strategie": label(k),
                                      "risque": rconf(k), "ouverture": trades[k]["entry_time"].to_numpy(),
                                      "fermeture": trades[k]["exit_time"].to_numpy(),
                                      "r": trades[k]["r"].to_numpy(float)}) for k in ids], ignore_index=True)
        fr = fr[(to_dt(fr["ouverture"]) >= lo_all) & (to_dt(fr["fermeture"]) <= hi_all)]
        rr_ = top_combinations(fr, strategies, rules, risk_pct, DAY_BUDGET, min_trades=min_trades, n_top=n_top,
                               n_seeds=6, n_cand=min(20, len(ids)), n_sim=1000, n_quick=100,
                               label="réalistes", live=False)
        for e in rr_.get("top", []):
            if e.get("hors_top"):
                continue
            e = judge(finish(e))
            if e["fiabilite"]["fiable"]:
                e["rang"] = len(real_c) + 1
                e["nom"] = f"Combinée réaliste n°{e['rang']}"
                real_c.append(e)
    n_bad = sum(1 for _, f, _ in judged if not f["fiable"])
    realistes = {"seules": real_s, "combinees": real_c, "presque": near_s, "testees": len(judged),
                 "hurdle": hurdle(n_tested),
                 "message": (f"{len(good)} stratégie(s) FIABLE(S) sur {len(judged)} testées ({n_bad} écartées : surtout de la "
                             f"chance, trop peu d'historique ou une seule bonne période)." if good else
                             f"AUCUNE stratégie fiable sur {len(judged)} testées : les « meilleures » des autres classements "
                             f"sont surtout de la chance. Il faut plus d'historique (M15, H1, H4 sur plusieurs années) et "
                             f"plus de paper trading avant de mettre de l'argent.")}
    n_ok = sum(1 for x in out_s if x["conforme"])
    n_base = sum(1 for k in trades if "@" not in k)
    n_var = len(trades) - n_base
    msg = (f"{n_base} stratégies du direct rejouées sur les {years:g} dernières années"
           + (f" + {n_var} variantes « meilleures heures » confirmées" if n_var else "")
           + (f" (les {max_strategies} plus actives)" if capped else "")
           + f" : {len(quick)} gagnantes avec au moins {min_trades} trades, {n_ok} des 10 meilleures seules "
             f"respectent la limite d'échecs. {msg_c}")
    if errors:
        msg += f" Marchés sans données : {'; '.join(errors[:5])}."
    return {"ok": True, "seules": out_s, "combinees": out_c, "strategies_testees": n_base, "variantes_horaires": n_var,
            "gagnantes": len(quick), "plafond": capped, "croise": cross, "planning": plan, "combinees_croisees": out_x, "general": general, "heures": hours[:300], "perso": perso, "annees": years, "erreurs": errors,
            "periode": f"{lo_all:%Y-%m-%d} → {hi_all:%Y-%m-%d}", "regles": rules.label(), "message": msg,
            "realistes": realistes}



def realistic_accounts(fia: dict) -> dict:
    """Gains par compte à partir du GAIN RÉALISTE (période de contrôle, divisé par 2, sans intérêts composés), ou
    rien du tout quand la stratégie n'est pas fiable : on n'affiche plus de chiffres auxquels on ne peut pas croire."""
    r = fia.get("realiste") if fia.get("fiable") else None
    if not r:
        return {"non_fiable": fia.get("raison") or "pas fiable"}
    out = {"realiste": True}
    for name, cap, usd_mois in (("finance", 100_000, r["finance_usd_mois"]), ("perso", 5_000, r["perso_usd_mois"])):
        out[name] = {"capital": cap, "gain_jour_usd": round(usd_mois / 21, 2), "gain_mois_usd": usd_mois,
                     "gain_an_usd": usd_mois * 12, "rendement_an_median": round(usd_mois * 12 / cap * 100, 1),
                     "dd_median": r["baisse_typique_pct"] * (2 if name == "perso" else 1), "realiste": True}
    return out


def _ratio(live_pct, live_days, bt_pct, bt_days):
    """Le paper trading fait combien % du backtest (gain par jour) ? None si on ne peut pas comparer."""
    if not live_days or live_pct is None or not bt_pct or bt_pct <= 0:
        return None
    return round(float(live_pct / live_days) / float(bt_pct / bt_days) * 100, 0)


def _tstat(r) -> float:
    r = pd.Series(r, dtype=float)
    sd = r.std(ddof=1) if len(r) > 1 else 0.0
    return round(float(r.mean() / sd * (len(r) ** 0.5)), 2) if sd and sd > 0 else 0.0


def cross_ranking(trades: dict, window: dict, live: pd.DataFrame | None, strategies: dict, risk_pct: float,
                  rules: FtmoRules, label, rconf, n: int = 10, min_bt: int = 10, min_live: int = 5,
                  n_sim: int = 1500) -> dict:
    """CLASSEMENT CROISÉ backtest × paper trading des stratégies seules.

    Pour chaque stratégie : solidité t (R moyen / écart-type x racine du nombre de trades : tient compte du nombre
    de trades) sur le backtest et sur le paper, puis son RANG en % parmi toutes (100 % = la meilleure).
      - « bonnes partout »           : le plus petit des deux rangs est le plus haut (bonne en backtest ET en paper)
      - « bonnes en paper seulement » : rang paper bien au-dessus du rang backtest
      - « bonnes en backtest seulement » : rang backtest bien au-dessus du rang paper (le backtest ne se confirme pas)
    Ratio : R moyen du paper / R moyen du backtest (100 % = le paper fait exactement comme le backtest)."""
    out = {"listes": {"partout": [], "paper": [], "backtest": []}, "ratios": {}, "min_paper": min_live,
           "comparees": 0}
    if live is None or not len(live):
        out["message"] = "Pas encore de trades en paper trading à comparer."
        return out
    tab = strategy_table(live[live["strategie_id"].isin(trades.keys())], 1)
    if not len(tab):
        out["message"] = "Aucune stratégie backtestée n'a encore tradé en paper."
        return out
    rows = []
    for r in tab.to_dict("records"):
        k = r["strategie_id"]
        tr = trades.get(k)
        if tr is None or len(tr) < min_bt:
            continue
        rb = tr["r"].to_numpy(float)
        bt = {"trades": int(len(rb)), "r_moyen": round(float(rb.mean()), 3), "r_total": round(float(rb.sum()), 1),
              "t": _tstat(rb), "gain_pct": round(float(rb.sum() * risk_pct), 1),
              "reussite": round(float((rb > 0).mean() * 100), 0)}
        pp = {"trades": int(r["trades"]), "r_moyen": r["r_moyen"], "r_total": r["r_total"], "t": r["t"],
              "gain_pct": round(float(r["r_total"] * risk_pct), 1), "reussite": r["reussite_pct"],
              "jours": r["jours"]}
        ratio = round(pp["r_moyen"] / bt["r_moyen"] * 100, 0) if bt["r_moyen"] > 0 else None
        out["ratios"][k] = ratio
        if pp["trades"] >= min_live:
            rows.append({"strategie_id": k, "bt": bt, "paper": pp, "ratio": ratio})
    out["comparees"] = len(rows)
    if not rows:
        out["message"] = f"Aucune stratégie n'a encore {min_live} trades en paper ET {min_bt} trades dans le backtest."
        return out
    pb = pd.Series([x["bt"]["t"] for x in rows]).rank(pct=True).to_numpy()
    pl = pd.Series([x["paper"]["t"] for x in rows]).rank(pct=True).to_numpy()
    for x, a, b in zip(rows, pb, pl):
        x["rang_bt"], x["rang_paper"] = round(float(a) * 100, 0), round(float(b) * 100, 0)
    lists = {
        "partout": sorted([x for x in rows if x["bt"]["r_moyen"] > 0 and x["paper"]["r_moyen"] > 0],
                          key=lambda x: (-min(x["rang_bt"], x["rang_paper"]), -(x["rang_bt"] + x["rang_paper"]))),
        "paper": sorted([x for x in rows if x["paper"]["r_moyen"] > 0 and x["rang_paper"] - x["rang_bt"] >= 25],
                        key=lambda x: (-(x["rang_paper"] - x["rang_bt"]), -x["rang_paper"])),
        "backtest": sorted([x for x in rows if x["bt"]["r_moyen"] > 0 and x["rang_bt"] - x["rang_paper"] >= 25],
                           key=lambda x: (-(x["rang_bt"] - x["rang_paper"]), -x["rang_bt"])),
    }
    for name, lst in lists.items():
        for i, x in enumerate(lst[:n], 1):
            x = dict(x)   # une stratégie peut être dans deux listes : chacune son rang
            k = x["strategie_id"]
            st = strategies[k]
            x.update(rang=i, symbole=st["symbole"], timeframe=st["timeframe"], strategie=label(k),
                     risque_config=rconf(k), en_pause=bool(st.get("en_pause")),
                     score=min(x["rang_bt"], x["rang_paper"]) if name == "partout" else
                     abs(x["rang_paper"] - x["rang_bt"]))
            rows_ = [{"symbole": st["symbole"], "timeframe": st["timeframe"], "strategie": x["strategie"],
                      "risk_pct": risk_pct, "trades": 0, "r_total": 0.0, "erreur": None,
                      "debut": f"{window[k][0]:%Y-%m-%d}", "fin": f"{window[k][1]:%Y-%m-%d}"}]
            x["backtest"] = account_report([trades[k].assign(w=risk_pct, comp=0)], rows_, *window[k], rules,
                                           {"day_budget": DAY_BUDGET}, risk_pct, n_sim)
            x["direct"] = live_summary(live, [k], {k: risk_pct}, rules, risk_pct)
            out["listes"][name].append(x)
    out["partout_ids"] = [x["strategie_id"] for x in lists["partout"]][:40]
    out["message"] = (f"{len(rows)} stratégies comparées (au moins {min_live} trades en paper et {min_bt} dans le "
                      f"backtest) : {len(lists['partout'])} gagnantes partout, {len(lists['paper'])} bien meilleures "
                      f"en paper qu'en backtest, {len(lists['backtest'])} bien meilleures en backtest qu'en paper.")
    return out
