# Grid Console — yhden asiakkaan kaupallistettava automaattinen kaupankäyntituote

**PRD v1.0 · 17.10.2026 · Tila: DEVELOPMENT / SELLABLE=NO_GO / LIVE=NO_GO**

Lähdekoodi: https://github.com/Jambovisuaalit/Revolut-X-Crypto-Grid-Bot

Tämä määrittely korvaa yhden asiakkaan **kaupallisen tavoitetilan** osalta
`docs/product/KAUPALLINEN_TUOTEMAARITTELY_FI.md`-luonnoksen
kuukausiveloitusehdotuksen: **ensimmäinen sopimus = kertalisenssi,
ei automaattisesti uusiutuvaa maksua**. Kehitystyö jatkuu nykyisessä repossa.

## 1. Tuotelupaus ja asiakas

Itsenäinen Windowsille asennettava, selkeä suomenkielinen sovellus.
Käyttäjä omistaa Revolut X -tilinsä ja käynnistää, pysäyttää ja konfiguroi
yhtä etukäteen varmennettua spot-grid-strategiaa. Sovellus kirjaa
kaikki päätökset, pörssin toteutukset, riskit, kulut ja epävarmat tilat.

**Ei taattua tuottoa, ei tiettyä €-tuottoa 1 000–2 500 € pääomalle,
ei väitettä 'high volume = kannattava'.** Asiakkaalle on sanottava,
että kauppamalli voi olla tappiollinen ja likviditeetti-/markkinariskit
voivat ylittää asetettuja ohjelmallisia tappiorajoja epäjatkuvissa markkinoissa.

Ensimmäinen tunnistettu kiinnostunut ostaja on ilmoittanut
omistavansa PC:n, kykenevänsä asentamaan tavallisia ohjelmia ja
ettei osaa koodata. Windows-version varmistus, toimeksiantovaltuudet,
ostohinnan hyväksyntä, maksukyky ja tilin omistus **ovat avoinna**.
`INTERESTED` ei ole varma tilaus eikä oikean tilin testin hyväksyntä.

## 2. Viisi liiketoimintakriittistä toimintoa

| Vastuu | Tavoite | Todennus ennen LIVE-tilaa |
| --- | --- | --- |
| Havainnointi | Markkina, saldot, active/historical/fills | autentikoitu read-only, datan ikä, täydellinen sivutus, ristiriitojen esto |
| Päätöksenteko | ETH/EUR post-only spot-grid | lukittu strategia-SHA, nettopalkkiot, ostojen/myyntien valinta, stressitulokset |
| Rajoittaminen | Pääoma, 5 % vapaan saldon tilauskatto, 3 % stop, yhteenlaskettu altistus | SQLite lukitus, riskikatot ennen jokaista toimeksiantoa, valvojan estotilat |
| Toteuttaminen | Idempotentti order POST/PUT/cancel | intent ennen lähetystä, tunnisteiden täsmäytys, ei automaattista toistokauppaa |
| Todentaminen | Exchange truth = local journal after restart | 429, timeout, 5XX, katkos, osatäyttö, crash, epävarma DELETE, varmuuskopio/restore |

Huom.: `cancel_all_orders` saattaa peruuttaa koko API-tilin / alatilin
toimeksiannot. Käytä botille erotettua alitiliä ja varmista identiteetti
ennen cancel-testin toteutusta. Ohjelmallinen 3 %:n stop ei
voi luvata enintään 3 %:n realisoitunutta tappiota.

## 3. V1 UI: ostaja ei saa tarvita komentoriviä

Valmis asennus → oma Grid Console -ikkuna, automaattinen mutta
suojattu ensimmäinen käynnistys. Vähintään seuraavat näkymät:

1. **Yleisnäkymä:** tila STOPPED/READ_ONLY/SHADOW/BLOCKED/RISK_LOCKED/
   LIVE (vain erillinen hyväksytty käyttöönotto), viimeisin
   onnistunut synkronointi, netti/API, havainnon ikä, riski.
2. **Tili:** alitilin tunniste (ei salaisuuksia), EUR/ETH available,
   reserved, total, avoimet tilaukset, fillit ja täsmäytyksen tulos.
3. **Strategia:** ETH/EUR, grid-tasot ja pääomakatto, fee/spread/
   slippage-varoitus, riskirajat. Parametrit eivät voi ylittää
   ydinmoottorin kovia turvarajoja.
4. **Kaupat:** paikallinen intent vs accepted venue order vs
   partial/full fills, asiakas- ja venue-ID:t, netto-P&L ja maksut.
5. **Turvallisuus:** selkeä PYSÄYTÄ UUSIEN TOIMEKSIANTOJEN LÄHETYS,
   varmistettava peruutuksen status, API-key-suoja, virheet,
   tapahtumaloki, avaimen paikallinen poisto/revokaatio-ohje.
6. **Raportit:** erottele realisoitunut/unrealisoitunut tuotto,
   kulut, alkupääoma, nykyinen equity, maxDD, aktiivinen altistus;
   älä näytä esimerkkituottoa oikeana.

Kaikki kriittiset toimet vaativat tilan tarkistuksen ja näkyvän
vahvistuksen; live ei aktivoidu päivityksen, uudelleenkäynnistyksen
tai tavallisen UI-kytkimen sivuvaikutuksena.

## 4. Kehityksen tarkistettu GitHub-pohja

**Yhdistetty offline-integraatio:** `integration/grid-console-one-product-v1`.

- Turvallisuus- ja GET-only-ketju PR #3, #8–#12.
- Shadow/market-data/asiakas-UI-ketju PR #4–#7.
- 42 puuttunutta tiedostoa siirretty identtisillä Git-blob-tunnisteilla.
- Yhteisten tiedostojen riskiturvallisuuden toteutus pysyi
  PR #12:n versiona. `main` pysyy muuttumattomana.
- Yhdistetty täydellinen Windows/Ubuntu testimatriisi ja
  palautumisraportit ovat hyväksyntäportti, eivät näyttö live-kaupasta.

Yhteinen koodipohja **ei tarkoita**, että toiminnot olisivat
vielä toisiinsa end-to-end kytkettyjä. Tuotteen nykyinen
Windows-asennin on vain synteettinen offline-käynnistin;
aiempi HTML/Grid Console -UI ei ole vielä sama asennettava
tuote tai oikeaa tiliä näyttävä sovellus.

## 5. P0: järjestelmän yhdistäminen toimivaksi kokonaisuudeksi

1. Lukitaan integraatiohaaran riippuvuudet, tehdään
   tiedostokohtainen review ja suoritetaan koko 2 OS:n CI.
2. Poistetaan vanhan rinnakkaisen SQLite-reservation
   stressitestin ajoitusriippuvuudesta johtuva epädeterminismi
   **heikentämättä** fail-closed-tilaa; testaa molemmat
   mahdolliset turvalliset lukituksen/rahoituksen estot perustellusti.
3. Yhtenäistetään `Grid Console` -käynnistin oikean
   `product_shell` UI:n kanssa; Windows-installerin
   offline-versio saa käynnistää UI:n, ei API:ta.
4. Tuodaan tilakoneen tila ja tapahtumahistoria UI:hin
   **read-only**, erillisestä paikallisesta testitietokannasta.
5. Lisätään versioitu SQLite-migraatio, varmuuskopio/WAL-checkpoint,
   restore/test, hätätilan pysyvyys ja yhden käyttäjän lukitus.
6. Kiinnitetään dependency-versiot, SBOM,
   lisenssien käyttö- ja jakeluoikeudet, koodiallekirjoituksen
   verifiointi sekä tiedostojen SHA-256-julkaisu.
7. Dokumentoidaan käyttöehdot, GDPR/rekisterinpitäjäroolit,
   asiakkaan oikeudet, ohjelmistovirheiden korjaus ja
   päivitystuen kesto.

## 6. P1: autentikoitu vain luku + strategian realismin todentaminen

1. Varmennetaan Revolut X:n API-key / alatilin oikeusraja
   ja tekninen API-sopimus nimenomaan palvelun dokumentaatiosta.
   GET-only-client ei vähennä avaimen palvelinpuolisia oikeuksia.
2. Vain erillisellä tilinomistajan hyväksynnällä,
   hänen omalla laitteellaan ja allekirjoitetulla
   julkaisulla suoritetaan rajattu autentikoitu GET-testi.
3. Todennetaan balances/active/history/fills vastaamaan
   Revolut X:n käyttäjänäkymää; ristiriidassa `BLOCKED`.
4. Pidetään REST GET -testin 25 yrityksen pysyvä katto,
   10 sivun listakatto ja 429 ilman retryä. Nykyinen
   `pilot_budget_store` kieltää vielä REAL_ACCOUNT-moodin.
5. Arvioidaan ETH/EUR-shadow v2: nykyiset 100 € tasot
   1 000 € pääomalle ovat ristiriidassa **enintään
   5 % vapaasta saldosta / toimeksianto** -ytimen kanssa.
6. Hyväksytään ensin 24 h todellisen keräimen datalaatu,
   sitten 30–90 kalenteripäivää todellista forward-shadow'ta,
   vertailuportfolio, maksut, spread, osatäytöt,
   simuloitu slippage ja maksimitappio. Kaikki tulokset
   raportoidaan myös tappiollisina; pidetään `NO_GO`
   ellei ennalta määriteltyjä ehtoja läpäistä.

## 7. P2: erotettu oikean tilin live-pilotti (EI AUTOMAATTINEN)

Ennen yhtäkään oikeaa POST/PUT/DELETE-kutsua tarvitaan:
- tilinomistajan erillinen tietoinen kirjallinen lupa ja
  turvallinen botille eristetty alitili;
- hyväksytty API-sopimus, käyttäjäkohtainen salaisuus,
  palvelinpuolisen oikeusrajan todentaminen;
- autentikoitu read-only API ja pysyvä paikallinen
  tilin täsmäytys todistetusti oikein;
- data- ja strategiariskin ennaltahyväksytyt rajat,
  riippumaton turvallisuuskatselmus ja juridinen palvelurajaus;
- tuloksellinen forward shadow sekä fail-closed-vikatestit;
- allekirjoitettu asennus ja pysäytyksen käyttötesti
  oikean käyttäjän Windows-koneella;
- erillinen live-pilottisuunnitelma: enimmäiskapitalisointi,
  ensimmäinen koetilaus, seuraajan paikallaolo, katkoskriteerit
  ja raportti. Ei jatkuvaa kaupankäyntiä ennen erillistä näyttöä.

## 8. Turvallisuus ja käyttötavat

- Kaikki avaimet vain ostajan paikallisessa
  `WindowsUserVault` DPAPI:ssa. Ei avaimia kehittäjälle,
  GitHubin CI:hin, Telegramiin tai käyttöliittymälokeihin.
- Revolut X -alatilin API-key voidaan sitoa tiettyyn
  alitiliin, mutta käyttöoikeuden
  **read-only-scopea ei tässä projektissa ole vahvistettu**.
- API-data vain sallittuun henkilökohtaiseen/sisäiseen
  käyttöön, ei edelleenjakelua pilvipalvelussa.
- Ei verkkokauppojen tai tunnusten yhdistämistä asiakastilien
  välillä; paikallinen sovellus ei käsittele nostotoimintoja.
- Käytettävä itsenäistä brändiä (esim. Grid Console)
  ja kerrottava yhteensopivuus faktana, ei Revolutin
  virallisena tuotteena.
- API:n sopimusehdot voivat muuttua, eikä tilausten
  toteutuminen tai peruutuksen kuittaus ole aina taattua.
- Suomen MiCA-toimilupa-arvio riippuu toteutuksesta:
  pelkkä lisensoitu ohjelmisto, asiakkaan puolesta
  operointi, neuvonta, toimeksiantojen välittäminen ja
  salkunhoito ovat eri toimintamalleja.
- Suomen kuluttajansuojan digitaalisen sisällön
  virhevastuu ja tarpeelliset päivitykset huomioidaan
  kirjallisessa lisenssissä. Ei 'ei mitään vastuuta'
  -lauseketta pakottavan lain kiertämiseksi.

## 9. Kaupallinen paketti — yksi ostaja

**Ensimmäinen tuote:** Grid Console — henkilökohtainen yhden
laitteen käyttöoikeus, **kertamaksu**, ei automaattista
kuukausitilausta. Ostaja omistaa Revolut X -tilin ja varat,
hallinnoi itse API-avaimia ja päättää käynnistyksestä.
Valmistaja ei toimi asiakkaan varainhoitajana
pelkän lisenssin perusteella; oikea toimintamalli
arvioidaan ennen myyntiä.

**Alustava kaupallinen hypoteesi:** 199 € kertamaksu
oikeasti toimitettavasta tuotteesta, ei
vahvistettu tarjous eikä asiakkaan hyväksymä hinta.
Sovittava, mitä vikatukea, korjauspäivityksiä ja
käyttöoikeuden kestoa hinta sisältää. Ei automaattista
29 €/kk huoltosopimusta. Myyntihinnasta ja
tukivelvollisuudesta sopiminen tapahtuu kirjallisesti.

**Älä peri maksua** nykyisestä kehitysbinaarista
automaattisena oikeaa rahaa käyttävänä trading-bottina.
Ensin voidaan tarjota maksuton offline-esikatselu
vain turvalliseksi hyväksytystä ja allekirjoitetusta
jakelusta. Ei tuottolupausta.

## 10. Julkaisu- ja hyväksymisportit

| Portti | Ehto | Nykytila |
| --- | --- | --- |
| G0 | Yhtenäinen nykyinen codebase, kaikki CI:t Win+Ubuntu | INTEGRATION_TEST_PENDING |
| G1 | Käyttöliittymän turvallinen allekirjoitettu asiakasasennus | BLOCKED_NO_CERT |
| G2 | Autentikoitu GET read-only ja oikea alitili vahvistettu | NOT_RUN |
| G3 | 24h data + varmuuskopio/restore, 99 % havaintopeitto | NOT_RUN |
| G4 | 30–90d forward-shadow riskit ja taloudellinen näyttö | NOT_RUN |
| G5 | Live-order epävarmat tilat ja pörssin todellinen täsmäytys | NOT_RUN |
| G6 | Juridinen tarkistus, lisenssi, tuen palvelukuvaus | OPEN |
| G7 | Asiakkaan itsenäinen käyttöönotto ilman koodausta | NOT_TESTED |
| G8 | Erillinen rajattu live-pilotti ja allekirjoitettu GO | NO_GO |

Kaikki portit ovat riippumattomia. CI PASS ei muuta
`LIVE=NO_GO` -päätöstä ilman oikean tilin näyttöjä
ja erillistä käyttäjän hyväksyntää.

## 11. Lähteet (päivitetty 17.10.2026)

- https://developer.revolut.com/docs/api/revolut-x-crypto-exchange
- https://www.revolut.com/fi-FI/legal/crypto-exchange-terms/
- https://www.revolut.com/fi-FI/legal/crypto-exchange-fees/
- https://www.finanssivalvonta.fi/finanssisektorin-toimijalle/paaomamarkkinat/toimiluvat-ja-rekisterointi/kryptovaratoimijat/
- https://www.kkv.fi/kuluttaja-asiat/digitaaliset-sisallot-ja-palvelut/
- https://kyberturvallisuuskeskus.fi/fi/ohjeet-ja-oppaat/ohjeet-ja-oppaat-organisaatioille-ja-yrityksille-uusi/ohjelmistoturvallisuus

