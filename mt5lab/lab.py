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
from .evaluator import Evaluator, compute_signal, describe
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
    seeds: list = field(default_factory=list)        # idées du Directeur : stratégies qui marchent ailleurs
    invent_bias: list = field(default_factory=list)  # indicateurs que le Directeur demande aux inventeurs d'explorer


def stress_test(df: pd.DataFrame, oos_start: int, f: Finding, cost: float, risk_pct: float) -> dict:
    """Contre-expertise sur la période hors-échantillon : coûts doublés + stabilité sur ses 2 moitiés."""
    sig = apply_filter(df, compute_signal(df, f.candidate["signal"]), f.candidate["filter"])
    cfg = RiskConfig(**f.candidate["risk"])
    oos, oos_sig = df.iloc[oos_start:], sig.iloc[oos_start:]
    double = run_backtest(oos, oos_sig, cfg, cost=cost * 2 + 1e-12, risk_pct=risk_pct)
    seg_pos, seg_n = 0, 0
    for part in np.array_split(np.arange(len(oos)), 2):
        r = run_backtest(oos.iloc[part], oos_sig.iloc[part], cfg, cost=cost, risk_pct=risk_pct)
        seg_n += 1
        seg_pos += r.trades >= 3 and r.avg_r > 0
    return {"double_cost_avg_r": double.avg_r, "segments_positive": f"{seg_pos}/{seg_n}",
            "robust": bool(double.avg_r > 0 and seg_pos == seg_n)}


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

        # chaque chef présente ses meilleurs candidats + TOUTES les inventions de ses agents
        short_a = lead_a.shortlist() + invented_a
        seen = {f.key for f in short_a}
        short_b = [f for f in lead_b.shortlist() + invented_b if f.key not in seen]
        t_min = rules.t_threshold(len(short_a) + len(short_b))
        journal.log("Plateforme", f"{len(short_a) + len(short_b)} candidats en validation -> seuil de significativité "
                                  f"corrigé pour tests multiples : t >= {t_min:.2f}")
        approved = [(f, lead_b) for f in lead_a.validate(short_a, t_min)] + \
                   [(f, lead_a) for f in lead_b.validate(short_b, t_min)]
        everything = list({f.key: f for f in list(lead_a.findings.values()) + list(lead_b.findings.values())
                           + invented_a + invented_b}.values())
        n_evals = ev.n_evals

    # contre-expertise croisée : chaque chef vérifie les trouvailles de l'autre
    for f, reviewer in approved:
        f.stress = stress_test(df, split, f, cost, cfg.risk_pct)
        if not f.stress["robust"]:
            f.verdict = "rejeté en contre-expertise (" + reviewer.name + ")"
    final = [f for f, _ in approved if f.verdict == "APPROUVÉ"]
    journal.log("Plateforme", f"Contre-expertise : {len(final)}/{len(approved)} stratégies survivent aux coûts doublés "
                              f"et restent positives sur chaque moitié de l'OOS")
    journal.log("Plateforme", f"{n_evals} backtests uniques exécutés en {time.time() - t0:.0f}s")

    # simulation du challenge FTMO sur les trades hors-échantillon de chaque finaliste testé
    from .evaluator import candidate_key
    from .ftmo import FtmoRules, daily_table, simulate
    oos_trades = []
    ftmo_res: dict[str, dict] = {}
    for f in everything:
        if not f.oos_res or not f.oos_res.get("trades"):
            continue
        sig = apply_filter(df, compute_signal(df, f.candidate["signal"]), f.candidate["filter"])
        _, tr = run_backtest(df_oos, sig.iloc[split:], RiskConfig(**f.candidate["risk"]), cost=cost,
                             risk_pct=cfg.risk_pct, return_trades=True)
        if not len(tr):
            continue
        tr = tr[["entry_time", "exit_time", "r"]].assign(key=candidate_key(f.candidate))
        oos_trades.append(tr)
        ftmo_res[f.key] = simulate(daily_table(tr, cfg.risk_pct, df_oos.index[0], df_oos.index[-1]),
                                   cfg.ftmo or FtmoRules(), n=3000, seed=0)
    if oos_trades:
        pd.concat(oos_trades, ignore_index=True).to_csv(out_dir / "trades_oos.csv", index=False)
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
            **{k: ftmo_res.get(f.key, {}).get(k) for k in
               ("ftmo_pass", "ftmo_p1", "ftmo_p2", "ftmo_jours_p1", "ftmo_jours_p2", "ftmo_echec_p1")},
            "candidate": json.dumps(f.candidate),
        })
    board = pd.DataFrame(rows)
    if len(board):
        board["_ok"] = board["verdict"].eq("APPROUVÉ")
        board = board.sort_values(["_ok", "sharpe_oos", "score_is"], ascending=False).drop(columns="_ok")
    if len(board):
        inv_keys = {candidate_key(e["candidate"]): (e["agent"], e["confirmed"]) for e in inventions}
        board["invention"] = [inv_keys.get(candidate_key(json.loads(c)), ("", None))[0] for c in board["candidate"]]
        board["confirmee_chef"] = [inv_keys.get(candidate_key(json.loads(c)), ("", None))[1] for c in board["candidate"]]
    board.to_csv(out_dir / "classement.csv", index=False)
    best = [json.loads(c) for c in board.loc[board["verdict"] == "APPROUVÉ", "candidate"].head(cfg.top)] if len(board) else []
    (out_dir / "meilleures_strategies.json").write_text(json.dumps(best, indent=2, ensure_ascii=False))
    (out_dir / "journal_agents.txt").write_text("\n".join(journal.lines), encoding="utf-8")

    from .report import write_report
    agents = lead_a.agents + lead_b.agents
    write_report(out_dir / "rapport.html", label, board, journal, agents, cfg, n_evals, df)
    return board
