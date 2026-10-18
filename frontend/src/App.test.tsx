// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, fireEvent } from "@testing-library/react";
import App from "./App";
const runtime={
 schema:"grid-console-local-runtime-v1",provenance:"LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED",
 status:"RISK_LOCKED",risk_lock:"TRIGGERED",heartbeat:"STALE",heartbeat_age_ms:30000,
 uncertain_order_count:1,events:[{time_utc:"2026-10-17T08:00:00Z",category:"ORDER_LOCAL",
 label:"Osto — paikallinen tila epävarma",uncertain:true,timestamp_kind:"ORDER_CREATED_AT"}],
 live_orders:"DISABLED",account_connection:"NOT_CONNECTED",reconciled_for_trading:false
};
function mockRuntime(payload:unknown,ok=true){
 const fn=vi.fn(async()=>({ok,json:async()=>payload}));vi.stubGlobal("fetch",fn);return fn;
}
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
describe("Grid Console v0.4 local-only dashboard",()=>{
 it("shows distinct Finnish risk and trading status with diagnostic codes",async()=>{
  const request=mockRuntime(runtime);render(<App/>);
  expect(await screen.findByText("Riskilukitus aktiivinen")).toBeTruthy();
  expect(screen.getByText("Kaupankäynti estetty")).toBeTruthy();
  expect(screen.getByText("Paikallinen tieto vanhentunut")).toBeTruthy();
  expect(screen.getByText("EPÄVARMA")).toBeTruthy();
  expect(screen.getByText(/LIVE-KAUPANKÄYNTI ESTETTY/)).toBeTruthy();
  expect(request).toHaveBeenCalledWith("/api/runtime",expect.objectContaining({method:"GET",cache:"no-store"}));
  fireEvent.click(screen.getByText("Tekniset arvot ja diagnostiikka"));
  expect(screen.getAllByText("RISK_LOCKED").length).toBeGreaterThan(0);
  expect(screen.getAllByText("TRIGGERED").length).toBeGreaterThan(0);
  expect(screen.queryByText("Käynnistä kaupankäynti")).toBeNull();
 });
 it("distinguishes missing data from triggered lock",async()=>{
  mockRuntime({...runtime,status:"NOT_CONFIGURED",risk_lock:"UNKNOWN",heartbeat:"UNKNOWN",uncertain_order_count:null,events:[]});
  render(<App/>);
  expect(await screen.findByText("Valvojaa ei määritetty")).toBeTruthy();
  expect(screen.getByText("Riskitila tuntematon")).toBeTruthy();
  expect(screen.queryByText("Riskilukitus aktiivinen")).toBeNull();
 });
 it("uses fail-closed state on invalid event data",async()=>{
  mockRuntime({...runtime,events:[{...runtime.events[0],category:"LIVE_ORDER"}]});
  render(<App/>);
  expect(await screen.findByText("Paikallisen tilan luku epäonnistui")).toBeTruthy();
  expect(screen.getByText("Tila tuntematon")).toBeTruthy();
  expect(screen.getByText("Riskitila tuntematon")).toBeTruthy();
 });
 it("uses fail-closed state on API failure and clears stale data",async()=>{
  mockRuntime(runtime,false);render(<App/>);
  expect(await screen.findByText("Paikallisen tilan luku epäonnistui")).toBeTruthy();
  expect(screen.queryByText("Riskilukitus aktiivinen")).toBeNull();
 });
 it("shows loading and latest successful refresh",async()=>{
  let complete:(value:unknown)=>void=()=>{};
  const deferred=new Promise<unknown>(resolve=>{complete=resolve;});
  vi.stubGlobal("fetch",vi.fn(()=>deferred));
  render(<App/>);
  expect(screen.getByText("Luetaan paikallista tilaa…")).toBeTruthy();
  complete({ok:true,json:async()=>runtime});
  await waitFor(()=>expect(screen.getByText("Riskilukitus aktiivinen")).toBeTruthy());
  expect(screen.getByText(/Viimeisin onnistunut päivitys:/).textContent).not.toContain("Ei vielä");
  expect(screen.getByRole("button",{name:"Päivitä tiedot"})).toBeTruthy();
 });
});
