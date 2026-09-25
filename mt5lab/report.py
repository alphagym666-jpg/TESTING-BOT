"""Rapport HTML autonome (aucune dépendance externe)."""
from __future__ import annotations

import html
import json

import numpy as np
import pandas as pd

from .backtest import RiskConfig, run_backtest
from .evaluator import compute_signal
from .strategies import apply_filter

CSS = """
:root{--bg:#f7f8fa;--card:#fff;--fg:#1c2230;--mut:#667085;--line:#e4e7ec;--ok:#12805c;--bad:#b42318;--acc:#2f5bea}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e6e8ee;--mut:#98a2b3;--line:#2a2f3a;--ok:#3ccb7f;--bad:#f97066;--acc:#7c9cff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1200px;margin:auto;padding:24px 16px}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 12px}
.mut{color:var(--mut)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card b{display:block;font-size:22px}.teams{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}
table{width:100%;border-collapse:collapse;background:var(--card);font-size:12.5px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{position:sticky;top:0;background:var(--card)}
.scroll{overflow:auto;max-height:640px;border:1px solid var(--line);border-radius:10px}
.ok{color:var(--ok);font-weight:600}.bad{color:var(--bad)}pre{white-space:pre-wrap;font-size:12px;background:var(--card);
border:1px solid var(--line);border-radius:10px;padding:12px;max-height:480px;overflow:auto}
.warn{border-left:4px solid var(--bad);padding:10px 14px;background:var(--card);border-radius:6px}
svg{width:100%;height:auto}
"""


def _equity_svg(r: np.ndarray, w=360, h=90) -> str:
    if len(r) < 2:
        return ""
    cum = np.concatenate([[0], np.cumsum(r)])
    lo, hi = cum.min(), cum.max()
    span = (hi - lo) or 1
    pts = " ".join(f"{i / (len(cum) - 1) * w:.1f},{h - (v - lo) / span * (h - 8) - 4:.1f}" for i, v in enumerate(cum))
    color = "var(--ok)" if cum[-1] > 0 else "var(--bad)"
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Courbe de gains en R">'
            f'<polyline fill="none" stroke="{color}" stroke-width="1.6" points="{pts}"/></svg>')


def write_report(path, label, board: pd.DataFrame, journal, agents, cfg, n_evals, df):
    esc = html.escape
    approved = board[board["verdict"] == "APPROUVÉ"] if len(board) else board
    cards = [
        ("Backtests uniques", f"{n_evals:,}".replace(",", " ")),
        ("Candidats finalistes", len(board)),
        ("Approuvés (OOS + contre-expertise)", len(approved)),
        ("Meilleure espérance OOS", f"{approved['avgR_oos'].max():+.2f} R" if len(approved) else "—"),
    ]
    cards_html = "".join(f'<div class="card"><span class="mut">{esc(k)}</span><b>{esc(str(v))}</b></div>' for k, v in cards)

    def team(title, ags):
        rows = "".join(f"<tr><td>{esc(a.tag)}</td><td>{esc(a.role)}</td><td>{a.tested}</td></tr>" for a in ags)
        return (f'<div class="card"><b style="font-size:16px">{esc(title)}</b>'
                f'<table><tr><th>Agent</th><th>Spécialité</th><th>Tests</th></tr>{rows}</table></div>')

    teams_html = team("Chef A — Exploration", agents[:5]) + team("Chef B — Optimisation", agents[5:])

    # courbes des 6 meilleures sur tout l'historique
    curves = ""
    for _, row in approved.head(6).iterrows():
        c = json.loads(row["candidate"])
        sig = apply_filter(df, compute_signal(df, c["signal"]), c["filter"])
        _, trades = run_backtest(df, sig, RiskConfig(**c["risk"]), return_trades=True)
        r = trades["r"].to_numpy() if len(trades) else np.array([])
        curves += (f'<div class="card"><span class="mut">{esc(row["risque"])}</span><div>{esc(row["strategie"])}</div>'
                   f'{_equity_svg(r)}<span class="mut">{len(r)} trades | {r.sum():+.1f} R au total</span></div>')

    cols = ["verdict", "strategie", "risque", "trouve_par", "trades_is", "wr_is", "avgR_is", "pf_is",
            "trades_oos", "wr_oos", "avgR_oos", "pf_oos", "ret_oos_pct", "dd_oos_pct", "cout_x2_avgR", "periodes_positives"]
    heads = "".join(f"<th>{esc(c)}</th>" for c in cols)
    body = ""
    for _, row in board.head(200).iterrows():
        tds = ""
        for c in cols:
            v = row.get(c)
            v = "" if v is None or (isinstance(v, float) and np.isnan(v)) else v
            cls = ' class="ok"' if c == "verdict" and v == "APPROUVÉ" else (' class="bad"' if c == "verdict" else "")
            tds += f"<td{cls}>{esc(str(v))}</td>"
        body += f"<tr>{tds}</tr>"

    doc = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Labo stratégies MT5</title><style>{CSS}</style></head>
<body><main>
<h1>Labo de stratégies MT5 — {esc(label)}</h1>
<p class="mut">{len(df)} bougies du {esc(str(df.index[0]))} au {esc(str(df.index[-1]))} · {cfg.rounds} rounds ·
budget {cfg.budget} tests/agent/round · validation hors-échantillon sur les derniers {cfg.oos_fraction:.0%}</p>
<div class="cards">{cards_html}</div>
<h2>L'équipe</h2><div class="teams">{teams_html}</div>
<h2>Courbes des meilleures stratégies (historique complet, en R)</h2>
<div class="cards">{curves or '<p class="mut">Aucune stratégie approuvée.</p>'}</div>
<h2>Classement</h2>
<p class="mut">IS = in-sample (données d'entraînement), OOS = out-of-sample (données jamais vues pendant la recherche).
avgR = gain moyen par trade en multiples du risque. Seul l'OOS compte vraiment.</p>
<div class="scroll"><table><thead><tr>{heads}</tr></thead><tbody>{body}</tbody></table></div>
<h2>Journal des agents</h2><pre>{esc(chr(10).join(journal.lines))}</pre>
<p class="warn">Un backtest n'est pas une garantie. Même après validation hors-échantillon, testez
toute stratégie en compte DÉMO pendant plusieurs semaines avant de risquer de l'argent réel.</p>
</main></body></html>"""
    path.write_text(doc, encoding="utf-8")
