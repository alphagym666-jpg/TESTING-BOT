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
from .ftmo import FtmoRules, count_challenges, daily_table, simulate, to_dt
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
                 progress=None, log=print) -> dict:
    """strategies : {id: {symbole, timeframe, candidate, strategie?, en_pause?, trades_direct?}}
    get_data(symbole, timeframe, candidates) -> (df, coût), avec au moins `years` ans d'historique (un peu plus
    pour que les indicateurs soient prêts au début)."""
    extras = extras or []
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
        start = df.index[-1] - pd.DateOffset(years=years)
        mask = df.index >= start
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
                                      "en_pause": bool(strategies[k].get("en_pause"))}],
                      "backtest": rep, "direct": live_summary(live, [k], {k: risk_pct}, rules, risk_pct)})

    # ---- stratégies combinées : le Chef des combinaisons sur les trades du backtest
    pool = [k for k, _ in quick[:40]] + [k for k in must if k in trades]
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
    if rows:
        frame = pd.concat(rows, ignore_index=True)
        frame = frame[(to_dt(frame["ouverture"]) >= lo_all) & (to_dt(frame["fermeture"]) <= hi_all)]
        ex = [x for x in extras if all(k in trades for k in x["keys"])]
        res = top_combinations(frame, strategies, rules, risk_pct, DAY_BUDGET, min_trades=min_trades, n_top=n_top,
                               n_seeds=8, n_cand=15, n_sim=1500, n_quick=150, extras=ex,
                               label=f"backtest {years:g} ans", live=False,
                               progress=lambda d, n: say("Chef des combinaisons", d, n))
        msg_c = res.get("message", "")
        for e in res.get("top", []):
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
            e["direct"] = live_summary(live, keys, weights, rules, risk_pct)
            e.pop("resultat", None)
            out_c.append(e)
        say("Chef des combinaisons", 1, 1)
    say("classement croisé backtest × paper trading", 0, 1)
    cross = cross_ranking(trades, window, live, strategies, risk_pct, rules, label, rconf, n=n_top,
                          min_bt=min_trades, n_sim=min(n_sim, 1500))
    for e in out_c:   # combinaisons : le paper confirme-t-il le backtest ? (gain par jour)
        e["ratio"] = _ratio(e["direct"].get("rendement_pct"), e["direct"].get("jours"),
                            (e["backtest"].get("tout") or {}).get("rendement_pct"), 252 * years)
    for e in out_s:
        k = e["composants"][0]["strategie_id"]
        e["ratio"] = next((x["ratio"] for lst in cross["listes"].values() for x in lst
                           if x["strategie_id"] == k), cross["ratios"].get(k))
    n_ok = sum(1 for x in out_s if x["conforme"])
    msg = (f"{len(trades)} stratégies du direct rejouées sur les {years:g} dernières années"
           + (f" (les {max_strategies} plus actives)" if capped else "")
           + f" : {len(quick)} gagnantes avec au moins {min_trades} trades, {n_ok} des 10 meilleures seules "
             f"respectent la limite d'échecs. {msg_c}")
    if errors:
        msg += f" Marchés sans données : {'; '.join(errors[:5])}."
    return {"ok": True, "seules": out_s, "combinees": out_c, "strategies_testees": len(trades),
            "gagnantes": len(quick), "plafond": capped, "croise": cross, "annees": years, "erreurs": errors,
            "periode": f"{lo_all:%Y-%m-%d} → {hi_all:%Y-%m-%d}", "regles": rules.label(), "message": msg}



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
    out["message"] = (f"{len(rows)} stratégies comparées (au moins {min_live} trades en paper et {min_bt} dans le "
                      f"backtest) : {len(lists['partout'])} gagnantes partout, {len(lists['paper'])} bien meilleures "
                      f"en paper qu'en backtest, {len(lists['backtest'])} bien meilleures en backtest qu'en paper.")
    return out
