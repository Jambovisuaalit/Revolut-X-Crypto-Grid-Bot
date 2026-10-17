# Grid Console Desktop v0.3 — paikallisen valvojan tila ja tapahtumahistoria

**17.10.2026 · Draft-kehitysversio · Ei live-testausta · Ei asiakasjulkaisua**

## Muutos olemassa olevaan tuotteeseen

Toteutus jatkaa graafisen asennettavan Grid Console Desktop v0.2:n
kehityshaaraa. Kaupankäyntiydintä (`GridBotSupervisor`, `GridExecutor`)
ja Revolut X:n API-asiakasta **ei ole muutettu**.

- `product_shell/local_runtime_readonly.py`: tarkastaa paikallisen SQLite-
  tietokannan `mode=ro` ja `PRAGMA query_only=ON` -asetuksilla.
- `GET /api/runtime`: kertoo valvojan viimeisimmän **tallennetun**
  tilan, riskilukituksen, heartbeat-havainnon ja peruutusmerkinnän.
  Reitti ei palauta yksityisiä avaimia, API-avaimia, käyttäjän saldoja,
  hintoja, määrätietoja, tilaustunnisteita eikä tietokantapolkua.
- `order_lineage` ja `fills_wire`: enintään 40 viimeisintä
  paikallista tapahtumaa, käyttöliittymässä suomalaiset kuvaustekstit
  ja aikaleimat. **Ei** niiden autentikoitua pörssivarmennusta.
- `bot_metadata`: riskin pysyvä `kill_switch_triggered`, valvojan
  tila ja syy sekä heartbeat-/lease-aika. Jos aktiiviseksi
  tallennetun valvojan lease/heartbeat on vanhentunut, näkymä näyttää
  tilan `BLOCKED`, ei `käynnissä`.
- Windowsin alkuperäiseen Tkinter-ikkunaan lisättiin paikallisen
  tietokannan valitsin. Valittu SQLite avataan vain lukemista varten
  uudessa localhost-katselupalvelimessa; aiempi palvelin suljetaan.
  Asetuksen valinta ei käynnistä valvojaa, keräintä tai strategiaa.
- `/api/status` ja sen aikaisempi turvallinen käyttö säilyy.

## Mitä käyttäjä näkee

**Valvoja:** `STOPPED`, `RECONCILING`,
`OFFLINE_MONITORING`, `BLOCKED`, `RISK_LOCKED`,
`NOT_STARTED` tai `NOT_CONFIGURED`. Nämä ovat paikallisia
merkintöjä, eivät väite käynnissä olevasta kaupankäyntiyhteydestä.

**Riskilukitus:** `TRIGGERED`, `NOT_TRIGGERED` tai
`UNKNOWN`. Puuttuva tieto ei koskaan näy vihreänä hyväksyntänä.

**Heartbeat:** tuore, vanhentunut tai tuntematon;
`OFFLINE_MONITORING`-tila muuttuu näytössä estetyksi,
jos heartbeat ja lease eivät ole tuoreita.
Vanhentumista arvioidaan 15 sekunnin rajan ja paikallisen UTC-kellon avulla.

**Peruutuksen tila:** vain alkuperäisen ydinlogiikan
`VERIFIED_NO_ACTIVE_ORDERS` näytetään paikallisena
varmennusmerkintänä, mutta sekään **ei** ole juuri sillä hetkellä
tehty pörssin order book -täsmäytys.

**Tapahtumahistoria:** paikalliset tilaukset ja
fill-kirjaukset. Tilauksen yhteydessä näkyy **nykyinen paikallinen tila**
ja alkuperäinen luontiaika, **ei** tilamuutoksen tapahtuma-aika.
Fillissä näkyy kirjausaika. Epävarmat tilaukset korostetaan ja koko
näkymän valvojan tilaksi asetetaan `BLOCKED`.

**Tärkeä rajaus:** nykyisessä ytimessä ei ole erillistä
append-only `supervisor_state_events`-historiataulua.
Tämä versio näyttää valvojan **viimeisimmän tilan**
ja toimeksiantojen/toteutusten **paikalliset merkinnät**,
ei kaikkien valvojan tilasiirtymien historiallista ketjua.

## Turvallisuus

Jos tiedostoa ei ole valittu, tulos `NOT_CONFIGURED`.
Vioittunut, väärärakenteinen tai epäselvä tietokanta =>
`BLOCKED`. Vastausten JSON sisältää vain ohjelman tuntemia
tilanimikkeitä ja staattisesti mapattuja, turvallisia
suomenkielisiä kuvauksia. SQLite-virheitä ei näytetä
käyttäjälle raakatekstinä.

Tietokannasta ei koskaan päätellä, että tilin oikea saldo tai
avoimet toimeksiannot olisi varmennettu. `reconciled_for_trading=false`,
`account_connection=NOT_CONNECTED`, `live_orders=DISABLED`.
HTTP POST/PUT/DELETE/PATCH säilyvät estettyinä.

## Testiportti

Windows + Ubuntu:

- 14 tilanne-, riski-, heartbeat-, tietokannan korruptio-,
  identiteettien peitto- ja HTTP-lukutilatestiä.
- Koko aiemman kaupankäynnin, GET-only-avainten,
  Windows-vaultin, shadow-strategian ja GUI:n offline-testisarja.
- Alkuperäisten prosessipalautumis- ja 3 % tappiorajatestien
  tarkat JSON-laskurit.
- Windows-asennus, paikallisten HTML/CSS/JS-assetien
  lataus ja allekirjoittamattoman julkaisun hylkääminen
  varmistetaan myös aiemmalla Windows-paketointityönkululla.

## Seuraava työkokonaisuus

1. Graafinen `onboarding` (vaatimusten tarkistus,
   paikallisen DPAPI-vaultin turvallinen käyttöönotto,
   erikseen hyväksyttävä testiyhteys).
2. Graafinen vain luku -asetusten tarkastus ja
   validoitu offline-shadow-strategian konfiguraatio.
3. Versioitu, erikseen auditoitu append-only
   valvojan tilasiirtymähistoria; nyt ei muuteta trading-corea.
4. Varsinainen oikean tilin lukutesti, 24 h datalaatu ja
   pitkä forward-shadow vasta erillisillä hyväksynnöillä.
5. Tuotantoon sopiva allekirjoitettu asennin, oikean
   asiakkaan laitetestit ja kaupalliset käyttöehdot.

**Ei toimintoa avainten lisäämiseen tai automaattisen
kaupankäynnin käynnistykseen tämän PR:n kautta.**
