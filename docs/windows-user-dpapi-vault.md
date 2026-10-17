# Windows-avainvarasto v1 — DPAPI CurrentUser, vain paikallinen

Päiväys 17.10.2026. **Tila: kehitystesti; ei asiakastoimitus eikä oikean tilin API-koe.**

## Rajaus ja arkkitehtuuri

Toteutus jatkaa PR #9:n erillistä GET-only-tililukijaa. Se ei lisää
kaupankäynti-API:a, kaupankäynnin valvontaa tai jatkuvaa keruuta.

- Windows-käyttäjäkohtainen DPAPI `CryptProtectData` /
  `CryptUnprotectData` pywin32:n kautta. `CRYPTPROTECT_LOCAL_MACHINE`
  ei ole käytössä; `CRYPTPROTECT_UI_FORBIDDEN` estää dialogia vaativan
  suojauksen.
- Sovelluksen oletuskansio on `%LOCALAPPDATA%\\GridConsole\\vault`.
- Suojaa kansion, lukitustiedoston ja salatun tietueen NTFS DACL:llä:
  vain nykyinen Windows-käyttäjä ja SYSTEM; perityt ACE:t estetään.
- Reparse pointit/symlinkit kiellettyjä vaultin hallinnoimissa poluissa.
- Avainvarastolla on lyhyt, ei-odottava prosessilukitus; rinnakkaiset
  muutokset estetään. Salattu päivitys kirjoitetaan ensin suojattuun
  väliaikaistiedostoon, fsync ja atominen korvaus.
- Ed25519-avaimen yksityinen 32 tavun raw-esitys sekä Revolut X:n
  64 merkin API-avain pysyvät **ainoastaan käyttäjän DPAPI-salatussa
  tiedostossa** ja väliaikaisesti Python-prosessin muistissa.
  Selväkielistä `private.pem`-tiedostoa ei generoida.
- Julkinen `-----BEGIN PUBLIC KEY-----`-avaintieto voidaan näyttää,
  jotta asiakas rekisteröi sen itse Revolut X -sovellukseen.
- Erillinen `make_get_only_reader(transport=...)` edellyttää
  eksplisiittistä HTTP-transporttia, eikä tarjoa automaattista
  verkkoyhteyden luontia. Tämän PR:n testit käyttävät vain
  synteettisiä mock-tietoja.

Tämä **ei** ole turvallisuusluokka, jossa admin/malware olisi
luotettavasti eristetty. Saman käyttäjän oikeuksin toimiva haittaohjelma
voi käyttää DPAPI:a. Pythonissa ei voida taata avainmateriaalin
puhdistumista muistista. Tiedostojen poistaminen ei takaa vanhan
salatun datan ylikirjoitusta tallennusvälineellä.

## Kehityskäyttö (EI vielä asiakkaan tuotantoavaimilla)

Python 3.11, Windows ja asennetut riippuvuudet:

```powershell
python -m pip install -r requirements.txt
python scripts/windows_vault_cli.py init
python scripts/windows_vault_cli.py status
python scripts/windows_vault_cli.py public
```

Käyttäjän julkinen PEM voidaan syöttää itse Revolut X:n API keys
-sivulla. Tämä on myöhempi hyväksyntävaihe; CI ei tee rekisteröintiä.

Vasta kun käyttäjä on saanut API-avaimen ja saanut erillisen luvan
oikean tilin käyttöönottoon:

```powershell
python scripts/windows_vault_cli.py link
```

Syöte annetaan vain **piilotettuun paikalliseen getpass-kehotteeseen**,
ei komennon parametrina, sähköpostilla, GitHubiin, lokiin,
tekoälypalveluun eikä etäohjelmaan. Komento tallentaa avaimen
DPAPI-salattuun tietueeseen muttei tee yhtään API-pyyntöä.

Paikallisen tiedoston poisto (EI revokoi Revolut X:n avainta):

```powershell
python scripts/windows_vault_cli.py remove --confirm-local-only-removal
```

Asiakkaan on tämän lisäksi **erikseen** poistettava/revokoitava
API-avain Revolut X:n käyttöliittymästä, jos käyttöoikeus
halutaan lakkauttaa. Varmenne ja API-avain ovat tämän jälkeen
epäkelpoja uusille pyynnöille vain, kun palvelimen peruutus
on varmistettu.

## Avaimen hallinnan riskit

1. DPAPI-salaus on sidottu Windows-käyttäjätiliin/koneeseen;
   uuden koneen asennus on uusi käyttöönotto, ei vaultin
   sellaisenaan kopiointi. Windows-profiilin palautus,
   salasanan ylläpitopalautus tai laitevika voi estää purun.
2. Ei automaattista avainten siirtoa, pilvivarmuuskopiointia,
   avainten tulostusta tai yksityisen avaimen export-API:a.
3. Ei avaimen hiljaista korvaamista. `bind_api_key` estää
   toisella arvolla ylikirjoittamisen, kun avain on jo sidottu.
   Avaimen kierto ja revokaatio tarvitsevat erillisen
   turvallisen käyttöpolun ennen kaupallista julkaisua.
4. DPAPI suojaa dataa levossa; se **ei muuta Revolut X:n
   API-avaimen oikeuksia**. GET-only-ohjelmistorajaus ja
   API-avaimen palvelinpuolen oikeuksien minimointi ovat eri asioita.
5. Avaimen vuotaessa ensitoimi on revokaatio
   Revolut X -palvelussa ja saldon/toimeksiantojen tarkistus.
6. Näkymä/CLI ei vielä ole valmis allekirjoitettu Windows-asennin.

## Dokumentaatiolähteet

- Microsoft DPAPI CurrentUser:
  https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata
- Microsoft DPAPI:n palautumisrajoitukset:
  https://learn.microsoft.com/en-us/windows/win32/seccrypto/example-c-program-using-cryptprotectdata
- Revolut X:n Ed25519, 64-merkkinen API key, virallinen
  rekisteröintimenettely:
  https://developer.revolut.com/docs/api/revolut-x-crypto-exchange

**Revolut Business API Console -ohjeiden read/write-skoopeista ei saa
päätellä suoraan, että Revolut X:n erillinen API tukee samaa
avaimen oikeuksien rajausmekanismia.** Tämä varmennetaan ennen
aidon tilin testejä.

## CI-hyväksyntä

Windows: DPAPI:n oikea natiivisalaus ja purku testiavainparilla;
salatun tiedoston tarkistus, NTFS DACL ja rinnakkaislukitus,
virheellisten avainten esto, vandalisoidun salatun blobin
hylkäys, paikallisen poistamisen vahvistus.

Ubuntu: vaultin kieltäytyminen käynnistymästä; olemassa
olevat API-, valvoja- ja palautumistestit säilyvät vihreinä.

Molemmissa: repo-yhteinen testisarja, mock GET-only-lukija,
CI:n JSON `live_trading=DISABLED` ja
`real_account_api_calls=NOT_RUN`.

## Seuraava erillinen portti

`docs/first-live-account-readonly-protocol.md` sisältää vain
**suunnitellun** oikean tilin testijärjestyksen. Se ei ole
automaattisesti hyväksytty ajo-ohje, eikä oikeita avaimia
rekisteröidä, liitetä tai lueta ilman käyttäjän erillistä lupaa.
