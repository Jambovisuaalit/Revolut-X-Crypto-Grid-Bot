export interface ShadowFill {time_ms:number|null;side:"BUY"|"SELL"|"SELL_HARD_STOP";quantity:string;price_eur:string;fee_eur:string;lot_id:string;cycle_id:string;synthetic:true}
export interface ShadowPosition {level:number;quantity:string;cost_basis_eur:string;synthetic:true}
export interface ShadowPoint {source_ms:number|null;equity_eur:string}
export interface ShadowView {
 schema:"grid-console-shadow-readonly-v1";
 status:"READY"|"NOT_CONFIGURED"|"NO_DATA"|"BLOCKED";
 reason:string;mode:"SHADOW_ONLY";pair:"ETH-EUR";
 account_connection:"NOT_CONNECTED";live_orders:"DISABLED";reconciled_for_trading:false;
 source:null|"SYNTHETIC_FIXTURE_ONLY"|"PUBLIC_REPLAY_UNVERIFIED";
 run_id:string|null;config_hash:string|null;
 journal_integrity:"PASS"|"NOT_VERIFIED";observations:number;
 initial_equity_eur:string|null;equity_eur:string|null;cash_eur:string|null;
 eth_qty:string|null;realized_pnl_eur:string|null;unrealized_pnl_eur:string|null;
 total_pnl_eur:string|null;fees_eur:string|null;max_drawdown_eur:string|null;
 risk_state:string;hard_stop_triggered:boolean|null;cost_basis_eur:string|null;
 last_source_ms:number|null;positions:ShadowPosition[];fills:ShadowFill[];equity_history:ShadowPoint[];
}
const NUMBER=/^-?(?:0|[1-9]\d*)(?:\.\d+)?$/;
const decimal=(x:unknown):x is string=>typeof x==="string"&&x.length<=80&&NUMBER.test(x)&&Number.isFinite(Number(x));
const text=(x:unknown,max=100):x is string=>typeof x==="string"&&x.length>0&&x.length<=max&&!/[\x00-\x1f\x7f]/.test(x);
const stamp=(x:unknown)=>x===null||(typeof x==="number"&&Number.isSafeInteger(x)&&x>=0);
const obj=(x:unknown):x is Record<string,unknown>=>typeof x==="object"&&x!==null&&!Array.isArray(x);
const money=["initial_equity_eur","equity_eur","cash_eur","eth_qty","realized_pnl_eur","unrealized_pnl_eur","total_pnl_eur","fees_eur","max_drawdown_eur","cost_basis_eur"];
export function isShadowView(v:unknown):v is ShadowView {
 if(!obj(v))return false;
 if(v.schema!=="grid-console-shadow-readonly-v1"||v.mode!=="SHADOW_ONLY"||v.pair!=="ETH-EUR"||
 v.live_orders!=="DISABLED"||v.account_connection!=="NOT_CONNECTED"||v.reconciled_for_trading!==false||
 !["READY","NOT_CONFIGURED","NO_DATA","BLOCKED"].includes(String(v.status))||
 !text(v.reason)||!stamp(v.last_source_ms)||!Number.isSafeInteger(v.observations)||Number(v.observations)<0||
 !Array.isArray(v.positions)||v.positions.length>4||
 !Array.isArray(v.fills)||v.fills.length>40||
 !Array.isArray(v.equity_history)||v.equity_history.length>300)return false;
 if(v.status!=="READY"){
   return v.journal_integrity!=="NOT_VERIFIED"?v.status==="NO_DATA"&&v.journal_integrity==="PASS":
    money.every(k=>v[k]===null)&&v.positions.length===0&&v.fills.length===0&&v.equity_history.length===0;
 }
 if(v.journal_integrity!=="PASS"||!["SYNTHETIC_FIXTURE_ONLY","PUBLIC_REPLAY_UNVERIFIED"].includes(String(v.source))||
 !text(v.run_id)||typeof v.config_hash!=="string"||!/^[a-f0-9]{64}$/.test(v.config_hash)||
 !money.every(k=>decimal(v[k]))||!text(v.risk_state,40)||typeof v.hard_stop_triggered!=="boolean")return false;
 return v.positions.every(p=>obj(p)&&Number.isSafeInteger(p.level)&&Number(p.level)>0&&Number(p.level)<=4&&decimal(p.quantity)&&decimal(p.cost_basis_eur)&&p.synthetic===true)
 &&v.fills.every(f=>obj(f)&&stamp(f.time_ms)&&["BUY","SELL","SELL_HARD_STOP"].includes(String(f.side))&&decimal(f.quantity)&&decimal(f.price_eur)&&decimal(f.fee_eur)&&text(f.lot_id,32)&&text(f.cycle_id,50)&&f.synthetic===true)
 &&v.equity_history.every(p=>obj(p)&&stamp(p.source_ms)&&decimal(p.equity_eur));
}
