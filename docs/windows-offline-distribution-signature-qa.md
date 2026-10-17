# Windows-jakelu ja Authenticode-allekirjoituksen tarkistus

**17.10.2026 — kehitystason toteutus, ei asiakasjulkaisu**

Tämä haara perustuu PR #11:n rajattuun GET-only-kehitykseen.
Ei main-mergeä, ei varmennehankintaa, ei asiakkaan tilin lukua,
ei jatkuvaa keruuta tai live-toimeksiantoja.

## Toteutettu paketointi

- `windows_distribution/offline_preview.py` käyttää **olemassa olevan**
  `bot_main.offline_main()`-valvojan tarkasti kolmen synteettisen
  havaintokerran offline-testiä, erillisessä väliaikaisessa SQLite-
  tietokannassa. Ei real-account-CLIä, ei API-avaimen lukemista,
  ei kaupankäynnin käynnistystä.
- PyInstaller rakentaa `GridConsoleOfflinePreview.exe`-ohjelman
  yhden kansion paketiksi (onedir).
- Inno Setup rakentaa erillisen **allekirjoittamattoman**
  `GridConsoleOfflinePreview-UNSIGNED-DEV-Setup.exe`-asentimen.
  Asennus tapahtuu nykyisen Windows-käyttäjän sovelluskansioon,
  ilman järjestelmänvalvojan oikeuksia. Käynnistystä ei ajasteta.
- CI testaa asenninta tilapäisellä Windows Actions -koneella,
  käynnistää asennetun ohjelman `--self-test`-tilassa ja varmistaa:
  `DEV_DEMO_PASS`, `live_trading=DISABLED`,
  `orders_sent=0`, `real_account_test=NOT_RUN`.
- Lisäksi luodaan siirrettävä ZIP ja SHA-256-tiivisteillä
  varustettu kehitysmanifesti. Manifestin `NO_GO` ei ole
  allekirjoitusvarmenne eikä osoita ohjelman jakeluluotettavuutta.

### Rajaus

Tämä on **offline-esikatselu**, ei täysimittainen asiakkaan
Grid Console eikä käyttäjän Revolut X -tiliin kytkettävä binääri.
Jakeluversio ei sisällä oikean tilin käyttöliittymää.
Kehitysartefakti on nimenomaisesti UNSIGNED-DEV-NOT-FOR-CUSTOMERS.

Älä ohita Windows SmartScreenin varoituksia tämän
kehityspaketin asentamiseksi asiakkaan laitteelle.

## Allekirjoituksen varmentaminen tulevaa julkaisua varten

`windows_distribution/Verify-WindowsRelease.ps1` on
**pelkästään verifiointi**, ei allekirjoituksen luonti.

Se vaatii erikseen hyväksytyn:
1. Tiedoston SHA-256-tiivisteen (julkaisun **allekirjoituksen jälkeen**).
2. Varsinaisen julkaisijan sertifikaatin SHA-1-thumbprintin
   riippumattomasta hyväksytystä lähteestä.

Lisäksi se edellyttää seuraavia koneellisia ehtoja:

- Windows `Get-AuthenticodeSignature.Status=Valid`;
- upotettu `Authenticode`-allekirjoitus, ei pelkkä catalog-signature;
- allekirjoittajan thumbprint vastaa täsmälleen hyväksyttyä;
- koodiallekirjoituksen EKU `1.3.6.1.5.5.7.3.3`;
- aikaleimauksen todentamiseen käytettävä sertifikaatti olemassa;
- `signtool.exe verify /pa /all /v` onnistuu;
- raportissa `status=PASS` vasta kaikkien ehtojen jälkeen.

Esimerkkikomento **vasta oikealla allekirjoitetulla tiedostolla**:

```powershell
./windows_distribution/Verify-WindowsRelease.ps1 \
  -FilePath 'release/GridConsoleSetup.exe' \
  -ExpectedSha256 '<SHA256-allekirjoituksen-jalkeen>' \
  -ExpectedSignerThumbprint '<hyvaksytty-40-merkkinen-thumbprint>' \
  -ReportPath 'release/verification.json'
```

Sama tarkistus vaaditaan **sekä** asiakasasentimelle
että asentimen mukana tulevalle binäärille. Riittävän
luotettavan asiakasjulkaisun todistaminen edellyttää myös
asennetun tiedoston tiivisteen ja allekirjoituksen tarkistusta.

**Huomio:** thumbprint ei ole sertifikaatin yksityinen avain.
Hyväksytty thumbprint on vahvistettava riippumattomasti, eikä
sitä pidä lukea vain ladatusta epäluotettavasta paketista.
`Get-AuthenticodeSignature` kertoo Windowsin tällä koneella
arvioiman luottamuksen; toisen Windows-laitteen varmenneympäristö
tai SmartScreen-maine voi erota.

## CI:n odotettu tulos ilman varmennetta

- Windowsissa PyInstaller-EXE ja Inno Setup -asennin rakentuvat.
- Molempien Authenticode on `NotSigned`.
- Kehitysmanifesti `status=PASS_UNSIGNED_DEV_ONLY` mutta
  `windows_customer_release=NO_GO`.
- Tiukka allekirjoitusverifikaatio ajetaan kummallekin tiedostolle
  tahallisesti kelvollisella tiedostohashilla ja **testiksi
  annetulla** thumbprintilla; sen PITÄÄ epäonnistua.
  Jos yksikin allekirjoittamaton tiedosto hyväksyttäisiin,
  koko Windows CI epäonnistuu.
- Ubuntu suorittaa vain koodin regressiotestit ja rajatun
  offline-komentorivitestin; ei Windows-allekirjoitusväitteitä.

CI ei rekisteröi varmennepalvelua, lataa yksityisiä
allekirjoitusavaimia, kutsu oikeaa pörssitiliä eikä luo
tuotantojulkaisua.

## Puuttuvat asiakasjulkaisun portit

1. Varmistetaan kaupallinen julkaisija, tunnisteet, oikeudet
   ja ohjelman lisenssiehdot yhtä asiakkaan konetta varten.
2. Mahdollinen luotettu varmenne hankitaan vasta
   erillisellä hyväksynnällä; tässä haarassa ei tehdä tilausta.
3. Allekirjoitus suoritetaan tarkistetussa ja rajatussa
   julkaisuprosessissa; sertifikaattia ja varsinkaan sen
   yksityistä avainta ei lisätä repoon tai tavalliseen CI-lokiin.
4. Molemmat tiedostot verifioidaan eri vaiheissa ja tarvittaessa
   asiakkaan koneella hyväksytyllä sertifikaattitunnisteella.
5. Oikean tilin testaaminen edellyttää lisäksi
   Revolut X:n API-oikeuksien varmennusta,
   asiakkaan nimenomaista lupaa ja GET-budjetin
   erillistä testin suorituspolkua.
6. Tuotantoversion turvallisuus ja grid-strategian
   forward-shadow sekä live-pilotti vaativat **erilliset**
   hyväksymisportit. Allekirjoituksen validius ei tarkoita,
   että automaattikaupankäynti olisi turvallista.

## Lähteet

- Microsoft Authenticode:
  https://learn.microsoft.com/powershell/module/microsoft.powershell.security/get-authenticodesignature
- Microsoft SignTool:
  https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool
- PyInstaller:
  https://pyinstaller.org/en/stable/usage.html
- Inno Setup:
  https://jrsoftware.org/isinfo.php
