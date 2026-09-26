"""Fiches détaillées des stratégies : tout ce qu'il faut pour la trader à la main, en paper trading ou la coder en bot.

Pour chaque stratégie : règles d'entrée en français, filtre, sens, stop, objectif, gestion, durée max, taille de
position, horaires, statistiques, pseudo-code, le code Python EXACT de la logique (référence pour un développeur
MQL5), et un fichier JSON directement utilisable par le bot Python de la plateforme (python run.py live ...).
"""
from __future__ import annotations

import html
import inspect
import json
import re
from pathlib import Path

from .backtest import RiskConfig
from .evaluator import describe

FILTER_TEXT = {
    "none": "Aucun filtre.",
    "trend_ema200": "Achats seulement si la clôture est au-dessus de l'EMA 200 ; ventes seulement en dessous.",
    "trend_ema50": "Achats seulement si la clôture est au-dessus de l'EMA 50 ; ventes seulement en dessous.",
    "adx_strong": "Seulement si l'ADX(14) est au-dessus de 25 (marché en tendance).",
    "adx_weak": "Seulement si l'ADX(14) est sous 20 (marché sans tendance).",
    "vol_high": "Seulement si l'ATR(14) est au-dessus de sa médiane des 200 dernières bougies (volatilité forte).",
    "vol_low": "Seulement si l'ATR(14) est sous sa médiane des 200 dernières bougies (volatilité faible).",
    "session_london_ny": "Seulement entre 7h et 17h (heure du serveur MT5).",
    "kill_zones": "Seulement de 7h à 10h et de 12h à 15h (killzones ICT, heure du serveur MT5).",
    "chop_trending": "Seulement si le Choppiness(14) est sous 45 (marché directionnel).",
    "chop_ranging": "Seulement si le Choppiness(14) est au-dessus de 55 (marché en range).",
}
DIRECTION_TEXT = {"both": "Achats et ventes.", "long": "Achats seulement.", "short": "Ventes seulement."}


def slug(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s[:80] or "strategie"


def stop_text(r: dict) -> str:
    if r["sl_mode"] == "atr":
        return f"Stop à {r['sl_value']:g} × ATR(14) du prix d'entrée (ATR mesuré sur la bougie du signal)."
    if r["sl_mode"] == "swing":
        return (f"Stop sous le plus bas (achat) / au-dessus du plus haut (vente) des {int(r['sl_value'])} dernières "
                f"bougies, au minimum 0,25 × ATR(14) du prix d'entrée.")
    return f"Stop à {r['sl_value']:g} % du prix d'entrée."


def target_text(r: dict) -> str:
    if r["rr"] is None:
        return "Pas de TP fixe : sortie à la clôture de la bougie où le signal inverse apparaît."
    return f"TP à {r['rr']:g} × la distance du stop (R:R 1:{r['rr']:g})."


def management_text(r: dict) -> str:
    return {"none": "Aucune : le stop et le TP ne bougent pas.",
            "breakeven": "Break-even : quand une bougie clôture à +1R, le stop est remonté au prix d'entrée.",
            "trailing": ("Stop suiveur : à chaque clôture, stop = clôture ∓ " +
                         (f"{r['sl_value']:g} × ATR(14)" if r["sl_mode"] == "atr" else "la distance du stop initial") +
                         ", il ne recule jamais.")}.get(r["management"], r["management"])


def _helpers(src: str, module) -> list[str]:
    """Code des fonctions du même module utilisées par la stratégie (un niveau)."""
    out = []
    for name, obj in inspect.getmembers(module, inspect.isfunction):
        if obj.__module__ == module.__name__ and re.search(rf"\b{name}\(", src) and f"def {name}(" not in src:
            try:
                out.append(inspect.getsource(obj))
            except (OSError, TypeError):
                pass
    return out


def logic_source(signal: dict) -> str:
    """Code Python exact qui calcule le signal (+1 achat, -1 vente, 0 rien) à chaque clôture de bougie."""
    from . import inventions
    from .strategies import REGISTRY
    if signal["type"] == "single":
        fn = REGISTRY[signal["name"]].func
        src = inspect.getsource(fn)
        mod = inspect.getmodule(fn)
        return "\n\n".join([src] + _helpers(src, mod))
    if signal["type"] == "rule":
        conds = [signal["trigger"]] + signal.get("filters", [])
        parts = [inspect.getsource(inventions.rule_signal), inspect.getsource(inventions._cond)]
        for f in dict.fromkeys(c["f"] for c in conds):
            parts.append(inspect.getsource(inventions.FEATURES[f][0]))
        return "\n\n".join(parts)
    return logic_source(signal["a"]) + "\n\n# ----- combinée avec -----\n\n" + logic_source(signal["b"])


def entry_text(signal: dict) -> list[str]:
    from .inventions import _cond_text
    from .strategies import REGISTRY
    if signal["type"] == "single":
        sd = REGISTRY[signal["name"]]
        params = ", ".join(f"{k} = {v}" for k, v in signal["params"].items()) or "aucun"
        return [f"Stratégie « {sd.name} » ({sd.family}) : {sd.description}.", f"Réglages : {params}.",
                "ACHAT quand la stratégie donne +1 à la clôture de la bougie, VENTE quand elle donne -1 "
                "(logique exacte dans le code ci-dessous)."]
    if signal["type"] == "rule":
        trig = _cond_text(signal["trigger"])
        lines = [f"Règle « {signal.get('name', 'INVENTION')} ».",
                 f"ACHAT à la clôture de la bougie où cette condition DEVIENT vraie : {trig}."]
        for c in signal.get("filters", []):
            lines.append(f"... et en même temps : {_cond_text(c)}.")
        if signal.get("mirror", True):
            lines.append("VENTE : exact miroir (chaque indicateur directionnel est inversé : > 0,3 devient < -0,3, "
                         "etc. ; les heures, jours et volumes restent identiques).")
        else:
            lines.append("Pas de vente : achats seulement.")
        return lines
    return [f"Combinaison : signal de A « {describe({'signal': signal['a'], 'filter': 'none'})} », "
            f"validé seulement si B « {describe({'signal': signal['b'], 'filter': 'none'})} » a donné le même sens "
            + ("sur la même bougie." if signal["mode"] == "and" else f"dans les {signal['window']} dernières bougies.")]


def pseudo_code(c: dict, tf: str, risk_pct: float | None) -> str:
    r = c["risk"]
    risk = f"{risk_pct:g}" if risk_pct is not None else "X"
    lines = [f"À CHAQUE CLÔTURE D'UNE BOUGIE {tf} :",
             "    signal = calculer_signal(bougies clôturées)          # +1 achat, -1 vente, 0 rien",
             f"    signal = appliquer_filtre(signal, '{c['filter']}')"]
    if r.get("direction", "both") != "both":
        lines.append(f"    ignorer les signaux {'de vente' if r['direction'] == 'long' else 'd achat'}")
    lines += ["    SI une position de cette stratégie est ouverte :",
              "        bougies_tenues += 1"]
    if r["rr"] is None:
        lines.append("        SI signal = sens inverse de la position : fermer au marché")
    lines.append(f"        SI bougies_tenues >= {r.get('max_hold', 200)} : fermer au marché")
    if r["management"] == "breakeven":
        lines.append("        SI clôture a avancé d'au moins 1R : stop = prix d'entrée")
    elif r["management"] == "trailing":
        lines.append("        stop = max(stop, clôture - distance)  (achat)  /  min(stop, clôture + distance)  (vente)")
    lines += ["    SINON SI signal != 0 :",
              "        prix = ask (achat) ou bid (vente)",
              "        distance = " + ({"atr": f"{r['sl_value']:g} × ATR(14)", "pct": f"prix × {r['sl_value']:g} %",
                                         "swing": f"écart au plus bas/haut des {int(r['sl_value'])} dernières bougies "
                                                  "(min 0,25 × ATR)"}[r["sl_mode"]]),
              "        stop = prix - distance (achat) / prix + distance (vente)",
              ("        tp = prix ± " + f"{r['rr']:g} × distance") if r["rr"] is not None else
              "        tp = aucun (sortie au signal inverse)",
              f"        lots = (capital × {risk} %) / (distance / taille_du_tick × valeur_du_tick)   # arrondi vers le bas",
              "        ouvrir la position avec stop et tp",
              "LE STOP ET LE TP SONT SURVEILLÉS À CHAQUE TICK (ordres stop/limit côté courtier)."]
    return "\n".join(lines)


def build_card(c: dict, symbol: str, tf: str, stats: dict | None = None, risk_pct: float | None = None,
               origin: str = "", extra_rules: dict | None = None) -> dict:
    r = c["risk"]
    name = describe(c)
    return {
        "id": slug(f"{symbol}-{tf}-{name}-{RiskConfig(**r).label()}"),
        "nom": name, "marche": symbol, "timeframe": tf, "origine": origin,
        "reglage": RiskConfig(**r).label(),
        "entree": entry_text(c["signal"]),
        "filtre": FILTER_TEXT.get(c["filter"], c["filter"]),
        "sens": DIRECTION_TEXT.get(r.get("direction", "both"), r.get("direction")),
        "stop": stop_text(r), "objectif": target_text(r), "gestion": management_text(r),
        "duree_max": f"Fermeture au marché après {r.get('max_hold', 200)} bougies {tf} si ni le stop ni le TP n'est touché.",
        "execution": [f"Signal calculé à la clôture de chaque bougie {tf} (bougies clôturées seulement, "
                      "rien ne regarde le futur).",
                      "Entrée au prix du marché juste après la clôture : ask pour un achat, bid pour une vente.",
                      "Une seule position à la fois pour cette stratégie.",
                      "Heures : celles du serveur MT5 de votre courtier."],
        "taille": (f"Risque {risk_pct:g} % du capital par trade : lots = (capital × {risk_pct:g} %) / "
                   "(distance du stop / taille du tick × valeur du tick), arrondi vers le bas au pas de lot."
                   if risk_pct is not None else "Risque par trade à choisir (ex. 0,5 % du capital)."),
        "regles_compte": extra_rules or {},
        "stats": stats or {},
        "pseudo_code": pseudo_code(c, tf, risk_pct),
        "code_python": logic_source(c["signal"]),
        "candidate": c, "risk_pct": risk_pct,
    }


CSS = """
:root{--bg:#f7f8fa;--card:#fff;--fg:#1c2230;--mut:#667085;--line:#e4e7ec;--acc:#2a78d6;--code:#f1f3f6}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e6e8ee;--mut:#98a2b3;--line:#2a2f3a;--acc:#3987e5;--code:#11141a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.55 system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:auto;padding:20px 16px}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:0}
.mut{color:var(--mut)}a{color:var(--acc)}
.toc{columns:2 320px;font-size:13px;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:18px 0}
dl{display:grid;grid-template-columns:170px 1fr;gap:6px 14px;margin:12px 0}dt{color:var(--mut)}dd{margin:0}
@media (max-width:640px){dl{grid-template-columns:1fr}}
pre{background:var(--code);border:1px solid var(--line);border-radius:8px;padding:10px;overflow:auto;font-size:12px;max-height:420px}
details summary{cursor:pointer;color:var(--acc);margin:6px 0}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;border:1px solid var(--line);font-size:12px;margin-left:6px}
table{border-collapse:collapse;font-size:12.5px}td{padding:3px 12px 3px 0}
"""


def write_cards(cards: list[dict], out_dir: Path, title: str = "Fiches des stratégies") -> Path:
    out_dir = Path(out_dir)
    (out_dir / "fiches").mkdir(parents=True, exist_ok=True)
    esc = html.escape
    for card in cards:
        (out_dir / "fiches" / f"{card['id']}.json").write_text(
            json.dumps({k: v for k, v in card.items() if k != "code_python"}, indent=2, ensure_ascii=False,
                       default=str), encoding="utf-8")
    toc = "".join(f"<li><a href='#{c['id']}'>{esc(c['marche'])} {esc(c['timeframe'])} — {esc(c['nom'][:90])}</a></li>"
                  for c in cards)
    body = ""
    for c in cards:
        st = c["stats"]
        stats = "".join(f"<tr><td class='mut'>{esc(k)}</td><td>{esc(str(v))}</td></tr>" for k, v in st.items() if v not in (None, ""))
        rules = "".join(f"<li>{esc(k)} : {esc(str(v))}</li>" for k, v in c["regles_compte"].items() if v is not None)
        body += f"""<section class="card" id="{c['id']}">
<h2>{esc(c['nom'])}<span class="tag">{esc(c['marche'])} {esc(c['timeframe'])}</span>{f'<span class="tag">{esc(c["origine"])}</span>' if c['origine'] else ''}</h2>
<p class="mut">{esc(c['reglage'])}</p>
<dl>
<dt>Entrée</dt><dd>{'<br>'.join(esc(x) for x in c['entree'])}</dd>
<dt>Filtre</dt><dd>{esc(c['filtre'])}</dd>
<dt>Sens</dt><dd>{esc(c['sens'])}</dd>
<dt>Stop loss</dt><dd>{esc(c['stop'])}</dd>
<dt>Objectif</dt><dd>{esc(c['objectif'])}</dd>
<dt>Gestion</dt><dd>{esc(c['gestion'])}</dd>
<dt>Durée max</dt><dd>{esc(c['duree_max'])}</dd>
<dt>Taille de position</dt><dd>{esc(c['taille'])}</dd>
<dt>Exécution</dt><dd>{'<br>'.join(esc(x) for x in c['execution'])}</dd>
{f'<dt>Règles du compte</dt><dd><ul>{rules}</ul></dd>' if rules else ''}
</dl>
{f'<table>{stats}</table>' if stats else ''}
<details open><summary>Pseudo-code (pour coder le bot)</summary><pre>{esc(c['pseudo_code'])}</pre></details>
<details><summary>Code Python exact de la logique du signal</summary><pre>{esc(c['code_python'])}</pre></details>
<p class="mut">Fichier pour le bot Python : fiches/{esc(c['id'])}.json</p>
</section>"""
    doc = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{CSS}</style></head><body><main>
<h1>{esc(title)}</h1>
<p class="mut">{len(cards)} stratégies. Chaque fiche contient tout ce qu'il faut pour la trader ou la coder en bot MetaTrader :
règles d'entrée et de sortie, stop, objectif, gestion, taille de position, pseudo-code et code exact.</p>
<ul class="toc">{toc}</ul>{body}
<p class="mut">Pour la faire trader par le bot Python de la plateforme sur un compte DÉMO :
python run.py live --symbol SYMBOLE --timeframe TF --strategies results/fiches/ID.json (simulation par défaut, --execute pour de vrais ordres).</p>
</main></body></html>"""
    path = out_dir / "fiches_strategies.html"
    path.write_text(doc, encoding="utf-8")
    return path
