"""LA PAGE UNIQUE : resultats.html — tout ce qu'il faut savoir, au même endroit.

1. LA meilleure stratégie combinée pour passer le challenge (et son bot)
2. Le TOP 10 de TOUTES les stratégies combinées construites (horaires, scénarios de perte max, mélanges,
   portefeuille du Chef FTMO, combinaison du direct), classées par vitesse pour réussir le challenge
3. Le compte perso et le compte financé
4. Quel compte FTMO choisir
5. Sur quelle période tout est calculé, et quels frais sont inclus
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from .boutons import button, script


def _f(v, fmt="{:.1f}", empty="—"):
    try:
        if v is None or v != v:
            return empty
        return fmt.format(v)
    except (TypeError, ValueError):
        return empty


def _load(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1b2130;--mut:#667085;--line:#e3e6eb;--ok:#0f7a55;--bad:#b42318;--acc:#2f5bd3;--gold:#b7791f}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e7e9ee;--mut:#98a2b3;--line:#2a2f3a;--ok:#3ccb7f;--bad:#f97066;--acc:#7aa2ff;--gold:#e5b454}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
main{max-width:1280px;margin:auto;padding:20px 16px 60px}h1{font-size:24px;margin:0 0 4px}h2{font-size:19px;margin:34px 0 10px}
.mut{color:var(--mut)}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:10px 0}
.hero{border:2px solid var(--acc)}.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:12px 0}
.kpi{background:var(--bg);border-radius:10px;padding:10px}.kpi b{display:block;font-size:20px}.kpi span{font-size:12.5px;color:var(--mut)}
.scroll{overflow:auto;border:1px solid var(--line);border-radius:12px}table{width:100%;border-collapse:collapse;background:var(--card);font-size:13.5px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{position:sticky;top:0;background:var(--card);white-space:nowrap}
td.n{white-space:nowrap}.pos{color:var(--ok);font-weight:600}.neg{color:var(--bad)}.star{color:var(--gold);font-weight:700}
details summary{cursor:pointer;color:var(--acc)}ul.comp{margin:6px 0 0;padding-left:18px;font-size:13px}
.tag{display:inline-block;font-size:12px;padding:1px 8px;border-radius:99px;border:1px solid var(--line);margin-left:6px}
"""


def _components(comb: dict) -> str:
    items = []
    for c in comb.get("composants", []):
        tpm = c.get("trades_mois")
        items.append(f"<li><b>{html.escape(str(c.get('symbole')))} {html.escape(str(c.get('timeframe')))}</b> · "
                     f"{html.escape(str(c.get('strategie', ''))[:110])} · {html.escape(str(c.get('risque_config', '')))} · "
                     f"{_f(c.get('risk_pct'), '{:g}')} %/trade"
                     + (f" · ~{_f(tpm, '{:.0f}')} trades/mois" if tpm is not None else "") + "</li>")
    return "<ul class='comp'>" + "".join(items) + "</ul>"


def _rules_text(comb: dict) -> str:
    from .ftmo import lock_text
    rg = comb.get("regles") or {}
    parts = [f"perte possible max {_f(rg.get('day_budget'), '{:g}')} %/jour"]
    if rg.get("day_stop"):
        parts.append(f"arrêt après -{rg['day_stop']:g} % dans la journée")
    if rg.get("max_open"):
        parts.append(f"{rg['max_open']} positions max")
    if rg.get("max_correles"):
        parts.append(f"{rg['max_correles']} max sur des marchés corrélés")
    if rg.get("frein"):
        parts.append("frein de bonne journée : " + lock_text(rg["frein"]))
    h = (comb.get("horaire") or {}).get("nom") or "24h/24"
    parts.append(f"entrées {h} (heure locale)")
    return " · ".join(parts)


def _period(res: dict) -> str:
    fen = res.get("fenetre") or ("", "")
    jours = res.get("jours_periode")
    mois = f" (~{jours / 30.44:.0f} mois)" if jours else ""
    return f"{fen[0]} → {fen[1]}{mois}" if fen and fen[0] else "—"


def _kpis(res: dict) -> str:
    oos = res.get("challenges_oos") or {}
    k = [("Challenge réussi en", f"~{_f(res.get('jours_attendus'), '{:.0f}')} jours de bourse",
          "reprises comprises, en moyenne"),
         ("Réussite du challenge", f"{_f(res.get('ftmo_pass'))} %", "sur des milliers de simulations"),
         ("Échec (limite touchée)", f"{_f(res.get('ftmo_echec_p1'))} %", ""),
         ("Trades par mois", f"~{_f(res.get('trades_mois'), '{:.0f}')}", "tous composants ensemble"),
         ("Plus grosse baisse", f"{_f(res.get('dd_max'), '{:.2f}')} %", "drawdown max sur la période"),
         ("Pire journée", f"{_f(res.get('pire_jour'), '{:.2f}')} %", "positions ouvertes comptées au pire"),
         ("Durée moyenne d'un trade", f"{_f(res.get('duree_moy_h'), '{:.0f}')} h", ""),
         ("Trades gardés le week-end", f"{_f(res.get('week_end_pct'), '{:.0f}')} %", ""),
         ("Gain sur la période", f"{_f(res.get('rendement_pct'), '{:+.1f}')} %", ""),
         ("Challenges enchaînés", f"{oos.get('reussis', '—')} réussis / {oos.get('rates', '—')} ratés",
          "rejoués jour après jour sur la période"),
         ("Période analysée", _period(res), "jamais vue pendant la recherche")]
    return "<div class='kpis'>" + "".join(
        f"<div class='kpi'><span>{html.escape(a)}</span><b>{html.escape(b)}</b><span>{html.escape(c)}</span></div>"
        for a, b, c in k) + "</div>"


def _top_table(combos: list[dict], n: int = 10) -> str:
    rows = []
    for e in combos[:n]:
        r, c = e["res"], e["comb"]
        star = " <span class='star'>★ N°1</span>" if e.get("rang") == 1 else ""
        rows.append(
            f"<tr><td class='n'><b>{e.get('rang', '')}</b></td>"
            f"<td><b>{html.escape(e['nom'])}</b>{star}<br><span class='mut'>{html.escape(_rules_text(c))}</span>"
            f"<details><summary>{len(c.get('composants', []))} stratégies ensemble</summary>{_components(c)}</details></td>"
            f"<td class='n pos'><b>~{_f(r.get('jours_attendus'), '{:.0f}')} j</b></td>"
            f"<td class='n'>{_f(r.get('ftmo_pass'))} %</td><td class='n'>{_f(r.get('ftmo_echec_p1'))} %</td>"
            f"<td class='n'>~{_f(r.get('trades_mois'), '{:.0f}')}</td><td class='n'>{_f(r.get('dd_max'), '{:.2f}')} %</td>"
            f"<td class='n'>{_f(r.get('pire_jour'), '{:.2f}')} %</td><td class='n'>{_f(r.get('duree_moy_h'), '{:.0f}')} h</td>"
            f"<td class='n'>{_f(r.get('week_end_pct'), '{:.0f}')} %</td><td class='n'>{html.escape(_period(r))}</td>"
            f"<td>{button(c, 'Bot MT5')}</td></tr>")
    head = ("<tr><th>#</th><th>Stratégie combinée (à quoi elle sert, règles, composants)</th><th>Challenge réussi en</th>"
            "<th>Réussite</th><th>Échec</th><th>Trades / mois</th><th>Plus grosse baisse</th><th>Pire journée</th>"
            "<th>Durée moy. d'un trade</th><th>Gardés le week-end</th><th>Période analysée</th><th>Bot</th></tr>")
    return f"<div class='scroll'><table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table></div>"


def _account_block(name: str, comb: dict | None) -> str:
    if not comb:
        return (f"<div class='card'><b>{html.escape(name)}</b> : pas encore construit "
                "(touche 1 du menu, TOUT FAIRE).</div>")
    r = comb.get("resultat", {})
    cap = float(comb.get("capital") or 0)
    k = [("Rendement médian par an", f"{_f(r.get('rendement_an_median'))} %", f"≈ {cap * (r.get('rendement_an_median') or 0) / 100:,.0f} $".replace(",", " ")),
         ("Par mois (médian)", f"{_f(r.get('rendement_mois_median'), '{:.2f}')} %", f"≈ {cap * (r.get('rendement_mois_median') or 0) / 100:,.0f} $".replace(",", " ")),
         ("Mauvaise année (1 sur 10)", f"{_f(r.get('rendement_an_p10'))} %", ""),
         ("Plus grosse baisse typique", f"{_f(r.get('dd_median'))} %", ""),
         ("Chances d'un gros problème en 1 an", f"{_f(r.get('p_probleme'))} %", ""),
         ("Trades par mois", f"~{_f(r.get('trades_mois'), '{:.0f}')}", ""),
         ("Période analysée", _period(r), "")]
    kp = "<div class='kpis'>" + "".join(
        f"<div class='kpi'><span>{html.escape(a)}</span><b>{html.escape(b)}</b><span>{html.escape(c)}</span></div>"
        for a, b, c in k) + "</div>"
    return (f"<div class='card'><b>{html.escape(name)} — {cap:,.0f} $</b>".replace(",", " ")
            + f"<p class='mut'>{html.escape(str(comb.get('but', '')))}. {html.escape(_rules_text(comb))}"
            + (" · AUCUNE position le week-end (compte Standard)" if comb.get("fermer_week_end") else "")
            + f"</p>{kp}<details><summary>{len(comb.get('composants', []))} stratégies ensemble</summary>"
            f"{_components(comb)}</details><p>{button(comb, 'Créer le bot MT5 de ce compte')}</p></div>")


def _advice(adv: dict | None) -> str:
    if not adv:
        return "<p class='mut'>Pas encore calculé (touche 1 du menu, TOUT FAIRE).</p>"
    rows = "".join(
        f"<tr><td><b>{html.escape(r['compte'])}</b></td><td class='pos'><b>~{_f(r.get('jours_attendus'), '{:.0f}')} j</b></td>"
        f"<td>{_f(r.get('reussite'))} %</td><td>{_f(r.get('echec'))} %</td>"
        f"<td>{_f(r.get('week_end_pct'), '{:.0f}')} %</td></tr>" for r in adv.get("lignes", []))
    return ("<div class='scroll'><table><thead><tr><th>Compte</th><th>Financé en</th><th>Réussite</th><th>Échec</th>"
            f"<th>Trades gardés le week-end</th></tr></thead><tbody>{rows}</tbody></table></div>"
            + "".join(f"<p>{html.escape(t)}</p>" for t in adv.get("texte", [])))


MODES = [("none", "Aucune"), ("breakeven", "Break-even à +1R"), ("trailing", "Stop suiveur"),
         ("paliers", "Paliers (BE à 1R, +1R à 2R…)"), ("intelligente", "Sortie intelligente")]


def _management(rows) -> str:
    if not rows:
        return "<p class='mut'>Pas encore calculé (touche 1 du menu, TOUT FAIRE).</p>"
    from collections import Counter
    wins = Counter(r.get("meilleure") for r in rows)
    lead = " · ".join(f"{dict(MODES).get(m, m)} : meilleure pour {n}" for m, n in wins.most_common())
    body = ""
    for r in rows[:60]:
        modes = r.get("modes", {})
        cells = ""
        for m, _ in MODES:
            x = modes.get(m)
            v = None if not x else x.get("r")
            cls = "pos" if m == r.get("meilleure") else ""
            cells += f"<td class='n {cls}'>{_f(v, '{:+.2f}R')}</td>"
        body += (f"<tr><td><b>{html.escape(str(r['symbole']))} {html.escape(str(r['timeframe']))}</b> · "
                 f"{html.escape(str(r['strategie'])[:90])}<br><span class='mut'>R:R {html.escape(str(r.get('rr', '')))} · "
                 f"gestion actuelle : {html.escape(dict(MODES).get(r.get('actuelle'), str(r.get('actuelle'))))}</span></td>{cells}</tr>")
    head = "<tr><th>Stratégie</th>" + "".join(f"<th>{html.escape(l)}</th>" for _, l in MODES) + "</tr>"
    return (f"<p>{html.escape(lead)}</p><details><summary>Voir le R moyen par trade de chaque gestion, stratégie par "
            f"stratégie</summary><div class='scroll'><table><thead>{head}</thead><tbody>{body}</tbody></table></div>"
            "</details>")


def costs_text(commission: str | None = None) -> str:
    return ("<ul><li><b>Spread</b> : celui de CHAQUE bougie de l'historique MT5 (au moins le spread médian), à "
            "l'ouverture et à la fermeture.</li>"
            f"<li><b>Commission du courtier</b> par lot aller-retour{(' : ' + html.escape(commission)) if commission else ''}"
            " (réglage COMMISSION de lancer.bat).</li>"
            "<li><b>Glissement (slippage)</b> : celui MESURÉ sur vos trades en direct (paper trading et bot) dès 10 "
            "mesures par marché ; avant ça, une estimation prudente de la moitié du spread médian en plus par "
            "trade.</li>"
            "<li><b>Swaps</b> : frais (ou crédit) de chaque nuit en position, le week-end compte pour 3 nuits.</li>"
            "<li>Les stratégies sont aussi testées avec des coûts DOUBLÉS : celles qui ne tiennent plus sont rejetées.</li></ul>")


def _avocat(a: dict | None) -> str:
    if not a:
        return ("<div class='card'><b>Avocat du diable</b> : pas encore passé (il travaille à la fin de la touche 1, "
                "TOUT FAIRE).</div>")
    color = {"bon": "var(--ok)", "moyen": "var(--gold)", "danger": "var(--bad)"}.get(a.get("niveau"), "var(--line)")
    rows = "".join(
        f"<tr><td><b>{html.escape(r['symbole'])} {html.escape(r['timeframe'])}</b></td>"
        f"<td>{r['vrais_valides']} validées · {r['vrais_essai']} à l'essai</td>"
        f"<td>{r['hasard_valides']} validées · {r['hasard_essai']} à l'essai</td></tr>" for r in a.get("cases", []))
    return (f"<div class='card' style='border:2px solid {color}'><b>Avocat du diable</b> "
            f"<span class='mut'>({html.escape(str(a.get('fait_le', '')))})</span>"
            "<p class='mut'>La même recherche, refaite sur les mêmes bougies remises dans un ordre au hasard : plus aucune "
            "vraie tendance ni aucun vrai motif. Ce qui « marche » là gagne par pure chance.</p>"
            "<div class='scroll'><table><thead><tr><th>Case</th><th>Sur les VRAIS prix</th><th>Sur des prix AU HASARD</th>"
            f"</tr></thead><tbody>{rows}</tbody></table></div><p><b>{html.escape(a.get('verdict', ''))}</b></p></div>")


def _comp_table(comb: dict) -> str:
    rows = "".join(
        f"<tr><td><b>{html.escape(str(c.get('symbole')))}</b></td><td>{html.escape(str(c.get('timeframe')))}</td>"
        f"<td>{html.escape(str(c.get('strategie', ''))[:140])}</td><td>{html.escape(str(c.get('risque_config', '')))}</td>"
        f"<td class='n'>{_f(c.get('risk_pct'), '{:g}')} %</td><td class='n'>~{_f(c.get('trades_mois'), '{:.0f}')}</td>"
        f"<td>{button({**{k: v for k, v in comb.items() if k != 'composants'}, 'composants': [c]}, 'Bot seule')}</td></tr>"
        for c in comb.get("composants", []))
    return ("<div class='scroll'><table><thead><tr><th>Marché</th><th>TF</th><th>Stratégie</th>"
            "<th>Stop · R:R · gestion des trades</th><th>Risque / trade</th><th>Trades / mois</th><th></th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div>")


def write_top10_page(out_dir: str | Path, rules_label: str = "") -> Path:
    """Page dédiée : les 10 meilleures stratégies combinées, chacune avec TOUTES ses infos et son bouton Bot MT5."""
    out = Path(out_dir)
    combos = (_load(out / "toutes_les_combinees.json") or [])[:10]
    cards = ""
    for e in combos:
        c, r = e["comb"], e["res"]
        tpm = r.get("trades_mois")
        label = f"Créer le bot MT5 de la n°{e.get('rang')}"
        cards += (f"<div class='card{' hero' if e.get('rang') == 1 else ''}'>"
                  f"<h2 style='margin:0 0 4px'>N°{e.get('rang')} · {html.escape(e['nom'])}</h2>"
                  f"<p class='mut'>Pour : {html.escape(e.get('pour', ''))} · {html.escape(_rules_text(c))}"
                  + (f" · ~{tpm / 21:.1f} trades par jour de bourse" if tpm else "") + "</p>"
                  f"<p>{button(c, label)}</p>"
                  f"{_kpis(r)}<details{' open' if e.get('rang') == 1 else ''}><summary>Les "
                  f"{len(c.get('composants', []))} stratégies qui la composent (et le bot de chacune seule)</summary>"
                  f"{_comp_table(c)}</details></div>")
    if not cards:
        cards = ("<div class='card hero'><b>Pas encore de stratégie combinée.</b> Lancez la touche 1 du menu "
                 "(TOUT FAIRE) : cette page se remplit à la fin.</div>")
    doc = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TOP 10</title><style>{CSS}</style></head><body><main>
<h1>TOP 10 des stratégies combinées</h1>
<p class="mut">{html.escape(rules_label)} · classées de la plus rapide à réussir le challenge à la moins rapide (celles qui
ratent trop souvent passent après). Chaque bouton « Créer le bot MT5 » prépare le bot de CETTE stratégie combinée et
l'installe dans MT5 : une plateforme doit être ouverte (touche 3, 4 ou 5 du menu). Frais inclus : spread, commission,
glissement et swaps. Période analysée : jamais vue pendant la recherche.</p>
{_avocat(_load(out / "avocat_du_diable.json"))}
{cards}
<p><a href="resultats.html">Tous les résultats (comptes perso et financé, choix du compte, gestion des trades)</a> ·
<a href="directeur.html">Rapport détaillé du Directeur</a></p>
</main>{script(out)}</body></html>"""
    path = out / "top10.html"
    path.write_text(doc, encoding="utf-8")
    return path


def write_results_page(out_dir: str | Path, rules_label: str = "", commission: str | None = None) -> Path:
    out = Path(out_dir)
    combos = _load(out / "toutes_les_combinees.json") or []
    best = combos[0] if combos else None
    perso, fin = _load(out / "strategie_combinee_perso.json"), _load(out / "strategie_combinee_finance.json")
    main = _load(out / "strategie_combinee.json") or {}
    if best:
        hero = (f"<div class='card hero'><h2 style='margin-top:0'>N°1 pour passer le challenge FTMO le plus vite : "
                f"{html.escape(best['nom'])}</h2><p class='mut'>{html.escape(_rules_text(best['comb']))}</p>"
                f"{_kpis(best['res'])}<details open><summary>Les {len(best['comb'].get('composants', []))} stratégies "
                f"qui la composent</summary>{_components(best['comb'])}</details>"
                f"<p>{button(best['comb'], 'Créer le bot MT5 de cette stratégie combinée')}</p>"
                "<p class='mut'>Pour la faire tourner en paper trading (avec les signaux pour le bot) : touche 3 du "
                "menu. Ou le bouton ci-dessus, puis LANCER_BOT.bat dans le dossier qui s'ouvre.</p></div>")
    else:
        hero = ("<div class='card hero'><b>Pas encore de stratégie combinée.</b> Lancez la touche 1 du menu "
                "(TOUT FAIRE) : la page se remplit à la fin.</div>")
    doc = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Résultats</title><style>{CSS}</style></head><body><main>
<h1>Résultats : les meilleures stratégies combinées</h1>
<p class="mut">{html.escape(rules_label)} · {len(combos)} stratégies combinées classées · mis à jour le {html.escape(str(main.get('cree_le', '')))}</p>
{_avocat(_load(out / "avocat_du_diable.json"))}
{hero}
<p><a href="top10.html"><b>→ La page dédiée au TOP 10 (toutes les infos et le bot de chacune)</b></a></p>
<h2>TOP 10 des stratégies combinées (de la plus rapide à réussir le challenge à la moins rapide)</h2>
<p class="mut">Toutes celles que le Directeur a construites : chaque horaire, chaque perte max par jour, les mélanges
du Chef des combinaisons, le portefeuille du Chef FTMO et la combinaison du direct. Classées par jours attendus pour
réussir le challenge (celles qui ratent trop souvent passent après). Cliquez « … stratégies ensemble » pour voir les
marchés, timeframes, risques et trades par mois de chaque composant.</p>
{_top_table(combos) if combos else "<p class='mut'>—</p>"}
<h2>Après le challenge : compte financé · et votre compte perso</h2>
{_account_block("Compte financé (après le challenge)", fin)}
{_account_block("Compte perso", perso)}
<h2>Gestion des trades : break-even, paliers ou sortie intelligente ?</h2>
<p class="mut">Chaque stratégie validée est rejouée sur la période jamais vue avec les 5 gestions : aucune ; break-even
quand le trade a gagné 1R ; stop suiveur ; PALIERS (break-even à +1R, stop à +1R une fois à +2R, à +2R une fois à
+3R… jusqu'au TP) ; SORTIE INTELLIGENTE (les paliers + fermeture avant un retournement : signal inverse de la
stratégie, ou le prix rend 1 ATR après avoir atteint +1R). Les meilleures versions entrent dans les stratégies
combinées ci-dessus, et le bot applique exactement la même gestion.</p>
{_management(_load(out / "gestion_trades.json"))}
<h2>Quel compte FTMO acheter ?</h2>
{_advice(main.get('conseil_compte'))}
<h2>Sur quoi c'est basé</h2>
<div class="card"><p><b>Période analysée</b> : chaque stratégie est cherchée sur le début de l'historique, puis jugée sur
les 35 % les plus récents, une période qu'aucun agent n'a vue (la colonne « Période analysée » donne les dates exactes de
cette période commune à tous les composants). Les chiffres du challenge viennent de milliers de challenges simulés à
partir des VRAIES journées de cette période, et des challenges enchaînés jour après jour.</p>
<p><b>Frais inclus dans tous les chiffres</b> :</p>{costs_text(commission)}
<p class="mut">Rien n'est garanti : le passé ne se répète jamais exactement. Faites tourner la n°1 en paper trading
(touche 3) quelques semaines et comparez son rythme de trades et ses résultats à cette page.</p>
<p><a href="directeur.html">Rapport détaillé du Directeur</a> · <a href="fiches_strategies.html">Fiches des stratégies</a> ·
<a href="comparaison.html">Comparaison par marché</a></p></div>
</main>{script(out)}</body></html>"""
    path = out / "resultats.html"
    path.write_text(doc, encoding="utf-8")
    write_top10_page(out, rules_label)
    return path
