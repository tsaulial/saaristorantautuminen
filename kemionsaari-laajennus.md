# Kemiönsaaren lisääminen ilman koko kartan uudelleenlaskentaa

Kemiönsaari on merta, mutta se ulottuu syvälle mantereelle, ja rannikon
latauskäytävä (`--rannikko`, oletusleveys) leikkasi sen poikki. Tässä
lisätään puuttuva osa **olemassa olevan tuotantoympäristön päälle** niin
että jo laskettu aineisto säilyy.

Kaikki komennot ajetaan **Ubuntulla**, hakemistossa
`~/saaristorantautuminen-0826/saaristorantautuminen`.

---

## 0. Koodi ajan tasalle

```bash
git pull
```

Mukana tulee kolme muutosta joita ilman laajennus laskisi silti kaiken
uudelleen tai laskisi väärin — ne on kuvattu lopussa kohdassa "Mitä
muuttui ja miksi".

---

## 1. Selvitä mitä oikeasti puuttuu

Älä luota arvioon toiselta koneelta. Skripti lukee rekisterin ladatuista
DEM-lehdistä, joten sen tulos on tasan tämän koneen tilanne:

```bash
.venv/bin/python3 kemionsaari_kartoitus.py
```

Se piirtää peittokartan (`#` = tiili on, `.` = puuttuu) ja tulostaa
valmiin latauskomennon puuttuvien ruutujen ympäriltä. **Käytä sitä
bboxia**, älä alla olevaa esimerkkiä.

Jos tuloste sanoo "Ei puuttuvia ruutuja", alue on jo katettu eikä
mitään tarvitse tehdä.

---

## 2. Lataa aineisto

```bash
source .venv/bin/activate
python3 -m backend.mml_lataus --bbox <SKRIPTIN ANTAMA BBOX> --taustakartta --kuiva
```

`--kuiva` näyttää lehtimäärät ja vektorihaun alan ajamatta mitään.
Tarkista:

- **vektorihaut alle 17 334 km²** — rajapinnan katto. Skripti varoittaa
  jos ylittyy; silloin bbox jaetaan kahtia.
- **DEM- ja karttalehtien määrä** on samaa suuruusluokkaa kuin
  puuttuvien ruutujen määrä. Jo ladatut lehdet ohitetaan latauksessa.

Sitten oikea ajo ilman `--kuiva`:ta:

```bash
python3 -m backend.mml_lataus --bbox <SAMA BBOX> --taustakartta
```

**`--mitatoi` EI ajeta.** Se on koko homman vastakohta: se poistaa
`_raw.npz`-tiedostot ja pakottaa juuri sen 20+ tunnin laskennan jota
tässä vältetään.

Levytilaa kuluu arviolta 2–4 Gt.

---

## 3. Rakenna

```bash
./eraajo.sh
```

`eraajo.sh` aktivoi venvin itse.

### Mitä säilyy

| | |
|---|---|
| `*_raw.npz` (pistemäärät, puskurimaskit) | **säilyy** — tämä on se 20+ tuntia |
| `*_lidar.npz` (laserkeilaus) | **säilyy** |
| `_fetch_global`, `_water_global` (pyyhkäisymatkat) | **säilyy vanhoilta ruuduilta**, uudet lasketaan |

### Mitä lasketaan uudelleen — ja miksi se on oikein

| | |
|---|---|
| Uusien tiilien `_raw.npz` | ne ovat uusia |
| Meri- ja korkeusmosaiikki | mosaiikki kattaa tasan sen tiilijoukon jolla se rakennettiin |
| Globaalit kynnysarvot ja tasapelin järjestysluku | "parhaat X %" on jakauma koko aineistosta, ja aineisto kasvoi |
| Kaikki osatekijä-, tasapeli- ja kärkipaikkakuvat | ne on kvantisoitu noita globaaleja jakaumia vasten |
| Rantaviivan pituus | mitataan tiilien peittämältä alalta |
| Vektoritasot (väylät, suojelualueet, palvelut) | haetaan tiilien peittämälle alueelle |

Kuvien uudelleentuotanto on mitattuna noin **1 s/tiili** pelkkään
levyltälukuun ja alinäytteistykseen, päälle PNG-pakkaus kolmelle
tasolle. Koko aineistolle se on tunteja, ei kymmeniä tunteja — mutta
tämä on arvio, ei mittaus tältä koneelta. **Lokista näkee todellisen
ajan.**

---

## 4. Mitä lokista katsotaan

Nämä rivit kertovat toimiiko inkrementaalisuus. Jos ne näyttävät
väärältä, keskeytä ennen kuin ajo on kulunut tunteja:

1. **`lahdeaineisto muuttunut, lasketaan uudelleen`** — pitäisi tulla
   vain uusille tai oikeasti muuttuneille tiilille. Jos se tulee
   sadoille vanhoille tiilille, sormenjälkien siirtymä ei toiminut.
2. **`muuttuneita tiilia: N`** — sama luku, sädelaskennan puolella.
3. **`merimosaiikki: tiilisto muuttunut, rakennetaan uudelleen`** —
   tämän **pitää** tulla. Jos se puuttuu, uudet ruudut jäivät
   avovedeksi.
4. **`korkeusmosaiikki: ei tiilistotietoa, rakennetaan uudelleen`** —
   odotettu ensimmäisellä ajolla; sivutiedostoa ei ole vielä olemassa.
5. **`rantaviiva: N tiilta -> X km`** — N kasvaa, X kasvaa.
6. **`rykelmia: [...]`** — Kemiönsaari kuuluu rannikkorykelmään, joten
   rykelmien määrän ei pitäisi kasvaa.
7. Ei OOM-tappoja: `dmesg | grep -i "out of memory"`

---

## 5. Todentaminen

```bash
tailscale serve status
```

Selaimessa `https://ubuntu.saola-capella.ts.net/`:

- Kemiönsaaren sisäosien rannat ovat väritettyjä eivätkä tyhjiä.
- **Aallokkonäkymässä sisälahdet eivät ole yhtä alttiita kuin
  ulkosaaristo.** Tämä on se kohta jossa mosaiikkivika olisi näkynyt:
  jos sisäsaaristo näyttää yhtä altiilta kuin avomeri, mosaiikkia ei
  rakennettu uudelleen.
- Vanha alue (esim. Helsingin edusta) näyttää samalta kuin ennen —
  värit voivat siirtyä hieman, koska globaali jakauma muuttui, mutta
  ei rajusti.

Kova lataus (`ctrl + shift + R`) ennen kuin epäilee buildia. Selaimen
välimuisti on johtanut tässä projektissa harhaan kolmesti.

---

## Mitä muuttui ja miksi

Kolme korjausta tehtiin, koska ilman niitä laajennus olisi joko laskenut
kaiken uudelleen tai laskenut väärin.

**1. Jaetut lähteet tunnistetaan sisällöstä** (`31bd24c`).
`rakennukset.gpkg` ja `hydrografia.gpkg` ovat jaettuja: yhdenkin alueen
lisäys kirjoittaa niihin. Tunnistus perustui tiedoston kokoon ja
muokkausaikaan, joten jokaisen tiilen `_raw.npz` vanheni. Päijänteen
lisäys pakotti näin 1126 tiilen laskennan josta 96 % oli turhaa. Nyt
luetaan sisältötunniste sille alueelle jota tiili käyttää — mitattuna
26 ms/tiili.

Siirtymä: olemassa olevissa tiedostoissa on vanhan muotoinen
sormenjälki. Se kelpaa jos versio, DEM ja karttalehti täsmäävät.
**Hinta on kirjattu:** jos jonkin vanhan tiilen oma vesi- tai
rakennusaineisto on muuttunut sen jälkeen kun se laskettiin, sitä ei
huomata tällä kertaa. Heti kun tiili lasketaan kerran uudelleen, sen
tarkistus on täsmällinen.

**2. Mosaiikin vanhentuminen (`c594286`) — tämä oli oikea vika.**
Meri- ja korkeusmosaiikki tarkistivat tuoreutensa vertaamalla taulukon
**muotoa**, joka on tiilien yhteisen laatikon koko. Kemiönsaaren
puuttuvat ruudut ovat syvällä aineiston sisällä — Ahvenanmaan ja
Suomenlahden välissä — joten laatikko ei olisi muuttunut lainkaan.
Vanha mosaiikki olisi kelvannut, ja uudet ruudut olisivat jääneet
merimosaiikin oletusarvoon **avovedeksi**: pyyhkäisymatka rajaton,
esteenkorkeus nolla, jokainen uusi ranta täysin altis. Mikään ei olisi
kaatunut eikä lokiin olisi tullut riviäkään.

Vika osui tasan siihen käyttötapaukseen jota varten inkrementaalinen
laajennus tehdään: aukon täydentämiseen.

**3. Sormenjälkiä verrataan yhdessä paikassa** (`e9e28b1`).
Vertailuja oli kaksi — `_raw.npz` ja `_fetch_global` — ja siirtymäsääntö
vain toisessa. Jälkimmäinen olisi ajanut koko rannikon sädelaskennan
uudelleen vaikka edellinen päästi samat tiilet läpi.

---

## Mitä tämä ei korjaa

Globaali tasapelin kvantisointi (`_global_tiebreak_sorted`) on yhä
**kaikkien** tiilien arvoista lajiteltu taulukko, joten sen muuttuminen
tuottaa kaikki kuvat uudelleen. Se on tämän laajennuksen jäljelle jäävä
kustannus. Ratkaisu olisi kvantisoida kiinteää asteikkoa vasten eikä
järjestyslukua vasten, mutta se muuttaisi olemassa olevien alueiden
värejä — oma päätöksensä, ei tähän ajoon.
