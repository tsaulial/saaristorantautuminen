# Rantabotti

Luonnollinen kysymys → **deterministinen suodatin** 17 222 rannan ja 28
ulottuvuuden yli. Kolmas erillinen demo: lukee `demo-oma/`:n aineiston,
kirjoittaa vain tänne.

```bash
python3 -m http.server 8771 --bind 127.0.0.1   # repon juuresta
# http://127.0.0.1:8771/demo-botti/index.html
```

## Turvallisuus on arkkitehtuurikysymys

Ihmiset rantautuvat veneellä. Botti joka sanoo "tämä ranta on hyvä" kun data
ei sano niin, on huonompi kuin ei vastausta.

**Malli ei koskaan tuota faktoja rannoista.** Se palauttaa vain kyselyn, joka
validoidaan valkolistaa vasten ja suoritetaan paikallisesti. Hallusinaatio ei
siis rakenteellisesti *voi* tuottaa väärää väitettä rannasta; pahin mahdollinen
virhe on väärin ymmärretty kysymys, ja se näkyy koska kysely näytetään takaisin.

Kolme porttia: kehote antaa vain sanaston → palvelin validoi → **selain
validoi uudelleen**. Keksittyä ulottuvuutta ei suoriteta, vaikka palvelin sen
palauttaisi.

Botti **kieltäytyy** turvallisuuskysymyksistä ja **sanoo suoraan** kun kysymys
koskee jotain mitä aineistossa ei ole (ruovikko, syvyys, laituri).

## Rakenne

```
python3 demo-botti/esteet.py          # esteenkorkeudet 12 sektorille
python3 demo-botti/vie.py             # suunta, absoluuttinen fetch, WGS84
python3 demo-botti/nimet.py           # paikannimet (vaatii nimisto-mml/)
python3 demo-botti/kysely.py          # sanaston ja varsien tarkistus
python3 demo-botti/testaa_sopimus.py  # Python ↔ JS ristiintarkistus
```

Päätepiste (valinnainen — ilman sitä varapolku):

```bash
ANTHROPIC_API_KEY=... python3 demo-botti/palvelin.py    # portti 8772
```

Selain käyttää oletuksena `http://127.0.0.1:8772/kysely`. **Ei samaa porttia
kuin sivu itse**: 8771 on staattinen tiedostopalvelin, joka vastaa POSTiin
501:llä. Muualle osoitetaan ilman koodimuutosta:

```
?api=https://ubuntu.saola-capella.ts.net/kysely
```

Ilman API-avainta palvelin käynnistyy silti ja vastaa koekutsuun, mutta
kyselyihin 503:lla — selain siirtyy varapolulle ja **kertoo syyn**:
*"Yritin: … — ei API-avainta."*

## Jaetun sopimuksen kolmas kopio

`shelterScoreFromFetch` on nyt kolmessa paikassa. **Se on velka, ei ratkaisu.**
Ainoa suoja on `testaa_sopimus.py`, joka ajaa `sopimus.js`:n nodessa ja vertaa
Pythoniin 392 yhdistelmällä (fetch × tuuli × este).

Mitattu: suurin ero **2,2·10⁻¹⁶** — tasan se suuruusluokka joka `pipeline.py`:hyn
on kirjattu `exp()`:n rajaksi. Toleranssi 1e-12.

**Jos se testi ei ole vihreä, botti ei saa näyttää yhtään aallonkorkeutta.**

## Sanasto on se paikka jossa laatu asuu

`sanasto.json`: 17 ominaisuutta, 8 suuntaa, 3 dynaamista. Kynnykset on
**mitattu**, ei arvattu — jokaisen perässä on osuus jonka se valitsee.

`kallio` on jätetty pois tarkoituksella: se korreloi `kallio_vektori`in kanssa
r = 0,941, ja molemmat sanastossa tekisivät tuloksesta mielivaltaisen.

### Varsien törmäystarkistus

Suomen taivutusta ei voi päätellä prefiksistä, joten varret on kirjattu käsin.
Lyhyt varsi osuu naapurisanaan helposti, ja seuraus on hiljainen — kysely saa
suodattimen jota kukaan ei pyytänyt. `kysely.py` tarkistaa tämän ja löysi kolme:

| varsi | osui sanaan | seuraus |
|---|---|---|
| `suo` | `suojaisa` | "suojaisa ranta" haki **suorantoja** |
| `jyrka` | `jyrkanteinen` | jyrkännekysely sai jyrkkyyssuodattimen |
| `rauhall` | `rauhallinen` | syrjäisyys sekaantui aallokkoon |

## Dynaaminen kerros

Kaksivaiheinen: staattinen suodatin ensin, aallonkorkeus vasta selviytyneille.

Mitattu 26.8.2026, tuuli 0,7–2,9 m/s: aallonkorkeuden mediaani 1,2–2,2 cm,
suurin 32 cm. **Esteenkorkeudet vaikuttavat 69 %:iin rannoista** — ilman niitä
altistus yliarvioituisi järjestelmällisesti.

Aika tarkistetaan **vaikka dynaamista ehtoa ei olisi**: jos kysyt 200 tunnin
päästä ja ennuste ulottuu 50 tuntiin, botti sanoo sen eikä ekstrapoloi.

## Paikannimet

`nimisto_koko_suomi` / `karttanimet_25k`, 1,27 Gt GML luettuna virtana.
3 327 nimeä testialueella, mediaanietäisyys **162 m**, 2 % yli 500 m.

**Lähin nimi ei ole sama kuin oikea nimi.** Siksi vastaus sanoo
*"lähellä: Prästnäsudden (26 m)"* eikä *"Prästnäsuddenin rannalla"*, ja yli
400 m päässä oleva nimi näytetään haaleana.

## Erillisyys

Koskemattomia: `frontend/`, `docs/`, `backend/`, `build_static.py`,
`demo-oma/`, `demo-viz/`. Niistä vain luetaan.
