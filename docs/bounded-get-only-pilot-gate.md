# Rajatun GET-only-tilipilotin koodissa pakotetut rajat

**17.10.2026 — offline-työpaketti, EI lupa oikean tilin testiin**

Jatkaa PR #10:tä. Työpaketti lisää vain olemassa olevaan
`account_readonly.py`-lukijaan vapaaehtoiset tiukat luku- ja
429-uusintarajat ja erillisen `account_pilot_gate.py`-valvojan.
Alkuperäistä kaupankäyntimoottoria, `GridBotSupervisor`-luokkaa,
DPAPI:n avainsuojausta tai päähaaraa ei ole muutettu.

## Tekninen rajaus

| Suoja | Konkreettinen toteutus |
| --- | --- |
| HTTP-metodi | Vain `get()`, ei yleistä request/write-metodia |
| Host | HTTPS `revx.revolut.com`, ei uudelleenohjauksia |
| Endpointit | Vain balances, active, historical, detail UUID, fills UUID |
| GET-pyyntöyritykset | Enintään 25 **yhteensä**, sisältää myös epäonnistuneet |
| Paginoitu aktiivisten lista | Enintään 10 sivua; puuttuva päätöskursori aiheuttaa FAIL |
| Paginoitu historiallista lista | Enintään 10 sivua; puuttuva päätöskursori aiheuttaa FAIL |
| HTTP 429 | Ei automaattisia uusintayrityksiä; välitön FAIL |
| GET transport fault | Yhden pilotin estotila, ei toista yritystä |
| Schema-vika | FAIL, ei osittaista snapshotia |
| Koko session toisto | Vain yksi `run_once()`; käytettyä istuntoa ei avata uudelleen |
| Ulostulo | Vain kokonaispyyntömäärä, tila ja NO_GO; ei saldoja/avaimia/ID:tä |

Toteutus käyttää kerroksia:

1. `ReadLimits(max_pages_per_list=10,max_rate_retries=0)` ohjaa
   aiempaa Ed25519 GET-lukijaa; ilman sitä vanha yleiskäytön
   datalukija toimii entiseen tapaan.
2. `GuardedGetTransport(max_attempts=25)` estää
   rajanylityksen jo ennen HTTP-pyyntöä ja kasvattaa
   laskuria ennen jokaista yritystä. Rinnakkaiset GET:t
   sarjoitetaan lukon sisällä, jolloin limiittiä
   ei ohiteta kahdella säikeellä.
3. `BoundedPilotSession.run_once()` muodostaa kolme
   tarvittaessa paginoituvaa lukutapahtumaa: balances,
   active orders ja historical orders. Yhdenkin vaiheen
   epäonnistuessa istunto pysyy estettynä ja raportti ei
   koskaan väitä pörssitäsmäytystä hyväksytyksi.

**Huomautus:** Session `AccountSnapshot` sisältää tilin tietoja
ainoastaan prosessin muistissa. Pilotin `report()` ei palauta
saldoja tai toimeksiantotunnisteita. Tulevan tuotantokäyttäjän
lokitus, tukityökalut ja käyttöliittymä on arvioitava erikseen.

## Prosessin keskeytys ja pyyntöbudjetin pysyvyys

Uusi `pilot_budget_store.py` tallentaa kokeellisen GET-budjetin
SQLiteen WAL + `synchronous=FULL` -asetuksilla. Jokainen GET
**varataan ja transaktio committoidaan ennen verkko-/mock-kutsua**.
Kulutettua yritystä ei hyvitetä, vaikka yhteys katkeaa, HTTP 429
palautuu tai prosessi kuolee pyynnön välissä. Pyyntömäärää ei voi
nostaa rinnakkaisilla säikeillä. Kerran olemassa olevaan
tietokantaan ei voi tässä toteutuksessa avata uutta
pilotti-istuntoa, vaikka aiempi prosessi olisi kaatunut.
Uusi hyväksyntä tarvitaan, eikä hiljaista reset-toimintoa ole.

**Tärkeä rajaus:** tämä durable-ledger hyväksyy **vain**
`OFFLINE_MOCK_ONLY`-käytön ja hylkää
`mode="REAL_ACCOUNT"`-arvon. Offline-testit todentavat
käyttäytymisen Windowsissa/Ubuntussa. Oikean tilin
kertaluonteinen käynnistin, asennuksen allekirjoituksen
tarkistus, tilinhaltijan suostumuksen tallennus ja
koodin avaaminen hyväksytylle reaaliyhteydelle ovat
erillisiä myöhempiä töitä. Tällä estetään vahingossa
live-käyttöön päätyvä testi.

## Offline-testit ja tämän työn hyväksyminen

- Windows/Ubuntu `tests/test_bounded_account_pilot.py`:
  25 GETin katto, 10 sivun katto, 429/401/403/409/5XX,
  virheellinen JSON/skeema, timeout, paluurajauksen läpäisy,
  URL allowlist, kaksoisajo, rinnakkaiskutsujen laskurit,
  allekirjoitettu GET vain olemassa olevasta lukijasta.
- Kaikki aikaisemmat 115+ olemassa olevaa testiä sekä
  prosessipalautuminen ja tappiorajan kirjanpito.
- JSON-verdict `real_account_gets=NOT_RUN`,
  `real_account_test_approval=NOT_GRANTED`,
  `live_trading_approval=NO_GO`.
- Mitään oikeaa Revolut X -API-avainta ei ladattu,
  eikä koodi tarjoa oikean tilin pilottia suorittavaa CLI-komentoa.

## Hyväksymättä olevat edellytykset

1. Täsmennetään, tukeeko **Revolut X** palvelinpuolen
   vain luku -API-avaimen rajausta. Revolut Business API
   Consolen permission-toiminnosta ei saa tehdä tätä päätelmää.
2. Asiakas hyväksyy tarkasti rajatun kokeen laitteellaan.
3. Allekirjoitettu asennuspaketti ja allekirjoituksen
   luotettavuuden tarkistus. Microsoftin vaihtoehdot:
   Microsoft Store MSIX tai yrityksille soveltuva
   Artifact Signing / CA:n OV-varmenne.
   Mitään varmennepalvelua ei ostettu tässä työssä.
4. Toteutetaan ja hyväksytään erikseen **oikean tilin testi-CLI**,
   jossa on omat hyväksyntä- ja testauksenrajaustarkistukset.
   Nyt koodi sisältää vain ohjelmallisen pilotin mallin.
5. Varmistetaan oikean tilin schema, mahdolliset saldojen
   komponenttien tulkintaerot ja asiakkaan Revolut X
   -näkymään tehtävä manuaalinen vertailu.
6. Oikean tilin GET-koe EI hyväksy automaattikaupankäyntiä.

## Lähteet

- https://developer.revolut.com/docs/api/revolut-x-crypto-exchange
- https://learn.microsoft.com/windows/apps/package-and-deploy/code-signing-options
- https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation
