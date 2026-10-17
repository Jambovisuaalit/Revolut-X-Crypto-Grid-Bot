# Ajettava GridBotSupervisor v1 — vain rajattu offline-testi

**Ei live-hyväksyntää.** Tässä työpaketissa ei käynnistetä jatkuvaa
keruuta eikä yhdistetä asiakkaan Revolut X -tiliä.

## Muutokset

- Olemassa oleva `GridBotSupervisor`, `GridExecutor`, SQLite ja
  PR #3:n pysyvä tappiorajan lukitus on säilytetty.
- `supervisor_runtime.py` toteuttaa valvojan elinkaaren:
  STOPPED → RECONCILING → OFFLINE_MONITORING → STOPPED,
  epävarmuudessa BLOCKED ja 3 % tappiorajassa RISK_LOCKED.
- SQLiteen tallennetaan yksinomistajuus (15 sekunnin lease),
  heartbeat, viimeisen synteettisen havainnon aikaleima,
  virtuaalinen kokonaispääoma ja eston syy.
- Käynnistyksen yhteydessä tehdään alkuperäisen valvojan
  `reconcile_open_orders()` ennen yhtäkään riskihavaintoa.
- `bot_main.py` tarjoaa **vain nimenomaisen ja rajatun**
  synteettisen komentoriviajon. Oletus-Docker-käynnistys
  antaa ohjeen, ei aloita prosessia.

## Esimerkkikomento — paikallinen rajattu offline-fixture

```sh
python bot_main.py --offline-fixture --db ./offline-artifacts/offline-supervisor.sqlite --ticks 3 --interval-ms 100 --report ./offline-artifacts/report.json
```

Tämä on paikallisen virtuaalisen EUR-saldon testi. Sovelluksella
ei ole API-avainta, aitoa tilitietoa tai markkinahintaa.
`--db` osoittaa vain erillistä testitietokantaa; olemassa oleva
tietokanta ilman offline-provenienssimerkintää estetään.
`bot_state.db` ja `collector.sqlite` on nimillä estetty.

## Turvaportit

1. `GridExecutor.live_trading=False` on pakollinen;
   myös `LIVE_TRADING=true`-ympäristö estetään.
2. Lähteen tunnisteen on oltava `SYNTHETIC_OFFLINE_ONLY`.
3. Aikaleimojen lähde- ja vastaanottojärjestys sekä enintään
   3000 ms ikä tarkistetaan; vanhentuminen ja kellon paluu estävät.
4. Kaikki saldot ja noteeraukset ovat äärellisiä Decimal-lukuja.
5. 3 % tappioraja kutsuu nykyistä
   `GridBotSupervisor.check_drawdown()`-metodia, tallentaa
   pysyvän riskilukituksen ja estää uudelleenkäynnistyksen.
6. Epäonnistunut alkutäsmäytys tuottaa pysyvän BLOCKED-tilan.
7. Toinen omistaja ei voi käynnistyä aktiivisen leasen aikana.
   Kaatumisen jälkeen lease umpeutuu; estettyä riskitilaa ei
   kuitata hiljaisesti.
8. Ohjelma ei koskaan lähetä POST/PUT/DELETE-pörssipyyntöjä:
   offline-clientin vastaavat metodit ovat virheeseen pysähtyviä.
9. `--ticks` on rajattu arvoihin 1–1000. Ei daemon-tilaa,
   ei automaattista strategiakauppaa.

## Jäljellä olevat rajoitukset

Tämä on **valvojan offline-ajon perusta**. Se ei vielä ole oikean
tilin turvallinen reaaliaikainen valvoja. Saldot, markkinahinnat,
tilikytkentä, tapahtumavirran tuoreus ja pörssin order book ovat
vielä erillään. Yhteensovitus tehdään vasta autentikoidun
GET-only-tililukijan hyväksymisen jälkeen. `BLOCKED`-tilan
poistaminen edellyttää suunniteltua erillistä
palautumismenettelyä, eikä siihen lisätä automaattista resetointia.

## Hyväksyminen

Windows- ja Ubuntu-CI: koko alkuperäinen testisarja, erilliset
valvojan riskitestit, rajattu kolmen havainnon offline-ajo,
raportin `PASS` ja `STOPPED`, nolla verkkopyyntöä, lisäksi
PR #3:n prosessipalautumisen ja tappiorajan raportit.
Testit eivät todista live- tai asiakastilin tuotantovalmiutta.
