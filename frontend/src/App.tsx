import { useEffect, useState } from "react";
import { fetchLocalRuntime } from "./api/client";
import type { LocalRuntime } from "./api/contracts";
import { statusPresentation,riskPresentation,heartbeatPresentation,tradingPresentation } from "./ui/status";
import StatusPanel from "./ui/StatusPanel";
import MetricPanel from "./ui/MetricPanel";
import EventsPanel from "./ui/EventsPanel";
import ShadowWorkspace from "./shadow/ShadowWorkspace";
import "./app.css";
export default function App() {
 const [runtime,setRuntime]=useState<LocalRuntime|null>(null);
 const [error,setError]=useState(false);
 const [loading,setLoading]=useState(true);
 const [updatedAt,setUpdatedAt]=useState<string|null>(null);
 const [refreshKey,setRefreshKey]=useState(0);
 useEffect(()=>{
   const controller=new AbortController();
   setLoading(true);
   fetchLocalRuntime(controller.signal).then(value=>{
     if(controller.signal.aborted)return;
     setRuntime(value);setError(false);setUpdatedAt(new Date().toISOString());setLoading(false);
   }).catch(()=>{
     if(controller.signal.aborted)return;
     setRuntime(null);setError(true);setLoading(false);
   });
   return ()=>controller.abort();
 },[refreshKey]);
 const blocked = !runtime || runtime.status==="BLOCKED" || runtime.status==="RISK_LOCKED" || runtime.risk_lock!=="NOT_TRIGGERED";
 return <div className="shell"><aside className="sidebar">
  <strong>GRID CONSOLE</strong><p>Paikallinen Windows-työtila</p>
  <nav aria-label="Päävalikko"><span aria-current="page">Yleisnäkymä</span><span aria-disabled="true">Strategiat · ei käytettävissä</span><span aria-disabled="true">Kaupat · ei käytettävissä</span><span aria-disabled="true">Asetukset · ei käytettävissä</span></nav>
  <p className="sidebar-foot">OFFLINE · EI PÖRSSIVARMENNETTU</p>
 </aside><main>
  <header><div><small>TYÖTILA / YLEISNÄKYMÄ</small><h1>Järjestelmän tila</h1><p className="timestamp">Viimeisin onnistunut päivitys: {updatedAt ? new Date(updatedAt).toLocaleString("fi-FI"):"Ei vielä"}</p></div>
   <button type="button" disabled={loading} onClick={()=>setRefreshKey(v=>v+1)}>{loading?"Päivitetään…":"Päivitä tiedot"}</button></header>
  <section className="trading-banner" role="status"><span className="eyebrow">PYSYVÄ TURVARAJA</span><strong>LIVE-KAUPANKÄYNTI ESTETTY</strong><p>Ei oikeaa tililiitäntää eikä toimeksiantoja. Näytettävä data on vain paikallista.</p></section>
  {error&&<section className="error-banner" role="alert"><strong>Paikallisen tilan luku epäonnistui</strong><p>Tietoja ei voi vahvistaa. Näkymä on estetty. Tarkista käynnistin ja kokeile päivitystä.</p></section>}
  {loading&&<p role="status" className="loading">Luetaan paikallista tilaa…</p>}
  <section className="primary-status" aria-label="Turvallisuustilat">
   <StatusPanel heading="Valvojan tila" value={statusPresentation(runtime?.status??null)} prominent/>
   <StatusPanel heading="Riskilukitus" value={riskPresentation(runtime?.risk_lock??null)} prominent/>
   <StatusPanel heading="Kaupankäynti" value={tradingPresentation}/>
  </section>
  <section className="metrics" aria-label="Paikalliset mittarit">
   <MetricPanel label="Heartbeat" value={heartbeatPresentation(runtime?.heartbeat??null).title} note={heartbeatPresentation(runtime?.heartbeat??null).description}/>
   <MetricPanel label="Epävarmat toimeksiannot" value={runtime?.uncertain_order_count===null||!runtime?"—":String(runtime.uncertain_order_count)} note="Paikalliset merkinnät, ei pörssivarmennettu"/>
   <MetricPanel label="Datan lähde" value="Paikallinen SQLite" note="Ei yhteyttä Revolut X -tiliin"/>
  </section>
  <section className="panel safety" aria-label="Turvatilan tulkinta"><h2>Turvatilan tulkinta</h2>
   <p className={blocked?"danger":"muted"}>{blocked?"Estetty tai turvallista tilaa ei voida vahvistaa":"Paikallinen tallennettu tila — ei pörssivarmennettu"}</p>
   <p className="muted">Tuntematon riskitila ei tarkoita riskilukituksen puuttumista.</p>
  </section>
  <ShadowWorkspace refreshKey={refreshKey}/>
  <EventsPanel events={runtime?.events??[]}/>
  <details className="panel diagnostics"><summary>Tekniset arvot ja diagnostiikka</summary><dl>
   <dt>Valvojan tekninen tila</dt><dd>{runtime?.status??"UNKNOWN"}</dd>
   <dt>Riskilukituksen tekninen tila</dt><dd>{runtime?.risk_lock??"UNKNOWN"}</dd>
   <dt>Heartbeat</dt><dd>{runtime?.heartbeat??"UNKNOWN"}</dd>
   <dt>Provenienssi</dt><dd>{runtime?.provenance??"LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED"}</dd>
   <dt>Live-toimeksiannot</dt><dd>DISABLED</dd>
   <dt>Pörssin nykytila varmistettu</dt><dd>FALSE</dd>
  </dl></details>
 </main></div>;
}
