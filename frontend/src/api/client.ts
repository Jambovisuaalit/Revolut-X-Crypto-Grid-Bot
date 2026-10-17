import { isLocalRuntime, type LocalRuntime } from "./contracts";
export async function fetchLocalRuntime(signal?: AbortSignal): Promise<LocalRuntime> {
  const response = await fetch("/api/runtime", { method: "GET", credentials: "same-origin", cache: "no-store", signal });
  if (!response.ok) throw new Error("LOCAL_RUNTIME_UNAVAILABLE");
  const payload: unknown = await response.json();
  if (!isLocalRuntime(payload)) throw new Error("LOCAL_RUNTIME_CONTRACT_INVALID");
  return payload;
}
