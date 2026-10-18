import type { LocalEvent } from "../api/contracts";
export default function EventsPanel({events}:{events:LocalEvent[]}) {
 return <section className="panel event-panel" aria-label="Paikalliset tapahtumat">
  <div className="section-head"><h2>Paikalliset tapahtumat</h2><span className="eyebrow">{events.length} / 40 merkintää</span></div>
  <p>Vain paikallisen tietokannan tapahtumia — ei todistetta pörssin tilasta.</p>
  {events.length===0 ? <p className="empty">Ei paikallisia tapahtumamerkintöjä.</p> :
   <ol>{events.map((event,index)=><li key={event.category+"-"+event.time_utc+"-"+index}>
    <time dateTime={event.time_utc}>{event.time_utc}</time><span>{event.label}</span>
    {event.uncertain&&<strong className="uncertain">EPÄVARMA</strong>}
   </li>)}</ol>}
 </section>;
}
