import { useEffect, useState } from "react";
import { fetchLocalRuntime } from "./api/client";
import type { LocalRuntime } from "./api/contracts";
import "./app.css";
export default function App() {
  const [runtime, setRuntime] = useState<LocalRuntime | null>(null);
  const [error, setError] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    fetchLocalRuntime(controller.signal).then(value => { setRuntime(value); setError(false); }).catch(() => {
      if (!controller.signal.aborted) { setRuntime(null); setError(true); }
    });
    return () => controller.abort();
  }, [refreshKey]);
  const blocked = !runtime || runtime.status === "BLOCKED" || runtime.status === "RISK_LOCKED" || runtime.risk_lock !== "NOT_TRIGGERED";
  return <div className="shell"><aside className="sidebar"><strong>GRID CONSOLE</strong><p>Paikallinen Windows-työtila</p><nav aria-label="Päävalikko"><span aria-current="page">Yleisnäkymä</span><span aria-disabled="true">Strategiat · tulossa</span><span aria-disabled="true">Kaupat · tulossa</span><span aria-disabled="true">Asetukset · tulossa</span></nav></aside><main><header><div><small>TYÖTILA / YLEISNÄKYMÄ</small><h1>Järjestelmän tila</h1></div><button type="button" onClick={() => setRefreshKey(v => v + 1)}>Päivitä tiedot</button></header><section className="warning" role="status"><strong>LIVE-KAUPANKÄYNTI ESTETTY</strong><p>Tämä on vain paikallinen kehitysnäkymä. Ei Revolut X -tiliyhteyttä.</p></section><section className="cards" aria-label="Valvojan tila"><article><span>Valvoja</span><strong>{error ? "Luku epäonnistui" : runtime?.status ?? "Ei varmennettu"}</strong></article><article><span>Riskilukitus</span><strong>{runtime?.risk_lock ?? "UNKNOWN"}</strong></article><article><span>Heartbeat</span><strong>{runtime?.heartbeat ?? "UNKNOWN"}</strong></article><article><span>Epävarmat toimeksiannot</span><strong>{runtime?.uncertain_order_count ?? "—"}</strong></article></section><section className="panel"><h2>Turvatila</h2><p className={blocked ? "danger" : "muted"}>{blocked ? "Estetty tai turvallista tilaa ei voida vahvistaa" : "Paikallinen tallennettu tila — ei pörssivarmennettu"}</p><p>Provenienssi: LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED</p></section><section className="panel"><h2>Paikalliset tapahtumamerkinnät</h2><p>Enintään 40 uusinta kirjausta. Ei todistetta pörssin nykyisestä tilasta.</p><ol>{runtime?.events.length ? runtime.events.map((event,i) => <li key={i}><time>{event.time_utc}</time> — {event.label}{event.uncertain ? " · EPÄVARMA" : ""}</li>) : <li>Ei paikallisia tapahtumamerkintöjä.</li>}</ol></section></main></div>;
}
