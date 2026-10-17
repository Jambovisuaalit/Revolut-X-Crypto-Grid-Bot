# Grid Console 0.1 — paikallisen esikatselun käyttöohje

**Tuotteen tila:** kehitysesikatselu, ei myytävä kaupankäyntituote.

## Sovelluksen avaaminen kehityskoneella

Python 3.11 ja projektin muut riippuvuudet:

```powershell
python -m pip install -r requirements.txt
python scripts/start_grid_console.py
```

Sovellus avaa selaimessa `http://127.0.0.1:<satunnainen_portti>`.
Verkkopalvelin kuuntelee **vain omalla tietokoneella**. Sulkemalla
komentoikkunan tai painamalla Ctrl+C sovellus pysähtyy.

Tämä ei edellytä API-avainta, Revolut X -kirjautumista tai omaa sijoituspääomaa.

## Valmiiksi koottu Windows-ESIKATSELU

GitHub Actions > **Grid Console - Customer Preview Safety**
> viimeisin onnistunut ajo > *Artifacts* >
`GridConsolePreview-Windows-UNSIGNED-NO-LIVE`.

Vain kehittäjäkäyttöön. `GridConsolePreview.exe` on **allekirjoittamaton**.
Älä pyydä käyttäjää ohittamaan Windows SmartScreen -varoituksia
kaupallisessa toimituksessa. Ennen käyttäjille toimittamista
tarvitaan allekirjoitettu asennin, päivitysmekanismi, julkaisutarkistus
ja riippumaton laitteen asennustesti.

## Mitä näkymä kertoo?

- Esimerkkistrategia ETH/EUR ja virtuaalinen aloituspääoma 1 000 €.
- Virtuaalisen riskin enimmäisaltistus 400 € ja hard-stop 30 €.
- Oikea tilisaldo, kaupat ja toteutunut tuotto: **ei saatavilla**.
- API-yhteys: **ei yhdistetty**.
- Kaupankäynti: **estetty**.
- Mahdollisen paikallisen keräintietokannan tilan voi näyttää
  valinnaisesti:
  `python scripts/start_grid_console.py --db /oma/polku/collector.sqlite`.

Näkymä ei käynnistä keräintä, analyysia, pörssikaupankäyntiä
tai muuta taustaprosessia. Käyttöliittymä tekee GET-pyyntöjä ainoastaan
omaan `127.0.0.1`-osoitteeseen.

## Tietoturva

- Älä koskaan syötä API-avainta tai yksityistä Ed25519-avainta
  esikatseluun, selaimen konsoliin tai viestillä myyjälle.
- Älä avaa paikallisen portin palomuuria ulkoverkkoon.
- Älä julkaise `collector.sqlite`-, WAL- tai SHM-tiedostoja
  verkkopalveluun. Exchange-markkinadatan jälleenjakelua on
  Revolut X:n ehdoissa rajoitettu.
- Tämän ohjelmiston kaupankäyntitoiminto ei ole käytössä.
- Maksulliseen levitykseen tarvitaan vähintään `docs/product/
  KAUPALLINEN_TUOTEMAARITTELY_FI.md` -porttien hyväksyntä.

## Nykyinen julkaisukielto

`scripts/check_grid_console_release_gate.py` tuottaa JSON-raportin,
jossa `sellable_product = NO_GO` ja `live_trading = NOT_ENABLED`.
`preview_approval = DEV_DEMO_ONLY` ei oikeuta myyntiin
automaattisena, oikeaa rahaa käyttävänä kaupankäyntituotteena.
