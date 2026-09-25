"""Orchestrateur : fait travailler les 2 chefs et leurs 10 agents, puis produit le classement final."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
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
    budget: int = 600          # tests max par agent et par round
    oos_fraction: float = 0.35  # dernière partie des données réservée à la validation
    risk_pct: float = 1.0
    workers: int | None = None
    seed: int = 42
    top: int = 30


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
    with Evaluator(df_is, df_oos, cost, cfg.risk_pct, cfg.workers) as ev:
        lead_a, lead_b = build_team(cfg.seed, cfg.budget, ev, journal, rules)
        lead_a.brief()
        lead_b.brief()
        for rnd in range(1, cfg.rounds + 1):
            champs = lambda: sorted(lead_a.champions() + lead_b.champions(), key=lambda f: f.score, reverse=True)[:15]
            lead_a.supervise_round({"round": rnd, "champions": champs()}, rnd)
            # le Chef A transmet ses meilleurs éléments au Chef B
            top = champs()
            if top:
                lead_a.log(f"Je transmets {len(top)} champions au Chef B. N°1 : {describe(top[0].candidate)}")
            lead_b.supervise_round({"round": rnd, "champions": top}, rnd)

        approved = [(f, lead_b) for f in lead_a.validate()] + [(f, lead_a) for f in lead_b.validate()]
        everything = list({f.key: f for f in list(lead_a.findings.values()) + list(lead_b.findings.values())}.values())
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
            "cout_x2_avgR": round(st.get("double_cost_avg_r", float("nan")), 3) if st else None,
            "periodes_positives": st.get("segments_positive"),
            "candidate": json.dumps(f.candidate),
        })
    board = pd.DataFrame(rows)
    if len(board):
        board["_ok"] = board["verdict"].eq("APPROUVÉ")
        board = board.sort_values(["_ok", "sharpe_oos", "score_is"], ascending=False).drop(columns="_ok")
    board.to_csv(out_dir / "classement.csv", index=False)
    best = [json.loads(c) for c in board.loc[board["verdict"] == "APPROUVÉ", "candidate"].head(cfg.top)] if len(board) else []
    (out_dir / "meilleures_strategies.json").write_text(json.dumps(best, indent=2, ensure_ascii=False))
    (out_dir / "journal_agents.txt").write_text("\n".join(journal.lines), encoding="utf-8")

    from .report import write_report
    agents = lead_a.agents + lead_b.agents
    write_report(out_dir / "rapport.html", label, board, journal, agents, cfg, n_evals, df)
    return board
