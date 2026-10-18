import {describe,it,expect} from "vitest";
import {isLocalRuntime} from "./contracts";
const base={
 schema:"grid-console-local-runtime-v1",provenance:"LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED",
 status:"RISK_LOCKED",risk_lock:"TRIGGERED",heartbeat:"STALE",heartbeat_age_ms:500,
 uncertain_order_count:1,live_orders:"DISABLED",account_connection:"NOT_CONNECTED",
 reconciled_for_trading:false,
 events:[{time_utc:"2026-10-17T08:00:00Z",category:"ORDER_LOCAL",label:"Paikallinen tapahtuma",uncertain:true,timestamp_kind:"ORDER_CREATED_AT"}]
};
describe("strict local runtime event contract",()=>{
 it("accepts supported local ISO UTC event",()=>expect(isLocalRuntime(base)).toBe(true));
 it.each([
  {category:"EXCHANGE_ORDER"}, {category:""},
  {time_utc:"2026-10-17 08:00:00"}, {time_utc:"2026-13-17T08:00:00Z"},
  {time_utc:"2026-10-17T25:00:00Z"}, {label:""}, {label:"x".repeat(241)},
  {label:"invalid\u0000text"}, {timestamp_kind:"RAW_EXCHANGE_TIMESTAMP"},
 ])("rejects bad event %j",change=>{
  const events=[{...base.events[0],...change}];
  expect(isLocalRuntime({...base,events})).toBe(false);
 });
 it("rejects unsafe execution capabilities",()=>{
  expect(isLocalRuntime({...base,live_orders:"ENABLED"})).toBe(false);
  expect(isLocalRuntime({...base,account_connection:"CONNECTED"})).toBe(false);
  expect(isLocalRuntime({...base,reconciled_for_trading:true})).toBe(false);
 });
 it("rejects invalid counters and stale negative ages",()=>{
  expect(isLocalRuntime({...base,uncertain_order_count:-1})).toBe(false);
  expect(isLocalRuntime({...base,heartbeat_age_ms:-1})).toBe(false);
 });
});
