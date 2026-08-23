# Uuden alueen lisääminen ilman koko kartan uudelleenlaskentaa

> **Tila 23.8.2026.** Kohta 1 on toteutettu ja todennettu (`31bd24c`,
> `e9e28b1`). Toteutuksen aikana löytyi lisäksi neljäs syy jota tässä
> ei ollut: mosaiikki ei huomannut laatikon **sisälle** lisättyjä
> tiiliä (`c594286`) — ks. kohta 4 alla. Kohta 2 on yhä auki ja on
> laajennuksen jäljelle jäävä kustannus.
>
> Ajo-ohje: [`kemionsaari-laajennus.md`](kemionsaari-laajennus.md).

## Ongelma

Päijänteen lisääminen 1078 tiilen rannikkoon pakotti kaikkien 1126 tiilen
uudelleenlaskennan, vaikka rannikon oma aineisto ei muuttunut lainkaan.
Työstä noin 96 % oli turhaa.

Syitä on kolme, ja ne on ratkaistava erikseen.

---

## 1. Jaettujen tiedostojen sormenjälki (suurin syy)

`lahde_sormenjalki` (`backend/pipeline.py:474`) käyttää **koko tiedoston**
kokoa ja muokkausaikaa:

```python
_tiedoston_sormenjalki(buildings_path)   # rakennukset.gpkg  - JAETTU
_tiedoston_sormenjalki(vesisto.GPKG)     # hydrografia.gpkg  - JAETTU
```

Uuden alueen lisääminen kirjoittaa molempiin, joten **jokaisen** tiilen
`_raw.npz` vanhenee. DEM ja karttalehti ovat tiilikohtaisia eivätkä aiheuta
tätä.

### Korjaus: tiilikohtainen sisältösormenjälki

Sormenjälki lasketaan siitä aineistosta jota tiili oikeasti käyttää, ei
koko tiedostosta. Halpa ja riittävä tunniste on bbox-haku ilman
geometriaa:

```python
info = pyogrio.read_dataframe(GPKG, layer=taso, bbox=tile.bounds,
                              columns=["mtk_id"], read_geometry=False)
tunnus = (len(info), hash(tuple(sorted(info["mtk_id"]))))
```

Mitattava ensin: paljonko tämä maksaa per tiili. Rannattomuustesti
(joka tekee bbox-haun **geometrioineen**) oli tällä koneella 85 ms/tiili
mutta Ubuntulla sekunteja, koska sama geometria on siellä noin 17
kappaleena. Ilman geometriaa haun pitäisi olla kertaluokkia halvempi —
mutta se on mitattava, ei oletettava.

**Jos se osoittautuu kalliiksi**, vaihtoehto on kirjanpito: `yhdista_*`
tietää mihin laatikkoon uusi aineisto tuli, ja se voi merkitä vain
kyseisen alueen tiilet vanhentuneiksi.

---

## 2. Globaali tasapelinpurun kvantisointi

`_global_tiebreak_sorted` (`pipeline.py:890`) on **kaikkien** tiilien
tasapelinpurkuarvoista lajiteltu taulukko, jolla arvot muunnetaan
0–255 järjestysluvuksi. Sitä käytetään `get_or_compute_factor_arrays`issa
(rivi 1064), joten kun taulukko muuttuu, **kaikki** osatekijä-, tasapeli- ja
kärkipaikkakuvat on tuotettava uudelleen.

Tämä on kuvatuotannon koko kustannus: noin 0,86 Mt/tiili ja tunteja.

### Korjaus: kiinteä kvantisointikäyrä

Taulukko on olemassa yhdestä syystä, joka on kirjattu koodiin:
tasavälinen kvantisointi hukkaisi tarkkuutta, koska arvot kasautuvat
jakauman yläpäähän. Se on **jakauman muodon** ominaisuus, ei tämän
nimenomaisen tiilijoukon.

Jos muoto kalibroidaan kerran ja jäädytetään monotoniseksi käyräksi
(esim. potenssi- tai logit-muunnos), kvantisointi säilyttää saman
tarkkuuden mutta lakkaa riippumasta aineistosta. Osatekijäkuvista tulee
silloin **puhtaasti paikallisia**.

Todennus: kvantisoi nykyisellä taulukolla ja ehdotetulla käyrällä sama
otos, ja vertaa järjestyksen säilymistä. Vaatimus on että tasapelien
sisäinen järjestys ei muutu havaittavasti.

**Kevyempi välivaihe**, jos käyrä ei kelpaa: pidä taulukko mutta lisää sen
tunniste kuvien nimiin. Silloin vanhat kuvat kelpaavat niin kauan kuin
taulukko ei oikeasti muutu, ja pieni lisäys ei välttämättä muuta sitä.

---

## 3. Globaalit kynnysarvot (ei ongelma)

"Parhaat 7 %", suojaisuus-, kärkipaikka- ja rantaviivakynnykset ovat
pieniä JSON-tiedostoja. Ne on laskettava uudelleen, mutta se on minuutteja
eikä tunteja — ja ne ovat juuri se asia jonka **pitääkin** muuttua, koska
käyttäjä valitsi yhden yhteisen kynnyksen koko aineistolle.

---

## Mikä ei ole ongelma jo nyt

| Osa | Miksi säilyy |
|---|---|
| `_fetch_global`, `_water_global` | avainnettu globaaleilla solutunnisteilla |
| Pyyhkäisymosaiikit | rykelmäjako eristää etäisen alueen omakseen |
| `_lidar.npz` | katsoo vain omaa tiiltään |
| Peruskartta | riippuu vain karttalehdestä |

Rykelmäjako tehtiin muistin takia, mutta se ratkaisee tästä jo osan:
Päijänne on 54 km rannikosta, joten sen pyyhkäisymatkat lasketaan
erillään eivätkä ne koske rannikon mosaiikkiin.

---

## Odotettu tulos

Päijänteen kaltaisen alueen (48 tiiltä) lisääminen 1078 tiilen
aineistoon:

| | Nyt | Korjausten jälkeen |
|---|---|---|
| Raakadata | 1126 tiiltä | 48 tiiltä |
| Osatekijäkuvat | 1126 tiiltä | 48 tiiltä |
| Globaalit kynnykset | uudelleen | uudelleen (minuutteja) |
| Pyyhkäisymatkat | oma alue | oma alue (jo nyt) |

Eli tunneista minuutteihin.

---

## Toteutusjärjestys

1. **Mittaa** bbox-haun hinta ilman geometriaa (kohta 1). Se ratkaisee
   kumpi korjaus valitaan.
2. **Kohta 1** yksin poistaa suurimman osan turhasta työstä ja on
   riskitön: väärä sormenjälki johtaa vain uudelleenlaskentaan, ei
   vääriin tuloksiin.
3. **Kohta 2** vasta sen jälkeen, ja vasta kun kvantisointikäyrä on
   todennettu. Väärä käyrä muuttaisi kärkipaikkojen järjestystä
   hiljaa.

Kohta 1 kannattaa tehdä ennen seuraavaa aluelisäystä. Kohta 2 vasta kun
kuvatuotanto on todettu pullonkaulaksi mittaamalla, ei olettamalla.


---

## 4. Mosaiikki ei huomannut laatikon sisälle lisättyjä tiiliä

*Tätä ei ollut alkuperäisessä listassa. Se löytyi vasta kun kohtaa 1
todennettiin Kemiönsaarella.*

`get_or_compute_sea_mosaic` ja `get_or_compute_height_mosaic`
tarkistivat tuoreutensa vertaamalla taulukon **muotoa** rekisteristä
laskettuun muotoon. Koodissa oli tästä nimenomainen kommentti, joka
piti tarkistusta geometrisena ja siksi "tarkempana kuin hajautusarvo".

Muoto on tiilien **yhteisen laatikon** koko. Tarkistus huomaa siis vain
laajennuksen joka kasvattaa laatikkoa — Ahvenanmaan tai Päijänteen —
mutta ei aukon täydentämistä. Kemiönsaaren puuttuvat ruudut ovat
Ahvenanmaan ja Suomenlahden välissä, joten laatikko ei olisi muuttunut
lainkaan.

Seuraus olisi ollut hiljainen ja väärä: merimosaiikin kattamattomat
ruudut ovat oletuksena `True` eli **avovettä**. Uusien tiilien
pyyhkäisymatka olisi ollut rajaton ja esteenkorkeus nolla, eli jokainen
Kemiönsaaren sisälahti olisi arvioitu yhtä alttiiksi kuin avomeri.

Korjaus: mosaiikkiin tallennetaan rekisterin sormenjälki ja se
tarkistetaan. Korkeusmosaiikilla sivutiedostossa, koska `.npy` kantaa
vain taulukon.

**Kuvio on sama kuin muissakin tämän projektin hiljaisissa vioissa:**
jokin päätettiin kerran ensimmäisestä palasta — tässä "mosaiikki kattaa
oikean alueen jos sen koko täsmää" — eikä sitä merkitty vanhenevaksi
oikeasta syystä. Tarkistus oli olemassa ja näytti huolelliselta; se
mittasi vain väärää asiaa.
