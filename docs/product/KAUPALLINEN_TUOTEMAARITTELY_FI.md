# Grid Console — kaupallisen tuotteen määrittely v0.1

Päiväys: 17.10.2026. Tila: SUUNNITTELU + TURVALLINEN KÄYTTÖLIITTYMÄESIKATSELU.
Tuotenimi on väliaikainen työotsikko; tavaramerkkiä tai domainia ei ole tarkistettu.
Itsenäinen ohjelmistotuote, jonka mahdollinen Revolut X -yhteensopivuus
perustuu dokumentoituun rajapintaan. Ei Revolutin virallinen tuote.

## 1. Ostajasignaalit (vain käyttäjän toimittamasta keskustelusta)

| Puheenvuoron sisältö | Tuotevaatimus / johtopäätös |
| --- | --- |
| "Joo on mul pc" | Ostajalla on tietokone; **Windows-käyttöjärjestelmää ei vielä vahvistettu**. |
| "kyllä mä ny osaan asentaa ohjelmat" | Tavallinen asennuspaketti, ei komentorivikäyttöä. |
| "Mut koodaaminen tms ei kyl lähe" | Ohjattu käyttöönotto ja ymmärrettävät virheilmoitukset. |
| "Pitää kerää paaluu atm uudestaa" | Kiinnostus ei vielä ole maksusitoumus; rahoitus viivästyttää pilottia. |
| "Joo kyl kyl" | Myönteinen keskustelusignaali, ei suostumus tuotto-, riski- tai sopimusehtoihin. |

Myyjän lausuma 1 000–2 500 €:n kassasta tai että botti "tekee rahaa"
ei ole testattu tuottoennuste. Tätä ei saa käyttää markkinointilupauksena.

## 2. Asiakaslupaus

> Yksi omaan laitteeseen asennettava käyttöliittymä, jolla näet botin tilan,
> riskirajat ja kaupankäyntihistorian. Päätät itse, milloin järjestelmä voi
> käydä kauppaa. Ei tuottotakuuta.

Myydään ohjelmiston **käyttöoikeutta ja ylläpitoa**, ei sijoitussignaaleja,
taattua tuottoa, talletuspalvelua tai asiakkaan varojen hoitoa.

## 3. Tuotteen oikea tila lokakuussa 2026

| Osa | Näyttö | Myyntitila |
| --- | --- | --- |
| Kaupankäyntiytimen prototyyppi | main ja PR #3 | Ei tuotantohyväksytty |
| ETH/EUR Grid -shadow | Draft PR #4 | Ohjelmistotestit, ei taloudellista näyttöä |
| Aito julkinen markkinadata | Draft PR #5 | Rajattu GET-keruu läpäissyt |
| Pysyvä julkisen datan keräin | Draft PR #6 | Testattu CI:ssä, ei asennettu 24/7-palvelimeen |
| Paikallinen asiakasnäkymä | Tämä feature-haara | Vain esikatselu, ei tiliavaimia |
| Aito kirjautunut tiliyhteys | Ei käyttäjän tilin todellista testiä | BLOKATTU |
| 30–90 pv forward-shadow | Ei tehty | BLOKATTU |
| Windowsin allekirjoitettu asennus | Ei tehty | BLOKATTU |
| Finanssioikeudellinen palvelurajaus | Ei erillistä asiantuntijan arviota | BLOKATTU |

## 4. Ensimmäisen myytävän version kohderyhmä

Yksi täysi-ikäinen itsenäinen käyttäjä, joka omistaa Revolut X -tilinsä,
hallinnoi itse talletuksia ja API-avaimia eikä tarvitse ohjelmointiosaamista.

Ensimmäinen toimitus: yhden tietokoneen paikallisesti toimiva sovellus.
Käyttöjärjestelmä vahvistetaan ostajalta ennen asennuksen toteuttamista.
Ei keskitettyä asiakasvarojen säilytystä, ei käyttäjätilien välisiä siirtoja,
ei moniasiakas-palvelinkaupankäyntiä.

## 5. Käyttäjäpolku — tavoitetila (ei valmis vielä)

1. Asiakas asentaa **allekirjoitetun**, tarkistetun sovelluksen.
2. Sovellus näyttää asennus- ja kellosynkronointitarkistukset.
3. Asiakas luo Ed25519-avaimen *omalla laitteellaan* ja rekisteröi julkisen
   avaimen Revolut X:n omassa käyttöliittymässä. Yksityinen avain ei koskaan
   lähde laitteelta tai sovelluksen valvotusta salaisuustallennuksesta.
4. Asiakas valitsee omassa Revolut X:ssään erillisen kaupankäynnin alatilin,
   jos käytettävissä; käyttöavain sidotaan siihen. Testataan oikealla
   käytännön tilillä vain erillisellä valtuutuksella.
5. Sovellus tekee **vain luku** -tilitestin ja näyttää EUR/ETH-saldot,
   avoimet toimeksiannot ja toteutushistorian. Virheellinen/epäselvä
   täsmäytys -> STOP / ei uusia tilauksia.
6. Asiakas valitsee ennalta testatun Grid-strategian parametrit,
   näyttäen tappioriskin, kulumallin ja position katon.
7. Eri moduulin itsenäinen shadow-validointi kulkee riittävän pitkään.
8. Live-kaupankäynti voidaan harkita vasta kaikkien hyväksyntäporttien
   jälkeen ja erillisellä nimenomaisella vahvistuksella.

Nykyinen preview toteuttaa vain yleisnäkymän ja riskirajojen esittelyn;
se ei toteuta kohtia 2–8 loppuun.

## 6. Kaupankäynnin turvarunko — hyväksyttävä ennen liveä

- Alkusaldon, vapaan saldon, sidotun saldon ja arvonmuutoksen täsmäytys
  API:n *todellisiin* arvoihin, ei vain julkiseen order bookiin.
- Maksimipääoma, maksimiosto ja kokonaispositio käyttäjän hyväksyminä;
  ydinmoottorin 5 % **vapaasta saldosta** per order on tiukempi kuin
  aiemman shadow-mallin 100 € / taso, kun vapaa saldo on 1 000 €.
- Epävarma POST/PUT/DELETE: pysyvä intent + tunniste + fail-closed
  täsmäytys. EI automaattista kaksoislähetystä.
- Osatäyttöjen, peruutusten, kulujen, reopen- ja korvausketjujen
  todellinen pörssivastaavuus aina ennen uusia mutaatioita.
- 3 %:n kill switch pysäyttää uudet ostot ja lukitsee toiminnon myös
  uudelleenkäynnistyksen jälkeen. Avoimien toimeksiantojen peruutus on
  erillinen todennettava yritys; verkkokatkon aikana ei voi taata, että
  pörssi vastaanotti peruutuksen.
- Ei omatoimista kaupankäynnin uudelleenkäynnistystä riskilukituksen jälkeen.
- Erilliset riskihyväksynnät jokaiselle tilille ja versionhallittu strategia.
- Ei "high volume" -lupausta: volyymi ja tuotto hyväksytään vain
  toteutuneeseen likviditeettiin, kuluihin ja pitkäkestoiseen dataan nojaten.

## 7. Mikä erottaa hyvän ohjelmiston huonosta?

Näkyvät yhteys- ja riskitilat: EI YHTEYTTÄ / SHADOW / VALMIS
/ PYSÄYTETTY / EPÄVARMA TILA / RISKILUKITUS. Täsmälliset
toimintakehotukset tavallisella suomen kielellä. Kauppataulukossa
erikseen tehty aikomus, pörssin hyväksyntä, osatäytöt, loppuhinta
ja nettotulos kulujen jälkeen. Käyttäjä ei joudu tulkitsemaan lokeja.

## 8. Tuotepaketit — HINNOITTELUHYPOTEESI, EI JULKAISTU HINNASTO

- Beta-käyttöoikeus: 99–199 € kertamaksu, ei liveä eikä tuottolupauksia.
  Tarkoitettu tuotekokemuksen ja asennuksen palautteeseen **vasta kun
  turvallinen beta voidaan oikeasti luovuttaa**.
- Ylläpitopalvelu: 9–29 €/kk vapaaehtoinen päivitys-, valvonta-
  ja tukipaketti. Etäkäyttöä asiakkaan varoihin tai avaimiin ei sisälly.
- Live-kaupankäynnin kaupallista pakettia **ei saa tarjota**
  ennen ohjelmisto-, oikeudellista ja tuotantovalmiuden hyväksyntää.

Hinnoittelu testataan yhden aidon maksusitoumuksen avulla, ei
keskustelun myönteisten vastausten perusteella. Asiakasta ei painosteta
hankkimaan sijoituskassaa, eikä ohjelmistomaksua piiloteta tuottolukuihin.

## 9. Sopimus-, compliance- ja lisenssiportti

Revolut X:n EU-ehdot: https://www.revolut.com/fi-FI/legal/crypto-exchange-terms/
- API-avaimet ja erityisesti yksityinen avain pidettävä käyttäjän hallussa.
- API-markkinadata vain henkilökohtaiseen tai sisäiseen käyttöön;
  edelleenjakelu kielletty. Ei keskitettyä uudelleenmyytävää hinta-feed-palvelua.
- Revolut X -tavaramerkkiä ei saa käyttää markkinointibrändinä ilman lupaa.
  Käytetään erillistä tuotemerkkiä, jonka oikeudet tarkistetaan.
- API:n käyttö ei takaa toimeksiannon toteutumista tai saatavuutta.

MiCA Suomessa:
https://www.finanssivalvonta.fi/finanssisektorin-toimijalle/paaomamarkkinat/toimiluvat-ja-rekisterointi/kryptovaratoimijat/
ESMAn toiminnallinen tulkinta:
https://www.esma.europa.eu/publications-data/questions-answers/2653

Ohjelmistolisenssin myynti, ohjelmiston operointi asiakkaan puolesta ja
asiakkaan toimeksiantojen välittäminen ovat eri toimintamalleja.
Niiden oikeudellinen luokittelu riippuu todellisesta toiminnasta,
ei sopimuksen otsikosta. Ennen asiakkaan oikeilla varoilla tehtävää
automaattikauppaa hankitaan tarvittava kirjallinen oikeudellinen arvio
ja tarkistetaan palvelu- ja API-sopimusehdot; tässä ei väitetä
ohjelmistomallin olevan varmasti toimiluvasta vapaa.

## 10. Päätöksenteon ja julkaisun portit

**A: DEMO** — UX-asennus ja tietoturvan read-only-testit vihreät,
kaikki saldot selkeästi merkitty virtuaalisiksi. Tämä vaihe.

**B: READ-ONLY BETA** — varmennettu oikean tilin kirjautuminen
asiakkaan laitteella, yksityisen avaimen salattu tallennus,
täsmäytys, salaisuuden poisto, onnistunut 24h keruu ja toimiva
varmuuskopiointi.

**C: TÄYSI SHADOW** — 30–90 päivää oikeaa forward-dataa,
kulujen ja slippagen vaikutus, virhetilanteet, vertailusalkku,
ei hyväksymättömiä riskitapahtumia.

**D: RAJATTU LIVE** — riippumaton turvallisuusauditointi,
oikeudellinen selvitys ja erillinen opt-in. Ei automaattista
siirtymää C:stä D:hen.

**E: MAKSETTU TUOTE** — oikea käyttöympäristö, allekirjoitettu
Windows-asennus, päivitys-/palautusrutiinit, käyttöehdot, tietosuoja,
tuki ja dokumentoitu asiakasperehdytys.

## 11. Julkistamiskielto ja väittämien tarkistus

Ei markkinointia: "tuottaa varmasti", "passiivista tuloa", "vähäriskinen
automaattinen tulokone", "täysin tuotantovalmis", "Revolut hyväksynyt"
tai "asentuu kaikille tietokoneille". Varmennetut väittämät
koskevat tällä hetkellä vain dokumentoitua prototyyppiä, CI-testejä,
rajattua GET-markkinadataa ja paikallista käyttöliittymäesikatselua.
