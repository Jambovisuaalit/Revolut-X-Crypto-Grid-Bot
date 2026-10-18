export type SupervisorStatus = "NOT_CONFIGURED" | "NOT_STARTED" | "STOPPED" | "RECONCILING" | "OFFLINE_MONITORING" | "BLOCKED" | "RISK_LOCKED";
export type EventCategory = "ORDER_LOCAL" | "FILL_LOCAL";
export interface LocalEvent { time_utc: string; category: EventCategory; label: string; uncertain: boolean; timestamp_kind?: string }
export interface LocalRuntime { schema: string; provenance: "LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED"; status: SupervisorStatus; risk_lock: "TRIGGERED" | "NOT_TRIGGERED" | "UNKNOWN"; heartbeat: "FRESH" | "STALE" | "UNKNOWN"; heartbeat_age_ms: number | null; uncertain_order_count: number | null; events: LocalEvent[]; live_orders: "DISABLED"; account_connection: "NOT_CONNECTED"; reconciled_for_trading: false }
const STATUSES:readonly string[]=["NOT_CONFIGURED","NOT_STARTED","STOPPED","RECONCILING","OFFLINE_MONITORING","BLOCKED","RISK_LOCKED"];
const RISKS:readonly string[]=["TRIGGERED","NOT_TRIGGERED","UNKNOWN"];
const HEARTBEATS:readonly string[]=["FRESH","STALE","UNKNOWN"];
const KINDS:readonly string[]=["ORDER_LOCAL","FILL_LOCAL"];
const TIME_KINDS:readonly string[]=["ORDER_CREATED_AT","FILL_CREATED_AT","UNKNOWN"];
function isRecord(v:unknown):v is Record<string,unknown> {return typeof v==="object"&&v!==null&&!Array.isArray(v);}
function isUtcTimestamp(v:unknown):v is string {
 if(typeof v!=="string"||v.length>35||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(v))return false;
 const ms=Date.parse(v);
 return Number.isFinite(ms)&&new Date(ms).toISOString().slice(0,19)===v.slice(0,19);
}
function isEvent(v:unknown):v is LocalEvent {
 if(!isRecord(v))return false;
 return KINDS.includes(String(v.category))&&isUtcTimestamp(v.time_utc)&&
  typeof v.label==="string"&&v.label.length>0&&v.label.length<=240&&
  !/[\x00-\x08\x0B\x0C\x0E-\x1F]/.test(v.label)&&
  typeof v.uncertain==="boolean"&&
  (v.timestamp_kind===undefined||(typeof v.timestamp_kind==="string"&&TIME_KINDS.includes(v.timestamp_kind)));
}
export const isLocalRuntime=(value:unknown):value is LocalRuntime=>{
 if(!isRecord(value))return false;
 const x=value;
 return x.schema==="grid-console-local-runtime-v1"&&x.provenance==="LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED"&&
  STATUSES.includes(String(x.status))&&RISKS.includes(String(x.risk_lock))&&HEARTBEATS.includes(String(x.heartbeat))&&
  (x.heartbeat_age_ms===null||(typeof x.heartbeat_age_ms==="number"&&Number.isFinite(x.heartbeat_age_ms)&&x.heartbeat_age_ms>=0))&&
  (x.uncertain_order_count===null||(typeof x.uncertain_order_count==="number"&&Number.isSafeInteger(x.uncertain_order_count)&&x.uncertain_order_count>=0))&&
  Array.isArray(x.events)&&x.events.length<=40&&x.events.every(isEvent)&&
  x.live_orders==="DISABLED"&&x.account_connection==="NOT_CONNECTED"&&x.reconciled_for_trading===false;
};