"""LES GESTIONNAIRES DE COMPTE : d'autres objectifs que le challenge FTMO, avec les MÊMES stratégies validées.

- « perso »   : compte personnel (5 000 $ par défaut), pas de règle de prop firm. But : le meilleur rendement à LONG
                TERME (intérêts composés : le risque suit le solde) sans jamais perdre une grosse partie du compte.
                Contrainte : au plus 5 % de chances de subir une baisse de 25 % depuis le plus haut en un an.
                Garde-fous : perte possible max 5 %/jour, trading arrêté à -30 % du capital de départ.
- « finance » : compte financé APRÈS le challenge (100 000 $). But : le meilleur rendement par mois, sans jamais
                perdre plus de 3 % dans une journée ni 10 % au total (le compte serait perdu).
                Contrainte : au plus 5 % de chances de toucher une de ces limites en un an.

Les stratégies viennent de la recherche habituelle (agents, chefs, génies) ; le gestionnaire choisit la combinaison,
le risque de chaque composant et les règles (arrêt journalier, positions max, marchés corrélés, frein de bonne
journée) pour SON objectif, en simulant 2 000 années possibles à partir des journées hors-échantillon.
"""
from __future__ import annotations

import copy
import json
import math
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from .ftmo import FtmoRules
from .manager import Director, _fmt, _per_month

PROFILES = {
    "perso": {"nom": "Compte perso", "capital": 5_000.0, "risk_pct": 2.0, "day_budget": 5.0, "total_budget": 30.0,
              "dd_limit": 25.0, "day_limit": None, "max_fail": 5.0, "compound": True,
              "but": "meilleur rendement à long terme (intérêts composés) sans grosse baisse du compte"},
    "finance": {"nom": "Compte financé", "capital": 100_000.0, "risk_pct": 1.0, "day_budget": 2.5,
                "total_budget": 10.0, "dd_limit": None, "day_limit": 3.0, "max_fail": 5.0, "compound": False,
                "but": "meilleur rendement par mois sans jamais perdre 3 % dans une journée ni 10 % au total"},
}
DAYS_YEAR, DAYS_MONTH = 252, 21


def profile(name: str, **over) -> dict:
    p = dict(PROFILES[name])
    p.update({k: v for k, v in over.items() if v is not None})
    p["cle"] = name
    return p


def ftmo_like(p: dict) -> FtmoRules:
    """Règles de suivi pour le paper trading et le bot : aucun objectif (on ne s'arrête jamais de gagner)."""
    if p["compound"]:  # compte perso : seulement l'arrêt à -total_budget %
        return FtmoRules(target1=0.0, max_daily=100.0, max_total=float(p["total_budget"]), min_days=0, best_day_pct=0)
    return FtmoRules(target1=0.0, max_daily=float(p["day_limit"]), max_total=float(p["total_budget"]), min_days=0,
                     best_day_pct=0)


def simulate_long(daily: pd.DataFrame, p: dict, n: int = 2000, days: int = DAYS_YEAR, seed: int = 0,
                  block: int = 5) -> dict:
    """Simule n années possibles en tirant au hasard des blocs de 5 journées réelles (hors-échantillon).

    Compte perso (composé) : le risque suit le solde ; « problème » = baisse de dd_limit % depuis le plus haut.
    Compte financé : risque sur le capital de départ ; « problème » = perte du jour >= day_limit % ou perte totale
    >= total_budget % -> compte perdu, l'année s'arrête là."""
    out = {"p_probleme": float("nan"), "rendement_an_median": float("nan"), "rendement_an_p10": float("nan"),
           "rendement_mois_median": float("nan"), "dd_median": float("nan"), "p_perte_an": float("nan"),
           "jours": int(len(daily))}
    if daily is None or len(daily) < 20:
        return out
    pnl = daily["pnl"].to_numpy(float)
    worst = daily["worst"].to_numpy(float)
    rng = np.random.default_rng(seed)
    nb = math.ceil(days / block)
    starts = rng.integers(0, max(1, len(pnl) - block + 1), size=(n, nb))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n, -1)[:, :days]
    P, W = pnl[idx] / 100, worst[idx] / 100
    stop_level = 1 - float(p["total_budget"]) / 100
    if p["compound"]:
        growth = np.cumprod(1 + P, axis=1)
        prev = np.concatenate([np.ones((n, 1)), growth[:, :-1]], axis=1)
        low = prev * (1 + W)
        peak = np.maximum.accumulate(np.concatenate([np.ones((n, 1)), growth], axis=1), axis=1)[:, :-1]
        dd = np.max(1 - low / peak, axis=1)
        stopped = (low <= stop_level).any(axis=1)
        first = np.where((low <= stop_level).any(axis=1), (low <= stop_level).argmax(axis=1), days - 1)
        final = np.where(stopped, low[np.arange(n), first], growth[:, -1])
        month = growth[:, DAYS_MONTH - 1] if days >= DAYS_MONTH else growth[:, -1]
        prob = (dd >= float(p["dd_limit"]) / 100) | stopped
    else:
        eq = np.cumsum(P, axis=1)
        prev = np.concatenate([np.zeros((n, 1)), eq[:, :-1]], axis=1)
        bad = (W <= -float(p["day_limit"]) / 100) | (prev + W <= -float(p["total_budget"]) / 100)
        prob = bad.any(axis=1)
        first = np.where(prob, bad.argmax(axis=1), days - 1)
        final = 1 + np.where(prob, prev[np.arange(n), first] + W[np.arange(n), first], eq[:, -1])
        low = 1 + prev + W
        peak = np.maximum.accumulate(np.concatenate([np.ones((n, 1)), 1 + eq], axis=1), axis=1)[:, :-1]
        dd = np.max(1 - low / peak, axis=1)
        m = min(DAYS_MONTH, days) - 1
        fm = np.minimum(first, m)
        month = np.where(prob & (first <= m), 1 + prev[np.arange(n), fm] + W[np.arange(n), fm], 1 + eq[:, m])
    out.update(p_probleme=float(prob.mean() * 100), rendement_an_median=float(np.median(final - 1) * 100),
               rendement_an_p10=float(np.percentile(final - 1, 10) * 100),
               rendement_mois_median=float(np.median(month - 1) * 100), dd_median=float(np.median(dd) * 100),
               p_perte_an=float((final < 1).mean() * 100))
    return out


class AccountManager(Director):
    """Le Directeur, mais avec l'objectif d'un compte perso ou d'un compte financé."""

    def __init__(self, cfg, get_data, prof: dict, log=print):
        r = float(prof["risk_pct"])
        levels = tuple(sorted({*cfg.risk_levels, *(x for x in (1.25, 1.5, 1.75, 2.0, 2.5, 3.0) if x <= r), r}))
        cfg = replace(cfg, risk_pct=r, risk_levels=levels, day_budget=float(prof["day_budget"]),
                      total_budget=float(prof["total_budget"]), max_fail=float(prof["max_fail"]),
                      ftmo=ftmo_like(prof), capital=float(prof["capital"]))
        super().__init__(cfg, get_data, log)
        self.prof = prof

    def say(self, msg: str):
        super().say(f"[{self.prof['nom']}] {msg}")

    def _eval(self, keys, weights, day_stop, max_open, trades, windows, n=1500, max_corr=None, pilot=None,
              day_lock=None):
        got = self._daily(keys, weights, day_stop, max_open, trades, windows, max_corr, day_lock)
        if got is None:
            return None
        daily, merged, lo, hi = got
        res = simulate_long(daily, self.prof, n=max(1000, n), seed=0)
        res.update(fenetre=(str(lo.date()), str(hi.date())), trades=int(len(merged)),
                   trades_mois=_per_month(len(merged), lo, hi),
                   pire_jour=float(daily["worst"].min()) if len(daily) else 0.0,
                   rendement_pct=float(daily["pnl"].sum()) if len(daily) else 0.0,
                   ftmo_pass=res["rendement_an_median"])  # compatibilité avec le classement des composants
        return res

    def _better(self, a, b) -> bool:
        """1. chances de « problème » sous le seuil ; 2. meilleur rendement annuel médian ; 3. moins de problèmes."""
        if a is None or not np.isfinite(a.get("rendement_an_median", float("nan"))):
            return False
        if b is None:
            return True
        lim = self.cfg.max_fail
        a_ok, b_ok = a["p_probleme"] <= lim, b["p_probleme"] <= lim
        if a_ok != b_ok:
            return a_ok
        if not a_ok:  # aucun des deux n'est assez sûr : le plus sûr gagne
            return a["p_probleme"] < b["p_probleme"] - 0.2
        ra, rb = a["rendement_an_median"], b["rendement_an_median"]
        if ra > rb + max(0.3, abs(rb) * 0.02):
            return True
        if ra < rb - max(0.3, abs(rb) * 0.02):
            return False
        return a["p_probleme"] < b["p_probleme"] - 0.2

    def _summary(self, res) -> str:
        what = "baisse de -{:g} %".format(self.prof["dd_limit"]) if self.prof["compound"] else \
            "limite touchée (-{:g} %/jour ou -{:g} %)".format(self.prof["day_limit"], self.prof["total_budget"])
        return (f"rendement médian {_fmt(res['rendement_an_median'])} %/an, {_fmt(res['rendement_mois_median'], '{:.2f}')} "
                f"%/mois, mauvaise année (1 sur 10) {_fmt(res['rendement_an_p10'])} %, {what} : "
                f"{_fmt(res['p_probleme'])} % de chances")

    def _tune_pilot(self, keys, weights, rules, best, trades, windows, info, say):
        return best, rules, weights  # le pilote de risque est fait pour le challenge

    def build(self, allr: pd.DataFrame) -> dict:
        comb = self.build_combined(allr)
        if not comb:
            return {}
        p = self.prof
        comb.update(nom=f"{p['nom']} : stratégie combinée", profil=p["cle"], capital=p["capital"],
                    composer=bool(p["compound"]), but=p["but"],
                    ftmo_regles=f"{p['nom']} : {p['but']}", horaire={"nom": "24h/24", "debut": None, "fin": None,
                                                                   "decalage_serveur": self.cfg.server_offset})
        if getattr(self, "trial_mode", False):
            comb["essai"] = True
        path = self.cfg.out / f"strategie_combinee_{p['cle']}.json"
        path.write_text(json.dumps(comb, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        write_account_report(self, comb)
        return comb


def run_accounts(director: Director, allr: pd.DataFrame | None = None, names=("perso", "finance"),
                 overrides: dict | None = None) -> dict:
    """Construit la stratégie de chaque compte avec les résultats de recherche déjà faits."""
    allr = director.review() if allr is None else allr
    out = {}
    for name in names:
        prof = profile(name, **((overrides or {}).get(name, {})))
        m = AccountManager(director.cfg, director.get_data, prof, director._print)
        m._data, m.journal = director._data, director.journal   # mêmes données en mémoire, même journal
        m.say(f"===== {prof['nom']} ({prof['capital']:,.0f} $) : {prof['but']} =====".replace(",", " "))
        comb = m.build(allr) if len(allr) else {}
        if not comb:
            m.say("pas de combinaison possible pour l'instant (pas assez de stratégies validées avec des trades).")
        out[name] = comb
    return out


def write_account_report(m: AccountManager, comb: dict) -> Path:
    import html as _h

    from .boutons import button, script, single, slim
    from .manager import CSS
    p, r = m.prof, comb["resultat"]
    money = lambda v: f"{v:,.0f}".replace(",", " ")
    cap = float(p["capital"])
    rows = "".join(
        f"<tr><td>{_h.escape(c['symbole'])}</td><td>{c['timeframe']}</td><td>{_h.escape(c['strategie'])}</td>"
        f"<td>{_h.escape(c['risque_config'])}</td><td>{c['risk_pct']:g} %</td>"
        f"<td>{button(single(c['candidate'], c['symbole'], c['timeframe'], c['risk_pct']))}</td></tr>"
        for c in comb["composants"])
    rg = comb["regles"]
    from .ftmo import lock_text
    limit = (f"baisse de {p['dd_limit']:g} % depuis le plus haut (ou arrêt à -{p['total_budget']:g} %)" if p["compound"]
             else f"perte de {p['day_limit']:g} % dans une journée ou {p['total_budget']:g} % au total (compte perdu)")
    body = f"""<h1>{_h.escape(p['nom'])} — {money(cap)} $</h1>
<p class="mut">But : {_h.escape(p['but'])}. Construit le {comb.get('cree_le', '')} avec les stratégies validées de la
recherche, simulé sur 2 000 années possibles à partir des journées hors-échantillon ({r['fenetre'][0]} → {r['fenetre'][1]}).
{"<b>À L'ESSAI : stratégies non validées, paper trading seulement.</b>" if comb.get("essai") else ""}</p>
<div class="cards">
<div class="card"><b>Rendement médian par an</b><br>{_fmt(r['rendement_an_median'])} % (≈ {money(cap * r['rendement_an_median'] / 100)} $)</div>
<div class="card"><b>Par mois (médian)</b><br>{_fmt(r['rendement_mois_median'], '{:.2f}')} % (≈ {money(cap * r['rendement_mois_median'] / 100)} $)</div>
<div class="card"><b>Mauvaise année (1 sur 10)</b><br>{_fmt(r['rendement_an_p10'])} %</div>
<div class="card"><b>Année perdante</b><br>{_fmt(r['p_perte_an'])} % de chances</div>
<div class="card"><b>Baisse max typique</b><br>{_fmt(r['dd_median'])} %</div>
<div class="card"><b>Trades par mois (environ)</b><br>{_fmt(r.get('trades_mois'), '{:.0f}')}</div>
<div class="card"><b>Chances de {limit}</b><br>{_fmt(r['p_probleme'])} % sur un an</div>
</div>
<h2>Règles</h2><p>Perte possible max {rg.get('day_budget')} %/jour · arrêt journalier {rg.get('day_stop') or 'aucun'} ·
positions max {rg.get('max_open') or 'illimité'} · marchés corrélés dans le même sens max {rg.get('max_correles') or 'illimité'} ·
frein de bonne journée : {lock_text(rg.get('frein'))} · {"risque calculé sur le SOLDE (intérêts composés)" if p['compound'] else "risque calculé sur le capital de départ"}</p>
<p>{button(slim(comb), "Créer le bot MT5 de ce compte")}</p>
<h2>Composants</h2><table><tr><th>Marché</th><th>TF</th><th>Stratégie</th><th>Réglage</th><th>Risque/trade</th><th>Bot seul</th></tr>{rows}</table>
<p class="mut">Rien n'est garanti : ce sont des estimations sur le passé. Faites-la tourner en paper trading
(menu lancer.bat) plusieurs semaines avant d'y mettre de l'argent.</p>"""
    path = m.cfg.out / f"compte_{p['cle']}.html"
    path.write_text(f"<!doctype html><html lang=fr><meta charset=utf-8><meta name=viewport content='width=device-width,"
                    f"initial-scale=1'><title>{_h.escape(p['nom'])}</title><style>{CSS}</style><body>{body}{script(m.cfg.out)}</body></html>",
                    encoding="utf-8")
    return path
