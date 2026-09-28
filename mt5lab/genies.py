"""Les deux génies : EINSTEIN (physicien) et HAWKING (cosmologiste / mathématicien).

Ils ne jouent pas avec les indicateurs classiques : ils INVENTENT DES FORMULES MATHÉMATIQUES.
Chaque génie part de grandeurs de sa discipline et les assemble librement (+, -, ×, ÷, tanh, carré, racine,
lissage, retard, z-score, max, min...) par programmation génétique : des milliers de formules naissent, sont
testées, se croisent et mutent. Ils peuvent aussi prendre comme ingrédients les meilleures stratégies trouvées
par les agents, et les prix des autres marchés.

- EINSTEIN (physique) : vitesse et accélération du prix, énergie cinétique (masse = volume), impulsion,
  force de rappel d'un ressort (loi de Hooke), frottement, relativité (un marché vu depuis un autre).
- HAWKING (maths et cosmologie) : exposant de Hurst (mémoire du marché), entropie de Shannon (désordre),
  asymétrie et queues épaisses (événements extrêmes, « trous noirs »), tendance de Holt (filtre),
  autocorrélation (cycles), gravité autour du prix moyen pondéré par le volume.

Une formule devient une stratégie : on calcule la formule à chaque clôture, on la normalise (z-score sur 200
bougies, sans regarder le futur), et on ACHÈTE quand elle franchit +k vers le haut (VENTE au miroir sous -k),
ou l'inverse si le génie a trouvé que le marché fait le contraire (« sens -1 »).

Les génies travaillent SEULS : aucun chef ne confirme leurs formules. Chacun se contrôle lui-même sur une période
qu'il n'a pas utilisée pour inventer, puis présente ses 3 meilleures lois. La plateforme les passe ensuite au même
contrôle hors-échantillon que tout le monde (sinon impossible de savoir si une loi est vraie ou un hasard) ;
toutes leurs découvertes restent affichées dans leur section, validées ou non.
"""
from __future__ import annotations

import copy
import json
import math
import random

import numpy as np
import pandas as pd

from . import indicators as ind

EPS = 1e-9


# ============================================================================ grandeurs de base (toutes causales)
def _r1(df):
    return np.log(df["close"].astype(float)).diff()


def _vol(df):
    return _r1(df).rolling(50, min_periods=20).std().replace(0, np.nan)


def _atr(df):
    return ind.atr(df, 14).replace(0, np.nan)


def _vratio(df):
    v = df["volume"].astype(float) if "volume" in df.columns else pd.Series(1.0, index=df.index)
    return v / v.rolling(50, min_periods=10).mean().replace(0, np.nan)


def _ext(df, s):
    col = f"ext:{s}"
    return df[col].astype(float) if col in df.columns else pd.Series(np.nan, index=df.index)


PRIMS: dict[str, tuple] = {}  # nom -> (fonction(df) -> Series, libellé, génie : "E", "H" ou "*" = les deux)


def prim(name, label, who):
    def deco(fn):
        PRIMS[name] = (fn, label, who)
        return fn
    return deco


@prim("rendement", "r", "*")
def p_ret(df):
    return _r1(df) / _vol(df)


@prim("rendement5", "r₅", "*")
def p_ret5(df):
    return np.log(df["close"].astype(float)).diff(5) / (_vol(df) * math.sqrt(5))


@prim("amplitude", "amplitude", "*")
def p_range(df):
    return (df["high"] - df["low"]) / _atr(df)


@prim("corps", "corps", "*")
def p_body(df):
    return (df["close"] - df["open"]) / _atr(df)


@prim("volume", "volume", "*")
def p_volume(df):
    return _vratio(df) - 1


@prim("position", "position", "*")
def p_pos(df):
    lo, hi = df["low"].rolling(50).min(), df["high"].rolling(50).max()
    return (df["close"] - lo) / (hi - lo).replace(0, np.nan) - 0.5


# ---- Einstein : physique
@prim("vitesse", "v", "E")
def p_speed(df):
    return _r1(df).ewm(span=5, adjust=False).mean() / _vol(df)


@prim("acceleration", "a", "E")
def p_acc(df):
    return p_speed(df).diff(3)


@prim("energie", "E꜀", "E")
def p_energy(df):
    v = p_speed(df)
    return 0.5 * _vratio(df) * v * v.abs()  # ½ m v², avec le signe du mouvement


@prim("impulsion", "p", "E")
def p_momentum(df):
    return _vratio(df) * _r1(df) / _vol(df)


@prim("ressort", "F", "E")
def p_spring(df):
    return (ind.ema(df["close"], 50) - df["close"]) / _atr(df)  # force de rappel -k·x


@prim("frottement", "μ", "E")
def p_friction(df):
    return p_range(df) / (p_ret5(df).abs() + 0.1)


# ---- Hawking : mathématiques et cosmologie
@prim("hurst", "H", "H")
def p_hurst(df):
    r1 = _r1(df)
    r5 = np.log(df["close"].astype(float)).diff(5)
    return r5.rolling(100, min_periods=50).var() / (5 * r1.rolling(100, min_periods=50).var()).replace(0, np.nan) - 1


@prim("entropie", "S", "H")
def p_entropy(df):
    p = (_r1(df) > 0).astype(float).rolling(50, min_periods=20).mean().clip(1e-6, 1 - 1e-6)
    return -(p * np.log2(p) + (1 - p) * np.log2(1 - p)) - 1


@prim("asymetrie", "γ", "H")
def p_skew(df):
    return _r1(df).rolling(100, min_periods=50).skew()


@prim("queues", "κ", "H")
def p_kurt(df):
    return _r1(df).rolling(100, min_periods=50).kurt()


@prim("holt", "τ", "H")
def p_holt(df):
    lvl = df["close"].astype(float).ewm(alpha=0.1, adjust=False).mean()
    return lvl.diff().ewm(alpha=0.1, adjust=False).mean() / _atr(df)


@prim("cycle", "ρ", "H")
def p_cycle(df):
    r1 = _r1(df)
    return r1.rolling(100, min_periods=50).corr(r1.shift(1))


@prim("gravite", "G", "H")
def p_gravity(df):
    v = df["volume"].astype(float) if "volume" in df.columns else pd.Series(1.0, index=df.index)
    vw = (df["close"] * v).rolling(100, min_periods=30).sum() / v.rolling(100, min_periods=30).sum().replace(0, np.nan)
    return (vw - df["close"]) / _atr(df)


# ---- feuilles spéciales : relativité (autre marché) et stratégies des agents
def _leaf_ext(df, s):
    """Relativité : mouvement de ce marché vu depuis l'autre marché (écart de rendements normalisés sur 5 bougies)."""
    e = _ext(df, s)
    re5 = np.log(e).diff(5) / (np.log(e).diff().rolling(50, min_periods=20).std() * math.sqrt(5)).replace(0, np.nan)
    return p_ret5(df) - re5


def _leaf_strat(df, leaf):
    """Stratégie d'un agent utilisée comme ingrédient : son signal (+1/-1/0) lissé sur 10 bougies."""
    from .evaluator import compute_signal
    from .strategies import apply_filter
    s = apply_filter(df, compute_signal(df, leaf["sig"]), leaf.get("flt", "none")).astype(float)
    return s.ewm(span=10, adjust=False).mean()


# ============================================================================ opérateurs
UNARY = {
    "neg": (lambda x: -x, "−{a}"),
    "tanh": (np.tanh, "tanh({a})"),
    "abs": (lambda x: x.abs(), "|{a}|"),
    "carre": (lambda x: x * x.abs(), "{a}²"),
    "racine": (lambda x: np.sign(x) * np.sqrt(x.abs()), "√{a}"),
    "lisse": (lambda x: x.ewm(span=10, adjust=False).mean(), "lissé({a})"),
    "retard": (lambda x: x.shift(3), "{a}[t−3]"),
    "variation": (lambda x: x.diff(3), "Δ{a}"),
    "z": (lambda x: (x - x.rolling(100, min_periods=30).mean()) / x.rolling(100, min_periods=30).std().replace(0, np.nan),
          "z({a})"),
}
BINARY = {
    "add": (lambda a, b: a + b, "{a} + {b}"),
    "sub": (lambda a, b: a - b, "{a} − {b}"),
    "mul": (lambda a, b: a * b, "{a} × {b}"),
    "div": (lambda a, b: a / (b.abs() + 0.05) * np.sign(b).replace(0, 1), "{a} ÷ {b}"),
    "max": (lambda a, b: np.maximum(a, b), "max({a}, {b})"),
    "min": (lambda a, b: np.minimum(a, b), "min({a}, {b})"),
}
CONSTS = [-2.0, -1.0, -0.5, 0.5, 1.0, 2.0, 3.0]
K_LEVELS = [0.5, 1.0, 1.5, 2.0, 2.5]

_CACHE: dict = {}


def eval_expr(df: pd.DataFrame, node: dict) -> pd.Series:
    key = (id(df), len(df), df.index[0], df.index[-1], json.dumps(node, sort_keys=True, default=str))
    if key in _CACHE:
        return _CACHE[key]
    if "c" in node:
        out = pd.Series(float(node["c"]), index=df.index)
    elif "f" in node:
        f = node["f"]
        if f == "ext_rel":
            out = _leaf_ext(df, node.get("s"))
        elif f == "strat":
            out = _leaf_strat(df, node)
        else:
            out = PRIMS[f][0](df)
    elif node["op"] in UNARY:
        out = UNARY[node["op"]][0](eval_expr(df, node["a"]))
    else:
        out = BINARY[node["op"]][0](eval_expr(df, node["a"]), eval_expr(df, node["b"]))
    out = pd.Series(out, index=df.index).astype(float).replace([np.inf, -np.inf], np.nan)
    if len(_CACHE) > 3000:
        _CACHE.clear()
    _CACHE[key] = out
    return out


def formula_signal(df: pd.DataFrame, spec: dict) -> pd.Series:
    """+1 quand la formule normalisée franchit +k vers le haut, -1 quand elle franchit -k vers le bas
    (inversé si sens = -1). Normalisation par z-score glissant sur 200 bougies : aucune donnée future."""
    x = eval_expr(df, spec["expr"])
    z = (x - x.rolling(200, min_periods=50).mean()) / x.rolling(200, min_periods=50).std().replace(0, np.nan)
    k = float(spec.get("k", 1.5))
    prev = z.shift(1)
    up = (z > k) & (prev <= k)
    dn = (z < -k) & (prev >= -k)
    sens = int(spec.get("sens", 1))
    out = np.where(up, 1, np.where(dn, -1, 0)) * sens
    if not spec.get("mirror", True):
        out = np.where(out > 0, out, 0)
    return pd.Series(out.astype(np.int8), index=df.index)


def expr_text(node: dict) -> str:
    if "c" in node:
        return f"{node['c']:g}"
    if "f" in node:
        if node["f"] == "ext_rel":
            return f"relativité[{node.get('s')}]"
        if node["f"] == "strat":
            return f"stratégie⟨{node.get('label', 'agent')}⟩"
        return PRIMS[node["f"]][1]
    if node["op"] in UNARY:
        inner = expr_text(node["a"])
        return UNARY[node["op"]][1].format(a=f"({inner})" if node["op"] in ("carre", "neg") and " " in inner else inner)
    a, b = expr_text(node["a"]), expr_text(node["b"])
    wrap = lambda t: f"({t})" if (" + " in t or " − " in t) and node["op"] in ("mul", "div") else t
    return BINARY[node["op"]][1].format(a=wrap(a), b=wrap(b))


def symbols_text(node: dict, out=None) -> dict:
    """Légende des symboles utilisés dans une formule."""
    out = {} if out is None else out
    if "f" in node and node["f"] in PRIMS:
        out[PRIMS[node["f"]][1]] = GLOSSARY.get(node["f"], node["f"])
    for k in ("a", "b"):
        if k in node:
            symbols_text(node[k], out)
    return out


GLOSSARY = {
    "rendement": "rendement de la bougie en écarts-types", "rendement5": "rendement sur 5 bougies en écarts-types",
    "amplitude": "hauteur de la bougie en ATR", "corps": "corps de la bougie en ATR",
    "volume": "volume relatif (0 = normal)", "position": "position dans le range des 50 dernières bougies",
    "vitesse": "vitesse du prix (moyenne des rendements récents / volatilité)",
    "acceleration": "accélération (variation de la vitesse)", "energie": "énergie cinétique ½·masse·v² (masse = volume)",
    "impulsion": "impulsion p = masse × vitesse", "ressort": "force de rappel vers l'EMA 50 (loi de Hooke)",
    "frottement": "frottement : amplitude dépensée par unité de déplacement",
    "hurst": "mémoire du marché (ratio de variance : > 0 tendance, < 0 retour)",
    "entropie": "entropie de Shannon des hausses/baisses (0 = ordre total, -1 = hasard pur)",
    "asymetrie": "asymétrie des rendements", "queues": "épaisseur des queues (événements extrêmes)",
    "holt": "pente de la tendance filtrée (Holt)", "cycle": "autocorrélation (cycle court)",
    "gravite": "attraction du prix moyen pondéré par le volume",
}


def describe_formula(spec: dict) -> str:
    sens = "" if int(spec.get("sens", 1)) > 0 else " (sens inverse : on joue le contraire)"
    return (f"{spec.get('name', 'LOI')} : ACHAT quand z[{expr_text(spec['expr'])}] franchit +{float(spec.get('k', 1.5)):g}"
            f"{' (vente au miroir)' if spec.get('mirror', True) else ' (achats seulement)'}{sens}")


def nodes(node: dict) -> int:
    return 1 + sum(nodes(node[k]) for k in ("a", "b") if k in node)


def leaves_used(node: dict, out=None) -> list:
    out = [] if out is None else out
    if "f" in node:
        out.append(node)
    for k in ("a", "b"):
        if k in node:
            leaves_used(node[k], out)
    return out


# ============================================================================ les génies
GENIES = [
    ("EINSTEIN", "Génie 1 Einstein", "Physicien : vitesse, accélération, énergie, impulsion, ressort, relativité", "E"),
    ("HAWKING", "Génie 2 Hawking", "Cosmologiste et mathématicien : Hurst, entropie, queues, cycles, gravité", "H"),
]


class Genius:
    def __init__(self, code: str, tag: str, role: str, who: str, rng: random.Random, ext: list,
                 strategies: list[dict]):
        self.code, self.tag, self.role, self.who = code, tag, role, who
        self.rng = rng
        self.ext = ext
        self.strategies = strategies  # stratégies des agents utilisables comme ingrédients
        self.prims = [p for p, (_, _, w) in PRIMS.items() if w in (who, "*")]
        # chaque génie privilégie SA discipline
        self.prims += [p for p, (_, _, w) in PRIMS.items() if w == who] * 2
        self.tested = 0
        self.counter = 0

    def leaf(self) -> dict:
        r = self.rng.random()
        if self.strategies and r < 0.12:
            s = self.rng.choice(self.strategies)
            return {"f": "strat", "sig": s["sig"], "flt": s["flt"], "label": s["label"]}
        if self.ext and r < (0.3 if self.who == "E" else 0.18):  # Einstein : la relativité est sa spécialité
            return {"f": "ext_rel", "s": self.rng.choice(self.ext)}
        if r > 0.93:
            return {"c": self.rng.choice(CONSTS)}
        return {"f": self.rng.choice(self.prims)}

    def grow(self, depth: int) -> dict:
        if depth <= 0 or (depth < 3 and self.rng.random() < 0.3):
            return self.leaf()
        if self.rng.random() < 0.4:
            return {"op": self.rng.choice(list(UNARY)), "a": self.grow(depth - 1)}
        return {"op": self.rng.choice(list(BINARY)), "a": self.grow(depth - 1), "b": self.grow(depth - 1)}

    def random_spec(self) -> dict:
        expr = self.grow(self.rng.choice([2, 3, 3, 4]))
        while "c" in expr:
            expr = self.grow(3)
        return {"type": "formula", "genie": self.code, "expr": expr, "k": self.rng.choice(K_LEVELS),
                "sens": self.rng.choice([1, 1, -1]), "mirror": self.rng.random() < 0.9}

    def _paths(self, node, path=()):
        yield path
        for k in ("a", "b"):
            if k in node:
                yield from self._paths(node[k], path + (k,))

    @staticmethod
    def _get(node, path):
        for k in path:
            node = node[k]
        return node

    def _set(self, root, path, new):
        if not path:
            return new
        parent = self._get(root, path[:-1])
        parent[path[-1]] = new
        return root

    def mutate(self, spec: dict) -> dict:
        s = copy.deepcopy(spec)
        r = self.rng.random()
        if r < 0.15:
            s["k"] = self.rng.choice(K_LEVELS)
        elif r < 0.22:
            s["sens"] = -int(s.get("sens", 1))
        elif r < 0.55:  # remplace un morceau de la formule par une nouvelle idée
            path = self.rng.choice(list(self._paths(s["expr"])))
            s["expr"] = self._set(s["expr"], path, self.grow(self.rng.choice([1, 2])))
        elif r < 0.8:  # change un opérateur ou une grandeur
            path = self.rng.choice(list(self._paths(s["expr"])))
            n = self._get(s["expr"], path)
            if "op" in n:
                n["op"] = self.rng.choice(list(UNARY if n["op"] in UNARY else BINARY))
            else:
                s["expr"] = self._set(s["expr"], path, self.leaf())
        else:  # simplifie : garde une sous-formule (rasoir d'Ockham)
            paths = [p for p in self._paths(s["expr"]) if p]
            if paths:
                sub = self._get(s["expr"], self.rng.choice(paths))
                if "c" not in sub:
                    s["expr"] = copy.deepcopy(sub)
        return self._trim(s)

    def crossover(self, a: dict, b: dict) -> dict:
        s = copy.deepcopy(a)
        pa = self.rng.choice(list(self._paths(s["expr"])))
        pb = self.rng.choice(list(self._paths(b["expr"])))
        s["expr"] = self._set(s["expr"], pa, copy.deepcopy(self._get(b["expr"], pb)))
        return self._trim(s)

    def _trim(self, s: dict, max_nodes: int = 15) -> dict:
        while nodes(s["expr"]) > max_nodes:
            paths = [p for p in self._paths(s["expr"]) if p]
            s["expr"] = copy.deepcopy(self._get(s["expr"], self.rng.choice(paths)))
        if "c" in s["expr"]:
            s["expr"] = self.leaf() if self.rng.random() < 0.5 else {"f": self.rng.choice(self.prims)}
        return s

    def name(self, spec: dict) -> dict:
        self.counter += 1
        spec = copy.deepcopy(spec)
        who = "D'EINSTEIN" if self.code == "EINSTEIN" else "DE HAWKING"
        spec["name"] = f"LOI {who} n°{self.counter}"
        return spec


def _fitness(res: dict, spec: dict, min_trades: int) -> float:
    if res["trades"] < min_trades:
        return -math.inf
    return res["sharpe"] - 0.04 * nodes(spec["expr"])  # une loi simple vaut mieux qu'une loi compliquée


def discover(genius: Genius, evaluator, part_train: str, part_check: str, generations: int = 12, pop: int = 60,
             min_trades: int = 30, n_best: int = 3, log=print) -> list[dict]:
    """Programmation génétique de formules. Le génie se contrôle lui-même sur part_check (période non utilisée
    pour inventer) et garde ses n_best lois les plus solides sur LES DEUX périodes. Aucun chef ne confirme."""
    from .inventions import INVENTION_RISKS
    population = [genius.random_spec() for _ in range(pop)]
    scored = []
    for gen in range(generations):
        cands = [{"signal": s, "filter": "none", "risk": r} for s in population for r in INVENTION_RISKS]
        before = evaluator.n_evals
        results = evaluator.evaluate(cands, part_train)
        genius.tested += max(0, evaluator.n_evals - before)
        best: dict = {}
        for c, res in results:
            k = json.dumps(c["signal"], sort_keys=True, default=str)
            f = _fitness(res, c["signal"], min_trades)
            if k not in best or f > best[k][0]:
                best[k] = (f, c, res)
        scored = sorted(best.values(), key=lambda t: t[0], reverse=True)
        elite = [t[1]["signal"] for t in scored[: max(6, pop // 5)] if math.isfinite(t[0])]
        if not elite:
            elite = [genius.random_spec() for _ in range(6)]
        children = []
        while len(children) < pop - len(elite):
            if len(elite) > 1 and genius.rng.random() < 0.35:
                children.append(genius.mutate(genius.crossover(*genius.rng.sample(elite, 2))))
            elif genius.rng.random() < 0.1:
                children.append(genius.random_spec())  # une idée neuve de temps en temps
            else:
                children.append(genius.mutate(genius.rng.choice(elite)))
        population = elite + children
        if scored and math.isfinite(scored[0][0]) and (gen == 0 or gen == generations - 1 or gen % 4 == 3):
            log(genius.tag, f"génération {gen + 1}/{generations} : meilleure loi t = {scored[0][2]['sharpe']:.2f} "
                            f"({scored[0][2]['trades']} trades) : {expr_text(scored[0][1]['signal']['expr'])}")
    finalists = [t for t in scored[:15] if math.isfinite(t[0])]
    if not finalists:
        log(genius.tag, "aucune loi n'a assez de trades sur cette période")
        return []
    checks = {json.dumps(c["signal"], sort_keys=True, default=str): res
              for c, res in evaluator.evaluate([t[1] for t in finalists], part_check)}
    ranked = []
    for f, cand, res_train in finalists:
        chk = checks.get(json.dumps(cand["signal"], sort_keys=True, default=str))
        if not chk or chk["trades"] < 8:
            continue
        solid = min(res_train["sharpe"], chk["sharpe"] * math.sqrt(max(res_train["trades"], 1) / max(chk["trades"], 1)))
        ranked.append((solid, cand, res_train, chk))
    ranked.sort(key=lambda t: t[0], reverse=True)
    out, seen = [], set()
    for solid, cand, res_train, chk in ranked:
        k = expr_text(cand["signal"]["expr"])
        if k in seen:
            continue
        seen.add(k)
        cand = {**cand, "signal": genius.name(cand["signal"])}
        log(genius.tag, f"DÉCOUVERTE : {describe_formula(cand['signal'])} | invention : {res_train['trades']} trades, "
                        f"{res_train['avg_r']:+.2f}R/trade | mon contrôle : {chk['trades']} trades, "
                        f"{chk['avg_r']:+.2f}R/trade, PF {chk['profit_factor']:.2f}")
        out.append({"candidate": cand, "train": res_train, "check": chk, "solidite": solid})
        if len(out) >= n_best:
            break
    return out
