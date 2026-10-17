# PR #3 — turvallisuusauditointi (2026-10-17)

Tila: **BLOCKED — ei mergeä eikä livekaupankäyntiä**.
Kohde: `fix/revx-contract-idempotent-recovery`; auditointiperusta commit `7ff753aad50ba2425d7687a2f892b903d95c9b98`.

## Varmennetut CI-tulokset
- Trading Core Safety Tests: run 39001437742, Windows ja Ubuntu success.
- Process Recovery Integration: run 39001437691, Windows ja Ubuntu success.
- Drawdown JSON molemmissa: baseline_equity=10000, pre_threshold_equity=9701, breach_equity=9690, drawdown_percent=3.10, kill_switch_persisted=true, restart_blocked=true, post_count=0, cancel_all_count=1, status=PASS.
- Nämä ovat offline/mock-testituloksia, eivät autentikoituja Revolut X -tuotantotodisteita.

## Havainnot

### P0 — pörssin peruutuksen jälkitilan varmennus puuttuu
`bot_main.py:check_drawdown` tallentaa kill switchin ja kutsuu `client.cancel_all_orders()`, mutta ei lue peruutuksen jälkeisiä aktiivisia toimeksiantoja eikä varmista niiden loppuun käsittelyä. `revolut_x_client.py:cancel_all_orders` palauttaa DELETE-vastauksen sellaisenaan. Nykyinen drawdown-integraatiotesti todentaa vain mockin kutsulaskurin.
**Hyväksymisportti:** kill switch pysyy pysyvänä myös DELETE-timeoutissa, 429/5xx-virheissä ja epäselvässä tuloksessa. Erillinen read-only-täsmäytys hakee kaikki sivutetut aktiiviset toimeksiannot; jäljelle jäävät toimeksiannot tai epävarma API-vastaus tuottavat `CANCELLATION_UNVERIFIED` eikä koskaan turvalliseksi tulkittua PASSia. Älä automaattisesti lähetä uutta epävarmaa DELETEä.

### P1 — markkinadatan tuoreus ei ole sidottu live-riskilaskentaan
`bot_main.py:mark_to_market` ottaa vastaan `balances` ja `quote_prices` ilman lähteen aikaleimaa. `shadow_runner.py:evaluate` validoi snapshotin aikaleiman, mutta on erillinen paperikaupan polku.
**Hyväksymisportti:** riskilaskennan live-syöte vaatii lähde- ja vastaanottoajan, aikavyöhykkeen, rajatun iän ja monotonisuuden; vanha/puuttuva/epäselvä data estää uusien toimeksiantojen dispatchin.

### P1 — ulkoisen HTTP-kutsun ja paikallisen kill switchin kilpailutilanne
`grid_executor.py:_assert_dispatch_allowed` tarkistaa pysäytyksen SQLite-transaktiossa ennen HTTP-kutsua. Koodi itse toteaa, ettei myöhempi ulkoinen HTTP-mutaatio ole atominen kill switchin kanssa.
**Hyväksymisportti:** rinnakkaisuustesti, jossa tappioraja aktivoituu tarkistuksen jälkeen mutta ennen HTTP-dispatchia. Dokumentoi jäävä riski ja hyväksyttävä käsittely; pelkkä vihreä yksikkötesti ei poista tätä rajaa.

### P1 — autentikoitu API-sopimuksen todentaminen puuttuu
`revolut_x_client.py` toteuttaa POST/PUT/GET/DELETE-rajapinnat ja epäselvän mutation fail-closed-käsittelyn. Testit ovat fake/mock-pohjaisia. Autentikoitua, luvallista API-sopimusnäyttöä ei ole tässä auditoinnissa varmennettu.
**Hyväksymisportti:** dokumentaation ja käyttöoikeuksien tarkistus; luvallinen read-only-validointi ennen mitään tuotantomutaatioita.

### P2 — raporttien todistusketju
GitHub Actions -artefaktit ovat saatavilla, ja niiden sisällöt tulostetaan myös lokeihin. Lisää raporttien skeeman koneellinen validointi ja commit SHA, jotta testitulokset voidaan sitoa yksiselitteisesti auditoituun commit-versioon.

## Seuraava korjausjärjestys
1. Lisää peruutuksen jälkeisen aktiivisten toimeksiantojen täsmäytyksen testit ja eksplisiittinen UNVERIFIED-tila.
2. Varmista, että peruutuksen epävarmuus säilyy SQLiteen ja estää kaupankäynnin uudelleenkäynnistyksessä.
3. Lisää vanhentuneen markkinadatan estot varsinaiseen riskipolkuun.
4. Lisää dispatch-kilpailutilanteen deterministinen integraatiotesti.
5. Aja koko Windows/Ubuntu CI, varmista JSON:t ja PR:n viimeinen SHA.
6. Päätä PR:n yhdistämisestä vasta P0/P1-porttien jälkeen.

**Ei live-toimeksiantoja, ei mergeä.**
