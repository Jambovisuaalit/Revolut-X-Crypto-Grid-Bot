import type {ShadowPoint} from "./contracts";
export default function EquitySparkline({history}:{history:ShadowPoint[]}){
 const values=history.map(p=>Number(p.equity_eur));
 if(values.length<2)return <section className="panel" aria-label="Virtuaalisen equityn kehitys"><h3>Simuloidun equityn kehitys</h3><p>Ei riittävästi havaintoja käyrän näyttämiseen.</p></section>;
 const min=Math.min(...values),max=Math.max(...values);
 const span=max-min||1;
 const points=values.map((value,i)=>[(i/(values.length-1))*680+16,150-((value-min)/span)*120+5]);
 const path=points.map(([x,y])=>x.toFixed(1)+","+y.toFixed(1)).join(" ");
 return <section className="panel equity-chart" aria-label="Simuloidun equityn kehitys">
  <div className="section-head"><h3>Simuloidun equityn kehitys</h3><span className="eyebrow">{values.length} tallennettua havaintoa</span></div>
  <svg viewBox="0 0 720 180" role="img" aria-label="Tallennetun virtuaalisen equityn muutos havaintojärjestyksessä">
   <line x1="16" x2="696" y1="20" y2="20" stroke="#304357"/>
   <line x1="16" x2="696" y1="95" y2="95" stroke="#304357" strokeDasharray="4 5"/>
   <line x1="16" x2="696" y1="155" y2="155" stroke="#304357"/>
   <polyline points={path} fill="none" stroke="#87cad6" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round"/>
  </svg>
  <p className="muted">Paikallisen shadow-journalin historiapisteet. Ei reaaliaikaista pörssihintaa eikä tuottoennustetta.</p>
 </section>;
}
