# Revolut X — ensimmäisen oikean tilin GET-only-testin protokolla

**Versio:** draft v0.1 / 17.10.2026
**Hyväksyntä:** EI ANNETTU
**Toteutuksen tila:** EI KÄYNNISTETTY
**Tarkoittaa:** yksi tilinhaltijan itse valtuuttama henkilökohtainen
read-only-koe omalla Windows-laitteellaan. Ei rahansiirtoja,
markkinadatan keruuta 24/7, strategian käynnistystä eikä
toimeksiannon lähettämistä.

## 0. Oikeudet ja ennakkoehdot — PAKOLLINEN STOP/GATE

- Tilin omistaja täysi-ikäinen, käyttää omaa Revolut X -tiliään.
- Tilin haltijan erillinen ja rajattu suostumus dokumentoidaan:
  laite, tarkoitus, sallitut GET-reitit, enimmäispyynnöt,
  testin ajankohta sekä lupa keskeyttää itse heti.
- Sovellettavat Revolut X API-/käyttöehdot ja kaupalliseen
  ohjelmistoon liittyvät rajoitukset on tarkistettu.
- **Todellinen API-avaimen oikeustaso varmennettu nimenomaan
  Revolut X:ssa.** Jos palvelu ei tue erillistä read-only-avainta,
  se on kirjattava riskiksi ja hyväksyntä vaatii lisäarvion.
  GET-only-koodi ei tee kaupankäyntiin kykenevästä avaimesta
  palvelinpuolella read-only-avainta.
- PIN-koodit, sisäänkirjautumiset, 2FA ja yksityiset avaimet
  pysyvät käyttäjän hallussa; niitä EI lähetetä myyjälle tai CI:hin.
- Asiakkaan omalle PC:lle on asennettu **tarkistettu ja
  allekirjoitettu** sovellus. Tässä PR:ssä ei vielä ole
  allekirjoitettua Windows-asenninta -> oikean tilin testi
  on tällä hetkellä estetty.

**Jos yksikin yllä olevista on puutteellinen: TEST NOT STARTED.**

## 1. Ennen oikeita pyyntöjä — offline-testi

- Windows-versio, virustorjunta, palomuuri ja kellosynkronointi
  tarkistetaan asiakkaan omalla laitteella.
- Käytössä vain allekirjoitettu, lukittu versio SHA256-hashilla,
  ei kehittäjän satunnaista viimeisintä feature-haaraa.
- DPAPI käyttäjäkohtainen vault on luotu paikallisesti.
- Asiakas luo itse Ed25519-avainparin vaultiin; vain julkinen
  PEM siirretään Revolut X:n avainrekisteröintinäkymään.
- API-avaimen salasana tai yksityinen allekirjoitusavain ei näy
  ruudulla eikä päädy lähetettäväksi.
- Asiakas liittää API-avaimen paikallisella syötteellä;
  ohjelma vahvistaa paikallisen tallennuksen ilman
  API-verkko-oppimispyyntöä.
- Offline mock-testit ja koko Windows-CI ovat vihreät
  juuri tällä SHA-versiolla. Todellisen datan schema on
  verrattu tuoreeseen viralliseen dokumentaatioon.

## 2. Hyväksyttävät rajatut GET-pyynnöt

Ainoastaan seuraavat, kerran kunkin testaustapauksen
hyväksymisessä, kiinteällä `https://revx.revolut.com/api`-hostilla:

| Järjestys | GET | Tarkistus | Sallitut sivut |
| --- | --- | --- | --- |
| 1 | `/1.0/balances` | HTTP 200 ja suora saldoarray, Decimal-arvot, komponenttisumma | 1 |
| 2 | `/1.0/orders/active` | HTTP 200, koko paginoitu lista, ei duplikaatteja | enintään 10 |
| 3 | `/1.0/orders/historical` | HTTP 200, muistetaan rajallinen historia; ei virheellisiä johtopäätöksiä | enintään 10 |
| 4 | `/1.0/orders/{uuid}` | Valinnainen vain, jos oikea jo olemassa olevan tilauksen UUID tunnetaan | 1 |
| 5 | `/1.0/orders/fills/{uuid}` | Valinnainen vain saman tilauksen UUID:llä | 1 |

Testisovellukseen pitää koodata tätä pilotin ehdollista
pyyntökattoa vastaava toimintatila ennen suorittamista.
Nykyinen yleinen GET-lukija sallii jopa 100 sivua,
joten se **ei vielä täytä** tämän pilotin 10 sivun rajaa.

Kertaluonteisen testisession ehdotettu enimmäismäärä:
**25 onnistunutta HTTP GET -pyyntöä** ja enintään
**kaksi lisäyritystä** (vain erillisellä hyväksynnällä).
Nämä ovat suunnittelurajoja, eivät toteutettuja runtime-guard-rajoja.
Ei automaattista toistoa virheellisen tilin tilassa.

## 3. Hyväksyminen ja STOP-säännöt

**Hyväksytty testi** edellyttää:

- Virallisesti sopivat Ed25519-allekirjoituksen headerit,
  HTTP 200 ja oikea odotettu response-schema;
  GET-lukija palauttaa kelvollisen, kokonaan luetun snapshotin.
- Yksityistä avainta tai API-avainta ei näy raporteissa,
  lokeissa, Windows Event Logissa, prosessilistoissa,
  komentoriviparametreissa tai verkkoon menevissä
  ylimääräisissä palveluissa.
- Tilin haltija vertaa palautetut EUR/ETH-saldot ja avoimet
  toimeksiannot **omaan** Revolut X -näkymäänsä; merkittävät
  erot merkitään FAIL, ei automaattisesti tulkita hinnanmuutokseksi.
- Historiapyyntö ei ole todiste siitä, ettei puuttuvaa
  toimeksiantoa koskaan lähetetty. Täsmäytysvalmiutta
  ei saa väittää saavutetuksi vain tilannekuvan lukemalla.
- JSON-raportissa vain operatiiviset tulokset ja
  tunnisteettomat laskurit. Ei saldoja, API-avainta,
  yksityistä/public keytä kokonaisuudessaan, henkilötietoja
  tai kokonaisia tilaus-ID:itä.

**Keskeytä välittömästi (NO_GO)**, jos
401/403, väärä tilin tunniste, epäselvä API-key scope,
429/5xx, aikaleiman epäselvyys, sivutusvirhe,
tietokannan epävarmuus, ohjelman odottamaton prosessi,
sertifikaattivirhe tai tietoturvapoikkeama. Tilin haltija
voi myös keskeyttää ilman perustetta.

API-kirjautumisen epäonnistuminen ei oikeuta automaattiseen
avaimen kiertoon tai write-metodin kokeilemiseen.

## 4. Todiste- ja hyväksyntäraportin rakenne

```json
{
  "schema": "revx-real-get-only-pilot-v1",
  "git_commit_sha": "TO_BE_APPROVED",
  "operating_system": "WINDOWS",
  "installation_signature_verified": false,
  "account_owner_consent": "NOT_RECEIVED",
  "revx_api_permission_scope_checked": false,
  "authenticated_get_requests": 0,
  "http_methods": ["GET"],
  "balances_valid": "NOT_TESTED",
  "active_orders_valid": "NOT_TESTED",
  "history_valid": "NOT_TESTED",
  "owner_ui_comparison": "NOT_TESTED",
  "api_secrets_in_evidence": false,
  "mutations_sent": 0,
  "real_account_test_status": "NOT_STARTED",
  "live_trading_approval": "NO_GO"
}
```

Tämän raportin **NOT_STARTED**-tila on suunnitelma,
ei onnistunut oikean tilin testi.

## 5. Testin jälkeen

- Tilin haltija saa suosituksen API-avaimen poistosta
  tai uusimisesta testin jälkeen. Palvelinpuolen revokaatio
  varmistetaan käyttäjän Revolut X -sovelluksesta erikseen.
- Mahdollinen jatkokäyttö edellyttää erillistä hyväksyntää
  avaimen säilytykselle, seurantarytmille ja jatkotesteille.
- Tuotantorunnerin toimeksiantotoiminnot pysyvät pois päältä.
- GET-testin läpäisy EI vielä hyväksy live-kaupankäyntiä.

## 6. Lähteet ja erottelu

- Revolut X:n API:n avainparit, allekirjoitus, API-key-rekisteröinti
  ja saldo-/tilaus-endpointit:
  https://developer.revolut.com/docs/api/revolut-x-crypto-exchange
- Microsoft DPAPI CurrentUser ja user/machine-sidonnaisuus:
  https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata

Avoimet kohdat: kaupallisen lisenssin sopivuus API:n käyttöehtoihin,
read-only API-key scope Revolut X:ssa, asiakkaan koneen turvallisuus,
julkaisun allekirjoitus sekä rajatun GET-testin oma käyttöliittymä
ja toteutettu pyyntöbudjetti. Näitä ei ole täydennetty oletuksilla.
