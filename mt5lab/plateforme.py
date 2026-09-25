"""Plateforme web locale : tous les trades fictifs en direct (http://localhost:8765).

Le serveur n'écoute que sur 127.0.0.1 (accessible uniquement depuis votre PC). La page interroge /api/etat
toutes les 3 secondes : positions ouvertes (entrée, SL, TP, prix, latent), historique complet des trades,
classement des stratégies, comparaison des R:R, suivi des challenges FTMO et journal des événements.
"""
from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, engine):
        super().__init__(addr, handler)
        self.engine = engine
        self._data = b"{}"
        self._lock = threading.Lock()

    def publish(self, snapshot: dict):
        data = json.dumps(snapshot, default=str).encode("utf-8")
        with self._lock:
            self._data = data

    def data(self) -> bytes:
        with self._lock:
            return self._data


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # pas de bruit dans la console
        pass

    def _send(self, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/etat":
            self._send(self.server.data(), "application/json; charset=utf-8")
        elif path == "/trades.csv":
            f = self.server.engine.out / "trades.csv"
            body = f.read_bytes() if f.exists() else b""
            self._send(body, "text/csv; charset=utf-8", {"Content-Disposition": "attachment; filename=trades.csv"})
        elif path in ("/", "/index.html"):
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        else:
            self.send_error(404)


def start_server(engine, port: int = 8765, open_browser: bool = True):
    try:
        srv = _Server(("127.0.0.1", port), _Handler, engine)
    except OSError as exc:
        print(f"[plateforme] port {port} indisponible ({exc}) : la plateforme web est désactivée")
        return None
    srv.publish(engine.snapshot())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if open_browser:
        try:
            webbrowser.open(f"http://localhost:{port}")
        except Exception:
            pass
    return srv


PAGE = r"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Plateforme paper trading</title>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;
--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--pos:#006300;--neg:#d03b3b;--accent:#2a78d6;
--track:#cde2fb;--fill:#2a78d6;--negbar:#e34948;--good:#0ca30c;--warn:#fab219;--crit:#d03b3b}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;
--ink:#fff;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--pos:#0ca30c;
--neg:#e66767;--accent:#3987e5;--track:#184f95;--fill:#3987e5;--negbar:#e66767}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink2:#c3c2b7;--muted:#898781;
--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--pos:#0ca30c;--neg:#e66767;--accent:#3987e5;--track:#184f95;
--fill:#3987e5;--negbar:#e66767}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--surface);border-bottom:1px solid var(--border);padding:10px 16px}
.row{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
h1{font-size:18px;margin:0 12px 0 0}
.mut{color:var(--muted)}.ink2{color:var(--ink2)}
.live{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink2)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--good)}.dot.off{background:var(--crit)}
.px{font-variant-numeric:tabular-nums;font-size:12.5px;padding:3px 8px;border:1px solid var(--border);border-radius:999px;background:var(--page)}
main{max-width:1500px;margin:auto;padding:14px 16px 40px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-bottom:14px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:10px 12px}
.tile .v{font-size:22px;font-weight:600;margin-top:2px}
.tabs{display:flex;flex-wrap:wrap;gap:4px;border-bottom:1px solid var(--border);margin:6px 0 10px}
.tabs button{border:0;background:none;color:var(--ink2);font:inherit;padding:8px 12px;border-bottom:2px solid transparent;cursor:pointer}
.tabs button.on{color:var(--ink);border-bottom-color:var(--accent);font-weight:600}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px}
select,input{font:inherit;color:var(--ink);background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:6px 8px}
input{min-width:220px}
.scroll{overflow:auto;max-height:68vh;border:1px solid var(--border);border-radius:10px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{padding:6px 8px;border-bottom:1px solid var(--grid);text-align:left;white-space:nowrap}
th{position:sticky;top:0;background:var(--surface);color:var(--ink2);font-weight:600;cursor:pointer;user-select:none}
td.n{text-align:right;font-variant-numeric:tabular-nums}
tr:hover td{background:color-mix(in srgb,var(--accent) 7%,transparent)}
.pos{color:var(--pos);font-weight:600}.neg{color:var(--neg);font-weight:600}
.tag{display:inline-block;padding:1px 7px;border-radius:999px;font-size:11.5px;border:1px solid var(--border)}
.ok::before{content:"✔ ";color:var(--good)}.ko::before{content:"✖ ";color:var(--crit)}.run::before{content:"● ";color:var(--accent)}
.meter{position:relative;width:120px;height:8px;border-radius:4px;background:var(--track);display:inline-block;vertical-align:middle}
.meter i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:var(--fill)}
.meter.neg i{background:var(--negbar)}
.bars{display:grid;grid-template-columns:110px 1fr 90px;gap:6px 10px;align-items:center;max-width:860px}
.bar{height:14px;position:relative}.bar i{position:absolute;top:0;bottom:0;border-radius:0 4px 4px 0;background:var(--fill)}
.bar i.neg{background:var(--negbar);border-radius:4px 0 0 4px}.bar .zero{position:absolute;top:-3px;bottom:-3px;width:1px;background:var(--axis)}
.empty{padding:28px;text-align:center;color:var(--muted)}
.note{font-size:12.5px;color:var(--ink2);margin:6px 0 10px}
</style></head>
<body>
<header><div class="row"><h1>Plateforme paper trading</h1><span class="live"><span class="dot" id="dot"></span><span id="maj">connexion…</span></span>
<span class="mut" id="info"></span></div><div class="row" id="prix" style="margin-top:6px"></div></header>
<main>
<div class="tiles" id="tiles"></div>
<div class="tabs" id="tabs"></div>
<div class="filters">
<select id="fSym"><option value="">Tous les marchés</option></select>
<select id="fTf"><option value="">Tous les timeframes</option></select>
<input id="fTxt" placeholder="Filtrer une stratégie (ex. INVENTION, rsi, 1:3)…">
<a class="tag" href="/trades.csv" style="align-self:center;color:var(--ink2);text-decoration:none">Télécharger tous les trades (Excel)</a>
</div>
<div id="view"></div>
</main>
<script>
const TABS=[["pos","Positions ouvertes"],["hist","Historique des trades"],["strat","Classement des stratégies"],
["rr","Meilleur R:R"],["ftmo","Challenges FTMO"],["log","Journal en direct"]];
let tab=localStorageGet("tab")||"pos",D=null,sortState={};
function localStorageGet(k){try{return localStorage.getItem(k)}catch(e){return null}}
function localStorageSet(k,v){try{localStorage.setItem(k,v)}catch(e){}}
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt=(v,d=2,sign=false)=>v==null||v===""||isNaN(v)?"—":(sign&&v>0?"+":"")+Number(v).toLocaleString("fr-FR",{minimumFractionDigits:d,maximumFractionDigits:d});
const cls=v=>v>0?"pos":v<0?"neg":"";
function px(v,r){if(v==null||v==="")return "sur signal";const d=(D.prix[r.symbole]||{}).digits;
 return d==null?esc(v):Number(v).toFixed(d)}
function money(v){return `<span class="${cls(v)}">${fmt(v,2,true)} $</span>`}
function rr(v){return `<span class="${cls(v)}">${fmt(v,2,true)}R</span>`}
document.getElementById("tabs").innerHTML=TABS.map(([k,l])=>`<button data-k="${k}">${l}</button>`).join("");
document.getElementById("tabs").onclick=e=>{const k=e.target.dataset.k;if(k){tab=k;localStorageSet("tab",k);render()}};
["fSym","fTf","fTxt"].forEach(id=>document.getElementById(id).addEventListener("input",render));
function filt(rows,symKey="symbole",tfKey="tf"){const s=fSym.value,t=fTf.value,q=fTxt.value.toLowerCase();
 return rows.filter(r=>(!s||r[symKey]===s)&&(!t||r[tfKey]===t)&&(!q||JSON.stringify(r).toLowerCase().includes(q)))}
function table(id,cols,rows){ // cols: [label,key,render,numeric]
 const st=sortState[id];if(st){const c=cols[st.i];rows=[...rows].sort((a,b)=>{const x=a[c[1]],y=b[c[1]];
  return (x>y?1:x<y?-1:0)*(st.d)})}
 if(!rows.length)return `<div class="scroll"><div class="empty">Rien pour l'instant</div></div>`;
 return `<div class="scroll"><table data-id="${id}"><thead><tr>${cols.map((c,i)=>`<th data-i="${i}">${c[0]}</th>`).join("")}</tr></thead><tbody>`+
 rows.slice(0,1500).map(r=>"<tr>"+cols.map(c=>`<td${c[3]?' class="n"':""}>${c[2]?c[2](r[c[1]],r):esc(r[c[1]])}</td>`).join("")+"</tr>").join("")+"</tbody></table></div>"}
document.getElementById("view").addEventListener("click",e=>{const th=e.target.closest("th");if(!th)return;
 const id=th.closest("table").dataset.id,i=+th.dataset.i;const s=sortState[id];
 sortState[id]={i,d:s&&s.i===i?-s.d:-1};render()});
function tiles(){const c=D.comptes,P=D.positions;const closed=c.reduce((a,x)=>a+x.trades,0),wins=c.reduce((a,x)=>a+x.gagnants,0);
 const pnl=c.reduce((a,x)=>a+x.pnl,0),lat=P.reduce((a,x)=>a+x.latent,0);
 const ok=c.filter(x=>x.ftmo==="RÉUSSI").length,ko=c.filter(x=>x.ftmo.startsWith("ÉCHOUÉ")).length;
 const T=[["Comptes fictifs",fmt(D.n_comptes,0),`${fmt(D.n_actifs,0)} ont déjà tradé`],["Positions ouvertes",fmt(P.length,0),`latent ${fmt(lat,0,true)} $`],
 ["Trades clôturés",fmt(closed,0),closed?`${fmt(wins/closed*100,0)} % gagnants`:""],["P&L réalisé (tous comptes)",fmt(pnl,0,true)+" $",""],
 ["Challenges FTMO",`${ok} réussis`,`${ko} échoués · ${fmt(D.ftmo.target1,0)} % / ${fmt(D.ftmo.max_daily,0)} % jour / ${fmt(D.ftmo.max_total,0)} % total`]];
 tiles_.innerHTML=T.map(([k,v,s])=>`<div class="tile"><div class="mut">${k}</div><div class="v">${v}</div><div class="mut" style="font-size:12px">${s}</div></div>`).join("")}
const tiles_=document.getElementById("tiles");
function viewPos(){return `<p class="note">Chaque ligne est un trade fictif en cours, calculé sur les vrais prix de MT5. Le SL et le TP sont vérifiés tick par tick.</p>`+
 table("pos",[["Marché","symbole"],["TF","tf"],["Sens","sens",v=>`<b>${v}</b>`],["Lots","lots",v=>fmt(v,2),1],["Ouverture","ouverture"],
 ["Entrée","entree",px,1],["SL initial","sl_initial",px,1],["SL actuel","sl",px,1],["TP","tp",px,1],["Prix actuel","prix",px,1],
 ["Pips → SL","pips_sl",v=>fmt(v,1),1],["Pips → TP","pips_tp",v=>fmt(v,1),1],["Latent","latent",money,1],["Latent R","latent_r",rr,1],
 ["Bougies","bougies",null,1],["Stratégie","strategie"],["Risque","risque"]],filt(D.positions))}
function viewHist(){return table("hist",[["Fermeture","fermeture"],["Ouverture","ouverture"],["Durée","duree_min",v=>v==null?"—":v<60?fmt(v,0)+" min":fmt(v/60,1)+" h",1],
 ["Marché","symbole"],["TF","timeframe"],["Sens","sens",v=>`<b>${v}</b>`],["Lots","lots",v=>fmt(v,2),1],["Entrée","prix_entree",px,1],
 ["SL initial","sl_initial",px,1],["SL final","sl_final",px,1],["TP","tp",px,1],["Sortie","prix_sortie",px,1],
 ["Raison","raison",v=>`<span class="tag">${esc(v)}</span>`],["Pips","pips",v=>`<span class="${cls(v)}">${fmt(v,1,true)}</span>`,1],
 ["R","r",rr,1],["P&L","pnl",money,1],["Solde","solde",v=>fmt(v,2),1],["Spread entrée","spread_entree_pts",v=>fmt(v,1)+" pts",1],
 ["Stratégie","strategie"],["Risque","risque"]],filt(D.trades,"symbole","timeframe"))}
function ftmoCell(v){return v==="RÉUSSI"?`<span class="tag ok">réussi</span>`:v.startsWith("ÉCHOUÉ")?`<span class="tag ko">${esc(v.toLowerCase())}</span>`:`<span class="tag run">en cours</span>`}
function prog(p){const t=D.ftmo.target1,w=Math.max(0,Math.min(100,Math.abs(p)/t*100));
 return `<span class="meter${p<0?" neg":""}" title="${fmt(p,2,true)} % sur ${t} %"><i style="width:${w}%"></i></span> ${fmt(p,2,true)} %`}
function viewStrat(){return `<p class="note">Un compte fictif par stratégie × marché × timeframe × R:R. Triez en cliquant sur les colonnes.</p>`+
 table("strat",[["Marché","symbole"],["TF","tf"],["Stratégie","strategie"],["Risque","risque"],["Origine","origine"],["Trades","trades",null,1],
 ["Réussite","gagnants",(v,r)=>r.trades?fmt(v/r.trades*100,0)+" %":"—",1],["R moyen","r_moyen",rr,1],["R total","r_total",rr,1],["P&L","pnl",money,1],
 ["Objectif FTMO","profit_pct",prog],["Pire jour","pire_jour_pct",v=>`<span class="${cls(v)}">${fmt(v,2)} %</span>`,1],["DD max","dd_max",v=>fmt(v,2)+" %",1],
 ["Challenge","ftmo",ftmoCell],["Attendu (recherche)","attendu_r",v=>v==null?"—":rr(v),1],["","en_position",v=>v?'<span class="tag run">en position</span>':""]],filt(D.comptes))}
function viewRR(){const c=filt(D.comptes),by={};c.forEach(x=>{const k=x.rr==null?"signal":"1:"+x.rr;(by[k]=by[k]||{k,rr:x.rr??99,t:0,w:0,R:0,p:0,n:0});
 const b=by[k];b.t+=x.trades;b.w+=x.gagnants;b.R+=x.r_total;b.p+=x.pnl;b.n++});
 const rows=Object.values(by).sort((a,b)=>a.rr-b.rr);if(!rows.length)return `<div class="empty">Pas encore de trade clôturé</div>`;
 const m=Math.max(...rows.map(r=>Math.abs(r.R)),1e-9);
 const bars=`<div class="bars">`+rows.map(r=>{const w=Math.abs(r.R)/m*50;
  return `<div><b>R:R ${esc(r.k)}</b></div><div class="bar" title="R:R ${esc(r.k)} : ${fmt(r.R,1,true)}R sur ${r.t} trades"><span class="zero" style="left:50%"></span><i class="${r.R<0?"neg":""}" style="${r.R<0?`right:50%`:`left:50%`};width:${w}%"></i></div><div class="n">${rr(r.R)}</div>`}).join("")+`</div>`;
 const strat={};c.forEach(x=>{const k=x.base;(strat[k]=strat[k]||{base:k,best:null,R:-1e9,t:0});const s=strat[k];s.t+=x.trades;if(x.trades&&x.r_total>s.R){s.R=x.r_total;s.best=x.rr==null?"signal":"1:"+x.rr}});
 return `<p class="note">R total cumulé de toutes les stratégies pour chaque R:R (marchés et timeframes filtrés ci-dessus).</p>`+bars+
 `<h3 style="margin:18px 0 8px;font-size:15px">Par R:R</h3>`+table("rrt",[["R:R","k"],["Comptes","n",null,1],["Trades","t",null,1],["Réussite","w",(v,r)=>r.t?fmt(v/r.t*100,0)+" %":"—",1],
 ["R total","R",rr,1],["R moyen / trade","R",(v,r)=>r.t?rr(v/r.t):"—",1],["P&L","p",money,1]],rows)+
 `<h3 style="margin:18px 0 8px;font-size:15px">Meilleur R:R pour chaque stratégie</h3>`+
 table("rrs",[["Stratégie","base"],["Meilleur R:R","best"],["R total (meilleur)","R",rr,1],["Trades (tous R:R)","t",null,1]],Object.values(strat).filter(s=>s.best).sort((a,b)=>b.R-a.R))}
function viewFtmo(){const c=filt(D.comptes);const ok=c.filter(x=>x.ftmo==="RÉUSSI"),ko=c.filter(x=>x.ftmo.startsWith("ÉCHOUÉ"));
 return `<p class="note">Chaque compte fictif est suivi comme un challenge FTMO (${esc(D.ftmo_label)}). La perte du jour compte le latent des positions ouvertes.</p>`+
 table("ftmo",[["Challenge","ftmo",ftmoCell],["Quand","ftmo_quand"],["Marché","symbole"],["TF","tf"],["Stratégie","strategie"],["Risque","risque"],
 ["Progression","profit_pct",prog],["Pire jour","pire_jour_pct",v=>`<span class="${cls(v)}">${fmt(v,2)} %</span>`,1],["Jours tradés","jours_trades",null,1],
 ["Trades","trades",null,1],["DD max","dd_max",v=>fmt(v,2)+" %",1]],[...ok,...c.filter(x=>x.ftmo==="en cours").sort((a,b)=>b.profit_pct-a.profit_pct),...ko])}
function viewLog(){return table("log",[["Heure","t"],["Type","type",v=>`<span class="tag">${esc(v)}</span>`],["Marché","symbole"],["TF","tf"],["Détail","texte"]],filt(D.evenements))}
function render(){if(!D)return;tiles();document.querySelectorAll(".tabs button").forEach(b=>b.classList.toggle("on",b.dataset.k===tab));
 const v={pos:viewPos,hist:viewHist,strat:viewStrat,rr:viewRR,ftmo:viewFtmo,log:viewLog}[tab]||viewPos;
 const el=document.getElementById("view"),sc=el.querySelector(".scroll"),top=sc?sc.scrollTop:0;el.innerHTML=v();
 const sc2=el.querySelector(".scroll");if(sc2)sc2.scrollTop=top}
function fillSelect(id,vals){const el=document.getElementById(id),cur=el.value,first=el.options[0].outerHTML;
 el.innerHTML=first+[...vals].sort().map(v=>`<option${v===cur?" selected":""}>${esc(v)}</option>`).join("")}
async function poll(){try{const r=await fetch("/api/etat",{cache:"no-store"});D=await r.json();
 dot.classList.remove("off");maj.textContent="en direct · "+D.maj;
 info.textContent=`${D.serveur} · capital fictif ${fmt(D.capital,0)} $ / compte · perte max ${D.risque_pct} % par trade · aucun ordre envoyé à MT5`;
 prix.innerHTML=Object.entries(D.prix).map(([s,p])=>`<span class="px"><b>${esc(s)}</b> ${Number(p.bid).toFixed(p.digits)} / ${Number(p.ask).toFixed(p.digits)} · spread ${p.spread}</span>`).join("");
 fillSelect("fSym",new Set([...D.comptes.map(x=>x.symbole),...Object.keys(D.prix)]));fillSelect("fTf",new Set(D.comptes.map(x=>x.tf).concat(D.positions.map(x=>x.tf))));
 render()}catch(e){dot.classList.add("off");maj.textContent="plateforme arrêtée (relancez le paper trading)"}}
const dot=document.getElementById("dot"),maj=document.getElementById("maj"),info=document.getElementById("info"),prix=document.getElementById("prix");
poll();setInterval(poll,3000);
</script></body></html>"""
