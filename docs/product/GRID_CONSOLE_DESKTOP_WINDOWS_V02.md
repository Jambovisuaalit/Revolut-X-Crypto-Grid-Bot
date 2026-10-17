# Grid Console Desktop — graafinen Windows-kehitysversio v0.2

**17.10.2026 — UNSIGNED DEVELOPMENT ONLY / REAL ACCOUNT NOT RUN / LIVE NO-GO**

## Käyttö

Tämä ei ole uusi kaupankäyntimoottori. Windows-sovellus käyttää
olemassa olevaa suomenkielistä Grid Console -käyttöliittymää
(`product_shell/assets/`) ja sen `product_shell.server`-palvelinta.
Kaupankäyntiytimen `GridBotSupervisor`- ja `GridExecutor`-logiikkaa
ei ole muutettu.

Käyttäjä avaa Windowsin Käynnistä-valikosta **Grid Console Desktop — DEV**.
Tavallinen Windows-ikkuna näyttää offline-tilan ja
**Avaa Grid Console** -painikkeen. Sovellus avaa käyttäjän
oletusselaimeen paikallisen suomenkielisen hallintapaneelin.
Oletusarvoisesti selain avautuu myös ensimmäisellä käynnistyksellä.
Tämä on tarkoituksella selaimen kautta näytettävä HTML-käyttöliittymä,
ei vielä upotettu WebView2-sovellus.

**Sulje sovellus** tai Windows-ikkunan sulkemispainike pysäyttää
`127.0.0.1`-palvelimen ja sulkee desktop-prosessin.
Pelkkä selaimen sulkeminen ei lopeta paikallista palvelinta.

### Vaarattoman tilan ehdot

- Live-kaupankäynti `DISABLED`, tilejä `NOT_CONNECTED`.
- Vain localhost `127.0.0.1`, satunnainen vapaa portti.
- `/api/status` ja kolme staattista käyttöliittymätiedostoa;
  POST/PUT/PATCH/DELETE palauttavat HTTP 405.
- HTML-UI ei voi käynnistää gridiä, aloittaa keräintä,
  tallentaa avaimia, muuttaa saldoja tai lähettää toimeksiantoja.
- Ilman keräintietokantaa näkymä ilmoittaa `NOT_CONFIGURED`.
- Oikea saldo ja P&L näytetään viivana `—`, ei 1 000 €:n
  synteettistä alkupääomaa oikeana tilisaldona.
- UI:n automaattinen avaus voidaan estää vain
  kehitysparametrilla `--no-auto-browser`.
- `--self-test --report PATH` testaa localhost-palvelimen,
  HTML/CSS/JS-resurssit, GET-lukutilan ja kirjoitusestot
  avaamatta GUI:ta tai selainta.

## Windows-asennus

Paketointi käyttää PyInstallerin `--windowed --onedir`-tilaa
ja olemassa olevia paikallisia UI-tiedostoja sekä
ETH/EUR-shadow-konfiguraatiota. Inno Setup rakentaa
käyttäjäkohtaisen asennuksen ilman admin-oikeuksia.
Käynnistä-valikon kuvake ei aloita kauppaa eikä asennin
käynnistä ohjelmaa automaattisesti.

CI:ssä `windows_distribution/build_grid_console_desktop.ps1`:

1. Luo `GridConsoleDesktop.exe`-ohjelman.
2. Käynnistää `--self-test`-tilan, lukee JSON-raportin.
3. Rakentaa `GridConsoleDesktop-UNSIGNED-DEV-Setup.exe`-asentimen.
4. Asentaa sovelluksen Windows-testikoneeseen
   ja suorittaa saman testin asennetulla .exe-tiedostolla.
5. Todentaa GUI-ikkunan avautumisen ja hallitun sulkeutumisen.
6. Varmistaa, että sekä .exe että asennin ovat **allekirjoittamattomia**
   ja että aiempi `Verify-WindowsRelease.ps1` hylkää molemmat.
7. Tallentaa kehitysversioiden SHA256-tiivisteet ja
   CI-raportit, mutta ei oikeita käyttäjäsaldoja tai tunnuksia.

Toteutus käyttää GitHubin Windows Actions -testikonetta; se ei
ole ostajan tietokoneella suoritettu asennustesti. Ennen
asiakasjakelua vaaditaan oikea allekirjoittaminen ja toisella
Windows-koneella tehty käyttökoe.

## Kehitystestaus ilman Windows-asenninta

```powershell
python -m pip install -r requirements.txt
python windows_distribution/grid_console_desktop.py --self-test --report grid-console-check.json
python windows_distribution/grid_console_desktop.py
```

Näistä jälkimmäinen avaa graafisen ohjainikkunan ja localhost-UI:n.
CI:n artefaktia ei saa jakaa asiakkaille allekirjoitettuina
tai kaupallisesti valmiina tuotteina.

## Edelleen ratkaistavat asiat ennen kaupallista tuotetta

1. Graafinen on-boardaus ja GET-only-avaimen turvallinen paikallinen
   kytkentä `WindowsUserVault`iin **vasta erillisellä luvalla**.
2. Yhtenäinen localhost-käyttöliittymä, josta näkyy pörssin
   todellinen saldo, avoimet toimeksiannot ja tapahtumat
   ristiriidoissa fail-closed. Nyt näkymä on edelleen offline.
3. ETH/EUR-shadow-strategian riskikattojen ristiriidan korjaus
   ja tarvittava pidempi todellinen forward-shadow.
4. Tapahtumakirjanpito, varmuuskopiointi/palautus,
   pääkäyttäjän riskin lukitus- ja käyttöönottoprosessi.
5. Todellinen allekirjoitusvarmenne, SignTool-varmennus ja
   oikean ostajan koneella tehty asennuskoe.
6. API:n käyttöoikeudet, Revolut X:n käyttöehdot,
   kuluttajan kirjalliset lisenssiehdot, tukivastuut ja
   finanssisääntelyn palvelurajaus.
7. UI:n automaattisen käytettävyyden, saavutettavuuden,
   käytönopastuksen ja virheiden käsittelyn testaus asiakkaalla.

**Julkaisupäätös:** `DEV_DEMO_ONLY`; `SELLABLE_PRODUCT=NO_GO`;
`LIVE_TRADING=DISABLED`. Oikeaan tiliin ei oteta yhteyttä.
