export type SupervisorStatus = "NOT_CONFIGURED" | "NOT_STARTED" | "STOPPED" | "RECONCILING" | "OFFLINE_MONITORING" | "BLOCKED" | "RISK_LOCKED";
export interface LocalEvent { time_utc: string; category: "ORDER_LOCAL" | "FILL_LOCAL"; label: string; uncertain: boolean; timestamp_kind?: string }
export interface LocalRuntime { schema: string; provenance: "LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED"; status: SupervisorStatus; risk_lock: "TRIGGERED" | "NOT_TRIGGERED" | "UNKNOWN"; heartbeat: "FRESH" | "STALE" | "UNKNOWN"; heartbeat_age_ms: number | null; uncertain_order_count: number | null; events: LocalEvent[]; live_orders: "DISABLED"; account_connection: "NOT_CONNECTED"; reconciled_for_trading: false }
export const isLocalRuntime = (value: unknown): value is LocalRuntime => {
  if (typeof value !== "object" || value === null) return false;
  const x = value as Record<string, unknown>;
  return x.schema === "grid-console-local-runtime-v1" && x.provenance === "LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED" &&
    ["NOT_CONFIGURED","NOT_STARTED","STOPPED","RECONCILING","OFFLINE_MONITORING","BLOCKED","RISK_LOCKED"].includes(String(x.status)) &&
    ["TRIGGERED","NOT_TRIGGERED","UNKNOWN"].includes(String(x.risk_lock)) &&
    ["FRESH","STALE","UNKNOWN"].includes(String(x.heartbeat)) &&
    (x.heartbeat_age_ms === null || (typeof x.heartbeat_age_ms === "number" && Number.isFinite(x.heartbeat_age_ms))) &&
    (x.uncertain_order_count === null || (Number.isSafeInteger(x.uncertain_order_count) && Number(x.uncertain_order_count) >= 0)) &&
    Array.isArray(x.events) && x.events.length <= 40 &&
    x.events.every((e: unknown) => typeof e === "object" && e !== null && typeof (e as LocalEvent).label === "string" && typeof (e as LocalEvent).time_utc === "string" && typeof (e as LocalEvent).uncertain === "boolean") &&
    x.live_orders === "DISABLED" && x.account_connection === "NOT_CONNECTED" && x.reconciled_for_trading === false;
};
