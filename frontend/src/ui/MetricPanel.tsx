export default function MetricPanel({label,value,note}:{label:string;value:string;note:string}) {
 return <article className="metric-panel"><span className="eyebrow">{label}</span><strong className="metric-value">{value}</strong><p>{note}</p></article>;
}
