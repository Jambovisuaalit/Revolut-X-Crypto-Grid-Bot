// @vitest-environment jsdom
import {afterEach,describe,expect,it,vi} from "vitest";
import {cleanup,render,screen,waitFor} from "@testing-library/react";
import ShadowWorkspace from "./ShadowWorkspace";
import {isShadowView} from "./contracts";
const safe={
 schema:"grid-console-shadow-readonly-v1",status:"READY",reason:"NONE",
 mode:"SHADOW_ONLY",pair:"ETH-EUR",account_connection:"NOT_CONNECTED",live_orders:"DISABLED",reconciled_for_trading:false,
 source:"SYNTHETIC_FIXTURE_ONLY",run_id:"v05-offline",config_hash:"a".repeat(64),journal_integrity:"PASS",observations:5,
 initial_equity_eur:"1000.00",equity_eur:"1001.25",cash_eur:"1001.25",eth_qty:"0",
 realized_pnl_eur:"1.25",unrealized_pnl_eur:"0",total_pnl_eur:"1.25",fees_eur:"0.18",
 max_drawdown_eur:"1.00",risk_state:"RUNNING",hard_stop_triggered:false,cost_basis_eur:"0",last_source_ms:1792195500000,
 positions:[],fills:[{time_ms:1792195500000,side:"SELL",quantity:"0.02",price_eur:"3000",fee_eur:"0.05",lot_id:"L1",cycle_id:"L1:C0",synthetic:true}],
 equity_history:[{source_ms:1792195500000,equity_eur:"1001.25"}]
};
function mock(payload:unknown,ok=true){
 vi.stubGlobal("fetch",vi.fn(async()=>({ok,json:async()=>payload})));
}
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
describe("Shadow Workspace read-only safety",()=>{
 it("shows only marked synthetic account figures after validated read",async()=>{
  mock(safe);render(<ShadowWorkspace refreshKey={0}/>);
  expect(await screen.findByText("1.25 €")).toBeTruthy();
  expect(screen.getByText(/Synteettinen testiaineisto/)).toBeTruthy();
  expect(screen.getByText(/SIMULOITU · ETH\/EUR · EI LIVE-KAUPPAA/)).toBeTruthy();
  expect(screen.getByText(/Ei oikean tilin saldo/)).toBeTruthy();
  expect(screen.queryByText(/Käynnistä kaupankäynti/)).toBeNull();
 });
 it("does not show synthetic balances when no file was selected",async()=>{
  mock({...safe,status:"NOT_CONFIGURED",journal_integrity:"NOT_VERIFIED",source:null,
   run_id:null,config_hash:null,observations:0,initial_equity_eur:null,equity_eur:null,cash_eur:null,eth_qty:null,
   realized_pnl_eur:null,unrealized_pnl_eur:null,total_pnl_eur:null,fees_eur:null,max_drawdown_eur:null,
   cost_basis_eur:null,last_source_ms:null,risk_state:"UNKNOWN",hard_stop_triggered:null,positions:[],fills:[],equity_history:[]});
  render(<ShadowWorkspace refreshKey={0}/>);
  expect(await screen.findByText(/Shadow-journalia ei ole valittu/)).toBeTruthy();
  expect(screen.queryByText("1001.25 €")).toBeNull();
 });
 it("fails closed if payload falsely claims a real account",async()=>{
  mock({...safe,live_orders:"ENABLED"});render(<ShadowWorkspace refreshKey={0}/>);
  expect(await screen.findByText(/Shadow-tietojen luku tai validointi epäonnistui/)).toBeTruthy();
  expect(screen.queryByText("1001.25 €")).toBeNull();
 });
 it("requires strict synthetic fills",()=>{
  expect(isShadowView({...safe,fills:[{...safe.fills[0],synthetic:false}]})).toBe(false);
  expect(isShadowView({...safe,source:"REVOLUT_X_LIVE"})).toBe(false);
 });
 it("shows risk status as a virtual halt, never as a live action",async()=>{
  mock({...safe,risk_state:"STOPPED",hard_stop_triggered:true});render(<ShadowWorkspace refreshKey={0}/>);
  expect(await screen.findByText(/Virtuaalinen HARD STOP lauennut/)).toBeTruthy();
 });
});
