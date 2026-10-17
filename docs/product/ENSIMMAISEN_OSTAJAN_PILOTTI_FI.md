# Ostajakeskustelun perusteella tehtävä ensimmäinen pilotti

**Lähde:** Käyttäjän toimittama Putki–Automaatti-keskustelu.
**Myyntitila:** alustava kiinnostus, ei varausta, ei maksua, ei sovittua päivää.
**Tietoaukot:** käyttöjärjestelmä, Revolut X -tilin saatavuus, ikä, API-oikeus,
budjetti, suostumus ja riskinymmärrys.

## Ostajasta tiedetään vain tämä

- Hänellä on PC ja hän kertoo osaavansa asentaa tavallisia ohjelmia.
- Hän ei osaa koodata.
- Hän on kiinnostunut, mutta aikoo mahdollisesti kerätä käyttöpääomaa
  muutaman viikon ajan. Aikomus ei ole varma.
- Hän ei kertonut tilin saldoa eikä hyväksynyt konkreettista hintaa.
- Hänelle on jo väitetty, että 1 000–2 500 €:n pääoma voisi saada botin
  tuottamaan järkevästi. Väittämälle ei ole tässä aineistossa näyttöä.
  Tämä ennakko-odotus on korjattava ennen myyntiä.

## Asiakaskoe, vaiheistus ja rajat

**Askel 1 — alle tunnin esikatselu omalla laitteella:**
Windows/Linux/Mac? Tarkista käyttöjärjestelmä. Näytä
Grid Console -prototyyppi ilman API-tunnuksia ja ilman kauppoja.
Ostaja palauttaa arvion asennuksen helppoudesta, sanastosta,
tietoturvan ymmärrettävyydestä ja siitä, miten käyttäjä lopettaa ohjelman.

**Askel 2 — lyhyt käyttöönottohaastattelu:** kysy
mihin aikaan laite on päällä, saako se olla päällä yön yli,
onko Revolut X jo käytössä, tarvitseeko oman alatilin ja
haluaako hän tilata vain käyttöoikeuden vai myös tukea.
Älä pyydä tilitunnuksia tai rahojen siirtämistä.

**Askel 3 — aito read-only asennustesti erillisellä päätöksellä:**
oma API-avain luodaan asiakaslaitteella; vain valtuutettu,
testattu read-only-tili voi näyttää saldot, toimeksiannot ja
toteutukset. Tämän vaiheen toteutus on nykytilassa puuttuva,
eikä nykyinen preview väitä tarjoavansa sitä.

**Askel 4 — shadow-tuloksien ymmärrystesti:** asiakkaan pitää
pystyä omin sanoin selittämään, mikä on
virtuaalinen saldo, nettotulos, avain, riskilukitus ja
mitä tehdään epävarmassa API-tilassa.

**Askel 5 — myyntipäätös vasta näyttöjen jälkeen:**
kerää ostohalukkuus konkreettiseen, kirjalliseen beta-hintaan
ja tukitasoon. Älä peri maksua sellaisesta automaattisesta
kaupankäynnistä, jota ei vielä ole turvallisesti toimitettavissa.

## Pilotin onnistumiskriteerit

- Asiakas saa esikatselun auki 10 minuutissa ilman koodausta
  (tavoite, ei vielä todennettu).
- Asiakas tunnistaa shadow-tilan ja erottaa oikean saldon esimerkkisaldosta.
- Asiakkaan salaisuuksia ei kerätä viesteihin, pilveen tai kehittäjälle.
- Asiakas kykenee pysäyttämään sovelluksen ilman teknistä tukea.
- Ei oikeita toimeksiantoja, ei todellisia varoja eikä
  implisiittistä live-käyttölupaa.
- Yksi kirjoitettu palaute ja yksi todellinen,
  ei sitova ostohalukkuusarvio.

## Asiakkaan yleisimmät kysymykset, joihin pitää valmistautua

**"Paljon tällä tekee rahaa?"** Ei ole luotettavaa tulosennustetta.
Kulut, spread, markkinatrendi ja mahdolliset tappiot vaikuttavat,
eikä aiempi muutaman havainnon synteettinen demo ole todiste tuotosta.

**"Pitääkö mun osata koodata?"** Lopullisen tuotteen ei pidä
vaatia koodaamista. Esikatselu toimii nyt paikallisena selaimessa
näytettävänä ohjelmana; allekirjoitettu kuluttaja-asennin puuttuu.

**"Pitääkö kone olla aina auki?"** Vain jos varsinainen keräin
ja/tai myöhempi kaupankäyntimoottori ajetaan hänen koneellaan
jatkuvasti; koneen lepotila estää seurantaa. 24/7-palvelin on
eri käyttöönotto, kustannus- ja turvallisuuspäätös.

**"Voinko antaa sulle API-avaimen?"** Ei. Asiakkaan yksityistä
avainta ei saa lähettää viestillä tai antaa kehittäjälle.
Paikallinen käyttöönotto on suunniteltava tätä varten.

**"Saako botin yhdistettyä Revolut X:ään?"** API tukee
avaimella allekirjoitettuja tilipyyntöjä, mutta kyseinen
kaupallinen käyttöliittymä ei ole vielä todistettavasti
yhdistetty asiakkaan oikeaan tiliin. Tästä ei saa puhua
toimitusvalmiina ominaisuutena.

## CRM-ehdotus

Pipeline: Interested → Preview reviewed → Read-only pilot
→ Evidence reviewed → Compliant paid launch.
Tämä henkilö on vaiheessa **Interested**, muut vaiheet avoinna.
Älä kirjaa kaupaksi, forecastiksi tai maksaneeksi ilman näyttöä.
