import type { Presentation } from "./status";
export default function StatusPanel({heading, value, prominent=false}:{heading:string;value:Presentation;prominent?:boolean}) {
 return <article className={"status-panel tone-"+value.tone+(prominent?" prominent":"")}>
  <span className="eyebrow">{heading}</span><strong className="status-title">{value.title}</strong>
  <p>{value.description}</p><p className="action">{value.action}</p>
 </article>;
}
