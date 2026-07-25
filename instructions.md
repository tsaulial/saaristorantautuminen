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
* Näkyvyyssyistä puskurivyöhyke **piirretään** visuaalisesti paksunnettuna (dilataatio, `backend/pipeline.py: BUFFER_VISUAL_DILATION_PX`) — todellinen 5–15 m -data pysyy tarkkana, vain rendattu kuva levennetään.

### E. Suo ($S$-rangaistus) — lisätty jälkikäteen
* Suodatetaan rasterikarttakuvasta keltainen suomerkintä (`#ffff40`), HSV-ikkuna (H 29–31, S 180–255, V 250–255) + morphological closing samaan tapaan kuin kallio.
* Suo on huono rantautumispaikka: pikselin **lopullinen** $S$ (kaikki kolme komponenttia A–C jo laskettuna) kerrotaan rangaistuskertoimella `SWAMP_PENALTY_FACTOR = 0.5` (`backend/pipeline.py`) — painoja A–C ei muuteta.
* "Parhaat rantautumispaikat" -kerros (ks. kohta 4, Frontend) korostaa pisteet, jotka ovat **koko aineiston** (kaikkien tiilien) puskurivyöhykkeen pisteiden 80. persentiilissä tai yli (`TOP_PERCENTILE = 80`, `backend/pipeline.py: compute_global_threshold`). Tämä on globaali, ei per-tiili, kynnysarvo — ensimmäinen `/api/overlay/{id}/top.png`-pyyntö laskee kaikkien tiilien raa'an pistemäärän jos sitä ei ole vielä välimuistissa (~2s/tiili, kertaluontoinen).

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
  5. Muuntaa overlay-kuvan rajat TM35FIN:stä (EPSG:3067) WGS84:ään (`pyproj`) ennen palautusta, jotta Leafletin `ImageOverlay` voi piirtää sen oikein.
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
     - `GET /api/tiles` — kaikki saatavilla olevat tiilet + WGS84-rajat
     - `GET /api/viewport?min_lon=&min_lat=&max_lon=&max_lat=` — palauttaa näkymään osuvat tiili-ID:t (frontend kutsuu tätä pan/zoomin yhteydessä)
     - `GET /api/overlay/{tile_id}.png` — RGBA-overlay, läpinäkyvä puskurivyöhykkeen ulkopuolella, lasketaan/välimuistetaan tarvittaessa (~2s kylmänä, ~2ms välimuistista)
     - `GET /api/overlay/{tile_id}/meta` — WGS84-rajat + tunnusluvut (rakennusmäärä, kallio-%, jne.)
   * `backend/tiles.py` yhdistää DEM- ja karttakuva-tiilet koordinaattien perusteella (ks. kohta 2).
   * `backend/pipeline.py` laskee kokonaispisteen $S$ (kaikki neljä komponenttia), resamplaa kallio/rantaviiva-maskit 1m/px → 2m/px enemmistöpäätöksellä, ja rajaa overlayn näkyväksi vain rantaviivan 5–15m puskurivyöhykkeelle maalla (DEM > 0m = maa).
   * Käynnistys: `uvicorn backend.api:app --reload`

4. **Vaihe 4: Frontend & Visualisointi** — VALMIS (`frontend/index.html`)
   * Rakenna yksinkertainen `index.html`-sivu Leaflet.js-kartalla, joka visualisoi laskentatulokset suoraan kartalle.
   * Taustakartaksi käytetään paikallista MML-karttakuvaa (`/api/basemap/{tile_id}.png`), ei ulkoista WMTS-palvelua — toimii täysin offline paitsi Leaflet-kirjaston CDN-lataus.
   * **Tärkeä löydös**: pelkkä kahden kulman koordinaattimuunnos (TM35FIN → WGS84) ei riitä tiilien rajaamiseen — TM35FIN-ruudukko ei ole linjassa todellisen pohjois-etelä-suunnan kanssa n. 330 km päässä keskimeridiaanista (27°E), joten naapuritiilten reunat eivät täsmänneet Leafletissa (näkyviä rakoja/limityksiä). Korjattu reprojisoimalla koko rasteri (basemap + overlay) EPSG:4326-pikseliruudukolle (`rasterio.warp.reproject`) ennen PNG-enkoodausta, jolloin vierekkäiset tiilet asettuvat saumattomasti. Basemapin reprojektion ulkopuolelle jäävä alue (rotatoidun tiilen bounds-suorakulmion kulmat) tehdään läpinäkyväksi peittomaskilla, ei mustaksi.
   * Palvelin (`uvicorn backend.api:app`) tarjoilee sekä API:n että staattisen frontendin samasta originesta (`app.mount("/", StaticFiles(...))`), joten CORS ei ole ongelma.
   * Testattu headless Chromella (`--headless --screenshot`): kaikki 11 tiiliä muodostavat saumattoman kartan, pistemääräkerros näkyy oikein rantaviivan puskurivyöhykkeellä.
   * "Parhaat rantautumispaikat (top 15%)" -kerros (`/api/overlay/{id}/top.png`, magenta) on oma togglettava `L.layerGroup`, **ladataan laiskasti** (vasta kun käyttäjä ruksii sen näkyviin `overlayadd`-tapahtumalla) — koska ensimmäinen pyyntö laskee globaalin kynnysarvon kaikista tiilistä ja on siksi hidas (~20s kylmänä).

5. **Vaihe 5: Täysin staattinen julkaisu (GitHub Pages)** — VALMIS (`build_static.py`)
   * Koska lähdedata ei muutu ajossa, koko laskenta voidaan ajaa kertaalleen build-vaiheessa ja tuotanto voi olla pelkkiä staattisia tiedostoja — ei Pythonia, GDAL:ia eikä palvelinta tuotannossa.
   * `python3 build_static.py` (ajetaan projektin juuresta): käy läpi `backend/pipeline.py`:n kautta kaikki tiilet, kirjoittaa `dist/cache/{tile}.png` (+ `_base.png`, `_top.png`) ja `dist/tiles.json` (tiilirajat + top-15 %-kynnysarvo), ja generoi `dist/index.html`:n `frontend/index.html`:sta korvaamalla `/api/...`-polut suhteellisilla staattisilla poluilla (`URL_REPLACEMENTS`-taulukko skriptissä).
   * **Suhteelliset polut ovat pakollisia**: GitHub Pages -projektisivut palvelevat osoitteesta `https://kayttaja.github.io/repo/`, ei domainin juuresta — jos poluissa olisi alkava `/`, kuvat ja `tiles.json` eivät latautuisi. Testattu toimivaksi alipolusta (`/dist/`) tavallisella `python3 -m http.server`:lla.
   * `/api/viewport`-endpointtia ei tarvinnut siirtää staattiseksi, koska frontend ei käyttänyt sitä koskaan (lataa kaikki 11 tiiltä suoraan alusta alkaen).
   * Lopputulos: `dist/` on ~26 MB, 36 tiedostoa, itsenäinen kokonaisuus. `.nojekyll`-tiedosto mukana estämässä GitHub Pagesin Jekyll-käsittelyä.
   * `dist/` tyhjennetään ja luodaan uudelleen joka ajolla (`shutil.rmtree`) — ei kasita hand-editoitavaksi.