# Revolut X — autentikoitu GET-only tililukija (mock-sopimusversio)

Tila: **Kehitysversio, EI oikean tilin testiä, EI tuotantokäyttöä**.
Feature-haara perustuu `feat/supervisor-offline-lifecycle-v1`-haaraan.
Alkuperäistä kaupankäyntiasiakasta ei korvata eikä `main`-haaraa muuteta.

## Virallinen rajapintasopimus

Lähde: https://developer.revolut.com/docs/api/revolut-x-crypto-exchange

- URL-alku **https://revx.revolut.com/api**
- Ed25519-signing: `timestamp + GET + /api{path} + query`,
  ilman erottimia; viesti UTF-8-tavuina, signature base64.
- Headerit: `X-Revx-API-Key`, `X-Revx-Timestamp`,
  `X-Revx-Signature`.
- GET /1.0/balances (vastauksena **suora array**, ei `data`-objekti).
- GET /1.0/orders/active (data/metadata ja next_cursor).
- GET /1.0/orders/historical (oletushistoria **ei** ole koko
  elinkaaren täydellinen historia).
- GET /1.0/orders/{venue_order_id} (UUID).
- GET /1.0/orders/fills/{venue_order_id} (UUID).

Rajoja ei ole laajennettu esimerkiksi tilaus-POSTiin, PUTiin,
DELETEen, siirtoihin tai nostopyyntöihin.

## Rakenteellinen eristys

`account_readonly.py` on erillinen eikä peri `RevolutXClient`-luokkaa.
Ainoa HTTP-kutsu on `self._transport.get(...)` kiinteään HTTPS-hostin
osoitteeseen. Ei `session.request(method,...)`, ei write-metodeja,
ei geneeristä polku-parametria julkisessa API:ssa. Ainoastaan
kanoninen UUID hyväksytään yksittäisen toimeksiannon polkuun.

Uudelleenohjaus estetään. Ympäristön automaattiset proxy-asetukset
estetään omassa sessiossa. 429 saa enintään kaksi rajoitettua
uudelleenyritystä ja vain dokumentoidun `Retry-After`-viiveen;
401, 403, 409 ja 5xx estävät. Pyyntö- ja vastauskoko on rajattu,
sivujen lukumäärä enintään 100 ja rivien enintään 10 000.

Tämän moduulin Python-API ottaa Ed25519-avainolion ja API-avaimen
**vain muistissa**. Tämä työpaketti EI toteuta käyttäjän avaimen
luontia, turvallista paikallista säilytystä tai avaimen uudistamista.

## Datan eheys

- Saldojen `available`, `reserved`, `staked` (jos löytyy)
  ja `total` ovat Decimal-lukuja ja ne täsmäytetään.
- Duplicate currency, negatiivinen tai NaN saldo -> virhe.
- Sivutus luetaan loppuun ennen kuin yksikään kokoelma palautetaan.
  Toistuva cursor tai order ID estää hyväksynnän.
- Order UUID, symbol, side, status ja käytettävissä olevat
  määräkentät validoidaan. Epäselvät rakenteet estävät.
- Tilannekuva on peräkkäisistä GET-kutsuista muodostettu,
  **ei atominen pörssitilannekuva**.
- Historiallisten toimeksiantojen puuttuminen ei todista,
  ettei epävarma toimeksianto koskaan toteutunut.
- `AccountSnapshot.reconciled_for_trading=False`, kunnes
  erillinen oikean tilin täsmäytys ja turvallisuushyväksyntä on
  toteutettu. Saldotietojen lukeminen ei anna kaupankäyntilupaa.

## Offline CI -hyväksyntä

Windows+Ubuntu: 15 mocked GET -sopimustestiä, koko riskitestisarja,
Ed25519-allekirjoituksen verifiointi julkisella avaimella,
aktiivi/historia-sivutus, saldomallin validointi, 429/401/403/5xx,
HTTP redirect -esto, virheelliset rakenteet, epäselvä historia,
SQLite-riski- ja prosessipalautumistestit, AST-perustainen
metodien eristystarkistus.

Tuloksissa tulee aina näkyä `authenticated_real_account_calls=NOT_RUN`,
`live_trading_approval=NO_GO` sekä `supervisor_integrated_with_account=False`.
CI ei sisällä API-avaimia eikä tee oikeita autentikoituja GET-kutsuja.

## Jäljellä olevat hyväksyntäportit

1. Paikallinen avainvarasto (Windows DPAPI tai muu sopiva,
   käyttäjäkohtainen salattu tallennus); ei koodiin,
   lokiin tai GitHubin salaisuuksiin.
2. Asiakkaan oma API-avaimen rekisteröinti ja oikeuksien minimointi.
   **GET-only ohjelmistomoduuli ei yksin tarkoita, että API-avaimen
   oikeusraja olisi vain luku.**
3. Aito oikean tilin GET-testi vaatii erillisen hyväksynnän sekä
   sovellettavien käyttöehtojen ja oikeuksien varmistamisen.
4. Saldojen, avointen tilausten ja toteutusten todellinen
   tilitäsmäytys, aikaleima- ja tunnistesynkronointi,
   incomplete / stale / ambiguous -tilojen käsittely.
5. Valvojaan integrointi erillisellä hyväksynnällä,
   lisäämättä live-mutaatioreittiä tähän moduuliin.
6. Oikeudellinen tarkistus ennen kaupallista live-pilottia.

Tässä PR:ssä ei aloiteta 24h keruuta, shadow'ta tai live-kaupankäyntiä.
