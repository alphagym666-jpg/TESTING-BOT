"""Les 10 agents chercheurs et les 2 chefs d'équipe.

Organisation :

    Chef d'équipe A — « Exploration » (supervise les agents 1 à 5)
        1. Chasseur de tendance      (famille trend)
        2. Contrarien                (famille mean_reversion)
        3. Spécialiste cassures      (familles breakout + session)
        4. Momentum                  (famille momentum)
        5. Price action              (famille price_action)

    Chef d'équipe B — « Optimisation » (supervise les agents 6 à 10)
        6. Spécialiste filtres       (ajoute filtres tendance / ADX / volatilité / session)
        7. Architecte de combos      (combine 2 stratégies : confirm / and / or)
        8. Optimiseur R:R            (balaye tous les SL, R:R et gestions de position)
        9. Explorateur aléatoire     (échantillonne tout l'espace au hasard)
       10. Généticien                (mute et croise les meilleurs candidats)

Chaque agent propose des candidats ; le chef les fait évaluer (in-sample), garde les meilleurs,
les valide hors-échantillon (out-of-sample) et rejette le sur-apprentissage. Les champions de
l'équipe A alimentent les agents d'optimisation de l'équipe B.
"""
from __future__ import annotations

import copy
import math
import random
import time
from dataclasses import asdict, dataclass, field

from .backtest import MANAGEMENT, RR_LEVELS, SL_MODES, RiskConfig
from .evaluator import Evaluator, candidate_key, describe, score
from .strategies import FILTERS, REGISTRY, expand_grid, families

DEFAULT_RISK = [
    asdict(RiskConfig("atr", sl, rr, "none"))
    for sl in (1.0, 1.5, 2.0)
    for rr in (1.0, 1.5, 2.0, 3.0, None)
]


def single(name: str, params: dict) -> dict:
    return {"type": "single", "name": name, "params": params}


def cand(sig: dict, flt: str = "none", risk: dict | None = None) -> dict:
    return {"signal": sig, "filter": flt, "risk": risk or DEFAULT_RISK[0]}


@dataclass
class Finding:
    candidate: dict
    is_res: dict
    score: float
    agent: str
    oos_res: dict | None = None
    verdict: str = "en attente"

    @property
    def key(self):
        return candidate_key(self.candidate)


@dataclass
class Journal:
    lines: list[str] = field(default_factory=list)

    def log(self, who: str, msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] {who:<28} | {msg}"
        self.lines.append(line)
        print(line, flush=True)


# ============================================================================== agents
class Agent:
    number = 0
    name = "Agent"
    role = ""

    def __init__(self, rng: random.Random, budget: int):
        self.rng = rng
        self.budget = budget
        self.tested = 0
        self.best: list[Finding] = []

    @property
    def tag(self):
        return f"Agent {self.number:>2} {self.name}"

    def propose(self, context: dict) -> list[dict]:
        raise NotImplementedError

    def _cap(self, cands: list[dict]) -> list[dict]:
        if len(cands) > self.budget:
            cands = self.rng.sample(cands, self.budget)
        return cands


class FamilyAgent(Agent):
    """Teste toutes les stratégies d'une famille, toutes les combinaisons de paramètres, plusieurs R:R."""

    families: tuple[str, ...] = ()

    def propose(self, context):
        fam = families()
        names = [name for f in self.families for name in fam.get(f, [])]
        share = max(1, self.budget // max(1, len(names)))  # budget réparti équitablement entre stratégies
        out = []
        for name in names:
            mine = []
            for p in expand_grid(REGISTRY[name].grid):
                if context["round"] == 1:
                    risks = DEFAULT_RISK
                else:  # rounds suivants : on explore d'autres gestions de risque au hasard
                    risks = [random_risk(self.rng) for _ in range(3)]
                mine += [cand(single(name, p), "none", r) for r in risks]
            out += self.rng.sample(mine, share) if len(mine) > share else mine
        return out


class TrendAgent(FamilyAgent):
    number, name, role, families = 1, "Chasseur de tendance", "Suivi de tendance", ("trend",)


class MeanReversionAgent(FamilyAgent):
    number, name, role = 2, "Contrarien", "Retour à la moyenne, Stochastique, divergences"
    families = ("mean_reversion", "stochastic", "divergence")


class BreakoutAgent(FamilyAgent):
    number, name, role = 3, "Spécialiste cassures", "Breakouts, sessions, ICT, pivots"
    families = ("breakout", "session")


class MomentumAgent(FamilyAgent):
    number, name, role, families = 4, "Momentum", "Momentum", ("momentum",)


class PriceActionAgent(FamilyAgent):
    number, name, role = 5, "Price action & SMC", "Order blocks, FVG, zones, retours, chandeliers"
    families = ("price_action", "candlestick", "smc", "zones")


class FilterAgent(Agent):
    number, name, role = 6, "Spécialiste filtres", "Filtres de régime de marché"

    def propose(self, context):
        out = []
        for f in context["champions"]:
            for flt in FILTERS:
                if flt == f.candidate["filter"]:
                    continue
                for direction in ("both", "long", "short"):
                    r = dict(f.candidate["risk"], direction=direction)
                    out.append(cand(f.candidate["signal"], flt, r))
        return self._cap(out)


class ComboAgent(Agent):
    number, name, role = 7, "Architecte de combos", "Combinaisons de 2 stratégies"

    def propose(self, context):
        champs = context["champions"]
        sigs = []
        for f in champs:
            s = f.candidate["signal"]
            if s["type"] == "single" and s not in sigs:
                sigs.append(s)
        out = []
        for a in sigs:
            for b in sigs:
                if a["name"] == b["name"]:
                    continue
                for mode, windows in (("confirm", (1, 3, 5)), ("and", (0,))):
                    for w in windows:
                        combo = {"type": "combo", "a": a, "b": b, "mode": mode, "window": w}
                        for r in DEFAULT_RISK[::2]:
                            out.append(cand(combo, "none", r))
        return self._cap(out)


class RROptimizerAgent(Agent):
    number, name, role = 8, "Optimiseur R:R", "Stop loss, R:R, break-even, trailing"

    def propose(self, context):
        out = []
        for f in context["champions"]:
            for mode, vals in SL_MODES.items():
                for v in vals:
                    for rr in RR_LEVELS:
                        for m in MANAGEMENT:
                            if rr is None and m == "breakeven":
                                continue
                            r = asdict(RiskConfig(mode, v, rr, m, f.candidate["risk"].get("max_hold", 200),
                                                  f.candidate["risk"].get("direction", "both")))
                            out.append(cand(f.candidate["signal"], f.candidate["filter"], r))
        return self._cap(out)


def random_risk(rng: random.Random) -> dict:
    mode = rng.choice(list(SL_MODES))
    rr = rng.choice(RR_LEVELS)
    m = rng.choice([x for x in MANAGEMENT if not (rr is None and x == "breakeven")])
    return asdict(RiskConfig(mode, rng.choice(SL_MODES[mode]), rr, m, rng.choice([50, 100, 200]),
                             rng.choice(["both", "both", "long", "short"])))


def random_single(rng: random.Random) -> dict:
    name = rng.choice(list(REGISTRY))
    return single(name, rng.choice(expand_grid(REGISTRY[name].grid)))


class RandomAgent(Agent):
    number, name, role = 9, "Explorateur aléatoire", "Recherche aléatoire dans tout l'espace"

    def propose(self, context):
        out = []
        for _ in range(self.budget):
            s = random_single(self.rng)
            b = random_single(self.rng)
            if self.rng.random() < 0.3 and b["name"] != s["name"]:
                s = {"type": "combo", "a": s, "b": b,
                     "mode": self.rng.choice(["confirm", "and"]), "window": self.rng.choice([1, 3, 5])}
            out.append(cand(s, self.rng.choice(list(FILTERS)), random_risk(self.rng)))
        return out


class GeneticAgent(Agent):
    number, name, role = 10, "Généticien", "Mutation & croisement des meilleurs"

    def _mutate_single(self, s):
        s = copy.deepcopy(s)
        grid = REGISTRY[s["name"]].grid
        if grid:
            k = self.rng.choice(list(grid))
            s["params"][k] = self.rng.choice(grid[k])
        return s

    def _mutate(self, c):
        c = copy.deepcopy(c)
        what = self.rng.random()
        sig = c["signal"]
        if what < 0.35:
            if sig["type"] == "single":
                c["signal"] = self._mutate_single(sig)
            else:
                part = self.rng.choice(["a", "b"])
                sig[part] = self._mutate_single(sig[part])
        elif what < 0.55:
            c["filter"] = self.rng.choice(list(FILTERS))
        elif what < 0.85:
            r = c["risk"]
            r["rr"] = self.rng.choice(RR_LEVELS)
            r["management"] = self.rng.choice([m for m in MANAGEMENT if not (r["rr"] is None and m == "breakeven")])
            if r["sl_mode"] in SL_MODES:
                r["sl_value"] = self.rng.choice(SL_MODES[r["sl_mode"]])
        else:
            c["risk"] = random_risk(self.rng)
        return c

    def _crossover(self, a, b):
        child = copy.deepcopy(a)
        child["risk"] = copy.deepcopy(b["risk"]) if self.rng.random() < 0.5 else child["risk"]
        child["filter"] = b["filter"] if self.rng.random() < 0.5 else child["filter"]
        if (a["signal"]["type"] == "single" and b["signal"]["type"] == "single"
                and a["signal"]["name"] != b["signal"]["name"] and self.rng.random() < 0.4):
            child["signal"] = {"type": "combo", "a": a["signal"], "b": b["signal"], "mode": "confirm", "window": 3}
        return child

    def propose(self, context):
        pop = [f.candidate for f in context["champions"]]
        if not pop:
            return []
        out = []
        for _ in range(self.budget):
            if len(pop) > 1 and self.rng.random() < 0.4:
                a, b = self.rng.sample(pop, 2)
                out.append(self._mutate(self._crossover(a, b)))
            else:
                out.append(self._mutate(self.rng.choice(pop)))
        return out


# ========================================================================== chefs
@dataclass
class ValidationRules:
    min_trades_is: int = 30
    min_trades_oos: int = 20
    min_oos_tstat: float = 1.64     # plancher de significativité de l'edge OOS
    shortlist: int = 20             # nb de candidats que chaque chef soumet à la validation OOS
    family_alpha: float = 0.20      # risque global accepté de valider une stratégie « chanceuse »

    def t_threshold(self, n_tested: int) -> float:
        """Correction de Šidák : plus on valide de candidats, plus le seuil de t-stat monte."""
        from statistics import NormalDist
        p = 1 - (1 - self.family_alpha) ** (1 / max(1, n_tested))
        return max(self.min_oos_tstat, NormalDist().inv_cdf(1 - p))
    min_oos_avg_r: float = 0.05     # espérance minimale hors-échantillon (en R)
    min_oos_pf: float = 1.10
    max_degradation: float = 0.75   # l'espérance OOS doit garder >= 25 % de l'espérance IS


class TeamLead:
    def __init__(self, name: str, mission: str, agents: list[Agent], evaluator: Evaluator,
                 journal: Journal, rules: ValidationRules, keep: int = 25):
        self.name = name
        self.mission = mission
        self.agents = agents
        self.ev = evaluator
        self.journal = journal
        self.rules = rules
        self.keep = keep
        self.findings: dict[str, Finding] = {}

    def log(self, msg):
        self.journal.log(self.name, msg)

    def brief(self):
        self.log(f"Mission : {self.mission}")
        for a in self.agents:
            self.log(f"  -> {a.tag} ({a.role}) | budget {a.budget} tests/round")

    def run_agent(self, agent: Agent, context: dict) -> list[Finding]:
        t0 = time.time()
        proposals = agent.propose(context)
        if not proposals:
            self.journal.log(agent.tag, "rien à tester (pas encore de champions)")
            return []
        results = self.ev.evaluate(proposals, "is")
        agent.tested += len(results)
        found = []
        for c, res in results:
            s = score(res, self.rules.min_trades_is)
            if math.isfinite(s):
                found.append(Finding(c, res, s, agent.tag))
        found.sort(key=lambda f: f.score, reverse=True)
        agent.best = found[:10]
        msg = f"{len(results)} combinaisons testées en {time.time() - t0:.1f}s"
        if found:
            b = found[0]
            msg += (f" | meilleur : {describe(b.candidate)} [{RiskConfig(**b.candidate['risk']).label()}] "
                    f"-> {b.is_res['trades']} trades, WR {b.is_res['win_rate']:.0f}%, "
                    f"{b.is_res['avg_r']:+.2f}R/trade, PF {b.is_res['profit_factor']:.2f}")
        self.journal.log(agent.tag, msg)
        return found

    def supervise_round(self, context: dict, round_no: int):
        self.log(f"--- Round {round_no} : je lance mes {len(self.agents)} agents ---")
        for a in self.agents:
            for f in self.run_agent(a, context)[: self.keep]:
                if f.key not in self.findings or self.findings[f.key].score < f.score:
                    self.findings[f.key] = f
        # le chef ne garde que la crème, avec diversité (max 3 variantes du même signal)
        ranked = sorted(self.findings.values(), key=lambda f: f.score, reverse=True)
        kept, per_signal = [], {}
        for f in ranked:
            sk = describe(f.candidate)
            if per_signal.get(sk, 0) >= 3:
                continue
            per_signal[sk] = per_signal.get(sk, 0) + 1
            kept.append(f)
            if len(kept) >= self.keep * 2:
                break
        self.findings = {f.key: f for f in kept}
        self.log(f"Round {round_no} terminé : {len(self.findings)} candidats retenus pour la suite")

    def shortlist(self) -> list[Finding]:
        ranked = sorted(self.findings.values(), key=lambda f: f.score, reverse=True)
        for f in ranked[self.rules.shortlist:]:
            f.verdict = "non présenté à la validation"
        return ranked[: self.rules.shortlist]

    def validate(self, cands: list[Finding], t_min: float) -> list[Finding]:
        """Test hors-échantillon : c'est ici que le chef élimine les stratégies sur-optimisées."""
        oos = {candidate_key(c): r for c, r in self.ev.evaluate([f.candidate for f in cands], "oos")}
        approved = []
        rej = {"trop peu de trades OOS": 0, "espérance OOS négative/faible": 0, "PF OOS < seuil": 0,
               "edge OOS non significatif": 0, "dégradation IS->OOS trop forte": 0}
        R = self.rules
        for f in cands:
            f.oos_res = oos.get(f.key)
            o = f.oos_res
            if o is None or o["trades"] < R.min_trades_oos:
                f.verdict = "rejeté : trop peu de trades OOS"
                rej["trop peu de trades OOS"] += 1
            elif o["avg_r"] < R.min_oos_avg_r:
                f.verdict = "rejeté : espérance OOS négative/faible"
                rej["espérance OOS négative/faible"] += 1
            elif o["profit_factor"] < R.min_oos_pf:
                f.verdict = "rejeté : PF OOS < seuil"
                rej["PF OOS < seuil"] += 1
            elif o["sharpe"] < t_min:
                f.verdict = "rejeté : edge OOS non significatif"
                rej["edge OOS non significatif"] += 1
            elif f.is_res["avg_r"] > 0 and o["avg_r"] < (1 - R.max_degradation) * f.is_res["avg_r"]:
                f.verdict = "rejeté : dégradation IS->OOS trop forte"
                rej["dégradation IS->OOS trop forte"] += 1
            else:
                f.verdict = "APPROUVÉ"
                approved.append(f)
        details = ", ".join(f"{k}: {v}" for k, v in rej.items() if v)
        self.log(f"Validation hors-échantillon (t-stat min {t_min:.2f}) : {len(approved)}/{len(cands)} approuvés"
                 + (f" (rejets -> {details})" if details else ""))
        return approved

    def champions(self, n: int = 12) -> list[Finding]:
        return sorted(self.findings.values(), key=lambda f: f.score, reverse=True)[:n]


def build_team(seed: int, budget: int, evaluator: Evaluator, journal: Journal, rules: ValidationRules):
    rng = random.Random(seed)
    explorers = [cls(random.Random(rng.random()), budget) for cls in
                 (TrendAgent, MeanReversionAgent, BreakoutAgent, MomentumAgent, PriceActionAgent)]
    optimizers = [cls(random.Random(rng.random()), budget) for cls in
                  (FilterAgent, ComboAgent, RROptimizerAgent, RandomAgent, GeneticAgent)]
    lead_a = TeamLead("Chef A (Exploration)", "cartographier toutes les familles de stratégies",
                      explorers, evaluator, journal, rules)
    lead_b = TeamLead("Chef B (Optimisation)", "filtrer, combiner et optimiser le R:R des champions",
                      optimizers, evaluator, journal, rules)
    return lead_a, lead_b
