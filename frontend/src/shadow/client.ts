import {isShadowView,type ShadowView} from "./contracts";
export async function fetchShadowView(signal?:AbortSignal):Promise<ShadowView>{
 const response=await fetch("/api/shadow",{method:"GET",credentials:"same-origin",cache:"no-store",signal});
 if(!response.ok)throw new Error("SHADOW_ENDPOINT_UNAVAILABLE");
 const payload:unknown=await response.json();
 if(!isShadowView(payload))throw new Error("SHADOW_CONTRACT_INVALID");
 return payload;
}
