"use strict";
const view = (id) => document.getElementById(id);
function fmtEUR(value) {
  if (value === undefined || value === null) return "—";
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("fi-FI", {
    minimumFractionDigits: 0, maximumFractionDigits: 2
  }).format(number) + " €" : "—";
}
function renderRuntime(info) {
  const knownStates = {
    NOT_CONFIGURED: "Ei paikallista tietokantaa",
    NOT_STARTED: "Ei käynnistetty",
    STOPPED: "Pysäytetty",
    RECONCILING: "Täsmäytys kesken (vain tallennettu tieto)",
    OFFLINE_MONITORING: "Offline-valvonta (tallennettu tieto)",
    BLOCKED: "ESTETTY — vaatii tarkistuksen",
    RISK_LOCKED: "RISKILUKITUS — ei saa käynnistää"
  };
  const state = knownStates[info.status] || "ESTETTY — tuntematon tila";
  view("runtime-state").textContent = state;
  view("runtime-state").className =
    ["BLOCKED", "RISK_LOCKED"].includes(info.status) ? "runtime-danger" : "";
  const locks = {TRIGGERED: "AKTIIVINEN — pysyvä lukitus",
                 NOT_TRIGGERED: "Ei laukaistu (paikallinen tieto)",
                 UNKNOWN: "Ei varmennettu"};
  view("runtime-risk").textContent = locks[info.risk_lock] || "Ei varmennettu";
  view("runtime-risk").className =
    info.risk_lock === "TRIGGERED" ? "runtime-danger" : "";
  const age = info.heartbeat_age_ms;
  view("runtime-heartbeat").textContent =
    info.heartbeat === "STALE" ? "Vanhentunut" :
    info.heartbeat === "FRESH" && typeof age === "number" ?
      "Tallennettu " + Math.max(0, Math.round(age / 1000)) + " s sitten" :
      "Ei varmennettua havaintoa";
  view("runtime-cancellation").textContent =
    info.cancellation === "VERIFIED_NO_ACTIVE_ORDERS" ?
      "Paikallinen varmennusmerkintä" : "Ei varmennettu";
  view("runtime-uncertain").textContent =
    Number.isSafeInteger(info.uncertain_order_count) ?
      String(info.uncertain_order_count) : "—";
  const safeLabels = {
    LEASE_EXPIRED_OR_HEARTBEAT_STALE: "Valvojan lukitus tai heartbeat vanhentui.",
    UNCERTAIN_LOCAL_ORDERS: "Paikallinen kirjanpito sisältää epävarmoja toimeksiantoja.",
    DATABASE_UNAVAILABLE: "Tietokantatiedostoa ei voitu avata.",
    UNRECOGNIZED_DATABASE_SCHEMA: "Tietokannan rakenne ei vastaa odotettua.",
    LOCAL_DATABASE_READ_FAILED: "Tietokannan luku epäonnistui.",
    INVALID_CLOCK: "Kellotiedon tarkistus epäonnistui.",
    UNRECOGNIZED_REASON: "Tuntematon eston syy — tarkista tila."
  };
  const message = safeLabels[info.reason] || (
    info.reason === "NONE" ? "Ei ilmoitettua syytä. Tämä ei ole pörssin täsmäytys." :
    typeof info.reason_label === "string" ? info.reason_label :
    "Paikallinen tila vaatii tarkistuksen."
  );
  view("runtime-reason").textContent = message;
  const list = view("runtime-event-list");
  list.replaceChildren();
  const events = Array.isArray(info.events) ? info.events.slice(0, 40) : [];
  if (!events.length) {
    const item = document.createElement("li");
    item.textContent = "Ei paikallisia toimeksianto- tai toteutusmerkintöjä.";
    list.appendChild(item);
  }
  for (const event of events) {
    const item = document.createElement("li");
    const stamp = document.createElement("time");
    const date = typeof event.time_utc === "string" ? new Date(event.time_utc) : null;
    stamp.textContent = date && Number.isFinite(date.getTime()) ?
      new Intl.DateTimeFormat("fi-FI", {dateStyle:"short",timeStyle:"short"}).format(date) + " · " :
      "Aikaleima epäselvä · ";
    const label = document.createElement("span");
    const context = event.timestamp_kind === "ORDER_CREATED_AT" ?
      "Tilauksen luontiaika · " : "Kirjausaika · ";
    label.textContent = context + String(event.label || "Tunnistamaton kirjaus");
    item.append(stamp, label);
    if (event.uncertain) item.className = "runtime-danger";
    list.appendChild(item);
  }
  view("runtime-events-note").textContent =
    "Näytetään enintään 40 uusinta paikallista kirjausta. Ei reaaliaikaista pörssivarmennusta.";
}
async function refreshRuntime() {
  try {
    const response = await fetch("/api/runtime", {cache:"no-store",credentials:"same-origin"});
    if (!response.ok) throw new Error("runtime unavailable");
    const result = await response.json();
    renderRuntime(result);
  } catch (_) {
    renderRuntime({status:"BLOCKED",risk_lock:"UNKNOWN",heartbeat:"UNKNOWN",
                   cancellation:"NOT_VERIFIED",reason:"LOCAL_DATABASE_READ_FAILED",
                   events:[],uncertain_order_count:null});
  }
}

async function refresh() {
  const button = view("refresh");
  button.disabled = true;
  try {
    const response = await fetch("/api/status", {cache: "no-store", credentials: "same-origin"});
    if (!response.ok) throw new Error("tilaa ei saatu");
    const state = await response.json();
    view("capital").textContent = fmtEUR(state.strategy.virtual_start_eur);
    view("loss").textContent = fmtEUR(state.strategy.hard_loss_limit_eur);
    view("exposure").textContent = fmtEUR(state.strategy.max_virtual_exposure_eur);
    const collector = state.collector;
    const badge = view("collector-status");
    badge.textContent = collector.status === "DATA_READY_FOR_REVIEW" ? "Laatutarkistus OK" :
      collector.status === "NOT_CONFIGURED" ? "Ei kytketty" : "Ei valmis";
    badge.className = "state " + (collector.status === "DATA_READY_FOR_REVIEW" ? "good" : "warn");
    const detail = collector.status === "NOT_CONFIGURED" ?
      "Ei paikallista keräintietokantaa. Ei uusia markkinapyyntöjä." :
      "Hyväksytyt näytteet: " + (collector.accepted_count ?? 0) + " / " +
      (collector.sample_count ?? 0) + ". " +
      ((collector.alerts && collector.alerts.length) ?
        "Tarkistus: " + collector.alerts[0] : "Ei aktiivisia varoituksia.");
    view("collector-detail").textContent = detail;
    view("last-update").textContent = "Näkymä päivitetty " +
      new Intl.DateTimeFormat("fi-FI", {hour:"2-digit", minute:"2-digit", second:"2-digit"}).format(new Date());
  } catch (_e) {
    view("collector-status").textContent = "Virhe";
    view("collector-status").className = "state warn";
    view("collector-detail").textContent = "Paikallisen tietotilan haku epäonnistui.";
    view("last-update").textContent = "Yhteys paikalliseen käyttöliittymään epäonnistui";
  } finally {
    await refreshRuntime();
    button.disabled = false;
  }
}
view("refresh").addEventListener("click", refresh);
refresh();
