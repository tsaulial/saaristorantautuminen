# Project Specification: Rantautumispaikkojen Analysointisovellus (Landing Site Finder)

## 1. Yleiskatsaus ja Tavoite
Sovelluksen tavoitteena on auttaa vesilläliikkujia (veneilijät, melojat, retkeilijät) löytämään parhaat rantautumispaikat suomalaisesta maastosta.

Sovellus renderöi verkkokäyttöliittymässä (Leaflet.js) taustakartan päälle lämpökarttamaisen värioverlayn (läpikuultava linja/alue rantaviivalla), jossa rantautumispaikat pisteytetään asteikolla **Vihreä (paras) -> Keltainen -> Punainen (heikoin)**.

kehitysympäristö: MacOS + Claude Code
Tuotantoympäristö: Linux (Ubuntu/Debian) -palvelin

---

## 2. Aineistot ja Lähdedata
1. **Korkeusmalli:** `korkeusmalli-mml/` -hakemisto (MML 2m DEM GeoTIFF -aineisto).
   * Kattaa vain karttalehdet L3114, L3123, L3124 (osalehtijako A–H, esim. `L3114D.tif`). Karttalehdillä L3111, L3112, L3113 ei ole korkeusmallia — näille alueille $S_{\text{slope}}$ ei voida laskea, joten overlay jätetään tyhjäksi/piirtämättä niillä kunnes DEM-data hankitaan.
   * Huom: korkeusmallin ja karttakuvan lehtijaot eroavat toisistaan (12km×12km 1m/px vs. 6km×6km osalehdet). Yhdistäminen on tehtävä koordinaattien (BBOX), ei tiedostonimien, perusteella.
2. **Rakennukset:** `rakennukset-mll/rakennukset.gpkg` (MML Maastotietokanta, vektoripolygonit, EPSG:3067). Sisältää useita layereita; etäisyyslaskennassa ($S_{\text{dist}}$) käytetään layereita **`rakennus`** ja **`rakennelma`** (kattaa myös laiturit, aitat, saunat yms. — ei pelkkiä varsinaisia rakennuksia).
3. **Karttarasteri:** `karttakuva-mll` MML Peruskarttapohja (GeoTIFF / PNG / WMTS-rajapinta).

---

## 3. Pisteytysalgoritmi (Score Engine)

Jokaiselle rantaviivan läheisyydessä (n. 5–15m vyöhyke rantaviivasta maalle päin) olevalle pikselille/pisteelle lasketaan kokonaispiste $S \in [0.0, 1.0]$:

$$S = (0.50 \cdot S_{\text{slope}}) + (0.35 \cdot S_{\text{dist}}) + (0.15 \cdot S_{\text{rock}})$$

### A. Jyrkkyys ($S_{\text{slope}}$) – Paino 0.50 (KORKEA)
* Lasketaan MML 2m DEM -korkeusmallista asteina ($0^\circ - 90^\circ$).
* $0^\circ - 5^\circ \rightarrow 1.0$ (Ihanne)
* $5^\circ - 20^\circ \rightarrow$ Lineaarinen pudotus $1.0 \rightarrow 0.0$
* $> 20^\circ \rightarrow 0.0$ (Hylätty / Ei sovellu rantautumiseen)

### B. Etäisyys rakennuksiin ($S_{\text{dist}}$) – Paino 0.35 (KESKIVERTO)
* Lasketaan euklidinen etäisyys lähimpään `rakennukset.gpkg` polygonin reunaan.
* $< 20\text{ m} \rightarrow 0.0$
* $20\text{ m} - 150\text{ m} \rightarrow$ Lineaarinen nousu $0.0 \rightarrow 1.0$
* $> 150\text{ m} \rightarrow 1.0$

### C. Kallioperä rasterilta ($S_{\text{rock}}$) – Paino 0.15 (MATALA)
* Suodatetaan rasterikarttakuvasta harmaa kalliomerkintä (`#d1c7d1`).
* Koska ruskeat korkeuskäyrät ja karttimerkit peittävät harmaata aluetta, käytä **HSV-väriavaruutta** ja OpenCV:n *morphological closing* (`cv2.morphologyEx`) -operaatiota aukkojen silloittamiseksi.
* Jos alue on tunnistettu kallioksi $\rightarrow 1.0$, muuten $\rightarrow 0.2$.

### D. Rantaviivan eristys
* Tunnistetaan rantaviiva rasterilta sinisen värin (`#0080ff`) perusteella HSV-suodatuksella tai leikataan DEM-korkeusmallista vedenpinnan rajaa vastaava taso.
* Pisteytys ja värikartta generoidaan **vain** tälle puskurivyöhykkeelle.
* Näkyvyyssyistä puskurivyöhyke **piirretään** visuaalisesti paksunnettuna (dilataatio) — todellinen 5–15 m -data pysyy tarkkana, vain rendattu kuva levennetään. Paksuus on käyttäjän säädettävissä muutaman kiintean esiasetuksen väliltä (`backend/pipeline.py: THICKNESS_PRESETS = [1, 5, 10, 15, 20]` px, oletus 10 px) — ks. kohta 4:n Vaihe 4 -löydös.

### E. Suo ($S$-rangaistus) — lisätty jälkikäteen
* Suodatetaan rasterikarttakuvasta keltainen suomerkintä (`#ffff40`), HSV-ikkuna (H 29–31, S 180–255, V 250–255) + morphological closing samaan tapaan kuin kallio.
* Suo on huono rantautumispaikka: pikselin **lopullinen** $S$ (kaikki kolme komponenttia A–C jo laskettuna) kerrotaan rangaistuskertoimella `SWAMP_PENALTY_FACTOR = 0.5` (`backend/pipeline.py`) — painoja A–C ei muuteta.
* "Parhaat rantautumispaikat" -kerros (ks. kohta 4, Frontend) korostaa pisteet, jotka ovat **koko aineiston** (kaikkien tiilien) puskurivyöhykkeen pisteiden yläpäässä. Käyttäjä säätää kynnyksen liukusäätimellä väliltä 1–10 % (`backend/pipeline.py: TOP_PERCENT_PRESETS = 1..10`, oletus 7 %) — persentiili numpy.percentile:lle on aina `100 - top_percent` (`pipeline.top_percent_to_percentile`, `compute_global_threshold`). Tämä on globaali, ei per-tiili, kynnysarvo, ja jokainen 1–10 %:n esiasetus lasketaan ja välimuistetaan erikseen (`_global_threshold_p{persentiili}.json`).
  * **Havaittu ja korjattu ongelma**: pistejakaumassa n. 8 % kaikista puskurivyöhykkeen pikseleistä sai tarkalleen maksimipisteen (`total_score = 1.0`, saavutetaan heti kun jyrkkyys ≤5°, etäisyys rakennuksiin >150 m JA kallio) — persentiilit 93–99 (top 1–7 %) tuottivat siis KAIKKI saman kynnysarvon 1.0 ja täsmälleen saman korostetun alueen. Käyttäjä huomasi tämän testatessaan liukusäädintä ("1 %:n pitäisi näyttää selvästi pienempi kaistale kuin 5 %:n"). **Korjattu** lisäämällä `rank_score = total_score + TIEBREAK_EPSILON · compute_tiebreak(slope_deg, dist_m)` (`backend/pipeline.py`, `TIEBREAK_EPSILON = 1e-4`) — `compute_tiebreak` käyttää DEM:n jatkuvia, RAJOITTAMATTOMIA raakalukemia (asteina/metreinä, ei valmiiksi 1.0:aan leikattuja `slope_score`/`dist_score`-arvoja) suosien tasaisempaa maastoa ja kauempana rakennuksista olevia pikseleitä myös leikkauskynnyksen yläpuolella. `rank_score`:a käytetään VAIN persentiilikynnyksen laskennassa ja "top X %" -maskin valinnassa (`compute_global_threshold`, `get_or_compute_top`) — näkyvä pistemäärä/värikoodaus (`score`, käytössä `get_or_compute_overlay`:ssä) pysyy täysin ennallaan. Epsilon on niin pieni ettei se voi kääntää kahden aidosti eri `total_score`-arvon järjestystä. Validoitu: jokainen 1–10 %:n esiasetus valitsee nyt tasan sen prosenttiosuuden puskurivyöhykkeen pikseleistä (esim. top 1% ≈ 61 190 px, top 5% ≈ 305 945 px, top 10% ≈ 611 890 px, tarkalleen 6 118 898 kokonaispikselistä) sen sijaan että 1–7 % olisivat identtisiä.

---

## 4. Teknologia-arkkitehtuuri

### Backend (Python / FastAPI)
* **Kirjastot:** `fastapi`, `uvicorn`, `rasterio`, `geopandas`, `shapely`, `opencv-python`, `numpy`, `scipy`, `pyproj`
  * ~~`richdem`~~ ei käytetä: kirjasto ei käänny nykyisellä Apple Clang 17:llä (macOS-kehitysympäristö). Jyrkkyys ($S_{\text{slope}}$) lasketaan sen sijaan `numpy.gradient`-funktiolla DEM-taulukosta + `np.arctan(np.hypot(dz/dx, dz/dy))` asteiksi muunnettuna — ei ulkoista riippuvuutta.
* **Vastuualueet:**
  1. Ottaa vastaan pyynnön tietylle maantieteelliselle alueelle (BBOX / karttaruutu).
  2. Suorittaa matriisilaskennan Numpylla jyrkkyydelle, etäisyyksille ja kalliomaskille.
     * Etäisyyslaskenta rakennuksiin rasteroidaan (`rasterio.features.rasterize`) ja lasketaan `scipy.ndimage.distance_transform_edt`-funktiolla — ei geometrista etäisyyttä pikseli kerrallaan (liian hidasta 12000×12000-matriisille).
  3. Generoi kunkin pistemäärän mukaisen RGB-läpikuultavan PNG-overlay-ruudun (tai GeoJSON-vektorin) ja palauttaa sen käyttöliittymälle.
  4. Overlay lasketaan koko karttalehdelle (12km×12km) kerralla ja tallennetaan levylle välimuistiin (esim. tiedostonimen tai lehtitunnuksen mukaan) — ei lasketa uudestaan joka pyynnöllä samalle lehdelle.
  5. Peruskartta (karttakuva-mll) on pipelinen viiteruudukko ja pysyy täysin muuttumattomana (natiivi 1m/px EPSG:3067, ei kiertoa) — kaikki muu data (DEM-pohjainen jyrkkyys/etäisyys, kallio/rantaviiva/suo-maskit, lopullinen pistemäärä) resamploidaan tämän saman ruudukon päälle, samalla CRS:llä. Ei geodeettista reprojisointia WGS84:ään missään vaiheessa — ks. kohta 4:n löydös CRS-käsittelystä frontendissä.
  6. Rantaviivan (`#0080ff`) ja kallion (`#d1c7d1`) väritunnistus HSV-avaruudessa käyttää toleranssia (esim. ±10/kanava), ei tarkkaa hex-osumaa.

### Frontend (HTML / JS)
* **Kirjastot:** `Leaflet.js` tai `OpenLayers`
* **Vastuualueet:**
  1. Näyttää taustalla MML:n peruskartan.
  2. Hakee ja näyttää backendin tuottaman dynamisen väri-overlayn (vihreä-keltainen-punainen) kartan päällä.

---

## 5. Kehityksen vaiheet ja tehtävät Claude Codelle

Etene kehityksessä seuraavassa järjestyksessä:

1. **Vaihe 1: GIS-pisteutusmoottori (CLI Skripti)** — VALMIS (`backend/score_engine.py`)
   * Rakenna Python-skripti, joka lukee sample-DEM-GeoTIFFin ja `rakennukset.gpkg`-tiedoston.
   * Laske jyrkkyysmatriisi ja etäisyysrakennusmatriisi, ja yhdistä ne Numpy-taulukoksi.

2. **Vaihe 2: OpenCV-suodatin rasterikuvalle** — VALMIS (`backend/raster_filters.py`)
   * Rakenna funktio, joka lukee karttarasterin, muuntaa sen HSV-muotoon, tunnistaa rannat (`#0080ff`) ja kalliot (`#d1c7d1`) sekä palauttaa binäärimaskit.

3. **Vaihe 3: FastAPI Backend** — VALMIS (`backend/pipeline.py`, `backend/tiles.py`, `backend/api.py`)
   * Yhdistä Vaiheet 1 & 2 FastAPI-rajapinnaksi, joka palauttaa pyydetylle BBOX-alueelle lasketun overlay-kuvan.
   * Toteutettu tiili-ID-pohjaisena (ei vapaamuotoisena BBOX-leikkauksena), koska Vaihe 1:n suunnittelupäätöksenä overlay lasketaan aina koko DEM-tiilelle kerralla ja välimuistetaan levylle (`output/cache/`):
     - `GET /api/tiles` — kaikki saatavilla olevat tiilet + EPSG:3067-rajat (peruskartan omalla ruudukolla)
     - `GET /api/viewport?min_x=&min_y=&max_x=&max_y=` — palauttaa näkymään osuvat tiili-ID:t (frontend kutsuu tätä pan/zoomin yhteydessä), rajat metreinä EPSG:3067:ssä
     - `GET /api/overlay/{tile_id}.png` — RGBA-overlay, läpinäkyvä puskurivyöhykkeen ulkopuolella, lasketaan/välimuistetaan tarvittaessa (~2s kylmänä, ~2ms välimuistista)
     - `GET /api/overlay/{tile_id}/meta` — EPSG:3067-rajat + tunnusluvut (rakennusmäärä, kallio-%, jne.)
   * `backend/tiles.py` yhdistää DEM- ja karttakuva-tiilet koordinaattien perusteella (ks. kohta 2).
   * `backend/pipeline.py` laskee kokonaispisteen $S$ (kaikki neljä komponenttia) peruskartan omalla 1m/px-ruudukolla — DEM-pohjaiset jatkuvat kentät (jyrkkyys, etäisyys, korkeus) resamploidaan omalta 2m/px-ruudukoltaan tähän ruudukkoon (`rasterio.warp.reproject` saman CRS:n sisällä), kallio/rantaviiva/suo-maskit ovat jo natiivisti sillä. Overlay rajataan näkyväksi vain rantaviivan 5–15m puskurivyöhykkeelle maalla (DEM > 0m = maa).
   * **Havaittu ja korjattu ongelma**: rantaviiva-väri (`#0080ff`) piirretään peruskartalla sekä merenrannalle että sisämaan puroille/ojille, joten sovellus ehdotti virheellisesti rantautumista myös purojen varsilta. Korjattu `compute_sea_mask`-funktiolla (`backend/pipeline.py`): peruskartan vesialueen täyttöväri (`WATER_FILL_HSV_*`, eri väri kuin rantaviiva-linja) ryhmitellään yhtenäisiksi alueiksi, ja vain riittävän suuret (`SEA_MIN_AREA_M2 = 50 ha`) alueet lasketaan mereksi — purot/lammet/pienet järvet jäävät tämän kynnyksen alle. Ennen ryhmittelyä täyttömaskille tehdään morphological closing (`SEA_CLOSING_RADIUS_M = 10 m`), koska tiet/laivaväylät yms. symbolit voivat muuten katkaista täytön värin kapeista salmista/lahdista ja pilkkoa aidosti merellisen alueen virheellisesti pieniksi paloiksi (havaittu esimerkki: tie katkaisi 18 ha:n suojaisan lahden yhteyden avomereen). Rantaviiva-maski suodatetaan lopuksi niin, että vain riittävän lähellä (`SEA_ADJACENCY_M = 5 m`) merta olevat pikselit säilyvät. Kalibroitu ja validoitu 11 tiilen aineistosta (ks. myös kohdan 2 väritunnistuksen kalibrointitapa).
   * Käynnistys: `uvicorn backend.api:app --reload`

4. **Vaihe 4: Frontend & Visualisointi** — VALMIS (`frontend/index.html`)
   * Rakenna yksinkertainen `index.html`-sivu Leaflet.js-kartalla, joka visualisoi laskentatulokset suoraan kartalle.
   * Taustakartaksi käytetään paikallista MML-karttakuvaa (`/api/basemap/{tile_id}.png`), ei ulkoista WMTS-palvelua — toimii täysin offline paitsi Leaflet-kirjaston CDN-lataus.
   * **Tärkeä löydös (historiallinen)**: alun perin overlay-kuvat reprojisoitiin geodeettisesti WGS84:ään (EPSG:4326), koska Leafletin oletus-CRS olettaa lat/lng-koordinaatteja. Pelkkä kahden kulman koordinaattimuunnos (TM35FIN → WGS84) ei riittänyt tiilien rajaamiseen — TM35FIN-ruudukko ei ole linjassa todellisen pohjois-etelä-suunnan kanssa n. 330 km päässä keskimeridiaanista (27°E), joten naapuritiilten reunat eivät täsmänneet (näkyviä rakoja/limityksiä). Korjattiin tuolloin reprojisoimalla koko rasteri EPSG:4326-pikseliruudukolle.
   * **Myöhempi arkkitehtuurimuutos**: geodeettinen WGS84-reprojisointi poistettiin kokonaan. Peruskartta pysyy nyt muuttumattomana natiivissa EPSG:3067:ssään (ei kiertoa, ks. kohta 4:n edellinen löydös — TM35FIN-ruudukko itsessään EI ole vino suhteessa omaan ruudukkoonsa, vain suhteessa WGS84:n lat/lng-suuntiin), ja Leaflet on konfiguroitu käyttämään `L.CRS.Simple`-CRS:ää: EPSG:3067-metrit syötetään suoraan lat/lng-pareina (pohjoinen=lat, itä=lng). Tämä on pikselintarkka ja saumaton täsmälleen samasta syystä kuin WGS84-reprojisointi oli — kaikki kerrokset ovat samalla ruudukolla — mutta ilman minkäänlaista resamplausta tai ylimääräistä riippuvuutta (ei proj4/proj4leaflet, pelkkä ydin-Leaflet). Basemap-kuvien tiedostokoko kasvoi merkittävästi (~4x), koska ne eivät enää ole downsampletut ~3m/px-näyttöresoluutioon vaan säilyttävät peruskartan täyden 1m/px-tarkkuuden.
   * Palvelin (`uvicorn backend.api:app`) tarjoilee sekä API:n että staattisen frontendin samasta originesta (`app.mount("/", StaticFiles(...))`), joten CORS ei ole ongelma.
   * Testattu headless Chromella (`--headless --screenshot`): kaikki 11 tiiliä muodostavat saumattoman kartan, pistemääräkerros näkyy oikein rantaviivan puskurivyöhykkeellä.
   * "Parhaat rantautumispaikat (top 7%)" -kerros (`/api/overlay/{id}/top.png`, magenta) on oma togglettava `L.layerGroup`, **ladataan laiskasti** (vasta kun käyttäjä ruksii sen näkyviin `overlayadd`-tapahtumalla) — koska ensimmäinen pyyntö laskee globaalin kynnysarvon kaikista tiilistä ja on siksi hidas (~20s kylmänä).
   * **Moniresoluutioinen nelitasoinen kuvasto** (`detail`/`near`/`mid`/`overview`, ks. `backend/pipeline.py: LEVEL_FACTORS`, kertoimet 1/2/4/16) lisättiin ratkaisemaan hidas alkulataus: peruskartan täysi 1m/px-tarkkuus (`detail`, ~103 MB/11 tiiltä) ladattiin aina kokonaan riippumatta zoomaustasosta. `near` (2m/px, ~41 MB), `mid` (4m/px, ~15 MB) ja `overview` (16m/px, ~1.6 MB) ovat kevyita lohkokeskiarvo-downsamplauksia (`cv2.INTER_AREA`) samoista kuvista, tiedostoina `{tile}_near.png`/`{tile}_mid.png`/`{tile}_overview.png` (vastaavasti basemap/top). Frontend valitsee tason `zoomend`-tapahtumalla (`L.CRS.Simple`: resoluutio zoomilla z on 2^-z m/px; kynnykset puolivälissä perättäisten tasojen natiiviresoluutiota vastaavien zoomien välissä — overview z=-4, mid z=-2, near z=-1, detail z=0) ja lataa **vain näkymässä olevien** tiilien kuvat kyseisellä tasolla (`moveend`, bbox-leikkaus `bounds_epsg3067`:aa vasten) — ei kaikkia 11:tä tiiltä kerralla, paitsi silloin kun ne kaikki sattuvat olemaan näkymässä.
   * **Puskurin/top-korostuksen paksunnus on sama pikselisäde jokaisella tasolla**, ei metrisäde — muuten ohut rantaviiva-indikaattori häviäisi karkeammalla resoluutiolla resize-pehmennyksessä. Tästä syystä `near`/`mid`/`overview`-tasot lasketaan raa'asta pistemäärästä/puskurimaskista uudelleen (downsample + dilataatio), ei suoraan valmiin `detail`-kuvan pienentämällä.
   * **Oletusnäkymä avautuu n. 50 % lähempää kuin "koko aineisto näkyvissä" -taso**: `init()` laskee `map.getBoundsZoom()`-arvon (sama zoom jonka `fitBounds` olisi valinnut) ja lisää siihen yhden zoom-askeleen (`L.CRS.Simple`: 2x resoluutio per askel = puolet leveydestä näkyvissä), keskitettynä koko aineiston keskipisteeseen.
   * **Kokeiltu ja hylätty: "oikea" XYZ-tiilipyramidi.** Nelitasoinen kokonaiskuva-kuvasto korvattiin kertaalleen todellisella nelipuu-tiilipyramidilla (kaikki 11 tiiltä yhdeksi mosaiikiksi, pilkottuna 512×512 px -ruutuihin standardin z/x/y-osoitteistuksella, `L.TileLayer`) — arkkitehtuurisesti "oikeampi" ja tarkemmalla zoomilla kevyempi, mutta osoittautui **käytännössä epävakaammaksi**: kartta pätki ja ruutuja jäi satunnaisesti lataamatta/piirtämättä. Käyttäjän arvion mukaan aiempi nelitasoinen kokonaiskuva-lähestymistapa oli robustimpi, joten se **palautettiin** (`git checkout` edelliseen committiin, koska XYZ-työ ei ollut vielä committoitu). Rantaviivan paksuuden esiasetusominaisuus (ks. yllä) toteutettiin sen sijaan tämän robustimman arkkitehtuurin päälle.

5. **Vaihe 5: Täysin staattinen julkaisu (GitHub Pages)** — VALMIS (`build_static.py`)
   * Koska lähdedata ei muutu ajossa, koko laskenta voidaan ajaa kertaalleen build-vaiheessa ja tuotanto voi olla pelkkiä staattisia tiedostoja — ei Pythonia, GDAL:ia eikä palvelinta tuotannossa.
   * `python3 build_static.py` (ajetaan projektin juuresta): käy läpi `backend/pipeline.py`:n kautta kaikki tiilet, kaikki neljä resoluutiotasoa (ks. kohta 4:n moniresoluutio-löydös), kaikki viisi paksuusesiasetusta (`THICKNESS_PRESETS`) JA (top-kerrokselle) kaikki kymmenen "parhaat rannat" -esiasetusta (`TOP_PERCENT_PRESETS`), kirjoittaa `docs/cache/{tile}{level}_t{thickness}.png` (overlay, ei riipu %-arvosta), `docs/cache/{tile}_top{level}_t{thickness}_p{percent}.png` (top, riippuu molemmista) ja `docs/cache/{tile}_base{level}.png` (kertaalleen per taso, ei riipu kummastakaan) sekä `docs/tiles.json` (tiilirajat + esiasetukset), ja generoi `docs/index.html`:n `frontend/index.html`:sta korvaamalla `/api/...`-polut suhteellisilla staattisilla poluilla (`URL_REPLACEMENTS`-taulukko skriptissä).
   * **Kansion nimi on `docs/`, ei `dist/`**: GitHub Pagesin "Deploy from a branch" -tila tukee lähdekansioksi vain juurta (`/`) tai nimenomaan `/docs`-kansiota — ei mielivaltaisia nimiä. Tämä huomattiin vasta kun `/dist`-vaihtoehtoa ei löytynyt Pagesin asetussivulta.
   * **Suhteelliset polut ovat pakollisia**: GitHub Pages -projektisivut palvelevat osoitteesta `https://kayttaja.github.io/repo/`, ei domainin juuresta — jos poluissa olisi alkava `/`, kuvat ja `tiles.json` eivät latautuisi. Testattu toimivaksi alipolusta tavallisella `python3 -m http.server`:lla.
   * `/api/viewport`-endpointtia ei tarvinnut siirtää staattiseksi, koska frontend ei käyttänyt sitä koskaan (lataa kaikki 11 tiiltä suoraan alusta alkaen).
   * Lopputulos: `docs/` on ~600 MB, 2464 tiedostoa (11 tiiltä × 4 tasoa × (1 basemap + 5 overlay-paksuutta + 5 paksuutta × 10 %-esiasetusta top-kerrokselle = 50)), itsenäinen kokonaisuus. `.nojekyll`-tiedosto mukana estämässä GitHub Pagesin Jekyll-käsittelyä. Build kestää n. 6–7 min.
   * `docs/` tyhjennetään ja luodaan uudelleen joka ajolla (`shutil.rmtree`) — ei kasita hand-editoitavaksi.