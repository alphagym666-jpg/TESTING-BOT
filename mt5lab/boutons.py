"""Boutons « Bot MT5 » dans TOUS les rapports (comparaison, Directeur, fiches, comptes...).

Un rapport est un simple fichier : il ne peut pas lancer Python. Le bouton demande donc à la plateforme en marche
(paper trading, options 6, 7, 8, 9, C, M, V du menu) de créer le bot : dossier results/bots/<nom>/ avec
LaboBot.mq5 réglé, LANCER_BOT.bat et LISEZMOI_BOT.txt, installé et compilé directement dans MT5.

Sécurité : la plateforme n'écoute que sur 127.0.0.1 et n'accepte la demande qu'avec le jeton secret du dossier
du projet (fichier .bot_token, jamais envoyé ailleurs) : un site web ne peut pas créer de bot à votre place.
"""
from __future__ import annotations

import html
import json
import secrets
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PORTS = (8765, 8768, 8766, 8767, 8769, 8856, 8857)   # plateformes du menu (la page essaie chacune)


def token() -> str:
    f = REPO / ".bot_token"
    try:
        t = f.read_text(encoding="ascii").strip()
        if len(t) >= 16:
            return t
    except OSError:
        pass
    t = secrets.token_hex(16)
    try:
        f.write_text(t, encoding="ascii")
    except OSError:
        pass
    return t


def single(candidate, symbol: str, tf: str, risk_pct: float = 1.0, name: str | None = None) -> dict:
    from .pont import single_strategy
    cand = json.loads(candidate) if isinstance(candidate, str) else candidate
    return single_strategy(cand, symbol, tf, float(risk_pct), name)


def combined(components: list[dict], name: str, rules: dict | None = None, **extra) -> dict:
    """components : [{symbole, timeframe, candidate, risk_pct, strategie?, risque_config?}, ...]"""
    import time
    comps = []
    for c in components:
        cand = json.loads(c["candidate"]) if isinstance(c["candidate"], str) else c["candidate"]
        comps.append({"symbole": c["symbole"], "timeframe": c["timeframe"], "candidate": cand,
                      "strategie": c.get("strategie", ""), "risque_config": c.get("risque_config", c.get("risque", "")),
                      "risk_pct": float(c.get("risk_pct", 1.0))})
    return {"nom": name, "regles": {"day_budget": 2.5, "total_budget": 10.0, "day_stop": None, "max_open": None,
                                     **(rules or {})},
            "composants": comps, "cree_le": time.strftime("%Y-%m-%d %H:%M"), **extra}


KEEP = ("nom", "regles", "horaire", "essai", "profil", "capital", "composer", "cree_le")
KEEP_C = ("symbole", "timeframe", "candidate", "strategie", "risque_config", "risk_pct")


def slim(comb: dict | None) -> dict | None:
    """Seulement ce qu'il faut pour le bot (pas les résultats de simulation, trop gros pour la page)."""
    if not comb:
        return None
    out = {k: comb[k] for k in KEEP if k in comb}
    out["composants"] = [{k: c[k] for k in KEEP_C if k in c} for c in comb.get("composants", [])]
    return out


def button(comb: dict | None, label: str = "Bot MT5") -> str:
    if not comb or not comb.get("composants"):
        return ""
    data = html.escape(json.dumps(comb, ensure_ascii=False, default=str, separators=(",", ":")), quote=True)
    return (f'<button type="button" class="labbot" data-bot="{data}" '
            f'title="Créer ce bot et l\'installer dans MT5">{html.escape(label)}</button>')


def script(results_dir: str | Path | None = None) -> str:
    """CSS + JS à mettre une fois dans la page (avant </body>)."""
    res = Path(results_dir).name if results_dir else "results"
    cfg = json.dumps({"t": token(), "r": res, "ports": list(PORTS)})
    return """<style>.labbot{font:inherit;font-size:12px;padding:3px 9px;border-radius:8px;border:1px solid currentColor;
background:transparent;color:inherit;cursor:pointer;white-space:nowrap;opacity:.85}.labbot:hover{opacity:1}
.labbot:disabled{opacity:.4;cursor:wait}</style>
<script>(function(){const L=""" + cfg + """;
async function ask(base,q){const r=await fetch(base+"/api/botfichier?"+q,{cache:"no-store"});if(!r.ok)throw new Error(r.status);return r.json()}
document.addEventListener("click",async e=>{const b=e.target.closest(".labbot");if(!b)return;b.disabled=true;
 const q="t="+L.t+"&r="+encodeURIComponent(L.r)+"&c="+encodeURIComponent(b.dataset.bot);
 const bases=(location.protocol.startsWith("http")?[location.origin]:[]).concat(L.ports.map(p=>"http://127.0.0.1:"+p));
 for(const base of bases){try{const j=await ask(base,q);alert(j.message);b.disabled=false;return}catch(err){}}
 alert("Pour créer le bot, une plateforme doit être ouverte (menu lancer.bat : option 6, 9 ou C), puis recliquez.\\n"+
       "Le bot sera créé dans results\\\\bots et installé directement dans MT5.");b.disabled=false})})();</script>"""
