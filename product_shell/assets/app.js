"use strict";
const view = (id) => document.getElementById(id);
function fmtEUR(value) {
  if (value === undefined || value === null) return "—";
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("fi-FI", {
    minimumFractionDigits: 0, maximumFractionDigits: 2
  }).format(number) + " €" : "—";
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
    button.disabled = false;
  }
}
view("refresh").addEventListener("click", refresh);
refresh();
