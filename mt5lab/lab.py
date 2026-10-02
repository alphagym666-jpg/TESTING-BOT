"""Orchestrateur : fait travailler les 2 chefs et leurs 10 agents, puis produit le classement final."""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .agents import Finding, Journal, ValidationRules, build_team
from .backtest import RiskConfig, run_backtest
from .evaluator import Evaluator, candidate_key, compute_signal, describe
from .strategies import REGISTRY, apply_filter


@dataclass
class LabConfig:
    rounds: int = 3
    budget: int = 1000         # tests max par agent et par round
    oos_fraction: float = 0.35  # dernière partie des données réservée à la validation
    risk_pct: float = 1.0
    workers: int | None = None
    seed: int = 42
    top: int = 30
    ftmo: object = None         # FtmoRules ; None = règles par défaut
    invent: bool = True         # round d'invention : chaque agent crée ses propres stratégies
    invent_generations: int = 8
    invent_pop: int = 40
    invent_attempts: int = 3
    bank_teams: bool = True     # équipes C (algorithmes des banques) et D (inventions institutionnelles)
    catalog: bool = True        # optimisation de chacune des stratégies du catalogue
    catalog_budget: int = 45    # essais par stratégie du catalogue (étape 1), + 20 en étape 2
    seeds: list = field(default_factory=list)        # idées du Directeur : stratégies qui marchent ailleurs
    invent_bias: list = field(default_factory=list)  # indicateurs que le Directeur demande aux inventeurs d'explorer
    genies: bool = True         # les 2 génies (Einstein, Hawking) inventent des formules mathématiques
    genie_generations: int = 12
    genie_pop: int = 60
    genie_ingredients: list = field(default_factory=list)  # stratégies des agents (recherche déjà faite)
    conseil: bool = True        # le Conseil : les meilleures stratégies de la case votent ensemble
    beat_bh: bool = False       # exiger de battre le buy & hold (acheter et garder) sur la période de validation
    bh_risk_pct: float = 1.0    # risque par trade utilisé pour comparer au buy & hold


# version de la méthode de calcul : le Directeur refait les recherches faites avec une version plus ancienne
# (3 = coûts historiques bougie par bougie, spread du moment ignoré, walk-forward, challenges enchaînés)
# (4 = swaps de nuit et de week-end vraiment comptés, durée des positions et trades gardés le week-end)
LAB_VERSION = 4
# stratégies non validées mais gagnantes hors-échantillon : suivies en paper trading pour les juger en direct
ESSAI = "À L'ESSAI"
ESSAI_MAX = 10


def audit_trades(r: np.ndarray, delayed_avg_r: float | None, n_boot: int = 2000, seed: int = 0) -> dict:
    """L'AUDITEUR anti-hasard : essaie de démolir une stratégie validée.
    - concentration : part du profit apportée par les 3 meilleurs trades (tout dépend-il de quelques coups ?) ;
    - rééchantillonnage : on retire au hasard des trades des milliers de fois ; % des cas où l'espérance <= 0 ;
    - entrée retardée d'une bougie : si la stratégie ne gagne plus, elle est trop fragile pour le réel."""
    r = np.asarray(r, dtype=float)
    out = {"audit": "OK", "audit_detail": "", "audit_grave": False}
    if len(r) < 5:
        return out
    gains = r[r > 0].sum()
    top3 = float(np.sort(r)[-3:].clip(min=0).sum() / r.sum() * 100) if r.sum() > 0 else 100.0
    rng = np.random.default_rng(seed)
    boot = rng.choice(r, size=(n_boot, len(r)), replace=True).mean(axis=1)
    p_hasard = float((boot <= 0).mean() * 100)
    notes, grave = [], False
    if r.sum() > 0 and top3 > 80:
        notes.append(f"{top3:.0f} % du profit vient de 3 trades")
        grave = True
    elif r.sum() > 0 and top3 > 50:
        notes.append(f"{top3:.0f} % du profit vient de 3 trades")
    if p_hasard > 5:
        notes.append(f"{p_hasard:.0f} % de risque que ce soit du hasard")
        grave = grave or p_hasard > 10
    if delayed_avg_r is not None and delayed_avg_r <= 0:
        notes.append(f"entrée retardée d'une bougie : {delayed_avg_r:+.2f}R")
        grave = True
    out.update(audit="GRAVE" if grave else ("fragile" if notes else "OK"),
               audit_detail=" ; ".join(notes) or f"solide (hasard {p_hasard:.1f} %, 3 meilleurs trades {top3:.0f} %)",
               audit_grave=grave, audit_hasard_pct=round(p_hasard, 1), audit_top3_pct=round(top3, 0),
               audit_retard_r=None if delayed_avg_r is None else round(delayed_avg_r, 3))
    del gains
    return out


def mark_trials(board: pd.DataFrame, n_max: int = ESSAI_MAX) -> pd.DataFrame:
    """Parmi les stratégies rejetées, garde les n_max meilleures qui GAGNENT quand même hors-échantillon
    (>= 20 trades, R moyen > 0, profit factor >= 1,05) : verdict « À L'ESSAI (paper seulement) ».
    Elles ne sont jamais mises dans le bot ; le paper trading dira si elles tiennent en direct."""
    if not len(board) or "trades_oos" not in board.columns:
        return board
    ok = board["verdict"].eq("APPROUVÉ")
    cand = board[~ok & board["verdict"].str.startswith("rejeté") & (board["trades_oos"].fillna(0) >= 20)
                 & (board["avgR_oos"].fillna(-1) > 0) & (board["pf_oos"].fillna(0) >= 1.05)]
    cand = cand.sort_values("sharpe_oos", ascending=False).head(n_max)
    board.loc[cand.index, "verdict"] = [f"{ESSAI} (paper seulement) — {v.replace('rejeté : ', 'non validée : ')}"
                                        for v in cand["verdict"]]
    return board


def stress_test(df: pd.DataFrame, oos_start: int, f: Finding, cost: float, risk_pct: float, wf_parts: int = 5) -> dict:
    """Contre-expertise : coûts doublés + stabilité sur les 2 moitiés de l'OOS + walk-forward sur tout l'historique
    (la stratégie, sans rien changer, doit gagner sur au moins 4 des 5 périodes successives)."""
    sig = apply_filter(df, compute_signal(df, f.candidate["signal"]), f.candidate["filter"])
    cfg = RiskConfig(**f.candidate["risk"])
    oos, oos_sig = df.iloc[oos_start:], sig.iloc[oos_start:]
    double = run_backtest(oos, oos_sig, cfg, cost=cost + 1e-12, risk_pct=risk_pct, cost_mult=2.0)
    seg_pos, seg_n = 0, 0
    for part in np.array_split(np.arange(len(oos)), 2):
        r = run_backtest(oos.iloc[part], oos_sig.iloc[part], cfg, cost=cost, risk_pct=risk_pct)
        seg_n += 1
        seg_pos += r.trades >= 3 and r.avg_r > 0
    wf_pos, wf_n, periods = 0, 0, []
    for part in np.array_split(np.arange(len(df)), wf_parts):
        r = run_backtest(df.iloc[part], sig.iloc[part], cfg, cost=cost, risk_pct=risk_pct)
        start = df.index[part[0]]
        periods.append({"debut": str(start)[:10], "fin": str(df.index[part[-1]])[:10], "trades": r.trades,
                        "r_moyen": round(r.avg_r, 3), "r_total": round(r.total_r, 1)})
        if r.trades >= 5:
            wf_n += 1
            wf_pos += r.avg_r > 0
    need = max(3, int(np.ceil(0.8 * wf_n)))
    return {"double_cost_avg_r": double.avg_r, "segments_positive": f"{seg_pos}/{seg_n}",
            "robust": bool(double.avg_r > 0 and seg_pos == seg_n),
            "wf": f"{wf_pos}/{wf_n}", "wf_ok": bool(wf_n >= 3 and wf_pos >= need), "periodes": periods}


def run_inventions(lead_a, lead_b, ev, df_train, cfg: LabConfig, journal, rules) -> list[dict]:
    """Round d'invention : chaque agent crée au moins une stratégie, que son chef confirme ou non."""
    import random as _random

    from .agents import Finding
    from .evaluator import score
    from .inventions import Inventor, invent

    journal.log("Plateforme", "=== ROUND D'INVENTION : chaque agent crée ses propres stratégies ===")
    out, confirmed_specs = [], []
    order = [(a, lead_a) for a in lead_a.agents] + [(a, lead_b) for a in lead_b.agents]
    # le Généticien (agent 10) passe en dernier : il fait évoluer les inventions confirmées des autres
    order.sort(key=lambda t: t[0].number == 10)
    for agent, lead in order:
        inv = Inventor(agent.number, agent.tag, df_train, _random.Random(cfg.seed * 100 + agent.number),
                       bias=cfg.invent_bias)
        seeds = confirmed_specs if agent.number == 10 else None
        entry = invent(inv, ev, "isa", "isb", cfg.invent_generations, cfg.invent_pop, cfg.invent_attempts,
                       seeds=seeds, log=journal.log)
        if not entry:
            journal.log(agent.tag, "aucune invention exploitable (pas assez de trades)")
            continue
        entry.update(agent=agent.tag, lead=lead)
        cand = entry["candidate"]
        rule = json.dumps({k: v for k, v in cand["signal"].items() if k != "name"}, sort_keys=True)
        twin = next((e for e in out if json.dumps({k: v for k, v in e["candidate"]["signal"].items() if k != "name"},
                                                  sort_keys=True) == rule), None)
        if twin is not None:
            lead.log(f"{agent.tag} retombe sur la règle déjà trouvée par {twin['agent']} : je ne la compte qu'une fois")
            continue
        if entry["confirmed"]:
            confirmed_specs.append({k: v for k, v in cand["signal"].items() if k != "name"})
            lead.log(f"Je confirme l'invention de {agent.tag} : {describe(cand)}")
        else:
            lead.log(f"Invention de {agent.tag} NON confirmée sur ma période de contrôle ; "
                     f"je la présente quand même à la validation finale : {describe(cand)}")
        res_is = ev.evaluate([cand], "is")[0][1]
        sc = score(res_is, rules.min_trades_is)
        entry["finding"] = Finding(cand, res_is, sc if math.isfinite(sc) else -99.0, agent.tag)
        out.append(entry)
    n_ok = sum(e["confirmed"] for e in out)
    journal.log("Plateforme", f"Round d'invention terminé : {len(out)} inventions, {n_ok} confirmées par les chefs")
    return out


def team_of(agent_tag: str) -> str:
    """Équipe d'un agent, d'après son numéro (1-5 A, 6-10 B, 11-15 C, 16-20 D)."""
    import re
    if "catalogue" in agent_tag:
        return "Optimiseur du catalogue"
    if "Directeur" in agent_tag:
        return "Directeur"
    if "Génie" in agent_tag:
        return "Génies"
    if "Conseil" in agent_tag:
        return "Conseil"
    m = re.search(r"Agent\s+(\d+)", agent_tag)
    if not m:
        return ""
    n = int(m.group(1))
    return "A" if n <= 5 else "B" if n <= 10 else "C" if n <= 15 else "D"


def run_bank_teams(ev, extra, cfg: LabConfig, journal, rules):
    """Équipe C : failles des algorithmes institutionnels. Équipe D : inventions à partir de ces failles."""
    import random as _random

    from .agents import Finding, TeamLead
    from .banques import TEAM_C, TEAM_D, BankAgent, describe_faille, scan, to_rule
    from .evaluator import score
    from .inventions import BANK_FEATURES, INVENTION_RISKS, Inventor, invent

    lead_c = TeamLead("Chef C (Algorithmes des banques)", "trouver les empreintes des algorithmes institutionnels",
                      [], ev, journal, rules)
    lead_d = TeamLead("Chef D (Inventions institutionnelles)", "inventer des stratégies à partir des failles",
                      [], ev, journal, rules)
    journal.log("Plateforme", "=== ÉQUIPE C : les 5 analystes cherchent les failles des algorithmes des banques ===")
    lead_c.log(f"Mission : {lead_c.mission}. Je revérifie chaque faille sur une période que l'analyste n'a pas vue.")
    agents_c = [BankAgent(*t) for t in TEAM_C]
    failles = []
    for a in agents_c:
        failles += scan(a, extra["isa"], extra["isb"], log=journal.log)
    lead_c.log(f"{len(failles)} failles confirmées au total")
    # chaque faille devient une stratégie jouable : le meilleur des 4 réglages de risque est présenté
    findings_c = []
    count: dict = {}
    for fa in failles:
        count[fa["agent_no"]] = count.get(fa["agent_no"], 0) + 1
        spec = {**to_rule(fa["cond"], fa["behaviour"]), "name": f"FAILLE A{fa['agent_no']}-{count[fa['agent_no']]}"}
        fa["strategie"] = spec["name"]
        best = None
        for c, res in ev.evaluate([{"signal": spec, "filter": "none", "risk": r} for r in INVENTION_RISKS], "is"):
            sc = score(res, rules.min_trades_is)
            if math.isfinite(sc) and (best is None or sc > best[0]):
                best = (sc, c, res)
        if best:
            findings_c.append(Finding(best[1], best[2], best[0], fa["agent"]))
            fa["jouable"] = True
    findings_c.sort(key=lambda f: -f.score)
    for f in findings_c:
        lead_c.findings[f.key] = f

    journal.log("Plateforme", "=== ÉQUIPE D : les 5 inventeurs partent des failles de l'équipe C ===")
    lead_d.log(f"Mission : {lead_d.mission}. Je confirme chaque invention sur une période que l'inventeur n'a pas vue.")
    seeds = [to_rule(fa["cond"], fa["behaviour"]) for fa in failles]
    bias = sorted({fa["cond"]["f"] for fa in failles} | set(BANK_FEATURES))
    agents_d, findings_d, inventions_d = [], [], []
    for num, name, role in TEAM_D:
        ag = BankAgent(num, name, role, [])
        before = ev.n_evals
        inv = Inventor(num, ag.tag, extra["isa"], _random.Random(cfg.seed * 100 + num), bias=bias)
        entry = invent(inv, ev, "isa", "isb", cfg.invent_generations, cfg.invent_pop, cfg.invent_attempts,
                       seeds=seeds, log=journal.log)
        ag.tested = ev.n_evals - before
        agents_d.append(ag)
        if not entry:
            journal.log(ag.tag, "aucune invention exploitable (pas assez de trades)")
            continue
        cand = entry["candidate"]
        lead_d.log(("Je confirme" if entry["confirmed"] else "NON confirmée, présentée quand même :")
                   + f" l'invention de {ag.tag} : {describe(cand)}")
        res_is = ev.evaluate([cand], "is")[0][1]
        sc = score(res_is, rules.min_trades_is)
        f = Finding(cand, res_is, sc if math.isfinite(sc) else -99.0, ag.tag)
        findings_d.append(f)
        lead_d.findings[f.key] = f
        entry.update(agent=ag.tag, lead=lead_d)
        inventions_d.append(entry)
    return {"lead_c": lead_c, "lead_d": lead_d, "agents": agents_c + agents_d, "failles": failles,
            "findings_c": findings_c[:10], "findings_d": findings_d, "inventions_d": inventions_d,
            "describe": describe_faille}


def run_genies(ev, extra, cfg: LabConfig, journal, rules, champions):
    """Les 2 génies inventent des lois mathématiques, SEULS (aucun chef ne confirme). Ils peuvent se servir des
    meilleures stratégies des agents et des prix des autres marchés comme ingrédients."""
    import random as _random

    from .agents import Finding
    from .evaluator import score
    from .genies import GENIES, Genius, discover
    from .inventions import ext_symbols

    journal.log("Plateforme", "=== LES GÉNIES : Einstein et Hawking cherchent des lois mathématiques, seuls ===")
    strategies = [{"sig": f.candidate["signal"], "flt": f.candidate.get("filter", "none"),
                   "label": f"{f.agent.split('|')[0].strip()} : {describe(f.candidate)[:50]}"}
                  for f in champions[:5] if f.candidate["signal"].get("type") != "formula"]
    if not strategies:  # recherche déjà faite : les meilleures stratégies des agents sont données aux génies
        strategies = [{"sig": c["signal"], "flt": c.get("filter", "none"),
                       "label": f"stratégie des agents : {describe(c)[:50]}"}
                      for c in cfg.genie_ingredients[:5] if c["signal"].get("type") != "formula"]
    ext = ext_symbols(extra["isa"])
    geniuses, findings = [], []
    for i, (code, tag, role, who) in enumerate(GENIES):
        g = Genius(code, tag, role, who, _random.Random(cfg.seed * 7 + i), ext, strategies)
        geniuses.append(g)
        journal.log(tag, f"Je travaille seul. Mes ingrédients : {role.split(':', 1)[1].strip()}"
                         + (f", les prix de {', '.join(ext)}" if ext else "")
                         + (f" et les {len(strategies)} meilleures stratégies des agents" if strategies else ""))
        for d in discover(g, ev, "isa", "isb", cfg.genie_generations, cfg.genie_pop, log=journal.log):
            cand = d["candidate"]
            res_is = ev.evaluate([cand], "is")[0][1]
            sc = score(res_is, rules.min_trades_is)
            findings.append(Finding(cand, res_is, sc if math.isfinite(sc) else -99.0, tag))
    return findings, geniuses


def run_genies_only(df: pd.DataFrame, cost: float, cfg: LabConfig, label: str, cell_dir: Path) -> pd.DataFrame:
    return run_newcomers(df, cost, cfg, label, cell_dir, genies=True, conseil=False)


def run_newcomers(df: pd.DataFrame, cost: float, cfg: LabConfig, label: str, cell_dir: Path, genies: bool = True,
                  conseil: bool = True) -> pd.DataFrame:
    """Case déjà recherchée par les agents : seuls les NOUVEAUX employés travaillent (les 2 génies et/ou le
    Conseil), puis leurs stratégies sont AJOUTÉES au classement et aux trades de la case (rien n'est recalculé
    pour les agents)."""
    import dataclasses
    cell_dir = Path(cell_dir)
    main_path = cell_dir / "classement.csv"
    main = pd.read_csv(main_path) if main_path.exists() else pd.DataFrame()
    ingredients = []
    if len(main) and "candidate" in main.columns:
        top = main[main["trades_oos"].notna()].sort_values("score_is", ascending=False) if "score_is" in main else main
        if "equipe" in top.columns:  # le Conseil ne fait pas voter ses propres votes
            top = top[~top["equipe"].astype(str).eq("Conseil")]
        ingredients = [json.loads(c) for c in top["candidate"].head(8)]
    cfg2 = dataclasses.replace(cfg, rounds=0, invent=False, bank_teams=False, catalog=False, genies=genies,
                               conseil=conseil, genie_ingredients=ingredients, seeds=[])
    teams = [t for t, on in (("Génies", genies), ("Conseil", conseil)) if on]
    sub = cell_dir / ("genies" if teams == ["Génies"] else "nouveaux")
    board = run_lab(df, cost, cfg2, label, sub)
    g = board[board["equipe"].astype(str).isin(teams)] if len(board) else board
    if len(main) and "equipe" in main.columns:
        main = main[~main["equipe"].astype(str).isin(teams)]
    merged = pd.concat([main, g], ignore_index=True, sort=False) if len(g) else main
    if len(merged):
        merged.to_csv(main_path, index=False)
    gt = sub / "trades_oos.csv"
    if gt.exists():
        mt = cell_dir / "trades_oos.csv"
        t = pd.concat([pd.read_csv(mt) if mt.exists() else pd.DataFrame(), pd.read_csv(gt)], ignore_index=True)
        t.drop_duplicates().to_csv(mt, index=False)
    if genies:
        (cell_dir / "genies_fait.txt").write_text("les génies ont travaillé sur cette case\n", encoding="utf-8")
    if conseil:
        (cell_dir / "conseil_fait.txt").write_text("le Conseil a travaillé sur cette case\n", encoding="utf-8")
    return g


def optimize_catalog(ev, cfg: LabConfig, journal, rules):
    """Chaque stratégie du catalogue est travaillée : réglages, R:R, stop, gestion, filtre et sens."""
    import random as _random

    from .agents import DEFAULT_RISK, Finding
    from .backtest import MANAGEMENT, RR_LEVELS
    from .evaluator import candidate_key, score
    from .strategies import FILTERS, expand_grid

    rng = _random.Random(cfg.seed + 99)
    journal.log("Plateforme", "=== OPTIMISATION DU CATALOGUE : la meilleure version de chaque stratégie ===")
    best_known: dict = {}
    for k, res in list(ev.seen.items()):
        if not k.startswith("is{"):
            continue
        c = json.loads(k[2:])
        sig = c["signal"]
        if sig["type"] != "single":
            continue
        sc = score(res, rules.min_trades_is)
        if math.isfinite(sc) and sc > best_known.get(sig["name"], (-math.inf, None))[0]:
            best_known[sig["name"]] = (sc, c)
    names = [n for n in REGISTRY if not n.startswith("_")]
    stage1, bases = [], {}
    for name in names:
        grid = expand_grid(REGISTRY[name].grid)
        base = best_known.get(name, (None, None))[1] or \
            {"signal": {"type": "single", "name": name, "params": grid[len(grid) // 2]}, "filter": "none",
             "risk": dict(DEFAULT_RISK[0])}
        bases[name] = base
        cands = [base] + [{**base, "signal": {"type": "single", "name": name, "params": p}}
                          for p in rng.sample(grid, min(8, len(grid)))]
        risks = []
        for rr in RR_LEVELS:
            for mode, v in (("atr", 1.0), ("atr", 1.5), ("atr", 2.0), ("swing", 10), ("pct", 0.5)):
                risks.append({**base["risk"], "sl_mode": mode, "sl_value": v, "rr": rr, "management": "none"})
        cands += [{**base, "risk": r} for r in rng.sample(risks, min(len(risks), max(0, cfg.catalog_budget - len(cands))))]
        stage1 += cands
    res1 = ev.evaluate(stage1, "is")
    best: dict = {}
    for c, res in res1:
        sc = score(res, rules.min_trades_is)
        n = c["signal"]["name"]
        if math.isfinite(sc) and sc > best.get(n, (-math.inf,))[0]:
            best[n] = (sc, c, res)
    stage2 = []
    for n, (sc, c, res) in best.items():
        opts = []
        for flt in FILTERS:
            opts.append({**c, "filter": flt})
        for m in MANAGEMENT:
            if not (c["risk"]["rr"] is None and m == "breakeven"):
                opts.append({**c, "risk": {**c["risk"], "management": m}})
        for d in ("both", "long", "short"):
            opts.append({**c, "risk": {**c["risk"], "direction": d}})
        stage2 += rng.sample(opts, min(20, len(opts)))
    for c, res in ev.evaluate(stage2, "is"):
        sc = score(res, rules.min_trades_is)
        n = c["signal"]["name"]
        if math.isfinite(sc) and sc > best.get(n, (-math.inf,))[0]:
            best[n] = (sc, c, res)
    findings = [Finding(c, res, sc, "Optimiseur du catalogue") for sc, c, res in best.values()]
    journal.log("Plateforme", f"Catalogue : {len(findings)}/{len(names)} stratégies ont une version jouable "
                              f"({len(stage1) + len(stage2)} essais)")
    return findings, {candidate_key(f.candidate): f.candidate["signal"]["name"] for f in findings}


def run_lab(df: pd.DataFrame, cost: float, cfg: LabConfig, label: str, out_dir: Path) -> pd.DataFrame:
    out_dir.mkdir(parents=True, exist_ok=True)
    journal = Journal()
    split = int(len(df) * (1 - cfg.oos_fraction))
    df_is, df_oos = df.iloc[:split], df.iloc[split:]
    journal.log("Plateforme", f"{label} : {len(df)} bougies | in-sample {df_is.index[0]} -> {df_is.index[-1]} "
                              f"| out-of-sample {df_oos.index[0]} -> {df_oos.index[-1]}")
    journal.log("Plateforme", f"{len(REGISTRY)} stratégies au catalogue | coût aller-retour {cost:.6g}")
    rules = ValidationRules()
    t0 = time.time()
    # l'in-sample est lui-même coupé en 2 pour les inventions : l'agent invente sur isa, son chef confirme sur isb
    cut = int(len(df_is) * 0.7)
    extra = {"isa": df_is.iloc[:cut], "isb": df_is.iloc[cut:]}
    inventions: list[dict] = []
    with Evaluator(df_is, df_oos, cost, cfg.risk_pct, cfg.workers, extra=extra) as ev:
        lead_a, lead_b = build_team(cfg.seed, cfg.budget, ev, journal, rules)
        lead_a.brief()
        lead_b.brief()
        if cfg.seeds:  # le Directeur apporte des idées venues d'autres marchés / timeframes
            from .evaluator import score as _score
            from .agents import Finding as _Finding
            n_ok = 0
            for c, res in ev.evaluate([dict(c) for c in cfg.seeds], "is"):
                sc = _score(res, rules.min_trades_is)
                if math.isfinite(sc):
                    f = _Finding(c, res, sc, "Directeur (idée transférée)")
                    lead_b.findings[f.key] = f
                    n_ok += 1
            journal.log("Directeur", f"J'apporte {len(cfg.seeds)} idées qui ont marché ailleurs ; {n_ok} tiennent la route "
                                     f"ici et partent chez le Chef B pour être filtrées, combinées et optimisées")
        for rnd in range(1, cfg.rounds + 1):
            champs = lambda: sorted(lead_a.champions() + lead_b.champions(), key=lambda f: f.score, reverse=True)[:15]
            lead_a.supervise_round({"round": rnd, "champions": champs()}, rnd)
            # le Chef A transmet ses meilleurs éléments au Chef B
            top = champs()
            if top:
                lead_a.log(f"Je transmets {len(top)} champions au Chef B. N°1 : {describe(top[0].candidate)}")
            lead_b.supervise_round({"round": rnd, "champions": top}, rnd)

        # candidats uniques présentés par les deux chefs (un doublon n'est validé qu'une fois)
        invented_a, invented_b = [], []
        if cfg.invent:
            inventions = run_inventions(lead_a, lead_b, ev, extra["isa"], cfg, journal, rules)
            for inv in inventions:
                f = inv.get("finding")
                if f is not None:
                    (invented_a if inv["lead"] is lead_a else invented_b).append(f)

        bank = run_bank_teams(ev, extra, cfg, journal, rules) if cfg.bank_teams else None
        cat_findings, cat_keys = optimize_catalog(ev, cfg, journal, rules) if cfg.catalog else ([], {})
        champions = sorted(lead_a.champions() + lead_b.champions(), key=lambda f: f.score, reverse=True)
        genie_findings, geniuses = run_genies(ev, extra, cfg, journal, rules, champions) if cfg.genies else ([], [])
        conseil_findings = []
        if cfg.conseil:
            from .conseil import run_conseil
            conseil_findings = run_conseil(ev, cfg, journal, rules, champions + cat_findings + genie_findings,
                                           cfg.genie_ingredients)

        # chaque chef présente ses meilleurs candidats + TOUTES les inventions de ses agents
        short_a = lead_a.shortlist() + invented_a
        seen = {f.key for f in short_a}
        short_b = [f for f in lead_b.shortlist() + invented_b if f.key not in seen]
        seen |= {f.key for f in short_b}
        short_c = [f for f in (bank["findings_c"] if bank else []) if f.key not in seen]
        seen |= {f.key for f in short_c}
        short_d = [f for f in (bank["findings_d"] if bank else []) if f.key not in seen]
        seen |= {f.key for f in short_d}
        n_main = len(short_a) + len(short_b) + len(short_c) + len(short_d)
        t_min = rules.t_threshold(n_main)
        journal.log("Plateforme", f"{n_main} candidats en validation -> seuil de significativité "
                                  f"corrigé pour tests multiples : t >= {t_min:.2f}")
        approved = [(f, lead_b) for f in lead_a.validate(short_a, t_min)] + \
                   [(f, lead_a) for f in lead_b.validate(short_b, t_min)]
        if bank:
            approved += [(f, bank["lead_d"]) for f in bank["lead_c"].validate(short_c, t_min)]
            approved += [(f, bank["lead_c"]) for f in bank["lead_d"].validate(short_d, t_min)]
        # le catalogue optimisé : famille de validation à part (99 stratégies), même exigence statistique
        cat_short = [f for f in cat_findings if f.key not in seen and f.is_res.get("sharpe", 0) >= 1.0]
        cat_weak = [f for f in cat_findings if f.key not in seen and f.is_res.get("sharpe", 0) < 1.0]
        if cat_short:
            t_cat = rules.t_threshold(len(cat_short))
            journal.log("Plateforme", f"Catalogue : {len(cat_short)} meilleures versions en validation, "
                                      f"seuil t >= {t_cat:.2f}")
            approved += [(f, lead_b) for f in lead_a.validate(cat_short, t_cat)]
        # les génies : leur propre famille de validation (aucun chef ne les confirme, mais la plateforme vérifie
        # leurs lois sur la période hors-échantillon comme pour tout le monde)
        short_g = [f for f in genie_findings if f.key not in seen]
        if short_g:
            from .agents import TeamLead
            lead_g = TeamLead("Contrôle des génies (plateforme)", "vérifier les lois des génies hors-échantillon",
                              [], ev, journal, rules)
            approved += [(f, lead_a) for f in lead_g.validate(short_g, rules.t_threshold(len(short_g)))]
        # le Conseil : sa propre famille de validation, même exigence hors-échantillon que tout le monde
        short_k = [f for f in conseil_findings if f.key not in seen and f.key not in {g.key for g in short_g}]
        if short_k:
            from .agents import TeamLead
            lead_k = TeamLead("Contrôle du Conseil (plateforme)", "vérifier les votes du Conseil hors-échantillon",
                              [], ev, journal, rules)
            approved += [(f, lead_b) for f in lead_k.validate(short_k, rules.t_threshold(len(short_k)))]
        if cat_weak:  # résultats hors-échantillon affichés pour le classement, sans validation possible
            oos = {candidate_key(c): r for c, r in ev.evaluate([f.candidate for f in cat_weak], "oos")}
            for f in cat_weak:
                f.oos_res = oos.get(f.key)
                f.verdict = "rejeté : trop faible en in-sample"
        everything = list({f.key: f for f in list(lead_a.findings.values()) + list(lead_b.findings.values())
                           + invented_a + invented_b + short_c + short_d + cat_findings
                           + genie_findings + conseil_findings}.values())
        n_evals = ev.n_evals

    # contre-expertise croisée : chaque chef vérifie les trouvailles de l'autre
    for f, reviewer in approved:
        f.stress = stress_test(df, split, f, cost, cfg.risk_pct)
        if not f.stress["robust"]:
            f.verdict = "rejeté en contre-expertise (" + reviewer.name + ")"
        elif not f.stress["wf_ok"]:
            f.verdict = f"rejeté : instable dans le temps (walk-forward {f.stress['wf']})"
    final = [f for f, _ in approved if f.verdict == "APPROUVÉ"]
    journal.log("Plateforme", f"Contre-expertise : {len(final)}/{len(approved)} stratégies survivent aux coûts doublés, "
                              f"restent positives sur chaque moitié de l'OOS et gagnent sur au moins 4 des 5 périodes "
                              f"de l'historique (walk-forward)")
    journal.log("Plateforme", f"{n_evals} backtests uniques exécutés en {time.time() - t0:.0f}s")

    # simulation du challenge FTMO sur les trades hors-échantillon de chaque finaliste testé
    from .ftmo import FtmoRules, challenge_columns, count_challenges, daily_table, simulate
    rules = cfg.ftmo or FtmoRules()
    oos_trades = []
    ftmo_res: dict[str, dict] = {}
    counts: dict[str, dict] = {}
    # buy & hold : acheter au début de la période de validation et garder jusqu'à la fin
    c_oos = df_oos["close"].to_numpy(dtype=float)
    bh_pct = float((c_oos[-1] / c_oos[0] - 1) * 100)
    bh_dd = float(np.max(1 - c_oos / np.maximum.accumulate(c_oos)) * 100)
    bh: dict[str, float] = {}
    for f in everything:
        if not f.oos_res or not f.oos_res.get("trades"):
            continue
        sig = apply_filter(df, compute_signal(df, f.candidate["signal"]), f.candidate["filter"])
        _, tr = run_backtest(df_oos, sig.iloc[split:], RiskConfig(**f.candidate["risk"]), cost=cost,
                             risk_pct=cfg.risk_pct, return_trades=True)
        if not len(tr):
            continue
        if f.verdict == "APPROUVÉ":  # l'auditeur anti-hasard passe sur chaque stratégie validée
            _, trd = run_backtest(df_oos, sig.shift(1).fillna(0).astype(sig.dtype).iloc[split:],
                                  RiskConfig(**f.candidate["risk"]), cost=cost, risk_pct=cfg.risk_pct, return_trades=True)
            f.audit = audit_trades(tr["r"].to_numpy(), float(trd["r"].mean()) if len(trd) else None)
            # le météorologue : dans quel type de marché cette stratégie gagne-t-elle ?
            from .strategies import market_regime
            reg = market_regime(df).reindex(pd.DatetimeIndex(tr["entry_time"])).to_numpy() \
                if isinstance(df.index, pd.DatetimeIndex) else None
            if reg is not None:
                parts = []
                for name in ("tendance calme", "tendance nerveuse", "range calme", "range nerveux"):
                    rr_ = tr["r"].to_numpy()[reg == name]
                    if len(rr_) >= 5:
                        parts.append(f"{name} {rr_.mean():+.2f}R ({len(rr_)})")
                f.audit["meteo"] = " ; ".join(parts)
            if f.audit["audit_grave"]:
                f.verdict = f"rejeté : audit anti-hasard ({f.audit['audit_detail']})"
                journal.log("Auditeur anti-hasard", f"JE REJETTE {describe(f.candidate)[:80]} : {f.audit['audit_detail']}")
        bh[f.key] = float((np.prod(1 + np.clip(tr["r"].to_numpy() * cfg.bh_risk_pct / 100, -0.99, None)) - 1) * 100)
        tr = tr[["entry_time", "exit_time", "r", "side"]].assign(key=candidate_key(f.candidate))
        oos_trades.append(tr)
        d_oos = daily_table(tr, cfg.risk_pct, df_oos.index[0], df_oos.index[-1])
        ftmo_res[f.key] = simulate(d_oos, rules, n=3000, seed=0)
        # combien de challenges auraient été réussis / ratés en enchaînant sur l'historique réel
        _, tr_all = run_backtest(df, sig, RiskConfig(**f.candidate["risk"]), cost=cost, risk_pct=cfg.risk_pct,
                                 return_trades=True)
        c_all = count_challenges(daily_table(tr_all, cfg.risk_pct, df.index[0], df.index[-1]), rules) \
            if len(tr_all) else count_challenges(None, rules)
        counts[f.key] = {**challenge_columns("total", c_all), **challenge_columns("oos", count_challenges(d_oos, rules)),
                         "ftmo_challenges": json.dumps(c_all["liste"][:60])}
    if oos_trades:
        pd.concat(oos_trades, ignore_index=True).to_csv(out_dir / "trades_oos.csv", index=False)
    if cfg.beat_bh:
        lost = [f for f in final if bh.get(f.key, -1e9) <= max(bh_pct, 0.0)]
        for f in lost:
            f.verdict = (f"rejeté : ne bat pas le buy & hold ({bh.get(f.key, float('nan')):+.1f} % à "
                         f"{cfg.bh_risk_pct:g} %/trade contre {bh_pct:+.1f} %)")
        final = [f for f in final if f not in lost]
        journal.log("Plateforme", f"Buy & hold sur la période de validation : {bh_pct:+.1f} % (baisse max {bh_dd:.1f} %) "
                                  f"-> {len(final)} stratégies le battent (à {cfg.bh_risk_pct:g} % de risque par trade)")
    ok_ftmo = [ftmo_res[f.key]["ftmo_pass"] for f in final if f.key in ftmo_res]
    if ok_ftmo:
        journal.log("Chef FTMO", f"Meilleure probabilité de réussir le challenge ({(cfg.ftmo or FtmoRules()).label()}) "
                                 f"parmi les validées : {np.nanmax(ok_ftmo):.1f} % (risque {cfg.risk_pct:g} %/trade)")

    # durée de la période hors-échantillon, pour ramener les gains « par mois » (comparable entre timeframes)
    oos_days = max((df_oos.index[-1] - df_oos.index[0]).total_seconds() / 86400, 1e-9) \
        if isinstance(df_oos.index, pd.DatetimeIndex) else float("nan")
    months = oos_days / 30.44
    rows = []
    for f in everything:
        o = f.oos_res or {}
        st = getattr(f, "stress", {})
        rows.append({
            "verdict": f.verdict,
            "strategie": describe(f.candidate),
            "risque": RiskConfig(**f.candidate["risk"]).label(),
            "trouve_par": f.agent,
            "equipe": team_of(f.agent),
            "score_is": round(f.score, 2),
            "trades_is": f.is_res["trades"], "wr_is": round(f.is_res["win_rate"], 1),
            "avgR_is": round(f.is_res["avg_r"], 3), "pf_is": round(f.is_res["profit_factor"], 2),
            "trades_oos": o.get("trades"), "wr_oos": round(o.get("win_rate", 0), 1),
            "avgR_oos": round(o.get("avg_r", 0), 3), "pf_oos": round(o.get("profit_factor", 0), 2),
            "ret_oos_pct": round(o.get("return_pct", 0), 1), "dd_oos_pct": round(o.get("max_dd_pct", 0), 1),
            "sharpe_oos": round(o.get("sharpe", 0), 2),
            "oos_jours": round(oos_days, 1),
            "oos_debut": str(df_oos.index[0]), "oos_fin": str(df_oos.index[-1]),
            "donnees_debut": str(df.index[0]), "donnees_fin": str(df.index[-1]),
            "trades_mois": round((o.get("trades") or 0) / months, 1) if months > 0 else None,
            "gain_mois_pct": round(o.get("return_pct", 0) / months, 2) if months > 0 else None,
            "cout_x2_avgR": round(st.get("double_cost_avg_r", float("nan")), 3) if st else None,
            "periodes_positives": st.get("segments_positive"),
            "walk_forward": st.get("wf"),
            "par_periode": json.dumps(st.get("periodes", [])) if st else None,
            **{k: ftmo_res.get(f.key, {}).get(k) for k in
               ("ftmo_pass", "ftmo_p1", "ftmo_p2", "ftmo_jours_p1", "ftmo_jours_p2", "ftmo_echec_p1")},
            **counts.get(f.key, {}),
            "rendement_oos_pct_risque_bh": round(bh[f.key], 1) if f.key in bh else None,
            "audit": getattr(f, "audit", {}).get("audit"), "audit_detail": getattr(f, "audit", {}).get("audit_detail"),
            "meteo": getattr(f, "audit", {}).get("meteo"),
            "buy_hold_oos_pct": round(bh_pct, 1), "buy_hold_dd_oos_pct": round(bh_dd, 1),
            "bat_buy_hold": (bh[f.key] > max(bh_pct, 0.0)) if f.key in bh else None,
            "candidate": json.dumps(f.candidate),
            "version_calcul": LAB_VERSION,
        })
    board = mark_trials(pd.DataFrame(rows))
    if len(board):
        board["_ok"] = board["verdict"].eq("APPROUVÉ").astype(int) * 2 + board["verdict"].str.startswith(ESSAI)
        board = board.sort_values(["_ok", "sharpe_oos", "score_is"], ascending=False).drop(columns="_ok")
        n_trial = int(board["verdict"].str.startswith(ESSAI).sum())
        if n_trial:
            journal.log("Plateforme", f"{n_trial} stratégies non validées mais gagnantes hors-échantillon passent "
                                      "« À L'ESSAI » : suivies en paper trading seulement, jamais dans le bot")
    if len(board):
        inv_keys = {candidate_key(e["candidate"]): (e["agent"], e["confirmed"])
                    for e in inventions + (bank["inventions_d"] if bank else [])}
        board["invention"] = [inv_keys.get(candidate_key(json.loads(c)), ("", None))[0] for c in board["candidate"]]
        board["confirmee_chef"] = [inv_keys.get(candidate_key(json.loads(c)), ("", None))[1] for c in board["candidate"]]
        board["meilleure_version_de"] = [cat_keys.get(candidate_key(json.loads(c)), "") for c in board["candidate"]]
    if bank:
        (out_dir / "failles.json").write_text(json.dumps(
            [{**{k: v for k, v in fa.items() if k != "cond"}, "condition": fa["cond"],
              "description": bank["describe"](fa)} for fa in bank["failles"]], indent=2, ensure_ascii=False,
            default=str), encoding="utf-8")
    board.to_csv(out_dir / "classement.csv", index=False)
    if cfg.genies and cfg.rounds > 0:
        (out_dir / "genies_fait.txt").write_text("les génies ont travaillé sur cette case\n", encoding="utf-8")
    if cfg.conseil and cfg.rounds > 0:
        (out_dir / "conseil_fait.txt").write_text("le Conseil a travaillé sur cette case\n", encoding="utf-8")
    best = [json.loads(c) for c in board.loc[board["verdict"] == "APPROUVÉ", "candidate"].head(cfg.top)] if len(board) else []
    (out_dir / "meilleures_strategies.json").write_text(json.dumps(best, indent=2, ensure_ascii=False))
    (out_dir / "journal_agents.txt").write_text("\n".join(journal.lines), encoding="utf-8")

    from .report import write_report
    teams = [("Chef A — Exploration", lead_a.agents), ("Chef B — Optimisation", lead_b.agents)]
    if bank:
        teams += [("Chef C — Algorithmes des banques", bank["agents"][:5]),
                  ("Chef D — Inventions institutionnelles", bank["agents"][5:])]
    if geniuses:
        teams += [("Les génies (travaillent seuls)", geniuses)]
    write_report(out_dir / "rapport.html", label, board, journal, teams, cfg, n_evals, df)
    return board
