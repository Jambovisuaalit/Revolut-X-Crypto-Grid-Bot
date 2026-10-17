import { describe, expect, it } from "vitest";
import { isLocalRuntime } from "./contracts";
const safe = {schema:"grid-console-local-runtime-v1",provenance:"LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED",status:"STOPPED",risk_lock:"UNKNOWN",heartbeat:"UNKNOWN",heartbeat_age_ms:null,uncertain_order_count:null,events:[],live_orders:"DISABLED",account_connection:"NOT_CONNECTED",reconciled_for_trading:false};
describe("read-only runtime contract",()=>{
  it("accepts a local-only snapshot",()=>expect(isLocalRuntime(safe)).toBe(true));
  it("rejects a live-trading response",()=>expect(isLocalRuntime({...safe,live_orders:"ENABLED"})).toBe(false));
  it("rejects claimed reconciliation",()=>expect(isLocalRuntime({...safe,reconciled_for_trading:true})).toBe(false));
  it("rejects unknown states",()=>expect(isLocalRuntime({...safe,status:"LIVE"})).toBe(false));
  it("rejects malformed event lists",()=>expect(isLocalRuntime({...safe,events:[{label:"bad"}]})).toBe(false));
});
