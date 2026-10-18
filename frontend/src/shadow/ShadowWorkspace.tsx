import {useEffect,useState} from "react";
import {fetchShadowView} from "./client";
import type {ShadowView} from "./contracts";
import MetricPanel from "../ui/MetricPanel";
function metric(v:string|null,unit:string):string {return v===null?"—":v+" "+unit;}
export default function ShadowWorkspace({refreshKey}:{refreshKey:number}){
 const [data,setData]=useState<ShadowView|null>(null);
 const [error,setError]=useState(false);
 const [loading,setLoading]=useState(true);
 useEffect(()=>{
  const controller=new AbortController();
  setLoading(true);setError(false);setData(null);
  fetchShadowView(controller.signal).then(result=>{if(!controller.signal.aborted){setData(result);setLoading(false);}})
   .catch(()=>{if(!controller.signal.aborted){setError(true);setData(null);setLoading(false);}});
  return()=>controller.abort();
 },[refreshKey]);
 const ready=data?.status==="READY";
 return <section className="shadow-workspace" aria-label="Virtuaalinen ETH/EUR-strategia">
  <div className="section-head"><h2>Shadow Trading Workspace</h2><span className="shadow-tag">SIMULOITU · ETH/EUR · EI LIVE-KAUPPAA</span></div>
  {loading&&<p role="status">Luetaan virtuaalikirjanpitoa…</p>}
  {error&&<p role="alert" className="danger">Shadow-tietojen luku tai validointi epäonnistui. Talouslukuja ei näytetä.</p>}
  {!error&&!loading&&!ready&&<p role="status" className="muted">{data?.status==="NOT_CONFIGURED"?"Shadow-journalia ei ole valittu. Valitse erillinen virtuaalisen strategian SQLite-journal käynnistysikkunasta.":data?.status==="NO_DATA"?"Shadow-journalissa ei ole vielä havaintoja.":"Shadow-journalin eheyttä ei voitu vahvistaa. Talouslukujen näyttö estetty."}</p>}
  {ready&&data&&<>
   <p className="muted">Tietolähde: {data.source==="SYNTHETIC_FIXTURE_ONLY"?"Synteettinen testiaineisto":"Julkisen datan offline-toisto (ei pörssivarmennettu)"}. Kaikki arvot ovat virtuaalisia.</p>
   <div className="shadow-metrics">
    <MetricPanel label="Alkupääoma · simuloitu" value={metric(data.initial_equity_eur,"€")} note="Lukittu ETH/EUR-koe"/>
    <MetricPanel label="Equity · simuloitu" value={metric(data.equity_eur,"€")} note="Kirjanpidon viimeinen konservatiivinen arvo"/>
    <MetricPanel label="Netto-P&L · simuloitu" value={metric(data.total_pnl_eur,"€")} note="Equity miinus alkupääoma"/>
    <MetricPanel label="Käteinen · simuloitu" value={metric(data.cash_eur,"€")} note="Ei oikean tilin saldo"/>
    <MetricPanel label="ETH · simuloitu" value={metric(data.eth_qty,"ETH")} note="Virtuaalinen omistus"/>
    <MetricPanel label="Kulut · simuloitu" value={metric(data.fees_eur,"€")} note="Sisältyvät netto-P&L-lukuun"/>
    <MetricPanel label="Realisoitunut · simuloitu" value={metric(data.realized_pnl_eur,"€")} note="Suljetut virtuaaliset lotit"/>
    <MetricPanel label="Realisoitumaton · simuloitu" value={metric(data.unrealized_pnl_eur,"€")} note="Konservatiivinen realisointiarvio"/>
    <MetricPanel label="Maksimidrawdown · simuloitu" value={metric(data.max_drawdown_eur,"€")} note="Kova riskiraja 30 €"/>
   </div>
   <div className={data.hard_stop_triggered?"shadow-risk danger":"shadow-risk"}>
    <strong>Riskitila: {data.risk_state}</strong><p>{data.hard_stop_triggered?"Virtuaalinen HARD STOP lauennut — tila on pysäytetty.":"Paikallinen shadow-riskitila; ei oikean tilin turvatakuu."}</p>
   </div>
   <div className="shadow-lists">
    <div className="panel"><h3>Virtuaaliset positiot</h3>{data.positions.length===0?<p>Ei avoimia simuloituja lotteja.</p>:<table><thead><tr><th>Taso</th><th>ETH</th><th>Hankintameno €</th></tr></thead><tbody>{data.positions.map(p=><tr key={p.level}><td>{p.level}</td><td>{p.quantity}</td><td>{p.cost_basis_eur}</td></tr>)}</tbody></table>}</div>
    <div className="panel"><h3>Simuloidut toteutukset</h3>{data.fills.length===0?<p>Ei virtuaalisia toteutuksia.</p>:<table><thead><tr><th>Aika UTC</th><th>Suunta</th><th>ETH</th><th>Hinta €</th><th>Kulu €</th></tr></thead><tbody>{data.fills.map((f,i)=><tr key={f.cycle_id+f.side+i}><td>{f.time_ms===null?"—":new Date(f.time_ms).toISOString()}</td><td>{f.side}</td><td>{f.quantity}</td><td>{f.price_eur}</td><td>{f.fee_eur}</td></tr>)}</tbody></table>}</div>
   </div>
   <details className="panel diagnostics"><summary>Shadow-journalin diagnostiikka</summary><dl>
    <dt>Run ID</dt><dd>{data.run_id}</dd><dt>Config SHA256</dt><dd>{data.config_hash}</dd>
    <dt>Havainnot</dt><dd>{data.observations}</dd><dt>Journalin eheys</dt><dd>{data.journal_integrity}</dd>
    <dt>Lähteen alkuperä</dt><dd>{data.source}</dd><dt>Live-toimeksiannot</dt><dd>DISABLED</dd>
   </dl></details>
  </>}
 </section>;
}
