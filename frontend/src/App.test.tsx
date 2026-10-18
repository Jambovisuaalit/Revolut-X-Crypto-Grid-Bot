// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import App from "./App";

const localRuntime = {
  schema: "grid-console-local-runtime-v1",
  provenance: "LOCAL_SQLITE_NOT_EXCHANGE_VERIFIED",
  status: "RISK_LOCKED",
  risk_lock: "TRIGGERED",
  heartbeat: "STALE",
  heartbeat_age_ms: 30000,
  uncertain_order_count: 1,
  events: [{
    time_utc: "2026-10-17T08:00:00Z",
    category: "ORDER_LOCAL",
    label: "Osto — nykyinen paikallinen tila: Lähetys epävarma",
    uncertain: true,
    timestamp_kind: "ORDER_CREATED_AT"
  }],
  live_orders: "DISABLED",
  account_connection: "NOT_CONNECTED",
  reconciled_for_trading: false
};
function mockRuntime(payload: unknown, ok = true) {
  const fetchMock = vi.fn(async () => ({ ok, json: async () => payload }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("React local-only risk display", () => {
  it("shows persisted kill switch and uncertain order without trading controls", async () => {
    const fetchMock = mockRuntime(localRuntime);
    render(<App />);
    expect(await screen.findByText("RISK_LOCKED")).toBeTruthy();
    expect(screen.getByText("TRIGGERED")).toBeTruthy();
    expect(screen.getByText("Estetty tai turvallista tilaa ei voida vahvistaa")).toBeTruthy();
    expect(screen.getByText(/EPÄVARMA/)).toBeTruthy();
    expect(screen.getByText(/LIVE-KAUPANKÄYNTI ESTETTY/)).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith("/api/runtime", expect.objectContaining({
      method: "GET", credentials: "same-origin", cache: "no-store"
    }));
    expect(screen.queryByText(/Käynnistä kaupankäynti/)).toBeNull();
  });

  it("renders stale blocked supervisor conservatively", async () => {
    mockRuntime({ ...localRuntime, status: "BLOCKED", risk_lock: "NOT_TRIGGERED", events: [] });
    render(<App />);
    expect(await screen.findByText("BLOCKED")).toBeTruthy();
    expect(screen.getByText("STALE")).toBeTruthy();
    expect(screen.getByText("Estetty tai turvallista tilaa ei voida vahvistaa")).toBeTruthy();
  });

  it("rejects unknown live status even if HTTP returned 200", async () => {
    mockRuntime({ ...localRuntime, status: "LIVE", live_orders: "ENABLED" });
    render(<App />);
    expect(await screen.findByText("Luku epäonnistui")).toBeTruthy();
    expect(screen.getByText("Estetty tai turvallista tilaa ei voida vahvistaa")).toBeTruthy();
    expect(screen.queryByText("LIVE")).toBeNull();
  });

  it("rejects a failed localhost request without showing stale healthy data", async () => {
    mockRuntime(localRuntime, false);
    render(<App />);
    expect(await screen.findByText("Luku epäonnistui")).toBeTruthy();
    expect(screen.getAllByText("UNKNOWN").length).toBeGreaterThanOrEqual(2);
  });

  it("shows no-data as unverified rather than a live account", async () => {
    mockRuntime({ ...localRuntime, status: "NOT_CONFIGURED", risk_lock: "UNKNOWN",
      heartbeat: "UNKNOWN", uncertain_order_count: null, events: [] });
    render(<App />);
    expect(await screen.findByText("NOT_CONFIGURED")).toBeTruthy();
    expect(screen.getByText(/Ei paikallisia tapahtumamerkintöjä/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Estetty tai turvallista tilaa ei voida vahvistaa")).toBeTruthy());
  });
});
