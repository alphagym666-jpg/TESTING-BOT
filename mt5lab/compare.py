"""Comparaison de tous les marchés × timeframes : quelle stratégie rapporte le plus ?

Les gains sont ramenés PAR MOIS sur la période hors-échantillon : un test M5 couvre quelques semaines,
un test D1 plusieurs années ; comparer des gains totaux n'aurait pas de sens.
"""
from __future__ import annotations

import html
from pathlib import Path

import pandas as pd

from .data import TIMEFRAMES


def collect(results_dir: Path) -> pd.DataFrame:
    frames = []
    for path in sorted(Path(results_dir).glob("*_*/classement.csv")):
        label = path.parent.name
        sym, _, tf = label.rpartition("_")
        if tf not in TIMEFRAMES or not sym:
            continue
        df = pd.read_csv(path)
        if not len(df):
            continue
        df.insert(0, "timeframe", tf)
        df.insert(0, "symbole", sym)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    for col in ("gain_mois_pct", "trades_mois", "oos_jours"):
        if col not in out.columns:  # résultats d'une ancienne version
            out[col] = float("nan")
    return out


def build_comparison(results_dir: Path, capital: float = 100_000) -> pd.DataFrame:
    results_dir = Path(results_dir)
    allr = collect(results_dir)
    if not len(allr):
        print("[comparaison] aucun résultat trouvé")
        return allr
    allr["gain_mois_usd"] = (allr["gain_mois_pct"] * capital / 100).round(0)
    allr["_ok"] = allr["verdict"].eq("APPROUVÉ")
    allr["_tested"] = allr["trades_oos"].notna()
    allr = allr.sort_values(["_ok", "gain_mois_pct"], ascending=[False, False])
    cols = ["verdict", "symbole", "timeframe", "strategie", "risque", "gain_mois_pct", "gain_mois_usd", "trades_mois",
            "wr_oos", "avgR_oos", "pf_oos", "dd_oos_pct", "trades_oos", "oos_jours", "candidate"]
    allr[cols].to_csv(results_dir / "comparaison.csv", index=False)
    _write_html(results_dir / "comparaison.html", allr, capital)
    ok = allr[allr["_ok"]]
    print(f"\n===== COMPARAISON : {len(ok)} stratégies approuvées sur "
          f"{allr[['symbole', 'timeframe']].drop_duplicates().shape[0]} marchés × timeframes =====")
    if len(ok):
        print(ok[["symbole", "timeframe", "strategie", "risque", "gain_mois_pct", "gain_mois_usd", "trades_mois",
                  "wr_oos", "dd_oos_pct"]].head(20).to_string(index=False))
    print(f"Comparaison : {results_dir / 'comparaison.html'}")
    return allr


CSS = """
:root{--bg:#f7f8fa;--card:#fff;--fg:#1c2230;--mut:#667085;--line:#e4e7ec;--ok:#12805c;--bad:#b42318;--okbg:#e7f6ee}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e6e8ee;--mut:#98a2b3;--line:#2a2f3a;--ok:#3ccb7f;--bad:#f97066;--okbg:#14301f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:1250px;margin:auto;padding:20px 16px}h1{font-size:22px;margin:0}h2{font-size:17px;margin:28px 0 10px}
.mut{color:var(--mut)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-top:14px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}.card b{display:block;font-size:20px}
.scroll{overflow:auto;border:1px solid var(--line);border-radius:10px;max-height:620px}
table{width:100%;border-collapse:collapse;background:var(--card);font-size:12.5px}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{position:sticky;top:0;background:var(--card)}
td.cell{text-align:center}td.good{background:var(--okbg);color:var(--ok);font-weight:600}
.pos{color:var(--ok);font-weight:600}.neg{color:var(--bad)}.warn{border-left:4px solid var(--bad);padding:10px 14px;background:var(--card);border-radius:6px}
"""


def _fmt(v, f="{:+.2f}"):
    return "" if pd.isna(v) else f.format(v)


def _table(df, capital, esc):
    rows = ""
    for i, (_, r) in enumerate(df.iterrows(), 1):
        g = r["gain_mois_pct"]
        rows += (f"<tr><td>{i}</td><td>{esc(r['symbole'])}</td><td>{esc(r['timeframe'])}</td><td>{esc(r['strategie'])}</td>"
                 f"<td>{esc(r['risque'])}</td><td class='{'pos' if g > 0 else 'neg'}'>{_fmt(g)} %</td>"
                 f"<td class='{'pos' if g > 0 else 'neg'}'>{_fmt(r['gain_mois_usd'], '{:+,.0f}')} $</td>"
                 f"<td>{_fmt(r['trades_mois'], '{:.1f}')}</td><td>{_fmt(r['wr_oos'], '{:.0f}')} %</td>"
                 f"<td>{_fmt(r['avgR_oos'])}</td><td>{_fmt(r['pf_oos'], '{:.2f}')}</td>"
                 f"<td>{_fmt(r['dd_oos_pct'], '{:.1f}')} %</td><td>{_fmt(r['trades_oos'], '{:.0f}')}</td></tr>")
    head = ("<tr><th>#</th><th>Marché</th><th>TF</th><th>Stratégie</th><th>Risque</th><th>Gain / mois</th>"
            f"<th>$ / mois (sur {capital:,.0f})</th><th>Trades / mois</th><th>Réussite</th><th>R moyen</th><th>PF</th>"
            "<th>DD max</th><th>Trades testés</th></tr>")
    return f"<div class='scroll'><table><thead>{head}</thead><tbody>{rows}</tbody></table></div>"


def _write_html(path: Path, allr: pd.DataFrame, capital: float):
    esc = html.escape
    ok = allr[allr["_ok"]]
    syms = list(dict.fromkeys(allr["symbole"]))
    tfs = [t for t in TIMEFRAMES if t in set(allr["timeframe"])]
    # matrice marché × timeframe : meilleure stratégie approuvée
    grid = "<tr><th>Marché</th>" + "".join(f"<th>{t}</th>" for t in tfs) + "</tr>"
    for sy in syms:
        grid += f"<tr><td><b>{esc(sy)}</b></td>"
        for t in tfs:
            cell = ok[(ok["symbole"] == sy) & (ok["timeframe"] == t)]
            tested = len(allr[(allr["symbole"] == sy) & (allr["timeframe"] == t)])
            if len(cell):
                b = cell.iloc[0]
                grid += (f"<td class='cell good' title='{esc(b['strategie'])} | {esc(b['risque'])}'>"
                         f"{b['gain_mois_pct']:+.2f} %/mois<br><span class='mut'>{len(cell)} validée(s)</span></td>")
            else:
                grid += f"<td class='cell mut'>{'aucune validée' if tested else 'non testé'}</td>"
        grid += "</tr>"
    best = ok.iloc[0] if len(ok) else None
    cards = [("Marchés × timeframes testés", allr[["symbole", "timeframe"]].drop_duplicates().shape[0]),
             ("Stratégies approuvées", len(ok)),
             ("Meilleur gain / mois (validé)", f"{best['gain_mois_pct']:+.2f} %" if best is not None else "—"),
             ("Meilleure combinaison", f"{best['symbole']} {best['timeframe']}" if best is not None else "—")]
    cards_html = "".join(f"<div class='card'><span class='mut'>{esc(k)}</span><b>{esc(str(v))}</b></div>" for k, v in cards)
    # stratégies qui marchent sur plusieurs marchés / timeframes = plus robustes
    fam = ""
    if len(ok):
        base = ok.assign(base=ok["strategie"].str.split("(").str[0])
        agg = (base.groupby("base").agg(n=("base", "size"), marches=("symbole", lambda s: ", ".join(sorted(set(s)))),
                                        tfs=("timeframe", lambda s: ", ".join(t for t in TIMEFRAMES if t in set(s))),
                                        gain=("gain_mois_pct", "mean"))
               .sort_values(["n", "gain"], ascending=False).head(15))
        fam = "".join(f"<tr><td>{esc(i)}</td><td>{r.n}</td><td>{esc(r.marches)}</td><td>{esc(r.tfs)}</td>"
                      f"<td>{r.gain:+.2f} %</td></tr>" for i, r in agg.iterrows())
    promising = allr[~allr["_ok"] & allr["_tested"] & (allr["gain_mois_pct"] > 0)].head(15)
    doc = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Comparaison des stratégies</title><style>{CSS}</style></head>
<body><main><h1>Quelle stratégie rapporte le plus ?</h1>
<p class="mut">Tous les marchés et timeframes testés. Les gains sont ceux de la période hors-échantillon (jamais vue pendant
la recherche), ramenés par mois pour comparer équitablement M1 et D1. Commission et spread inclus.</p>
<div class="cards">{cards_html}</div>
<h2>Carte marché × timeframe (meilleure stratégie validée)</h2>
<div class="scroll"><table>{grid}</table></div>
<h2>Classement des stratégies validées, par gain mensuel</h2>
{_table(ok.head(50), capital, esc) if len(ok) else "<p class='mut'>Aucune stratégie validée pour l'instant.</p>"}
<h2>Stratégies qui marchent sur plusieurs marchés / timeframes (plus robustes)</h2>
{"<div class='scroll'><table><thead><tr><th>Stratégie</th><th>Validée</th><th>Marchés</th><th>Timeframes</th><th>Gain moyen / mois</th></tr></thead><tbody>" + fam + "</tbody></table></div>" if fam else "<p class='mut'>—</p>"}
<h2>Prometteuses mais NON validées</h2>
<p class="mut">Gagnantes hors-échantillon mais recalées par les chefs (pas assez de trades, pas statistiquement significatif,
fragiles aux coûts…). À surveiller en paper trading, pas à trader.</p>
{_table(promising, capital, esc) if len(promising) else "<p class='mut'>—</p>"}
<p class="warn">Gain élevé ≠ meilleure stratégie : regardez aussi le drawdown, le nombre de trades et si elle marche sur plusieurs
marchés. Les gains passés ne garantissent rien : validez en paper trading avant tout.</p>
</main></body></html>"""
    path.write_text(doc, encoding="utf-8")
