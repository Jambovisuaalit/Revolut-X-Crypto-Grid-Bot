import type { LocalRuntime } from "../api/contracts";
export type Tone = "danger" | "warning" | "neutral" | "ok";
export interface Presentation { title: string; description: string; action: string; tone: Tone; technical: string }
const values: Record<LocalRuntime["status"], Omit<Presentation,"technical">> = {
  NOT_CONFIGURED: { title:"Valvojaa ei määritetty",description:"Paikallista valvojan tietokantaa ei ole valittu.",action:"Valitse paikallinen SQLite-testitiedosto käynnistysikkunasta.",tone:"neutral" },
  NOT_STARTED: { title:"Valvojaa ei käynnistetty",description:"Paikallinen valvoja ei ole toiminnassa.",action:"Tarkista testitietokannan tila. Älä aktivoi live-kaupankäyntiä.",tone:"neutral" },
  STOPPED: { title:"Valvoja pysäytetty",description:"Paikallinen valvontaprosessi on pysähtynyt.",action:"Tarkista viimeisimmät paikalliset tapahtumat.",tone:"warning" },
  RECONCILING: { title:"Paikallinen selvitys kesken",description:"Tallennettuja tapahtumia tarkastetaan; pörssin nykytilaa ei ole varmennettu.",action:"Älä tulkitse tätä kaupankäyntivalmiudeksi.",tone:"warning" },
  OFFLINE_MONITORING: { title:"Offline-valvonta",description:"Vain paikallinen tilatieto; ei pörssivarmennusta.",action:"Seuraa tietojen ikää ja epävarmoja tapahtumia.",tone:"neutral" },
  BLOCKED: { title:"Valvonta estetty",description:"Valvojan turvallista toimintatilaa ei voida vahvistaa.",action:"Tarkista diagnostiikka ja paikallinen data.",tone:"danger" },
  RISK_LOCKED: { title:"Riskilukitus aktiivinen",description:"Paikallinen pysyvä turvalukitus on lauennut.",action:"Älä nollaa lukitusta. Selvitä syy offline-tilassa.",tone:"danger" }
};
export function statusPresentation(status: LocalRuntime["status"] | null): Presentation {
  if (!status) return {title:"Tila tuntematon",description:"Paikallista tilaa ei voitu vahvistaa.",action:"Tarkista yhteys ja yritä päivitystä.",tone:"warning",technical:"UNKNOWN"};
  return {...values[status],technical:status};
}
export function riskPresentation(lock: LocalRuntime["risk_lock"] | null): Presentation {
  if (lock === "TRIGGERED") return {title:"Riskilukitus aktiivinen",description:"Paikallinen riskiraja on laukaissut lukituksen.",action:"Älä käynnistä kaupankäyntiä tai nollaa lukitusta.",tone:"danger",technical:lock};
  if (lock === "NOT_TRIGGERED") return {title:"Lukitusta ei havaittu",description:"Paikallinen merkintä ei osoita laukaistua lukitusta. Tämä ei ole pörssivarmennus.",action:"Pidä kaupankäynti estettynä.",tone:"neutral",technical:lock};
  return {title:"Riskitila tuntematon",description:"Lukituksen tilaa ei pystytä vahvistamaan.",action:"Valitse tunnistettu testitietokanta tai tarkista diagnostiikka.",tone:"warning",technical:"UNKNOWN"};
}
export const tradingPresentation: Presentation = {
  title:"Kaupankäynti estetty",description:"Sovelluksella ei ole live-toimeksiantoja eikä Revolut X -tiliyhteyttä.",action:"Käytä vain paikallista offline-tarkastelua.",tone:"neutral",technical:"DISABLED"
};
export function heartbeatPresentation(value: LocalRuntime["heartbeat"] | null): Presentation {
  if (value === "FRESH") return {title:"Paikallinen merkintä tuore",description:"Paikallinen heartbeat on tuore; ei pörssivarmennus.",action:"Tarkista myös riskitila.",tone:"neutral",technical:value};
  if (value === "STALE") return {title:"Paikallinen tieto vanhentunut",description:"Valvojan heartbeat on vanhentunut.",action:"Älä käytä tilaa kaupankäyntipäätöksiin.",tone:"warning",technical:value};
  return {title:"Heartbeat tuntematon",description:"Tuoreutta ei voi todentaa.",action:"Tarkista paikalliset tietolähteet.",tone:"warning",technical:"UNKNOWN"};
}
