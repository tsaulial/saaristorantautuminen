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
   * **Käyttäjän valittavat pisteytystekijät (`Maaston jyrkkyys` / `Suo` / `Kallio` / `Rakennukset`) omalla asetussivullaan (`frontend/settings.html` → `docs/settings.html`).** Tekijävalinta **ei ole toteutettavissa VANHALLA esilasketulla arkkitehtuurilla lainkaan**: 4 valintaruutua = 15 eri pisteytystä, jotka kertautuisivat paksuuden (5) ja prosentin (10) kanssa n. 37 000 tiedostoksi (~9 GB). Ratkaisuksi valittiin **osatekijöiden koodaaminen kuvakanaviin ja pistemäärän kokoaminen vasta selaimessa** (alla) — datamäärä ei tällöin riipu lainkaan valintojen määrästä, eli viides tekijä ei kasvattaisi sitä ollenkaan (sama peruste kuin kohdan 6 vektoritiiliharkinnassa).
     - **Kaksi kuvaa per tiili** (`backend/pipeline.py: get_or_compute_factor_png`, `docs/cache/{tile}_factors.png` + `_tiebreak.png`, yhteensä ~5 MB koko aineistolle). Kanavat: `factors`-kuvassa **R**=jyrkkyyspisteet, **G**=etäisyyspisteet rakennuksiin, **B**=bittikenttä (bitti 0 = kallio, bitti 1 = suo), **A**=puskurivyöhyke-peittomaski; `tiebreak`-kuvassa **R**=tasapelinpurku globaaliksi järjestysluvuksi kvantisoituna.
     - **Miksi tasapelinpurku on omassa kuvassaan**: selaimen Canvas säilöö pikselit **alfalla esikerrottuina**, joten `A<255` pyöristää RGB-arvoja ja `A=0` nollaa ne kokonaan `getImageData`:ssa. `factors`-kuvassa tämä ei haittaa (A=0 vain puskurin ulkopuolella, jota ei lueta), mutta tasapelinpurku tarvitaan tarkkana — siksi oma kuva, jossa alfa on 255 kaikkialla. Kanavia olisi muuten riittänyt yhteenkin kuvaan, mutta vain kuutta bittiä tarkkuutta uhraamalla.
     - **Painojen uudelleennormalisointi**: valitsematta jäänyt tekijä ei saa painoa 0 vaan poistuu kokonaan, ja jäljelle jäävät painot suhteutetaan summaksi 1 (`score_from_components` / `scoreFromComponents`). Muuten esim. pelkkä jyrkkyys tuottaisi korkeintaan 0,50 pistettä ja koko kartta näyttäisi punaiselta. Suo on kertova rangaistus eikä painotettu termi; jos VAIN suo on valittuna, pohjapistemäärä on 1,0. Vähintään yksi tekijä on pakko olla valittuna (asetussivu estää viimeisen poistamisen, ja kartta hylkää tyhjän valinnan myös suoraan `localStorage`ia muokattaessa).
     - **"Parhaat X %" on pakko esilaskea backendissä** (`compute_factor_thresholds` → `docs/factor_thresholds.json`): kynnys on GLOBAALI koko aineiston yli, eikä selain näe kerrallaan kuin näkymässä olevat tiilet. Kynnykset lasketaan kaikille 15 tekijäyhdistelmälle × 10 prosenttiesiasetusta (150 lukua) **tasan samoista 8-bittisistä arvoista jotka selain lukee kuvista** — ei natiiveista liukuluvuista — jotta kynnys ja näytetty kuva vastaavat toisiaan pikselilleen. `score_from_components` (Python) ja `scoreFromComponents` (JS) muodostavat tämän sopimuksen kaksi puolta ja on pidettävä laskujärjestystä myöten samoina.
     - **Asetussivu on oma sivunsa, ei modaali** (käyttäjän pyyntö), joten arvot kulkevat kartan ja sen välillä `localStorage`-avaimessa `rantautuminen-asetukset`. Kartta lukee ne uudelleen `pageshow`-tapahtumassa — ei `load`-tapahtumassa — koska selaimen takaisin-nappi palauttaa sivun bfcachesta ajamatta skriptiä uudelleen. Paksuus- ja prosenttiliukusäätimet **siirrettiin kartalta asetussivulle**; kartan legendaan jäi tilalle tiivis yhteenvetorivi (`Tekijät: … · paksuus … px · parhaat … %`) ja linkki asetuksiin.
     - **"Kärkipaikat" — kolmas kerros, joka vaatii KOKO rantakaistaleen olevan hyvä.** Nykyinen "Parhaat rantautumispaikat" valitsee parhaat X % **pikseli kerrallaan**, jolloin loiva kohta aivan vesirajassa saa täydet pisteet vaikka 8 m sisämaahan olisi kelvoton kallio — eikä paikkaan käytännössä pysty rantautumaan. Lisäksi valituksi tuli muutaman metrin siivuja. Kärkipaikat ratkaisee molemmat kaksivaiheisella aggregoinnilla (`compute_prime_components`).
     - **Poikkileikkausryhmittely ilman uutta geometriaa**: `compute_shoreline_buffer` laski jo etäisyysmuunnoksen rantaviivaan mutta heitti sen pois. Sama muunnos `return_indices=True`-lipulla antaa jokaiselle pikselille **lähimmän rantaviivapikselin indeksin**, eli valmiin ryhmittelyn poikkileikkauksiin. Vaihe 1: kullekin poikkileikkaukselle sen arvojen **alin kymmenys** koko `PRIME_ZONE_MAX_M = 30` m:iin asti. Vaihe 2: **minimi ±`PRIME_ALONGSHORE_RADIUS_M` = 10 m** ikkunassa rantaviivan suunnassa, jolloin kelpuutettu kohta vaatii ~20 m yhtäjaksoisen hyvän jakson ympärilleen.
     - **Alin kymmenys eikä tiukka minimi**: korkeusmalli on 2 m ruudukolta resamploitu 1 m:iin, joten yksittäisiä virheellisen jyrkkiä pikseleitä esiintyy. Minimi antaisi niiden pudottaa muuten moitteettomia paikkoja.
     - **Aggregoidaan OSATEKIJÄT, ei pistemäärää** — samasta syystä kuin tekijävalinnassa: pistemäärä riippuu 15 tekijäyhdistelmästä, joten valmiiksi laskettuna se vaatisi 15 muunnelmaa. Osatekijät ovat valinnasta riippumattomia, ja selain kokoaa niistä pistemäärän **samalla `scoreFromComponents`-funktiolla** kuin muutkin kerrokset. Kallio ja suo ovat binaarisia ja noudattavat samaa sääntöä: kallio kelpaa jos ≥90 % kaistaleesta on kalliota, suo haittaa jos ≥10 % on suota (suo käsitellään käännettynä "ei suota" -hyvyytenä, jolloin molempiin käy sama persentiili ja sama minimi).
     - **Arviointi 5–30 m, näyttö 5–15 m**: laajempi vyöhyke on vain arviointia varten; tulos piirretään nykyiselle puskurivyöhykkeelle, jotta kerros asettuu tarkalleen muiden päälle. Nykyinen puskurivyöhyke (6,12 km²), jakaumakuvaaja ja 700 km:n arvio säilyvät koskemattomina.
     - **Mitattu vaikutus** (koko aineisto, kaikki tekijät, top 7 %): kärkipaikat valitsee **saman 7 %** rantaviivasta mutta **eri kohdat** — nykyisestä top 7 %:sta säilyy vain **43 %**. Laikkujen määrä putoaa **7581 → 2330**, mediaanikoko nousee **24 m² → 110 m²** (4,6-kertaiseksi) ja alle 100 m²:n siivujen osuus laskee **81,6 % → 48,7 %**. Sama määrä rantaviivaa siis, mutta harvempina ja selvästi suurempina yhtenäisinä kohtina.
     - **Mitattu ja tietoisesti hyväksytty rajoitus — kapea salmi.** ±10 m ikkuna on 2D-kiekko, joten alle 20 m leveässä salmessa se ulottuu vastarannalle ja ottaa huonomman rannan arvon. Altistus on **12 % rantapikseleistä** (vaihteluväli 5,6–15,2 % tiileittäin), mikä vaikutti paljolta, joten vaikutus mitattiin vertaamalla nykyistä versiota sellaiseen jossa ikkuna on rajattu samaan rantakomponenttiin (`scipy.ndimage.label` + `find_objects`-rajauslaatikot): jyrkkyysarvo muuttuisi **1,75 %:lla** puskuriruuduista, ja top 7 %:sta muuttuisi **1,04 %** — **521 ruutua tulisi mukaan, 0 jäisi pois**. Vaikutus on siis pieni ja **yksinomaan konservatiivinen**: vuoto voi ainoastaan hylätä kelvollisia paikkoja, ei koskaan hyväksyä kelvottomia. Korjaus vaatisi komponenttikohtaisen minimisuodatuksen, joka on moninkertaisesti raskaampi kuin 1 %:n hyöty, joten sitä **ei toteutettu**.
     - **Asetussivun jakaumakuvaajassa on kärkipaikkojen käyrä** (violetti porrasviiva, `prime_histograms_m`) tavallisten pylväiden päällä, ja jokaisessa luokkakortissa vastaava kilometrimäärä. Kärkipaikkojen jakauma lasketaan **samalla natiiviruudukolla** (`compute_prime_components(native=True)`) kuin tavallinen, jotta käyrät ovat vertailukelpoisia — muuten toinen olisi 1 m:n ja toinen 3,5 m:n ruudukolta. Käyrä on siirtynyt vasemmalle, koska jokainen kohta saa koko kaistaleensa heikoimman arvon.
     - **Raakavälimuistiin lisättiin `shoreline_mask` ja `land_mask`** (molemmat boolean ⇒ pakkautuvat hyvin; DEM:iä kokonaisuudessaan ei tallenneta). Versiotarkistus osoittaa nyt `shoreline_mask`-avaimeen ja osoittaa aina VIIMEKSI lisättyyn avaimeen.
     - **Kärkipaikkojen kuva ja kynnykset ladataan vasta kun kerros valitaan** (`loadPrimeImage`, `ensurePrimeThresholds`) — suurin osa käyttäjistä ei avaa kerrosta lainkaan. VANHA toteutus lukittuu pois myös tässä tilassa, koska esilaskettuja kuvia ei ole.
     - **Suojaisuus viidentenä tekijänä (`FACTOR_SHELTER = 16`, `ALL_FACTORS_MASK` 15 → 31).** Aallokon suuruus rannassa riippuu pyyhkäisymatkasta (fetch) eli siitä kuinka pitkältä avovedeltä tuuli ehtii sitä kasvattaa. Lasketaan jokaiselle rantaruudulle 12 ilmansuuntaan (30° sektorit). **Oletuksena POIS PÄÄLTÄ** — oletustilassa sovellus käyttäytyy tasan kuten ennen eikä ota yhteyttä mihinkään.
     - **Fetch lasketaan kaikkien tiilien yli kerralla**, ei tiili kerrallaan: säde voi kulkea naapuritiilen läpi. Merimaskeista kootaan yksi 10 m mosaiikki (1800×3000). Mosaiikki lasketaan **suoraan karttarasterista** (`get_or_compute_sea_mosaic`), ei raakavälimuistin kautta — se ei tarvitse DEM:iä eikä rakennuksia, joten raakavälimuistin versiota ei tarvinnut nostaa. Tämä poikkesi suunnitelmasta ja säästi 15 min täyden uudelleenlaskennan.
     - **Aukot ja reunat = tuntematon = altis.** Tiilistö on 3×5 ruudukko josta 4 puuttuu. Säde joka poistuu aineistosta juoksee `MAX_FETCH_M`-kattoon ja ranta merkitään alttiiksi — emme väitä paikkaa suojaisaksi silloin kun emme tiedä.
     - **Kaksi bugia, jotka vain visuaalinen tarkistus paljasti.** (1) Säde lähti puskuriruudusta joka on **maalla** 5–15 m rantaviivasta sisämaahan, joten se osui maahan heti ensimmäisellä askeleella ja *jokainen* ranta näytti suojaisalta joka suunnasta. Korjattu kaksivaiheisella etenemisellä: ensin veteen (enintään `MAX_INITIAL_LAND_STEPS`), sitten mitataan yhtenäinen avovesijakso. (2) Säteet jotka eivät koskaan osuneet maahan jäivät oletusarvoon "suojassa" vaikka juuri ne ovat kaikkein alttiimpia — koko aineistossa ei ollut yhtään kattoon osunutta sädettä. Kummankin bugin tilastot näyttivät uskottavilta; vasta kartalle piirretty fetch paljasti ne.
     - **Pakkaus**: 12 sektoria à 4 bittiä = 6 tavua, jotka mahtuvat tasan kahden kuvan RGB-kanaviin (`{tile}_fetcha.png`/`_fetchb.png`, alfa 255 kaikkialla — esikerrotun alfan takia, sama syy kuin tasapelinpurussa). Kvantisointi on **logaritminen**, koska aallonkorkeus kasvaa fetchin neliöjuuressa.
     - **Fetch-tasojen metrimäärät ovat KIINTEÄ TAULUKKO** (`FETCH_LEVEL_METRES`), ei exp/log-lasku: numpy ja selaimen JS käyttävät eri matematiikkakirjastoja eivätkä `exp`/`log` ole niissä taatusti bitilleen samoja. Taulukko + `sqrt` (IEEE-tarkka molemmissa) pitää pistemäärät identtisinä, mikä on koko kynnysarvojärjestelmän ehto.
     - **Tuulen suunta pyöristetään sektoriin ja nopeus viiteen luokkaan — ei interpoloida.** Interpolointi tuottaisi pistemääriä joita esilasketut kynnykset eivät vastaa, jolloin "parhaat 7 %" ei enää olisi 7 %. Yhden havaintoaseman tuulessa on joka tapauksessa enemmän epävarmuutta kuin sektorin 30°.
     - **Tuulesta riippuvat kynnykset** (`compute_shelter_thresholds` → `shelter_thresholds.json`, 493 kt): maski × sektori × nopeusluokka × prosentti, sekä tavalliselle että kärkipaikkakerrokselle. **Ladataan vasta kun tekijä otetaan käyttöön.**
     - **Todennettu**: kynnysten poikkeama ≤ 0,10 pp; Python ↔ JS bitilleen identtinen 46 500 tapauksessa (31 maskia × 3 tuuliyhdistelmää); vaikuttavuus — tyynellä (2 m/s) 98,1 % tuulettomista kärkipaikoista säilyy, kovalla etelätuulella (14 m/s) enää 70,3 %; selaimessa kolmasosa korostetuista paikoista vaihtuu kun tuuli kääntyy etelästä pohjoiseen.
     - **Parametrit kiristettiin mittausten perusteella** (0,10–0,60 m → **0,05–0,30 m**, paino 0,40 → **0,70**). Alkuperäisillä arvoilla tekijä ei käytännössä erotellut mitään, ja kolme syytä kertautui: (1) `SHELTER_ROUGH_M = 0,60 m` oli liian lepsu — pyyhkäisymatkan mediaani pahimmassa suunnassa on 2 241 m, mistä 14 m/s tuottaa 0,34 m aallokon eli pistemäärän 0,52, joten myrskyssäkin puolet saaristosta sai yli puolet pisteistä ja vain 8,2 % jäi alle 0,5:n; (2) tekijän lisääminen **NOSTI** kokonaispisteen keskiarvoa (+0,031 vielä 14 m/s:ssä), koska suojaisuuden keskiarvo oli korkeampi kuin muiden tekijöiden ja painojen normalisointi veti pistettä ylöspäin — suojaisuus päälle teki kartasta vihreämmän, ei punaisemman; (3) paino 0,40 antoi vain 28,6 %:n osuuden viiden tekijän kesken. Uusilla arvoilla 14 m/s:ssä: vaikutuksen hajonta **0,094 → 0,168**, alle 0,5:n jäävien osuus **8,2 % → 19,0 %**, keskimääräinen muutos **+0,031 → +0,007** (käytännössä neutraali), painoarvo **41,2 %**. Kärkipaikkoihin: tyynellä säilyy 88,8 % (ennen 98,1 %), kovalla etelätuulella **50,9 % (ennen 70,3 %)**. Kynnysten poikkeama parani 0,103 → 0,051 pp ja Python ↔ JS pysyi bitilleen identtisenä. Osa pienuudesta oli fysikaalisesti oikein eikä sitä pidäkään poistaa: 23,7 % rantaruuduista on alle 500 m pyyhkäisymatkan päässä pahimmassakin suunnassa.
     - **Kynnys irrotettiin tuulesta: lasketaan TYYNESSÄ, tuuli vain karsii.** Aiemmin kynnys laskettiin erikseen jokaiselle tuulisuunnalle ja -nopeudelle, jolloin valittuja oli **aina tasan X %** säästä riippumatta. Valinta kyllä kiristyi oikein — 14 m/s:ssä valittujen pyyhkäisymatkan mediaani oli 50 m eli pienin mahdollinen ja suurin aallokko 11 cm — mutta koska MÄÄRÄ ei muuttunut, kartta näytti yhtä täydeltä myrskyssä kuin tyynellä eikä tuulen voimakkuus näkynyt käyttäjälle lainkaan (käyttäjän havainto testissä). Nyt kynnys lasketaan kerran tuulennopeudella 0, jolloin "parhaat X %" tarkoittaa *"paikat jotka olisivat parhaan X %:n joukossa tyynellä"* ja tuuli voi vain pudottaa paikkoja pois. Asetuksella 7 %: tyynellä 7,0 %, 7,5 m/s etelätuulessa 4,1 %, 14 m/s 3,1 %, 18 m/s 1,5 % — ja suunta vaikuttaa (14 m/s kaakosta 2,5 %, pohjoisesta 4,1 %). Selaimessa mitattuna korostettuja pikseleitä 3402 → 2251 → 1310 tuulen noustessa 1 → 7,5 → 16 m/s.
     - **Kaksi seurausta.** (1) `shelter_thresholds.json` kutistui **493 kt → 8,2 kt**, koska sektori- ja nopeusulottuvuutta ei enää tarvita. (2) **30°:n pyöristys poistettiin**: se oli olemassa vain jotta esilasketut kynnykset vastaisivat näytettyä pistemäärää, joten tuulesta riippumattoman kynnyksen myötä selain voi interpoloida pyyhkäisymatkan kahden sektorin välillä (`fetch_at_bearing` / `fetchWeights`) ja käyttää tarkkaa tuulennopeutta. Tuulisäätimestä tuli portaaton (suunta 5°, nopeus 0,5 m/s). Python ↔ JS pysyi bitilleen identtisenä 46 500 tapauksessa myös interpoloiduilla arvoilla — `score_from_components` sai `fetch_m`-parametrin, joka on selaimen käyttämä polku.
     - **Este ei ole vain este: tehollinen pyyhkäisymatka ja esteen korkeus.** Käyttäjän havainto: *matala luoto katkaisee aallon mutta ei tuulta*. Malli kohteli kaikkea maata samanlaisena esteenä ja oletti täyden tuulen kaikkialla. Mitattuna esteistä 22,6 % on alle 2 m ja mediaani 4,1 m — mutta vain kun korkeus luetaan **osuman takaa** (100 m matkalta): pelkän osumapisteen korkeus antoi mediaaniksi 1,1 m, koska rantaviiva itsessään on aina matala. Tämä oli mittausvirhe joka olisi johtanut väärään johtopäätökseen.
     - **Aallon läpäisy osoittautui vääräksi selitykseksi.** Ehdotin ensin ylilyöntiä matalan luodon yli, mutta vakiintuneen läpäisykaavan mukaan jo 0,9 m luoto pysäyttää 0,6 m aallon lähes kokonaan. Todellinen syy on **geometrinen**: 30 m leveä luoto 200 m päässä peittää vain noin 8° kulmasektorista, ja aallokko saapuu sen ohi ja taitse. Korjaus on siksi **Savillen tehollinen pyyhkäisymatka** (13 sädettä ±45°, `F_eff = Σ(F_i·cos²θ)/Σ(cos θ)`) eikä läpäisykerroin — fysikaalisesti oikeampi JA halvempi, koska tallennettava data ei kasva.
     - **Mitattu vaikutus**: pyyhkäisymatkan mediaani esteen koon mukaan, yksi säde → tehollinen: alle 0,1 ha **200 → 765 m (+282 %)**, 0,1–1 ha **70 → 571 m (+716 %)**, 1–10 ha 50 → 50 m, yli 10 ha 50 → 117 m. Pienten luotojen valesuoja katosi, isot saaret pysyvät suojaisina (117 m tarkoittaa 14 m/s tuulella 8 cm aallokkoa).
     - **Tuulensuoja esteen korkeudesta** (`sheltered_wind`): `U_eff = U·(1 − 0,6·exp(−F/2 / (8·h)))`. 200 m pyyhkäisymatkalla 2 m luoto jättää tuulen 99,9 %:iin, 10 m saari laskee sen 82,8 %:iin ja 20 m saari 67,9 %:iin. Aivan saaren kupeessa (50 m) 10 m saari puolittaa tuulen. Matala luoto ei siis tuota tuulensuojaa lainkaan — juuri havainnon ydin.
     - **Kasvillisuus päätellään saaren koosta, ei väristä.** MML:n korkeusmalli on maanpintamalli eikä sisällä puustoa. Käyttäjä huomautti, ettei peruskartan valkoinen kelpaa metsän tunnisteeksi tässä saaristossa: suurilla saarilla se voi olla metsää, pienillä ei. Toteutus `h_eff = DEM_max + 12 m · [saari ≥ 1 ha] · [ei kalliota] · [ei suota]` käyttää `ndimage_label`-komponenttien pinta-aloja ja jo välimuistissa olevia kallio-/suomaskeja — uutta värikynnystä ei tarvinnut virittää. Tulos: alle 0,1 ha luodot pysyvät 0,9 m:ssä, yli 50 ha saaret nousevat 15,3 m:iin.
     - **Fetch lasketaan nyt kerralla kaikille tiileille** (`get_or_compute_fetch_global`): sädehaarukka on 13-kertainen, joten sama suunta kannattaa laskea vain kerran koko aineistolle. 135 492 ruutua, 335 s.
     - **Ristiintarkistus sallii nyt 1e-12 poikkeaman** — `exp()` on ainoa transkendenttifunktio jaetussa sopimuksessa, eivätkä numpy ja V8 takaa sille bitilleen samaa tulosta (mitattu ero 2,2e-16). Se ei haittaa, koska kynnysarvot lasketaan tuulennopeudella 0, jolloin koko tuulensuojatermi on merkityksetön.
     - **Tuulikompassi legendaan**: SVG-ympyrä, jossa nuoli osoittaa suuntaan johon tuuli puhaltaa, ja nuolen pituus sekä väri (vihreä→punainen) kertovat voimakkuuden.
     - **Tämän aineiston fysikaalinen tosiasia**: 71,8 % rantaviivasta on alle 10 cm aallokossa vielä 14 m/s tuulessakin. Tiheässä sisäsaaristossa myrskykään ei tee rantautumisesta mahdotonta, joten "vain suojaisimmat rannat" on täällä edelleen paljon rantaa.
     - **Jakaumakuvaaja jätetään ilman suojaisuutta** ja se kerrotaan käyttäjälle: tuulesta riippuva jakauma muuttuisi joka kerta kun tuuli kääntyy eikä kertoisi rantaviivan pysyvistä ominaisuuksista.
     - **Asetussivun alalaidassa on rantaviivan jakaumakuvaaja** (`compute_shoreline_stats` → `docs/shoreline_stats.json`, piirto `renderChart`): koko aineiston rantaviiva jaettuna 25 pistemääräluokkaan, y-akselilla kilometrit, pylväät värjätty samalla asteikolla kuin kartta. Päivittyy välittömästi tekijävalinnoista, koska backend on esilaskenut jakauman kaikille 15 yhdistelmälle — selain vain valitsee rivin. Katkoviiva merkitsee "parhaat X %" -rajan, ja se lasketaan **samasta jakaumasta** kuin pylväät, jolloin merkki ja pylväät ovat keskenään yhtäpitäviä (kartan oma kynnys tulee `factor_thresholds.json`:ista, ero <0,4 prosenttiyksikköä).
     - **Jakauma lasketaan natiivilla 1 m -ruudukolla**, ei 3,5 m selainruudukolla, jotta metrimäärät vastaavat tarkalleen puskurivyöhykkeen todellista pinta-alaa (6 118 898 m²) — osatekijät kvantisoidaan silti samoiksi 8-bittisiksi arvoiksi kuin kuviin, jotta pistemäärät vastaavat sitä mitä kartta näyttää.
     - **Rantaviivan pituus on ARVIO (`SHORELINE_LENGTH_M = 700 000`), ei mitattu tarkka arvo** — lähde on peruskartan rasteroitu rantaviiva, ei vektorigeometriaa, joten pituus riippuu sekä maskin kohinasta että mittakaavasta (rantaviivaparadoksi). Mitattu kolmella tavalla, joiden vinoumat osoittavat vastakkaisiin suuntiin: puskurin ala / mitattu leveys (10,2 m) → **601 km** (aliarvio, kapeilla kannaksilla vyöhykkeet sulautuvat), rantaviivamaskin ääriviiva / 2 → **899 km** (yliarvio, rasterin porrastus), Zhang–Suen-ohennus → 1405 km (selvä yliarvio). Kaksi ensimmäistä rajaavat totuuden väliinsä. **Jakauman muoto ja prosenttiosuudet ovat tarkkoja** (suoria pikselilaskentoja); vain metriasteikon absoluuttinen taso kantaa tämän epävarmuuden, ja se sanotaan käyttäjälle kuvaajan alla.
     - Asetussivu ei enää ole API-vapaa, joten `build_static.py` ajaa senkin `URL_REPLACEMENTS`-käsittelyn läpi (`write_static_settings_html`) sen sijaan että kopioisi sen sellaisenaan.
     - **VANHA toteutus ("Nykyinen") lukittuu pois käytöstä heti kun jokin tekijä poistetaan**, koska sen esilasketut kuvat on laskettu kaikilla neljällä tekijällä eivätkä ne voi seurata valintoja. Valinta palautuu käyttöön kun kaikki neljä ovat taas mukana. Vertailu säilyy siis mahdollisena oletusasetuksilla.
   * **Kokeellinen UUSI toteutus rinnakkain vanhan kanssa (vertailua varten, ks. kohta 6: vektoritiilipohjainen arkkitehtuuri).** Koska rantaviivan pistemäärän jatkuva väriliuku ei sovi vektorigeometriaksi tuhoamatta suorituskykyä, rakennettiin sen sijaan vaihtoehtoinen ratkaisu joka pysyy rasterina mutta siirtää värityksen/kynnyksen/paksuuden laskennan selaimen Canvas-pikselikäsittelyyn yhden esilasketun kuvan päältä, karkeammalla 3,5×3,5 m -resoluutiolla (`backend/pipeline.py: NEW_PIXEL_FACTOR = 3.5`, käyttäjän hyväksymä kompromissi — kokeiltiin ensin 5×5 m:llä, joka osoittautui liian karkeaksi). Tämä kuvapari (ks. tekijävalinta yllä) korvaa VANHAN 5×10=50 esilasketun yhdistelmäkuvan (top-kerrokselle per taso). Selain (`frontend/index.html: renderFactorTile`) kokoaa pistemäärän valituista tekijöistä, värittää sen HSV-LUT:lla, vertaa tasapelinpurulla täydennettyä arvoa esilaskettuun kynnykseen ("top X %"), ja dilatoi (paksuus) yksinkertaisella erotettavalla JS-neliödilataatiolla (`dilateAlpha`/`dilateWithNearestColor`) — kaikki portaattomasti, ei enää presetteihin sidottuna (jaettu asetusarvo VANHAN kanssa pitää vertailun reiluna).
     - **Käyttöliittymä**: "Rantautumispisteytys"/"Parhaat rantautumispaikat" muutettiin **checkboxeista radiopainikkeiksi** ("Näytä") — vain toinen näkyvissä kerrallaan, ei koskaan molempia. Uusi "Toteutus (vertailua varten)" -radiovalinta ("Nykyinen"/"Uusi") mahdollistaa suoran vierekkäisvertailun samoilla liukusäätimen arvoilla. Kolme `L.layerGroup`:ia (`oldScoreGroup`/`oldTopGroup`/`newGroup`), joista vain yksi on kartalla kerrallaan (`switchActiveGroup`).
     - **Havaittu ja korjattu suorituskykyongelma**: alkuperäinen toteutus laski Canvas-värityksen/dilataation uudelleen JOKAISELLA `moveend`-tapahtumalla myös pelkän panoroinnin aikana, vaikka näkyvä tiilijoukko ei muuttunut — pudotti kuvataajuuden 17 fps:ään (mitattu). Korjattu `renderResultCache`-välimuistilla (avain: tiili+näyttötila+paksuus+prosentti+tekijävalinta) — VANHA toteutus ei kärsinyt tästä koska selaimen HTTP-välimuisti hoiti saman asian ilmaiseksi staattisille kuville. Korjauksen jälkeen 60 fps, sama kuin VANHASSA.
     - **Havaittu ja korjattu "outo punainen reunus"**: paksunnettujen (dilatoitujen) pikselien väri haettiin pikselin OMASTA raa'asta pistemäärästä, joka puskurivyöhykkeen ulkopuolella on mielivaltainen eikä koskaan tarkoitettu näytettäväksi (DEM:n maa/vesi-raja tuottaa siellä keinotekoisen jyrkkiä eli punaisia arvoja) — syntyi punainen reunus rantaviivan ympärille. Korjattu kahdesta suunnasta: (1) backendissä R/G-kanavien downsamplaus tehdään **peittopainotettuna keskiarvona** (`_masked_downsample`), eli vain aidosti puskurivyöhykkeellä olevat natiivipikselit vaikuttavat ruudun arvoon; (2) selaimessa score-tilan dilataatio käyttää `dilateWithNearestColor`:ia, joka levittää lähimmän AIDOSTI puskurilla olevan pikselin värin. Top-tilassa riittää yhä kevyempi `dilateAlpha`, koska väri on siellä kiinteä magenta.
     - **Mitattu ja nopeutettu dilataatio.** `dilateWithNearestColor` osoittautui renderöinnin kalleimmaksi vaiheeksi: se kävi jokaiselle pikselille koko ikkunan (−r..+r) läpi etsien minimietäisyyttä, vaikka lähin osuma löytyy tyypillisesti heti — peittomaskissa on vain **3,7 % pikseleistä** (108 021 / 2 937 796 mitattuna tiilellä L3123E). Haku etenee nyt ulospäin keskeltä ja katkeaa ensimmäiseen osumaan: **79,4 → 43,0 ms** oletuspaksuudella (1,8×), 5 px:llä 43,6 → 21,9 ms (2,0×). Tulos on **bitilleen sama** kaikilla `THICKNESS_PRESETS`-arvoilla, koska kun jokaisella etäisyydellä `d` tarkistetaan `−d` ennen `+d`:tä, tasapelit ratkeavat kuten vanhassa silmukassa.
     - **Varoittava esimerkki mittaamisesta**: ensimmäinen vertailuni antoi tälle nopeutukseksi 10×. Luku oli väärä, koska vanha versio ajettiin `eval`-deklaraationa ja uusi tavallisena funktiona — V8 optimoi vain jälkimmäisen. Oikea vertailu vaatii, että **molemmat versiot poimitaan samalla tavalla ja ajetaan samassa kontekstissa**; silloin ero on 1,8×. Sama koskee selainmittauksia: `--virtual-time-budget` pysäyttää `performance.now()`:n, jolloin kaikki vaiheet näyttävät nollalta.
     - Testattu headless Chromella: molemmat toteutukset renderöivät saumattomasti, persentiilisuodatus toimii oikein (top 1 % selvästi suppeampi kuin top 10 %), paksuussäädin toimii portaattomasti, ja UUSI toteutus perii oikein VANHAN meri/puro-erottelun (sama pohjadata, ks. kohdan 3 löydös) — sisämaan lampi (Långsund) ei näy kummassakaan.

   * **Melottavuus: olosuhteet veden päällä (neljäs "Näytä"-vaihtoehto).** Sovellus arvioi tähän asti vain rantaa, mutta matkamelojalle olennaista on myös pääseekö sinne. Sama pyyhkäisymatkakoneisto (`compute_fetch_and_obstacle`) kelpasi sellaisenaan — vain pistejoukko vaihtui rantaruuduista vesiruutuihin.
     - **Oma karkeampi ruudukko vedelle** (`WATER_GRID_M = 50`): merta on mosaiikissa 4,6 M ruutua 10 m välein, mikä olisi liikaa. 50 m välein niitä on 125 518 eli sama suuruusluokka kuin rantaruutuja (587 s). Aallokkokenttä on veden päällä sileä, joten 50 m riittää — ja kuvat ovat vain 120×120 per tiili, koko lisäys **976 kt**.
     - **Aallokko ja puuska erikseen** (`paddle_difficulty`): aallokko tulee jatkuvasta tuulesta ja määrää veneen liikkeen, puuska määrää käsiteltävyyden. Vaikeus on **huonomman mukaan**. Todennettu että termi puree itsenäisesti: sama 5 m/s tuuli antaa **0 % "ei suositella" tasaisena (puuska 8,8) mutta 47,5 % puuskaisena (puuska 16)**.
     - **Luokkajakauma etelätuulella**: tyyni 2 m/s → 100 % helppoa; navakka 9/15,5 → 96 % vaativaa; kartalla merenselät punaisia ja salmet vihreitä.
     - **Ulkoinen validointi MET Norwayn aallokkoennustetta vastaan** (avomeripisteet 59,9 N ja 59,5 N): oma malli antaa 0,35–0,43 m siellä missä MET antaa 0,50–0,60 m — systemaattisesti n. 25 % matalampi. **Syy on `MAX_FETCH_M = 15 km` -katto, ei `WAVE_COEFF`**: MET:n 0,50 m vastaa 5,6 m/s tuulella noin 31 km pyyhkäisymatkaa. Aineiston reunoilla ja avoimimmilla selillä malli siis **aliarvioi aallokkoa**, mikä on turvallisuuden kannalta väärään suuntaan. Katon nostaminen ~30 km:iin korjaisi tämän, mutta vaatii `FETCH_LEVEL_METRES`-taulukon uusimisen molemmin puolin ja kaiken uudelleenlaskennan — **tekemättä, kirjattu**.
     - **Rajapinnat todennettu oikeilla kutsuilla**: sekä Ilmatieteen laitos että MET Norway tarjoavat jatkuvan tuulen JA puuskan ilman API-avainta, CORS auki (FMI `WindGust`, MET `wind_speed_of_gust`). Puuskakerroin mitattuna 1,58–2,10. **FMI:n WAM-aallokkomalli ei kata saaristoa** (NaN), MET Norwayn merisääennuste kattaa mutta on muutaman kilometrin hilassa. Työnjako: merenselät → MET, suojaiset vedet → oma 10 m malli. **Ennustehaku itsessään on vielä tekemättä** (vaihe 2) — tuuli ja puuskat säädetään käsin.

   * **Sääennusteet selaimeen: Ilmatieteen laitos ja MET Norway rinnakkain.** Molemmat tarjoavat avointa dataa **ilman API-avainta ja avoimella CORS:illa**, joten sovellus pysyy täysin staattisena — ilman sitä tämä olisi vaatinut palvelimen. MET vaatii ehdoissaan tunnistavan `User-Agent`-otsakkeen, mutta selain ei anna JS:n asettaa sitä; **todennettu ettei MET torju selaimen omaa tunnistetta** (200 OK, CORS auki). Molempien lukemat näytetään rinnakkain, koska ennusteiden ero on itsessään tieto epävarmuudesta; käyttäjä valitsee kumpi ohjaa karttaa. Käsin säädettävä tuuli jää kolmanneksi vaihtoehdoksi ja varakeinoksi — kerros ei kaadu ulkoisen rajapinnan mukana. Lähdemaininnat (CC BY 4.0) lisätään kartan attribuutioriville vasta kun ennuste on haettu.
     - Aikajanan pituus seuraa valittua lähdettä: FMI 50 h tunnin välein, MET 10 vrk.
     - **MET:n aallokkoennuste sekoitetaan omaan malliin** painolla `w = F_eff / MAX_FETCH_M`: suojaisassa poukamassa luotetaan omaan 10 m malliin (MET:n muutaman kilometrin hila ei näe sinne), avomerellä MET:iin, koska oma malli katkaisee pyyhkäisymatkan 15 km:iin ja aliarvioi siellä aallokkoa n. 25 %. Pelkkä maksimi ei kelpaisi — se pilaisi suojaiset arvot. **Tämä on myös vastaus siihen, korjaako karttalehtien lisääminen katto-ongelman: ei korjaa.** Katto on 15 km riippumatta aineiston laajuudesta, ja Ahvenanmaalta etelään on 200+ km avovettä jota ei rasteroida. Aina jää raja, jonka takana vastaus on "avomeri jatkuu" — ja juuri siellä aallokkoennuste on auktoriteetti. Lehtiä lisättäessä katto on silti nostettava, koska 15 km alkaisi katkaista säteitä keskellä tunnettua vettä.

   * **Melottavuusnäkymän tietopallot.** Kartan klikkaus lisää klikattuun kohtaan pyöreän merkin, ja niitä voi olla **mielivaltainen määrä** — kukin pallo näyttää oman paikkansa olosuhteet, joten kahden reittivaihtoehdon voi vertailla rinnakkain. Pallon klikkaus poistaa juuri sen. Pallot toimivat **kaikissa näyttötiloissa**, eivät vain melottavuuskerroksessa: rantautumispisteytystä katsellessa on yhtä olennaista tietää, millainen tuuli ja aallokko kohteen edustalla on. Muut kerrokset eivät lataa vesiruudukkoa, joten pallo hakee sen itse tarvittaessa (`ensureWaterFor`, n. 90 kt/tiili) — pallo ilmestyy heti ja aallonkorkeus täydentyy latauksen valmistuttua. Merkissä on tuulen nopeus, puuskat, suunta ja **mallin laskema paikallinen aallonkorkeus**. Aallonkorkeus käyttää samaa laskentaa kuin kerroksen väritys, MET-sekoitus mukaan lukien — muuten luku ja väri kertoisivat eri asiaa. **Tuulen suunta on pallo joka liikkuu kehällä kuin kellon osoitin** (`ringPointAt`). Se korvasi aiemman pienen nuolen: nuoli oli sisällön yläreunassa oleva erillinen elementti, kun taas kehällä liikkuva merkki lukee yhdellä silmäyksellä eikä vie tilaa teksteiltä. Pallo istuu **tuulen tulosuunnassa** (tuuliruusun käytäntö), jolloin se ja sen alla lukeva ilmansuunta ("lounaasta") kertovat saman asian — aiempi nuoli osoitti päinvastoin eli suuntaan johon tuuli puhaltaa, mikä olisi paikkamerkkinä ollut ristiriidassa tekstin kanssa. Pallo käyttää **tasan samaa sädekaavaa kuin kehä**, joten se istuu aaltoilevalla viivalla eikä leiju sen vieressä; valkoinen reunus erottaa sen kehästä myös kun värit ovat lähellä toisiaan.

     **Pallon koko ja väri kertovat puuskan voimakkuuden.** Säde on 4 px tyynellä ja 10 px kun puuskat yltävät "ei suositella" -rajalle, väri vihreästä punaiseen — tuulen luonteen näkee vilkaisulla ennen kuin lukuja ehtii lukea. Mittarina on nimenomaan **puuska** eikä jatkuva tuuli, koska puuska määrää veneen käsiteltävyyden. Asteikko on `PADDLE_GUST_LIMITS` ja väri samasta LUT:sta kuin kerroksen väritys, joten luvut ovat suoraan vertailukelpoisia. Mitattu selaimessa: 3 m/s → 4,8 px `rgb(29,115,0)`, 9 → 6,5 px `rgb(95,115,0)`, 13 → 8,5 px `rgb(115,58,0)`, 18 → 10,0 px `rgb(115,0,0)`.

     **Kehä on väritetty kokonaisvaikeuden mukaan** (max aalto/puuska) ja pallo pelkän puuskan mukaan. Kun ne eroavat, se on itsessään tietoa: punainen kehä ja vihreä pallo tarkoittaa että vaikeus tulee aallokosta eikä tuulesta. Kasvava pallo ulottuu kehän ulkopuolelle (säde 54 + aaltoilu 6 + pallo 10 = 70 > kuvan puolikas 62), joten `.pb-ring` tarvitsi `overflow: visible` — vaihtoehtona olisi ollut kutistaa kehää niin paljon, ettei teksti olisi mahtunut sisään.

     Kehä värjätään vaikeusasteen mukaan — mutta **eivät sellaisenaan**: vaikeusasteen väri on täysin kylläinen (HSV s=v=1), ja kirkkaan keltaisen kontrasti valkoisella on 1,07:1 eli lukukelvoton. Sävy säilytetään mutta kirkkautta lasketaan, tuulipallolle enemmän (×0,45) kuin kehälle (×0,60), joka on paksu viiva osittain kartan päällä. **Teksti ei ole värikoodattu lainkaan**, vaan lähes mustaa (17,9:1, apurivit 11,5:1); vaikeusaste luetaan pallosta, kehästä ja itse luvusta.

   * **Väärä diagnoosi kannattaa kirjata muistiin.** Kun käyttäjä ilmoitti kolmesti, ettei pallon teksti erotu, sitä korjattiin joka kerta värikontrastia parantamalla — ja mitatut arvot todella paranivat. Oikea syy oli aivan muu: `leaflet.css` sisältää jälkeläisvalitsimen `.leaflet-map-pane svg { z-index: 200 }`, joka on tarkoitettu kartan omille vektorikerroksille mutta osuu myös `divIcon`-merkkien sisällä oleviin SVG:hin. Aallonkorkeuskehys nousi siten tekstin **päälle** ja peitti sen valkoisella täytöllään. Vika oli harhaanjohtava, koska nuoli (nykyisin kehällä liikkuva pallo) näkyi normaalisti — sekin on SVG ja sai saman noston, joten pallo näytti ehjältä ja vain teksti puuttui, mikä muistuttaa kontrastiongelmaa. Korjaus on määritellä pinojärjestys nimenomaisesti (`.pb-ring { z-index: 0 }`, `.pb-content { z-index: 1 }`) sen sijaan että luotettaisiin DOM-järjestykseen kirjaston tyylien alla. Todennus ei enää nojaa silmämääräiseen arvioon: `document.elementFromPoint` jokaisen tekstirivin keskipisteessä palautti ennen korjausta `path`, korjauksen jälkeen `DIV.pb-speed` / `DIV.pb-sub` / `DIV.pb-wave`. **Opetus: kun sama oire toistuu korjausten jälkeen, vika on todennäköisesti muualla kuin siinä mitä on korjattu.** **Reunaviiva itsessään on aallonkorkeuden mittari**: se on SVG-polku `r(θ) = R + A·sin(12θ)`, jonka amplitudi kasvaa aallonkorkeuden mukana ja saavuttaa maksiminsa `PADDLE_WAVE_LIMITS`-asteikon ylimmällä rajalla. Tyynellä pallo on ympyrä, myrskyssä selvästi aaltoileva — muoto kertoo olosuhteista jo ennen kuin lukuja ehtii lukea. Kehys on siksi SVG eikä CSS-reunus, ja sisältöön jätettiin marginaali ettei teksti osu aaltoiluun. Kaikkien pallojen arvot lasketaan uudelleen kun tuuli tai ennustehetki muuttuu (`updatePaddleBubbles` kutsutaan `refreshActive`:sta) — jokainen on eri paikassa eli eri olosuhteissa. Toteutus on `L.divIcon`, joten merkki pysyy ankkuroituna karttapaikkaan zoomatessa.

   * **Varoitus olosuhteiden heikkenemisestä.** Vaarallinen tilanne melojalle ei ole se, että olosuhteet ovat kovat — sen näkee kartalta — vaan se, että lähtee helppoon keliin ja jää kiinni muutokseen. Pallo varoittaa siksi erikseen, jos olosuhteet heikkenevät **oleellisesti seuraavan kolmen tunnin aikana**.

     "Oleellisesti" mitataan **samalla asteikolla jolla vesikerros väritetään** (`piecewiseClass`, 0–3 luokka-askelta) — jos varoituksella olisi oma mittarinsa, pallo ja kartta kertoisivat eri asiaa. Ehto on kaksiosainen: **(A)** olosuhteet ylittävät "ei suositella" -rajan 3 h sisällä vaikka eivät ole siellä nyt, **tai (B)** vaikeus nousee ≥ `WARN_CLASS_RISE` = 0,75 luokka-askelta ja päätyy vähintään tasolle "kohtalainen". Jälkimmäinen alaraja estää varoittelun muutoksista, jotka pysyvät helpossa vedessä; ehto (A) on erillinen, koska muuten pahin tapaus jäisi kynnyksen alle (jo vaativissa oloissa `d` on lähellä kattoa 3,0 eikä voi enää nousta 0,75:tä).

     Käyttäjän hyväksymä kalibrointi ja mitattu käytös: puuska 8→12 m/s varoittaa (nousu 1,00), 9→11 m/s ei (0,50); aalto 15→35 cm varoittaa (1,00), 30→40 cm ei (0,50). Varoitus **nimeää sen tekijän joka nousi eniten**, koska aalto ja puuska tarkoittavat melojalle eri asioita (veneen liike vs. käsiteltävyys).

     Toteutus nojaa siihen, että `waterConditionsAt` parametroitiin ottamaan tuuli ja ennustehetki argumentteina — tulevat tunnit lasketaan **tasan samalla koodilla** kuin nykyhetki, ei rinnakkaisella toteutuksella. **Maalla** pyyhkäisymatkaa ei ole eikä aaltotermiä voi laskea, joten siellä käytetään pelkkää puuskaluokkaa suojaamattomana; se on karkeampi mutta rehellinen, koska maalla pallo ei muutenkaan näytä aallonkorkeutta eikä mikään näytetty luku ole ristiriidassa. **Käsin säädetyllä tuulella varoitusta ei ole** — tulevaisuutta ei silloin ole olemassa, ja rivi jää pois ilman virheilmoitusta.

     Pallo **kasvaa 124 → 158 px** kun varoitus on esillä. Kasvu on samalla itsessään signaali: pallo erottuu muista jo ennen kuin tekstiä ehtii lukea. Koko oli pakko ottaa parametriksi myös `wavyCirclePath`:iin, joka oli kovakoodattu 124 px:n mittoihin — muuten isolle pallolle olisi piirtynyt väärän kokoinen kehä keskelle kuvaa. 158 px (ei 150) siksi, että ympyrän alaosassa käytettävä leveys kapenee, eikä kaksirivinen varoitus mahtunut ilman että alin rivi hipoi kehää.

     Aalto-osa on **paikallinen** (pyyhkäisymatka on jokaisella pallolla oma), puuskaennuste taas koko alueelle sama piste — kaksi palloa voivat siis varoittaa eri tavalla. Rajat ovat arvioita kuten `PADDLE_WAVE_LIMITS` ja `PADDLE_GUST_LIMITS`, ja kalibroitavissa mittaamalla.

   * **Tuuli haetaan hilana, ei yhdestä pisteestä.** Aiemmin tuuli tuli yhdestä kovakoodatusta pisteestä (60,28 N 20,76 E) koko kartalle. Nykyisellä 18×30 km alueella se riittää, mutta tavoitealueella (~1 000 × 450 km) ei: **mitattuna samalla hetkellä tuuli oli Merenkurkussa 11,6 m/s ja Perämerellä 2,2 m/s eli 5,3-kertainen ero**. Yhdellä pisteellä kovin keli olisi näkynyt tyynenä — virhe olisi ollut pahin siellä missä se on vaarallisin.

     **Rajapintalöydökset, todennettu oikeilla kutsuilla eikä dokumentaatiosta:** `latlon`-parametrin voi **toistaa** `multipointcoverage`-kyselyssä, ja rajoite on **URL:n pituus (~2 048 merkkiä), ei pistemäärä** — 95 pistettä kahdella desimaalilla = 2 010 merkkiä toimii, 100 pistettä = 2 105 merkkiä palauttaa HTTP 400. Pyyntö paloitellaan siksi URL-pituuden mukaan. FMI:n aallokkomalli **WAM toimii samalla kyselyllä** (avomeri 1,00 m, Selkämeri 1,36 m, Perämeri 0,67 m, **saaristo `NaN`**), joten aallokon lähde vaihdettiin MET Norwaysta FMI:hin ja se skaalautuu nyt alueen mukana. MET ei kelpaa hilaksi: `locationforecast` on yhden pisteen rajapinta ja käyttöehdot kieltävät hilamaisen haravoinnin.

     **Hila seuraa aineistoa, ei bounding boxia** (`build_static.py: wind_grid_points`): tiilet niputetaan 30 km ruutuihin ja vain ruutu jossa on tiiliä saa pisteen — muuten pisteitä kuluisi tyhjään mereen. Piste kirjoitetaan `wind_grid.json`:iin **sekä EPSG:3067-metreinä että lat/lon-parina**, jolloin selain ei tarvitse projektiokirjastoa: kartta työskentelee raakoina 3067-metreinä (`L.CRS.Simple`) ja lat/lon menee sellaisenaan rajapintakutsuun. Nykyiset 11 tiiltä antavat 4 pistettä.

     **Tuuli interpoloidaan vektorina, ei asteina** (`windAt`). Asteiden keskiarvo 350° ja 10° on 180° eli tasan vastakkainen suunta — vaarallisin mahdollinen virhe juuri suojaisuuslaskennassa. Painotus on käänteinen etäisyyden neliö neljästä lähimmästä pisteestä (EPSG:3067-metreissä), ja hilapisteen kohdalla interpolointia ei tapahdu lainkaan.

     **`NaN` ei tarkoita puuttuvaa mittausta vaan "WAM ei kata tätä aluetta".** Ensimmäinen toteutus vain ohitti NaN-pisteet ja interpoloi lopuista — testi paljasti että silloin jäljelle jäi pelkkä avomeren naapuri ja sen 1,4 m aallokko vuoti saaristoon täysimääräisenä, eli tasan se mitä säännön piti estää. **Lähin piste ratkaisee kattavuuden**: jos se on NaN, arvoa ei ole ja käytetään pelkkää omaa pyyhkäisymatkamallia, joka on siellä se oikea vastaus.

     **Renderöinti: kulmainterpolointi + alihila.** Pikselikohtainen `windAt` olisi maksanut rantaviivakerroksessa **1 833 ms per tiili** (lajittelu ja `atan2` joka pikselille). Tuuli interpoloidaan siksi tiilen **neljässä kulmassa** — naapuritiilet jakavat kulmat, joten kenttä on jatkuva eikä tiilirajoille synny saumaa — ja siitä 8×8 alihilaan (750 m), josta pikseli lukee lähimmän ruudun. Suunta muuttuu tiilen sisällä alle asteen ja pyyhkäisymatkan sektorit ovat 30° leveitä, joten jäljelle jäävä porras on ~0,02° eli näkymätön. Rivi- ja sarakeindeksit esilasketaan, jottei jokaiselle pikselille tule jakolaskua. Mitattu koko 1714×1714 ruudukolla: **1 833 ms → 7,6 ms** (240×), vesikerroksessa 9,1 → 0,63 ms.

     **Kompassiruusu ja tarkka yksittäinen lukema poistettiin** legendasta: kun tuuli vaihtelee kartalla, yksi nuoli ja yksi luku olisivat valhe. Tilalla on sanallinen kuvaus näkyvän alueen vaihtelusta (`windRangeText`), esim. "Tuuli 6–7 m/s etelästä–lännestä, puuskat 9–11 m/s". Suuntien haarukka lasketaan **vektorikeskiarvon ympäriltä**, ei asteiden min/max:sta — muuten pohjoisen ympärillä haarukaksi tulisi "10–350" eli kaikki muut suunnat paitsi oikea. Ensimmäisessä versiossa hajonnan kaava oli `180 - d` kun sen pitää olla `d`, jolloin nollahajonta antoi 180° ja legenda näytti **vastakkaista ilmansuuntaa** — testi paljasti sen. Käsisäädössä liukusäätimet jäävät, koska ne ovat syötteitä eivätkä lukemia; siellä yksi tuuli pätee yhä kaikkialla ja se on myös varakeino rajapinnan kaatuessa.

   * **MET Norway toisena mielipiteenä tietopallossa.** MET pudotettiin kerroksesta koska `locationforecast` on **yhden pisteen rajapinta** jota käyttöehdot kieltävät haravoimasta hilaksi. Pallo on kuitenkin piste — juuri se mihin rajapinta on tarkoitettu — joten sama este ei koske sitä, ja haku tehdään **pallon omilla koordinaateilla**. Yksi piste koko alueelle olisi ollut virhe: kaksi palloa 50 km päässä toisistaan näyttäisivät eri FMI-luvun mutta saman MET-luvun, mikä näyttäisi rikkinäiseltä.

     **MET ei syötä mallia.** Pallon väri, kehän aaltoilu, vaikeusluokka ja varoitus tulevat kaikki FMI-pohjaisesta laskennasta. Jos MET vaikuttaisi johonkin niistä, kaksi mallia kiistelisivät siitä minkä värinen piste on. MET on merkintä, ei syöte.

     **Rivi näkyy vain kun lähteet ovat eri mieltä** — kun ne ovat samaa mieltä, toinen luku ei kerro mitään mutta veisi tilan ahtaassa pallossa. Kynnykset: nopeusero ≥ 2,0 m/s, puuskaero ≥ 3,0 m/s tai **suuntaero ≥ 45°**. Suunta on mukana koska se on tälle sovellukselle tärkein erimielisyys: jos lähteet ovat eri mieltä tulosuunnasta, ne osoittavat vastakkaista rantaa suojaisaksi. Todellisella datalla testihetkellä FMI antoi 6,3 m/s 186° ja MET 6,0 m/s 213° — erot 0,3 m/s ja 27°, kaikki kynnysten alle, joten riviä ei tullut. Se on haluttu käytös.

     **Haku on välimuistitettu pyöristetyllä koordinaatilla** (2 desimaalia ≈ 1,1 km, sama pyöristys kuin FMI-hilakutsussa): pallo syntyy yhdellä klikkauksella, ja ilman välimuistia kartalla naputtelu tuottaisi kymmeniä pyyntöjä. Haku tapahtuu vasta pallon lisäyksen jälkeen, joten **pallo ilmestyy heti** eikä jää odottamaan toista rajapintaa; vertailurivi täydentyy kun vastaus saapuu. Epäonnistunut haku ei kaada mitään eikä sitä yritetä uudestaan.

     **Pallon koko mukautuu**: 124 px perus, 158 px yhdellä lisälohkolla, 196 px kun sekä varoitus että MET-erimielisyys ovat esillä. MET-rivi on **neutraali siniharmaa**, ei punainen — se kertoo epävarmuudesta, ei vaarasta, ja punainen on varattu varoitukselle. Rivit on pidettävä lyhyinä: ympyrä kapenee alaspäin ja alimmalla rivillä käytettävä leveys on vain noin 130 px. Ensimmäinen yritys ("10,0 m/s, puuska 14, luoteesta") valui kehän yli molemmilta puolilta, joten syylista jätettiin pois näytöstä — erot näkyvät suoraan numeroista FMI-lukujen rinnalla.

     **Aikajänne pysyy 50 tunnissa** (FMI) vaikka MET yltää kymmeneen vuorokauteen: käyttäjän päätös, koska sovellus on tarkoitettu alle 8 tunnin jänteellä ympäristöään katsovalle eivätkä pidemmät ennusteet ole riittävän varmoja. **Aallokkoa ei haeta MET:ltä**: sen merisääennusteen hila on muutamia kilometrejä eikä erota yksittäistä lahtea, joten saaristossa oma pyyhkäisymatkamalli on tarkempi ja kolmas aaltoluku olisi kohinaa.

   * **Mobiililayout claude.ai/design-mockupista.** Käyttöliittymä oli suunniteltu työpöydälle: legendapaneeli oikeassa alakulmassa, ei yhtään `@media`-kyselyä. Puhelimella se peitti kartan. Mockup (`Karttanäkymä.dc.html`, projekti *Map interface design mockup*) määritteli mobiiliasettelun: tumma yläpalkki, oikealta liukuva paneeli, alaosassa aikajana ja kerrosvälilehdet.

     **Koko layout on `@media (max-width: 700px)` -kyselyn takana**, joten työpöytänäkymä ei muutu lainkaan — muutos ei voi rikkoa olemassa olevaa käyttöliittymää. Design-järjestelmästä (*Modernist*) otettiin käyttöön vain tokenit: Archivo-kirjasin, värit, välit ja varjot. Sen `_ds_bundle.js` on tyhjä (`"components":[]`), eli järjestelmä on **pelkkää CSS:ää** eikä komponenttikirjastoa ollut porttattavana.

     **Mobiilikuori ei pidä omaa tilaa.** Jokainen painallus ohjaa samaa radiopainiketta tai liukusäädintä jota työpöytänäkymäkin käyttää, ja tila luetaan takaisin niistä (`syncMobileShell` kutsutaan `updateLegendFromSettings`:stä). Näkymät eivät siis voi ajautua erilleen, ja jos kuori poistettaisiin, mikään ei rikkoutuisi. Legenda saa mobiilissa uuden asun mutta on **sama DOM-elementti** — ei kopiota.

     **Kolme välilehteä, ei neljää.** "Parhaat rantautumispaikat" jätettiin alapalkista pois; se tulee myöhemmin osaksi Ranta-valintaa. Aikajana pidettiin **50 tunnissa** eikä mockupin kuudessa, koska pidempi jänne on jo linjattu tarpeelliseksi.

     **Mitattu asettelu** eikä arvioitu silmämääräisesti: välilehtien tekstileveydet mitattiin molemmilla kirjasinkooilla, ja kolme nimeä mahtuu 320 px:stä (iPhone SE, tarvitaan 104 px / tilaa 106 px) 430 px:ään asti. Alle 400 px:n näytöillä kirjasin pienenee 12,5 → 11 px ja välistys nollataan; ilman sitä "Melottavuus" katkeaisi 360 px:llä. Aikajanan kello (72 px) rajataan puolikkaan leveyden päähän radan päistä, muuten se leikkautuisi ruudun ulkopuolelle janan alussa.

     **Kaksi testausansaa kannattaa muistaa.** `--virtual-time-budget` **ei aja CSS-siirtymiä**, joten paneelin sijainti mitattiin animaation alkuarvosta ja näytti rikkinäiseltä — sama ilmiö kuin `performance.now()`:n kanssa aiemmin. Lisäksi **Chrome headlessin viewportin minimileveys on 500 px**: `--window-size=430` antaa yhä `innerWidth = 500`, jolloin kuvakaappaus on rajaus eikä layout-vika vaikka välilehti näyttäisi katkeavan. Molemmat kierrettiin mittaamalla ohjelmallisesti sen sijaan että olisi luotettu kuvaan.

     Sivulla ei ole globaalia `box-sizing: border-box` -sääntöä, joten paneeli tarvitsi sen erikseen — muuten padding ja reunus kasvattivat leveyden 270 → 304 px:iin.

   * **Legenda jaettiin välilehtiin.** Paneeli kasvoi kahdeksan vaiheen aikana noin 950 pikseliä korkeaksi eikä mahtunut selainikkunaan. Sisältö on nyt kolmella välilehdellä — **Näytä** (värit + näkymän valinta), **Tuuli** (lähde, ennuste, kompassi, säätimet) ja **Lisää** (toteutusvertailu, asetuslinkki, yhteenveto) — joista yksi on kerrallaan näkyvissä. Korkein paneeli on 480 px. **Tuuli-välilehti näkyy vain kun tuulella on vaikutusta** (suojaisuustekijä päällä tai melottavuuskerros valittuna), ja jos se piilotetaan aktiivisena, näkymä palaa Näytä-välilehdelle. Varmuuden vuoksi `max-height: calc(100vh - 130px)` + oma vieritys: 620 px ikkunassa pisimmällä sisällöllä (ennuste päällä) paneeli vierittyy itse eikä valu ikkunan yli.

   * **Kartta muistaa tilansa.** Asetukset ovat oma sivunsa, joten sieltä palatessa kartta latautui alusta: näkymä hyppäsi oletuskohtaan, näyttötila palasi pisteytykseen ja tietopallot katosivat. Juuri asetuksia säätänyt käyttäjä haluaa nähdä vaikutuksen samaan paikkaan johon oli tullut. Tila (keskipiste, zoom, näyttötila, toteutusvalinta, legendan välilehti, tuuli ja ennustehetki sekä pallojen sijainnit) tallennetaan omaan `localStorage`-avaimeensa `rantautuminen-nakyma` — **eri avain kuin asetuksilla**, koska asetussivu kirjoittaa oman avaimensa eikä saa törmätä tähän. Kirjoitus tapahtuu `refreshActive`:ssa, joka ajetaan jo valmiiksi jokaisesta merkitsevästä muutoksesta (siirto, zoom, tilan vaihto, tuulen muutos). Palautus lukee arvot ennen ensimmäistä piirtoa, jolloin oletusnäkymän `fitBounds` ohitetaan kokonaan eikä kartta välähdä väärässä kohdassa. Kaikki arvot validoidaan (`isFinite`, sallitut nimet), jotta vanha tai käsin muokattu tallennus ei kaada latausta, ja ennustehaku palautuksessa on `try`-lohkossa: verkon ollessa poikki jäädään tallennettuun tuuleen sen sijaan että kartta jäisi lataamatta.

5. **Vaihe 5: Täysin staattinen julkaisu (GitHub Pages)** — VALMIS (`build_static.py`)
   * Koska lähdedata ei muutu ajossa, koko laskenta voidaan ajaa kertaalleen build-vaiheessa ja tuotanto voi olla pelkkiä staattisia tiedostoja — ei Pythonia, GDAL:ia eikä palvelinta tuotannossa.
   * `python3 build_static.py` (ajetaan projektin juuresta): käy läpi `backend/pipeline.py`:n kautta kaikki tiilet, kaikki neljä resoluutiotasoa (ks. kohta 4:n moniresoluutio-löydös), kaikki viisi paksuusesiasetusta (`THICKNESS_PRESETS`) JA (top-kerrokselle) kaikki kymmenen "parhaat rannat" -esiasetusta (`TOP_PERCENT_PRESETS`), kirjoittaa `docs/cache/{tile}{level}_t{thickness}.png` (overlay, ei riipu %-arvosta), `docs/cache/{tile}_top{level}_t{thickness}_p{percent}.png` (top, riippuu molemmista) ja `docs/cache/{tile}_base{level}.png` (kertaalleen per taso, ei riipu kummastakaan) sekä `docs/tiles.json` (tiilirajat + esiasetukset), ja generoi `docs/index.html`:n `frontend/index.html`:sta korvaamalla `/api/...`-polut suhteellisilla staattisilla poluilla (`URL_REPLACEMENTS`-taulukko skriptissä).
   * **Kansion nimi on `docs/`, ei `dist/`**: GitHub Pagesin "Deploy from a branch" -tila tukee lähdekansioksi vain juurta (`/`) tai nimenomaan `/docs`-kansiota — ei mielivaltaisia nimiä. Tämä huomattiin vasta kun `/dist`-vaihtoehtoa ei löytynyt Pagesin asetussivulta.
   * **Suhteelliset polut ovat pakollisia**: GitHub Pages -projektisivut palvelevat osoitteesta `https://kayttaja.github.io/repo/`, ei domainin juuresta — jos poluissa olisi alkava `/`, kuvat ja `tiles.json` eivät latautuisi. Testattu toimivaksi alipolusta tavallisella `python3 -m http.server`:lla.
   * `/api/viewport`-endpointtia ei tarvinnut siirtää staattiseksi, koska frontend ei käyttänyt sitä koskaan (lataa kaikki 11 tiiltä suoraan alusta alkaen).
   * `docs/` tyhjennetään ja luodaan uudelleen joka ajolla (`shutil.rmtree`) — ei käsin editoitavaksi.
   * Lopputulos: `docs/` on **93 MB** (ks. WebP-kohta alla; ennen sitä 593 MB), 2585 välimuistitiedostoa (+ `index.html`, `tiles.json`, `.nojekyll`) (11 tiiltä × (4 tasoa × (1 basemap + 5 overlay-paksuutta + 5 paksuutta × 10 %-esiasetusta top-kerrokselle = 50) + 1 UUDEN toteutuksen koodattu kuva, ks. kohdan 4 vertailu-löydös)), itsenäinen kokonaisuus. `.nojekyll`-tiedosto mukana estämässä GitHub Pagesin Jekyll-käsittelyä. Build kestää n. 6–7 min ensimmäisellä ajolla (välimuistista uudelleen n. 10 s; WebP-muunnoksen kanssa ensimmäinen ajo n. 5 min lisää).

   * **Visuaaliset kerrokset häviöttömänä WebP:nä (593 MB → 93 MB, 6,4×).** 98 % `docs/`:sta oli visuaalisia kerroksia, jotka selain vain näyttää (`L.imageOverlay`): peruskartta ja esilasketut värikerrokset. **Häviötön WebP voitti häviöllisen jokaisella kuvatyypillä** — epäintuitiivista, mutta selittyy sillä ettei aineistossa ole yhtään valokuvaa: peruskartta on rasteroitua kartografiaa ja kerrokset synteettisiä tasavärialueita. Mitattuna 6000×6000 kuvilla: peruskartta 9,84 → 2,28 MB (4,3×; häviöllinen q80 olisi 3,99 MB), värikerros 6,73 → 0,46 MB (14,7×; q80 0,77 MB), top-kerros 0,29 → 0,04 MB (8,2×; q80 0,22 MB). **Visuaalisesta laadusta ei tarvinnut tinkiä lainkaan.**
     - **Todennettu pikselitasolla** lähde-PNG:tä vastaan: alfakanava identtinen, **näkyvien pikselien RGB-ero tasan 0**. Tiedostot eivät silti ole tavu tavulta samoja, koska libwebp nollaa RGB:n täysin läpinäkyvien pikselien (A=0) alta pakkauksen parantamiseksi — ne arvot eivät päädy ruudulle koskaan.
     - **Datakuvat jäävät PNG:ksi** (`_factors`, `_tiebreak`, `_prime`, `_fetch*`, `_water*`, yhteensä 12,7 MB). Niistä selain lukee pikseliarvoja `getImageData`:lla ja purkaa nibble-pakatut kentät, ja juuri tuo läpinäkyvien pikselien RGB-nollaus **tuhoaisi ne** — niissä myös "läpinäkyvä" pikseli kantaa merkitsevää dataa. Raja kulkee siis tasan siinä, kutsuuko frontend kuvalle `loadImageData()` (= PNG) vai `L.imageOverlay()` (= WebP), ei muodon mukavuuden mukaan.
     - **Enkoodaus on hidasta** (1–2,7 s per 6000×6000 kuva × 2 464 kuvaa), joten `build_static.py` ajaa sen kahdeksassa rinnakkaisessa prosessissa ja välimuistittaa tuloksen `output/cache/_webp/`-hakemistoon. Välimuisti on **sisältöosoitteinen** (avain = lähde-PNG:n SHA-1), joten vanhentunutta tulosta ei voi vahingossa käyttää eikä erillistä mitätöintiä tarvita. Ilman näitä kahta lämmin build olisi noussut sekunneista tunteihin.
     - **Kehityspalvelin (`backend/api.py`) tarjoilee yhä PNG:tä** — muunnos tapahtuu vain staattisessa buildissa. Tuotannon ja kehityksen kuvamuoto siis eroaa; se on tietoinen valinta (putki ja välimuisti pysyvät koskemattomina), mutta tarkoittaa että WebP-kohtaiset ongelmat näkyisivät vain `docs/`-versiossa.

   * **`docs/` EI OLE ENÄÄ VERSIONHALLINNASSA (2026-07-31), ja julkaisu on siksi tällä hetkellä poikki.** Hakemisto on kokonaan johdettua — `build_static.py` tyhjentää ja luo sen joka ajolla seuratusta lähtöaineistosta — mutta sitä oli silti versioitu, jolloin git-historian blobeista **83 % (647 Mt / 778 Mt) oli pelkkää välimuistia**. Repo kasvoi niin isoksi, ettei sitä voinut enää siirtää työkalujen välillä. Historia kirjoitettiin uusiksi (`git filter-repo --path docs --invert-paths`), `docs/` lisättiin `.gitignoreen`, ja tulos on: seurattuja tiedostoja 2 637 → **44**, `.git` 582 Mt → **161 Mt**, tuore klooni **290 Mt**. Kaikki 25 committia säilyivät, koska yksikään ei koskenut pelkkää `docs/`:ia — mutta **commit-tunnisteet muuttuivat**, joten tätä vanhemmat SHA-viittaukset tässä dokumentissa eivät enää täsmää. Loput 129 Mt on lähtöaineistoa (`karttakuva-mll/`, `korkeusmalli-mml/`, `rakennukset-mll/`), joka pidettiin seurattuna toisinnettavuuden vuoksi.

     **Seuraus: GitHub Pages oli tilassa "Deploy from a branch: `main` `/docs`", eikä `main`issa ole enää `docs/`-kansiota — sivusto palauttaa 404:n kunnes julkaisutapa ratkaistaan.** Tämä hyväksyttiin tietoisesti. Kaksi vaihtoehtoa, kumpaakaan ei ole vielä valittu:
     - **Erillinen julkaisurepo**: `docs/` pushataan omaan repoonsa. Säilyttää nykyisen "push ja live parissa minuutissa" -työnkulun eikä vaadi CI:tä, mutta julkaisuosoite muuttuu.
     - **GitHub Actions**: workflow ajaa `build_static.py`:n ja julkaisee tuloksen artefaktina ilman committia. Mahdollista, koska koko putken syöte (30 seurattua tiedostoa) on repossa ja `output/cache/` on johdettua. Vaatii rasterio/geopandas-asennuksen ajurille, ja **kylmän ajon kesto on todentamatta** — yllä mainittu 6–7 min koskee ajoa, jossa `output/cache/` on jo olemassa. Tyhjästä ajo laskee koko putken eikä sitä ole mitattu.

---

## 5b. Laserkeilausaineisto: selvitys, ei vielä käytössä (2026-08-04)

Kasvillisuuden korkeus on mallin heikoin lenkki: se **arvataan saaren koon perusteella** (`MIN_VEG_ISLAND_HA = 1.0`, `VEG_HEIGHT_M = 12.0`). Sääntö syntyi käyttäjän havainnosta, ettei peruskartan valkoinen ole saaristossa luotettava metsän merkki. Selvitin, voisiko MML:n laserkeilausaineisto korvata arvauksen mittauksella.

### Mitä arvaus maksaa — mitattuna

Lisä osuu **65,8 %:iin maa-alasta** (54,2 km² nykyalueella). Näiden ruutujen mediaanikorkeus on lisän kanssa 16,7 m ja ilman sitä 4,7 m — arvaus siis **hallitsee esteen korkeutta valtaosalla maasta**. Virhe tuulensuojaan (4 m vs 12 m puusto): 50 m pyyhkäisymatkalla **26 %**, 100 m 26 %, 200 m 19 %, 400 m 7 %. Virhe on suurin 50–200 m matkoilla eli tasan niissä suojaisissa poukamissa, joihin rantaudutaan.

### Aineisto

| | 0,5p | 5p |
|---|---|---|
| Lisenssi | **CC BY 4.0, ilmainen** | maksullinen, 0,033 €/km², min 33,50 € |
| Tiheys | 0,5 p/m², pisteväli ~1,4 m | ≥5 p/m² |
| Koordinaatisto | **EPSG:3067** — sama kuin tällä projektilla | EPSG:3067 |

**0,5p riittää**, ja se on valmiiksi luokiteltu: 2 = maanpinta, 3 = matala kasvillisuus (0–0,5 m), 4 = keskikorkea (0,5–2 m), 5 = korkea (2–50 m). Latvuskorkeus olisi luokkien 3–5 maksimi miinus maanpinta.

### Riittääkö tiheys? Kaksi käyttötapausta eroavat ratkaisevasti

| Käyttö | Ruutu | Pisteitä | Keskivirhe |
|---|---:|---:|---:|
| Esteen korkeus (`FETCH_GRID_M`) | 10 m | 50 | 0,071 |
| Rantaviivan pisteytys (`NEW_PIXEL_FACTOR`) | 3,5 m | 6 | **0,202** |

Ero johtuu **laskettavasta suureesta**. Latvuskorkeus on maksimi — muutama piste riittää. Aluskasvillisuuden tiheys on osuus, ja osuuden keskivirhe kutistuu vain neliöjuurena: kuudesta pisteestä laskettu suhde heittelee ±0,20 eli on käyttökelvoton. **Tuulimalli toimii siis suoraan, rantautumiskelpoisuus ei.**

Riippumaton vahvistus: [karttapullautin](https://github.com/karttapullautin/karttapullautin) laskee vihreän 3 m ruudussa mutta **aluskasvillisuuden 18 m ruudussa** (`greendetectsize=3`, `step=6`) — kymmenen kertaa tiheämmällä aineistolla. Se on tehnyt saman havainnon.

**Ristiriita ja sen ratkaisu:** aluskasvillisuus tarvitsisi 14–18 m ruudun, mutta rantavyöhyke on vain 5–15 m leveä; poikkisuuntaan 18 m ruutu vuotaisi veteen ja sisämaahan. Ratkaisu on jo koodissa: `compute_prime_components` aggregoi **rantaviivan suuntaan** (`_grouped_percentile`, `_alongshore_min`, `PRIME_ALONGSHORE_RADIUS_M`). Kärkipaikkoja varten rakennettu poikkileikkauslogiikka sopisi tähän sellaisenaan.

### Mitä karttapullautin tekee

Rust-sovellus, joka tekee suunnistuskarttoja luokitellusta LiDAR-aineistosta. Ydinmittaus (`src/vegetation.rs`) on latvuskorkeus `hh = piste.z − maanpinta`, jossa maanpinta interpoloidaan bilineaarisesti. Siitä johdetaan avoin maa (≥90 % pisteistä alle 0,9 m), metsän tiheys (painotettu pistelasku korkeusvyöhykkeittäin) ja **aluskasvillisuus** `ug/(ug+ugg)` alle 1,2 m pisteistä — käytännössä valmis kuljettavuusindeksi. Emme tarvitsisi sen paluukaikuheuristiikkaa, koska MML:n aineisto on jo luokiteltu; karttapullautin tekee raskaan työn tukeakseen luokittelematonta dataa.

### Vuodenaika — korjaus alkuperäiseen oletukseeni

Oletin ensin, että lehdettömyys aliarvioi tuulensuojan. Se oli epätarkkaa. Malli on

```
U_eff = U · (1 − S_max · exp(−(F/2) / (8·h)))     S_max = WIND_SHELTER_MAX = 0,6 kiinteä
```

**Korkeus `h` määrää vain etäisyysskaalan, ei suojan voimakkuutta.** Lehdetön puu on yhtä korkea kuin lehtipuinen, joten LiDAR mittaa korkeuden oikein vuodenajasta riippumatta. Kausivaihtelu koskee latvuston **huokoisuutta**, jota malli ei esitä lainkaan.

Tästä seuraa, että sovellus on kesäkäyttöön ja kiinteä 0,6 vastaa jokseenkin tiheää kesälatvustoa — **kausivaihtelu ei siis estä tuulimallin parantamista**. Vaikutus jos huokoisuus otettaisiin mukaan (15 m lehtipuusto, lehdetön 0,3 vs kesä 0,6): tuulennopeudessa 32 % / 25 % / 15 % matkoilla 50 / 100 / 200 m, mutta **aallonkorkeutena vain noin sentti**, koska lyhyellä matkalla aalto on joka tapauksessa pieni.

Kausivaihtelu olisi olennainen vasta, jos **aluskasvillisuus** otetaan rantautumiskelpoisuuden tekijäksi: huhtikuussa keilattu lehdetön pensaikko näyttää kuljettavalta, heinäkuussa se on läpitunkematon. Siihen on suora lähde: Luken MVMI-aineiston teema *"Puuston latvuspeittävyys, lehtipuut"*, 16 m rasteri, EPSG:3067, CC BY.

**Keilausta ei tehdä aina lehdettömänä.** Kevätkeilaus on maalis–huhtikuussa, mutta siirtyy kesään jos sää ei salli — aineisto on sekoitus, ja epäjohdonmukaisuus on karttalehtien välillä. LAS-otsakkeessa on keilauspäivä (`File Creation Day of Year`), joten tila on luettavissa lehdittäin eikä sitä tarvitse arvata.

### Merimetsot — selvitetty ja hylätty

Merimetsojen guano tappaa puuston, joten yhdyskuntasaari on iso mutta puuton — juuri se tapaus, jossa "yli 1 ha → 12 m metsää" epäonnistuu pahiten. Selvitin lähteet: **GBIF** toimii ilman avainta (86 823 havaintoa Suomessa) mutta on **vääränlaista dataa** — suurin tuottaja on rengastusrekisteri ja koordinaattiepävarmuus oli ensimmäisessä tuloksessa 1 000 m. Lentävä merimetso ei kerro, minkä saaren puut kuolevat. **laji.fi** vaatii tokenin (HTTP 403). **SYKE:n merimetsoseuranta** on oikeaa dataa (~52 yhdyskuntaa) mutta julkaistaan PDF-raportteina; rajapintaa tai ladattavaa paikkatietoaineistoa en löytänyt.

**Ei kannata toteuttaa, koska LiDAR tekee kysymyksen tarpeettomaksi**: merimetsojen tappama saari näkyy suoraan matalana latvustona, eikä mallia kiinnosta *miksi* puut ovat poissa. Sama koskee myrskytuhoja ja hakkuita, jotka eivät ole harvinaisempia. Merimetsotieto olisi hyödyllistä vain LiDARin **ajantasaisuuden** merkkinä — hienosäätöä, joka kannattaa tehdä vasta jos ongelma osoittautuu todelliseksi.

### Toteutettu 2026-08-04: mittaus korvasi arvauksen

**API-avain hankittiin ja koko putki rakennettiin** (`backend/lidar.py`). Kaikki 11 tiiltä ladattiin **166 sekunnissa**, LAZ-tiedostot poistettiin heti ja välimuistiin jäi **17 Mt** — noin 2 Gt raakadataa tiivistyi sadasosaansa.

**Rajapinnan kolme yksityiskohtaa, jotka eivät selviä dokumentaatiosta:**

1. POST-runkoon tarvitaan `"id"`-kenttä prosessin nimellä. Ilman sitä vastaus on **HTTP 400 ilman virheilmoitusta**, eikä lehtinimen vaihtaminen auta — harhauttavaa, koska vika näyttää olevan parametreissa.
2. `results`-lista sisältää **yhden ylimääräisen alkion ilman `path`-kenttää** (4 lehteä → 5 alkiota). Suora indeksointi kaatuu siihen.
3. Vastauksen metatieto on **tarkempi kuin LAS-otsake**: `dateOfScanning` ja `project` kertovat keilauspäivän ja usein vuodenajan. Tämä ratkaisi lehdellinen/lehdetön-kysymyksen ilman päättelyä pistepilvestä.

**Lehtinimet** ovat 1:10000-tiili + numero 1–4 (`L3123E` → `L3123E1`…`L3123E4`). Neljännesten numerointia **ei oleteta**: jokainen lehti sijoitetaan omien otsikkorajojensa mukaan, joten väärä oletus ei voi mennä läpi huomaamatta.

**Aineisto on kauttaaltaan kesäkeilausta**: projekti `20250103_Leica_Kumlinge_kesa`, päivät 20.–22.6.2025. Kausikysymys ratkesi siis parhaalla mahdollisella tavalla — tämä on tasan se tila, jota kesäkäyttöön tarkoitettu sovellus tarvitsee.

#### Validointi: LiDARin maanpinta vs. korkeusmalli

Ratkaisevin tarkistus. Molempien pitäisi olla N2000-järjestelmässä, ja ne täsmäävät: **erotuksen mediaani +0,13 m ja 99 % ruuduista alle metrin sisällä**. Korkeusjärjestelmät ovat siis samat eikä muunnosta tarvita.

#### Mitä mittaus muutti

Ratkaiseva havainto on, että **arvaus oli kaksihuippuinen ja väärässä molempiin suuntiin**:

| Efektiivinen kasvillisuuslisä | Arvaus | Mittaus |
|---|---:|---:|
| Mediaani | 12,0 m | **7,5 m** |
| Keskiarvo | 6,5 m | **7,5 m** |
| Ruutuja joissa tasan 12,0 m | **54 %** | – |
| Ruutuja joissa 0 m | **46 %** | 3 % |

Arvaus antoi joko tasan 12 m tai tasan nolla, mitään siltä väliltä. Mittaus on jatkuva. Käytännössä:

- **Yli 1 ha saarilla lisä oli liian suuri** (12 m → mitattu 7,6 m)
- **Pienillä saarilla, kalliolla ja suolla lisä puuttui kokonaan** (0 m → todellinen puusto)

Nettovaikutus on **+1,0 m keskimäärin**, mutta se on kahden vastakkaisen korjauksen summa: 24 % ruuduista laski yli 3 m ja 38 % nousi yli 3 m.

#### Väärä hälytys, joka kannattaa muistaa

Raportoin ensin **suunnitteluvirheen**: että turvaverkko `max(DEM, LiDAR)` estäisi mittausta korjaamasta arvausta alaspäin. **Väite oli väärä.** Maksimi on DEM:n **maastoa** vastaan, ei arvausta vastaan — arvaus lisätään vasta myöhemmin ja vain mittaamattomiin ruutuihin. Mittaus siis korvaa arvauksen kokonaan, myös alaspäin.

Päättelin virheen +1,0 m nettomuutoksesta olettaen, että sen pitäisi olla negatiivinen. Oletus unohti, että arvaus oli nolla 46 %:ssa maasta. **Opetus: kun aggregaatti yllättää, tarkista jakauma ennen kuin syytä koodia.** Kaksi seuraavaakin hypoteesiani (kelpoisuuskriteerin harha, kriteerien ero) osoittautuivat vääriksi mittaamalla.

#### Kelpoisuuskriteerejä on kaksi eri tarkoitukseen

`has_surface` (≥3 kaikua) riittää **esteen korkeuteen**, koska pinnan huippu ei tarvitse maanpintaa. `valid` (≥3 maapistettä) tarvitaan **latvuskorkeuteen** maanpinnasta. Erottelu tehtiin siksi, että tiheä latvus estää maakaiut juuri siellä missä puusto on korkeinta — yhden kriteerin käyttö olisi hylännyt parhaat metsäruudut. Käytännössä ero osoittautui pieneksi (185 ruutua 236 883:sta), mutta periaate on oikea.

#### Tallennettu tuote

Rannikkoalueelle raakadataa olisi kymmeniä gigatavuja, joten **kaikki suureet irrotetaan yhdellä lukukerralla**: pinnan huippu, maanpinta, latvuspeittävyys, aluskasvillisuus, pistemäärä ja keilauspäivä. Uudelleenlataus maksaisi koko aineiston, joten myös toistaiseksi käyttämättömät suureet talletetaan nyt.

Mitattu tiheys maalla on **0,38 p/m²** eikä luvattu 0,5 — vesi ei tuota kaikuja lainkaan, joten koko lehden keskiarvo (0,19) on harhaanjohtava. 10 m ruudussa on silti ~38 pistettä, mikä riittää maksimiin hyvin.

### Aiempi tila: eristetty, ei toteutettu

Latausrajapinta (`avoin-paikkatieto.maanmittauslaitos.fi/tiedostopalvelu/ogcproc/v1/`) **vaatii API-avaimen — todennettu, palauttaa ilman sitä HTTP 401**. Avain on maksuton (OmaTili-rekisteröinti), mutta ilman aineistoa toteutusta ei voi todentaa, eikä todentamatonta LiDAR-lukua kannata kirjoittaa.

Kasvillisuusarvio on siksi **eristetty omaksi funktiokseen** (`pipeline.vegetation_height_m`), jotta LiDAR-toteutus korvaa vain sen rungon eikä muuhun putkeen tarvitse koskea. Refaktorointi on todennettu tuottavan **bitilleen saman** korkeusmosaiikin (3000×1800 ruutua). Funktion kommentissa on mitattu virhe, aineiston tiedot ja syy siihen miksi toteutusta ei ole tehty.

**Kokeilu ei vaadi avainta:** yhden karttalehden voi ladata käsin Karttapaikan tiedostopalvelusta ja katsoa oikealla datalla, paljonko latvuskorkeus muuttaa mallia. Avain kannattaa hakea vasta aluetta laajennettaessa.

---

## 5c. Vektorikarttatasot: väylät, suojelualueet ja palvelut (2026-08-05)

Sovellus vastasi vain kysymykseen *onko tämä ranta hyvä rantautua*. Melojalle ja pienveneilijälle puuttui kolme asiaa: mitä pitää **väistää**, minne ei ehkä saa **mennä**, ja mitä on **tarjolla**. Kaikki kolme löytyivät avoimena datana ja ovat nyt toteutettuina (`backend/vektoritasot.py`).

### Rajapintalöydökset (todennettu kutsuilla, ei dokumentaatiosta)

| Lähde | Osoite | Lisenssi |
|---|---|---|
| Väylävirasto | `avoinapi.vaylapilvi.fi/vaylatiedot/ows` | CC BY 4.0 |
| SYKE | `paikkatiedot.ymparisto.fi/geoserver/**inspire_ps**/wfs` | CC BY 4.0 |
| OpenStreetMap | `overpass-api.de/api/interpreter` | ODbL |

- **SYKE:n yleinen `/geoserver/ows` vastaa "Service WFS is disabled".** Toimiva polku on työtilakohtainen. Tämä maksoi yhden turhan kierroksen ja on siksi kirjattu myös koodiin.
- **Väylä ja SYKE palauttavat GeoJSONin suoraan EPSG:3067:ssä** (`outputFormat=application/json`) eli projektin omassa koordinaatistossa. Projisointia ei tarvita kummassakaan päässä — selain saa parit muodossa `[itä, pohjoinen]` ja `L.CRS.Simple` lukee `[pohjoinen, itä]`, joten ainoa muunnos on parin järjestyksen vaihto.
- Väyläluokat `vaylaluokkakoodi` 1–6. **VL1/VL2 ovat kauppamerenkulkua** eli satojen metrien rahtialuksia; loput veneilyä. Ero on turvallisuusasia, ei luokitteludetalji, ja se säilytetään selaimeen asti.
- WFS:n `numberMatched` luetaan ja verrataan saatuun määrään. Ilman tätä palvelimen hiljainen katkaisu näyttäisi täysin normaalilta tasolta.

### Löydös 1: maihinnousukieltoa ei ole olemassa paikkatietona

Tämä on toteutuksen tärkein rajoite. SYKE:n aineisto antaa alueen **rajauksen, nimen, tyypin ja säädöksen** — mutta **ei kenttää joka kertoisi saako rantautua**. Varsinaiset maihinnousukiellot ovat Metsähallituksen järjestyssäännöissä, jotka julkaistaan PDF-julkaisuina ja Luontoon.fi:ssä. Metsähallituksen avoin biotooppiaineisto **rajaa merialueet nimenomaisesti ulos** — eli tasan tämän sovelluksen toimialueen.

Seuraus käyttöliittymään: taso ja tietopallo kertovat **missä** ollaan ja kehottavat tarkistamaan järjestyssäännön. **Ne eivät koskaan sano että rantautuminen on sallittua tai kiellettyä.** Väärä "sallittu" olisi pahempi kuin ei tietoa lainkaan.

### Löydös 2: koealue on juuri se paikka jossa aineistot ovat ohuimmat

Ahvenanmaa hoitaa omat aineistonsa itse. Mitattuna samankokoisilla alueilla:

| | Ahvenanmaa (koealue) | Turun saaristo |
|---|---:|---:|
| Natura-lintualueet | 0 | 5 |
| Luontodirektiivialueet | 0 | 18 |
| Valtion suojelualueet | 0 | 16 |
| Yksityiset suojelualueet | 3 | 202 |
| OSM-palvelukohteet | 17 | 390 |

**Todentaminen on siksi tehtävä mannersaaristossa** — koealueella tyhjä taso on oikea tulos eikä todista toteutusta toimivaksi. Väylät sen sijaan kattavat Ahvenanmaan (omistaja on maakuntahallitus, jakelu saman rajapinnan kautta).

### Ratkaisut jotka eivät ole ilmeisiä

- **Tasot ovat päälle/pois, eivät näkymiä.** `currentDisplay` on toisensa poissulkeva rasterinäkymä, mutta rahtiväylä on yhtä tärkeä melottavuusnäkymässä kuin rantautumispisteytyksessä. Siksi erillinen `activeOverlays`-joukko, joka tallentuu samaan näkymätilaan.
- **Oma Leaflet-pane eksplisiittisellä z-indeksillä**: rasteri 400 < vektorit 450 < tietopallot 600. Ilman tätä kuvakerrokset ja vektorit olisivat samassa panessa ja järjestys jäisi `leaflet.css`:n varaan — sama tiedosto aiheutti aiemmin piiloon jääneen elementin (ks. Vaihe 5:n tietopallo-ongelma).
- **Kaksi yksinkertaistustoleranssia.** Suojelualueet ovat kilometrien mittaisia ja rantaviivaa seuraavia → 20 m. Väyläalueet ovat kapeita käytäviä, ja juuri reuna on se mitä väistetään → 5 m. Mitattu: Saaristomeren kansallispuiston koordinaattiluvut **653 020 → 21 576 (3,3 %)**, pinta-ala muuttui **−0,225 %**.
- **Pallon koko lasketaan lisäriveistä, ei lohkoista.** Aiempi "jokainen lohko painaa saman verran" piti paikkansa kahdella kaksirivisellä lohkolla, mutta suojelualuelohko on nelirivinen ja valui ympyrän alareunan yli.

### Rajaukset

- **Pisteytys ei muutu.** Python↔JS-sopimus koskematon, ei uutta tekijää eikä kynnysten uudelleenlaskentaa.
- **Ei lajihavaintoja.** laji.fi karkeistaa arkaluontoisten lajien sijainnit nimenomaan siksi, ettei pesiä löydettäisi kartalta. Karkeistettu piste olisi sekä hyödytön että helposti väärin luettu.
- **Ei merimerkkejä**: `turvalaitteet_uusi` antoi 173 kohdetta pelkällä koealueella. Milloin merimerkki auttaa melojaa ja milloin se on ruuhkaa, on oma suunnittelukysymyksensä.
- **Koko**: kolme JSONia yhteensä 63 kt eli häviävän pieni osa `docs/`-hakemiston 93 Mt:sta.

---

## 5d. Itälaajennus ja MML-latausputki (2026-08-05)

Demo kattoi 18×30 km Ahvenanmaalta (11 tiiltä), ja aineisto oli ladattu käsin Karttapaikasta. Nyt mukana on myös **Helsingin edusta** — yhteensä **38 tiiltä**, `docs/` 353 Mt.

### Lataus rajapinnasta

`backend/mml_lataus.py` hakee kolme aineistoa MML:n tiedostopalvelusta (sama OGC API Processes -rajapinta kuin laserkeilauksella): `korkeusmalli_2m_karttalehti` (TIFF), `maastokartta_rasteri_karttalehti` + `dataSetInput=maastokartta_rasteri_10k_painovari` (PNG) ja `maastotietokanta_bbox` + `themeInput=rakennukset` (GPKG). Rajat: **100 lehteä/haku**, maastotietokannalla 17 334 km².

`backend/mml.py` sisältää avaimen käsittelyn ja työn ajamisen — **yksi toteutus, koska avain on salaisuus**. `lidar.py` käyttää nyt samaa.

### Karttalehtijako johdettu aineistosta

`backend/karttalehti.py` laskee TM35FIN-lehtinimen koordinaateista ja päinvastoin. Sääntö on **johdettu olemassa olevista tiedostoista, ei dokumentaatiosta**, ja se toistaa kaikki 20 tunnettua lehteä metrin tarkkuudella molempiin suuntiin (`python3 -m backend.karttalehti`).

Tämä ei ole ylityötä: Helsingin edusta osuu **kahden ykköstason lehden rajalle** (`K42…`/`L41…`), koska raja kulkee y = 6 666 000 eli kaupungin läpi. Arvaamalla se olisi mennyt väärin.

### Karttatuote todennettiin ennen joukkolatausta

Vesimaski tunnistaa meren **väristä**. Väärä tuotevariantti ei kaataisi mitään vaan tekisi kaikista pistemääristä vääriä. Siksi `--todenna-kartta` lataa yhden lehden, joka meillä jo on, ja vertaa `detect_water_fill_mask`-maskit pikselitasolla. Tulos: **0 eroavaa pikseliä 144 miljoonasta.**

### Rannaton tiili kaatoi ajon — ja paljasti hiljaisen vian

`build_static.py` kaatui kolmen tunnin jälkeen tiileen `L4131F` (Helsingin sisämaata, 100 % maata):

```
ValueError: index -6000 is out of bounds for array with size 36000000
```

**`distance_transform_edt` ei kaadu jos taustapikseleitä ei ole yhtään** — se mittaa etäisyyden haamupisteeseen rivillä −1 ja palauttaa `indices[0] = -1`. Etäisyydet näyttävät täysin uskottavilta (1, 2, 3 … metriä yläreunasta). Toistettu erikseen pienoiskoossa.

Sama haamu tuotti hiljaisen vian, joka ei kaatanut mitään: neljä rannatonta tiiltä sai **160 puskuripikseliä** yläreunaansa, ja ne olivat mukana kynnysarvojen laskennassa. Molemmat korjattu eksplisiittisellä haaralla `pipeline.py`:ssä.

Kolmas `distance_transform_edt`-kutsu (`score_engine.py`, rakennusetäisyys) on **turvassa vahingossa**: taulukkoa paddataan `DIST_IDEAL_M` = 150 m, joten haamu jää rajauksen ulkopuolelle. Mitattu `L3124G`:llä (Ahvenanmaan tiili ilman rakennuksia): kaikki 367 261 puskuripikseliä saivat tasan 1,0.

### Laajennus paljasti reunavirheen vanhassa aineistossa

Todennuksen tärkein invariantti oli, ettei Ahvenanmaa saa muuttua (yli 200 km päässä, `MAX_FETCH_M` 15 km). Pyyhkäisymatkat olivatkin **bitilleen identtiset**. Estekorkeuksista sen sijaan **0,53 % arvoista muuttui, ja jokainen muuttunut arvo oli pienempi** (8 603 pienempää, 0 suurempaa, keskiarvo −0,32 m).

Syy on `_march_ray`-funktion esteenetsinnässä: se rajaa katseen `np.clip`illä taulukon reunaan. Vanha 18×30 km mosaiikki oli niin pieni, että reunalla katse leikkautui takaisin maalle ja **yliarvioi esteen korkeuden**. Kaikki muuttuneet solut ovat alle 15 km vanhan reunan etäisyydellä (mediaani 1 495 m, muuttumattomilla 4 910 m).

**Ahvenanmaan tuulensuoja on siis ollut liian optimistinen alueen reunoilla.** Virhe on näkymätön niin kauan kuin katsoo vain yhtä aluetta — ja se koskee edelleen koko aineiston ulkoreunaa, ei vain entistä. Jos tämä halutaan poistaa, mosaiikkia on levennettävä `MAX_FETCH_M`:n verran tiilijoukon ympärille.

### Välimuistin mitätöinti

Tiilien lisääminen muuttaa merimosaiikin origon ja muodon, jolloin kaikki mosaiikkiin sidotut välimuistit vanhenevat **hiljaa**. `--mitatoi` poistaa ne. Per-tiili `*_raw.npz` ja `*_lidar.npz` säilyvät (eivät riipu mosaiikista) — se säästää nykyisten tiilien kalleimman työn.

Listalta jäi ensin pois `{tiili}_water.npz` ja `{tiili}_fetch.npz`, koska kuviot osuivat vain PNG-nimiin. Juuri sellaista hiljaista vanhentumista tämä listaus torjuu.

---

## 5e. Vektorirantaviiva, vanhan toteutuksen poisto ja skaalautuvuus (2026-08-06)

### Rantaviiva ja meri tulevat nyt vektorista (`backend/vesisto.py`)

Ne luettiin peruskartan **väreistä**. Helsingin aineisto hajotti sen kahdella tavalla:

- **Meri ja järvi ovat samanvärisiä**, joten ne erotettiin 50 ha pinta-alalla. Tiiliruukinlahti Herttoniemessä jäi 9,9 ha "sisävedeksi" — Itäväylän penger katkaisee lahden suun — eikä sinne ulottunut rantaviiva eikä aallokkoanalyysi.
- **Vesistöjen nimet on painettu tasan samalla sinisellä kuin rantaviiva.** Sanat "Purolahti" ja "Bäckviken" tulkittiin rantaviivaksi keskellä lahtea.

Viisi erottelijaa kokeiltiin tekstin poistamiseksi — maan läheisyys, sama avattuna, viivan paksuus (mediaani 1,0 px molemmilla), valkoinen reunus (0,210 vs 0,177, jakaumat päällekkäin) ja etäisyys vesialueen rajasta (5 m kynnys poisti 58 % aidosta). **Yksikään ei toiminut.**

Maastotietokannan `hydrografia`-teemassa meri on oma tasonsa ja järvi omansa. Vaihto poisti kolme kiertotietä: `SEA_MIN_AREA_M2`, `SEA_CLOSING_RADIUS_M`, `SEA_BRIDGE_M` ja funktiot `compute_sea_mask`/`sea_closing`.

**Todennettu vanhaa vasten:** Ahvenanmaalla (L3123F) uusi rantaviiva on samassa paikassa kuin vanha (mediaanietäisyys **0,0 m**, 90 % 0,0 m). Helsingissä mediaani 2,8 m mutta 90 % piste 281 m — ero on tasan ne lahdet jotka rasteripolku hukkasi. Rantaruutuja tuli 473 006 → 500 153 (**+5,7 %**).

Lataus on 0,03 Mt/km² eli 41 Mt koko nykyalueelle. Kallio ja suo luetaan edelleen rasterista; niillä ei ole vastaavaa nimitörmäystä.

### Vanha toteutus poistettu, uusi ruudukko 2,0 m

Kartta piirrettiin kahdella tavalla rinnakkain. Esilasketut paksuus × prosentti × taso -kuvat (50/tiili) olivat **53 % koosta ja 2/3 laskenta-ajasta**.

Ennen poistoa mitattiin laatu, koska ehtona oli ettei se heikkene. 3,5 m ruudulla pistemäärät olivat käytännössä samat (mediaaniero 0,003) mutta **rantakaistale leveni 44 %** ja reunat porrastuivat näkyvästi; väriluokka vaihtui 3,2 %:ssa pikseleistä.

Ratkaisu ei ollut valita kahdesta huonosta vaan tihentää ruutua: `NEW_PIXEL_FACTOR` 3,5 → **2,0**. Mitattu koko: factors+tiebreak 1,22 Mt/tiili (3,5 m: 0,57), ja `docs/` on silti pienempi kuin ennen — säästö ei tullut karkeasta ruudusta vaan yhdistelmäräjähdyksen poistosta. Jopa 1 m tarkkuudella tulos olisi ollut nykyistä pienempi.

Tulos: `docs/` 358 → **256 Mt**, tiedostoja 8 930 → **570**.

Poistettu: `get_or_compute_overlay`, `get_or_compute_top`, `/api/overlay/*`, toteutusvalinta legendassa, `currentImpl`/`oldScoreGroup`/`oldTopGroup`/`refreshOld`.

### Mosaiikki alueellistettu — koko rannikko on nyt mahdollinen

Merimosaiikki on tiheä taulukko tiilien rajaaman suorakaiteen yli. Itärajalta Tornioon se olisi 39 566 × 71 680 = **2,8 miljardia solua** eli korkeusmalli float32:na **11,3 Gt**, vaikka käytävä on siitä ~5 %.

Laskenta jaetaan nyt alueisiin (`_laskenta_alueet`, `MAX_MOSAIC_CELLS = 300 M`). Kukin kattaa osan tiilistä (**ydin**) ja lisäksi kaikki alle `MAX_FETCH_M` päässä olevat (**konteksti**), jotta reunan säteet näkevät oikean maan. **Alueet limittyvät tahallaan.**

**Todennettu:** alueittain laskettu tulos on bitilleen identtinen kerralla laskettuun (104 098 solua, fetch ja obstacle max ero 0,000000).

Sivutuotteena per-tiili-laskenta irrotettiin globaalista mosaiikista: `_tile_mosaic_cells` laskee tunnisteet koordinaateista ja vesiruudukon merimaski rasteroidaan suoraan vektorista.

### Lähteen sormenjälki — välimuisti tunnistaa päivityksen

Välimuisti tarkisti **vain tiedoston olemassaolon**. Jos MML julkaisi uuden karttalehden tai korjatun rantaviivan, putki käytti vanhaa tulosta hiljaa.

Nyt jokaisen tiilen `_raw.npz`:ään tallennetaan `lahde_sormenjalki` (koko + muokkausaika DEM:stä, karttalehdestä, rakennuksista ja hydrografiasta). Muuttunut tiili käsitellään pyyhkäisymatkoissa samoin kuin lisätty — koneisto oli jo olemassa, vain muutossignaali puuttui.

Sormenjälki ei ole sisällön tiiviste: lähteet ovat satoja megatavuja ja tiivisteen laskenta maksaisi enemmän kuin uudelleenlaskenta. Hinta on että aikaleiman hävittävä kopiointi näyttää muutokselta. **Se on oikea suunta erehtyä.**

### Eräajo toiselle koneelle (`eraajo.sh`)

Koko putki yhdellä komennolla: lataus → laserkeilaus → mitätöinti → build. Toistettava ja keskeytettävä.

Raportoi etenemisen rivi kerrallaan jokaisessa pitkässä vaiheessa — **pyyhkäisymatkat tulostavat ilmansuunnan kerrallaan (48 kpl) aika-arvioineen**, koska se on ajon pisin hiljainen jakso. Kaikki Python-kutsut `-u`-lipulla: puskuroitu tuloste näyttää tunteja tyhjältä lokilta.

Edellytystarkistus ajetaan **ennen** tuntien työtä. Se löysi ensimmäisellä ajolla puuttuvat `Pillow` ja `laspy[lazrs]` — todellinen puute `requirements.txt`:ssä, joka ei näkynyt kehityskoneella koska paketit olivat asennettuina muuta kautta.

### Julkaisu: Railway, ei vielä käytössä (linjaus 2026-08-06)

- **Railway on ensisijainen julkaisualusta.** Palvelua ei ole vielä tilattu; se otetaan käyttöön kun projekti on pidemmällä.
- **GitHub on vain sisäinen versionhallinta.** Ei GitHub Pages -julkaisua.
- **Mitään ei julkaista Railwayhin nyt.**
- `docs/` on `.gitignore`ssa, joten sivustoa ei ole verkossa. Sovellus ajetaan paikallisesti (`python3 -m http.server docs/` tai kehityspalvelin).

Mitoitus julkaisua varten: nykyinen 38 tiiltä = 256 Mt, 20 km käytävä (244 lehteä) ≈ 1,6 Gt, koko rannikko (867 lehteä) ≈ 5,8 Gt.

---

## 5f. Levätilanne: kaksi satelliittia ja rannalta tehdyt havainnot (2026-08-07)

`backend/leva.py`, ajetaan **erillään buildista**:

    python3 -m backend.leva --paivita [--ulos docs] [--paivia 7]

Buildista erillään siksi, että täysi ajo kestää tunteja ja koskee aineistoa
joka muuttuu harvoin; levä muuttuu päivittäin. **Ajojärjestys: build ensin,
levä sen jälkeen** — `build_static.py` tyhjentää `docs/`-hakemiston.
Railwaylla tämä on cron-palvelun komento; komennossa ei ole
ajastinoletuksia.

### Luokka-asteikko mitattiin, ei oletettu

GeoTIFF-arvo on legendan luokka **miinus yksi**. Tämä piti ratkaista
mittaamalla, koska tiedoston `nodata`-tagi on 4 ja SYKE:n legendassa luokka
4 on "Varmaa" — väärä tulkinta olisi värittänyt kartan tasan päinvastoin.
Todennettu hakemalla sama rajaus sekä GeoTIFFinä että PNG:nä ja vertaamalla
pikseli pikseliltä: **100 % vastaavuus jokaisella luokalla**, myös 2:lla ja
3:lla, ja erikseen molemmille satelliittikerroksille.

| Arvo | Väri | Merkitys |
|---:|---|---|
| 0 | `#2B7EA8` | ei levää |
| 1 | `#FFFFC9` | mahdollista |
| 2 | `#FFDC28` | todennäköistä |
| 3 | `#CD3301` | varmaa |
| 4 | `#FFFFFF` | ei dataa |

### Kattavuusongelma ei ollut pilvi vaan vesimaski

Pelkkä Sentinel-3 kattaa Helsingin edustalla 27 % merialueesta ja alle
kilometrin päässä rannasta vain 10 %. Ikkunan pidentäminen 7:stä 21
vuorokauteen **ei lisännyt kattavuutta lainkaan**. Syy: eri päivien
datalliset ruudut ovat aina saman joukon **osajoukkoja** (Jaccard 1,000) —
pilvi vain poistaa ruutuja, ei koskaan lisää. Matalassa vedessä pohjan
heijastus estää tulkinnan.

Lisälähde auttoi siihen mihin pidempi ikkuna ei:

| | OLCI 300 m | + Landsat 8, 30 m |
|---|---:|---:|
| koko merialueesta | 27 % | **56 %** |
| alle 2 km rannasta | 15 % | 45 % |
| alle 1 km rannasta | 10 % | 27 % |
| alle 0,5 km rannasta | 7,5 % | 10,6 % |

Ahvenanmaalla ero on suurempi (0,5 % → 12 %), koska sen merialue on lähes
kokonaan kapeaa saaristovettä.

**Tuoreus ratkaisee ennen tarkkuutta**: päivät käydään uusimmasta
vanhimpaan ja saman päivän sisällä tarkin lähde ensin. Ikä on
luotettavuuden päätekijä, joten vuorokauden vanha OLCI voittaa kahden
vuorokauden ikäisen Landsatin. Ruudukko on 60 m eli Landsatin rehellinen
alinäytteistys; karkeampi yhteinen ruudukko olisi heittänyt pois sen mitä
Landsatista haettiin.

### Kansalaishavainnot täyttävät rantavyöhykkeen

Open311, `rajapinnat.ymparisto.fi/api/kansalaishavainnot`, palvelukoodi
`algaebloom_service_code_201808151546171`, ei avainta, **CC0**.

Havainnot osuvat tasan sinne minne satelliitti ei näe: Helsingin edustalla
27 merihavaintoa, **mediaanietäisyys rannasta 60 m**, kaikki alle 180 m.

Kaksi rajoitetta, jotka on kirjattu koodiin:

- **Harvuus.** Näkemättömästä rannikkovedestä 23 % on alle 2 km:n päässä
  havainnosta, 68 % alle 5 km:n. Siksi pisteet **piirretään pisteinä eikä
  levitetä** — interpolointi olisi keksittyä tietoa, ja rannan tuntumassa
  se olisi vaarallisinta.
- **Eri asteikko kuin satelliitilla.** Luettu rajapinnan omasta
  määrittelystä (`services/<koodi>.xml`): 1 = ei sinilevää, 2 = hieman,
  3 = runsaasti, 4 = erittäin runsaasti. Ihminen arvioi **määrää**,
  satelliitti **todennäköisyyttä**. Nimet pidetään erillään; väriramppi on
  sama, koska molemmat kulkevat ei-levää → paljon-levää.

**Rajapinnassa on 1 000 havainnon katto eikä sivutus toimi**: `page`
palauttaa joka kerta saman joukon (todennettu sivuille 1–3, päällekkäisyys
1000/1000). Kierretään kahden vuorokauden ikkunoilla, joiden
päällekkäisyys on mitattuna 0. Jos katto silti tulee vastaan, siitä
varoitetaan lokissa.

Ahvenanmaalla havaintoja on 0 — sama valtakunnallisten aineistojen katve
kuin suojelualueilla.

### Luotettavuusmalli

    sekoitusannos = Σ max(0, U − 5 m/s) · Δt
    luotettavuus  = exp(−annos/40 − ikä_vrk/10)

Pintalevä sekoittuu syvemmälle kun tuuli ylittää noin 5 m/s. Tuuli
**mitätöi havainnon molempiin suuntiin** — nähty levä on voinut sekoittua
pois, ja "ei levää" -havainnon jälkeen tyyni on voinut nostaa kukinnan
pintaan. Siksi luotettavuus **ei riipu havaitusta luokasta**. Sama malli
koskee myös kansalaishavaintoja: annos luetaan samasta tuulikentästä.

| Tilanne | Luotettavuus |
|---|---:|
| 1 vrk, tyyni | 0,90 |
| 3 vrk, tyyni | 0,74 |
| 7 vrk, tyyni | 0,50 |
| 3 vrk, joista 12 h 12 m/s | 0,09 |

**Vakiot ovat arvioita, eivät julkaistu standardi** — sama asema kuin
`PADDLE_WAVE_LIMITS`-rajoilla. Mallia ei ole kalibroitu mittausaineistoa
vastaan.

**Tunnettu vinouma:** tuulen havaintoasemat ovat rannikolla ja saarissa,
joten avomerituuli aliarvioituu ja luotettavuus on siltä osin liian
optimistinen. Tätä ei korjata keksityllä kertoimella vaan dokumentoidaan.

### Esitystapa: kolme eri syytä olla värittämättä

Datakuvan kanavat (`R` = luokka, `254` = maa, `255` = merta ilman
havaintoa; `G` = ikä vrk; `B` = luotettavuus; **`A` = 255 aina**, koska
esikerrottu alfa turmelisi RGB:n `getImageData`:ssa).

Selaimessa nämä erotellaan toisistaan:

- **maa** — ei piirretä lainkaan
- **näkemättä jäänyt** — vino **viirutus**, ei tasaväri
- **epävarma havainto** — värillinen mutta haalea

Viirutus siksi, että tasaväri asettuisi samalle asteikolle värikoodattujen
luokkien kanssa ja käyttäjä lukisi sen arvona ("harmaa = vähän levää").
Ensimmäinen yritys oli harmaa tasaväri alfalla 0,20, ja se hukkui
peruskartan sinisen alle kokonaan.

**Pisteet menevät `vektorit`-paneen, eivät `leva`-paneen.** Samassa panessa
kuvakerros ja SVG jäisivät `leaflet.css`:n säännön
`.leaflet-map-pane svg { z-index: 200 }` varaan — mitattuna svg 200 ja img
1, eli pisteet olisivat rasterin päällä **vahingossa**. Juuri tämä sääntö
on projektissa jo kerran aiheuttanut piiloon jääneen elementin.

### Rajaukset

- **Pisteytys ei muutu.** Levä on olosuhde kuten tuuli, ei rannan pysyvä
  ominaisuus. Python↔JS-sopimus pysyy koskemattomana.
- **Ei kasautumisen ennustamista.** Tuulta käytetään vain havainnon
  vanhenemiseen, mikä on paljon heikompi ja siksi puolustettava väite.
- **Vikasietoisuus:** jos SYKE tai FMI ei vastaa, vanha tiedosto jää
  voimaan eikä kartta kaadu. Kansalaishavaintojen puuttuminen ei kaada
  satelliittikerrosta.

---

## 6. Natiivisovellus (iOS/Android) — tuleva suunta, EI toteuteta vielä

**Tilanne (kirjattu muistiin 2026-07-28)**: web-pohjaista sovellusta kehitetään edelleen ensisijaisesti, eikä natiivin kehitystä aloiteta lähiaikoina. Tämä kohta dokumentoi tehdyn arvioinnin, jotta web-kehityksen aikana tehtävät arkkitehtuuripäätökset voivat ottaa tulevan natiivitarpeen huomioon eivätkä vahingossa sulje sitä pois. **Ei aktiivinen tehtävälista — ei toteuteta ilman erillistä pyyntöä.**

### Kääntämistavat
Web-sovellus (Leaflet.js) ei käänny suoraan natiiviksi — kyse olisi uudelleenkirjoituksesta. Kolme vaihtoehtoa:
1. **WebView-kääre** (Capacitor/Cordova) — nopein, ei ratkaise mitään suorituskykyongelmaa (sama selainmoottori kuin nyt), hyötynä vain sovelluskauppa + kotinäyttöikoni.
2. **Cross-platform** (React Native/Flutter + MapLibre-sidokset) — yksi koodikanta molemmille alustoille, kohtuullinen työmäärä, oikeasti natiivi kartanpiirto.
3. **Täysin natiivi** (Swift+MapKit / Kotlin+Google Maps SDK) — paras suorituskyky/integraatio, kaksinkertainen ylläpito (kaksi koodikantaa).

### Karttanäkymän sulavuus natiivina
Tässä projektissa "oikea" XYZ-tiilipyramidi (ks. kohdan 4 Vaihe 4: "Kokeiltu ja hylätty") osoittautui epävakaaksi NIMENOMAAN selaimessa (CSS `feMorphology`-suodatin + murtolukuzoomi aiheuttivat pätkimistä). Natiivit karttamoottorit (MapKit, Google Maps SDK, MapLibre Native) on rakennettu juuri GPU-kiihdytetylle tiilirenderöinnille eikä niissä ole selaimen kompositio-overheadia — sama arkkitehtuuri, joka jouduttiin perumaan web-versiosta, saattaisi toimia hyvin natiivina. Natiivi GPU-kuvankäsittely (Core Image/Metal, RenderScript/OpenGL) tekisi myös rantaviivan paksuuden ja "parhaat rannat %" -kynnyksen **portaattoman** säädön halvaksi — näissä jouduttiin web-versiossa tyytymään kiinteisiin esiasetuksiin (`THICKNESS_PRESETS`, `TOP_PERCENT_PRESETS`, ks. kohta 4).

**Este**: `EPSG:3067`/`L.CRS.Simple` (peruskartta natiivissa projektiossa, ei WGS84-muunnosta — ks. kohdan 4 WGS84-löydökset) ei ole suoraan tuettu MapKit/Google Maps SDK:ssa (olettavat Web Mercator/WGS84:ää) — vaatisi joko MapLibre Nativen (custom-projektiotuki, monimutkaisempi pystyttää) tai paluun geodeettiseen reprojisointiin (se, mistä alun perin luovuttiin monimutkaisuuden takia).

### Mitä natiivi tarjoaisi
- **Offline-kartat** (tiilien esilataus/välimuistitus laitteelle) — erityisen relevantti veneilijälle/melojalle avomerellä ilman verkkoyhteyttä.
- GPS/sijaintiintegraatio, kompassisuunnattu kartta.
- Sovelluskauppanäkyvyys, kotinäyttöikoni.
- Vektoritiilet voisivat pienentää datamäärän murto-osaan nykyisestä rasteripohjaisesta mallista.

### Ongelmat/haasteet
- **Jakelukoko**: nykyinen ~600 Mt (ks. Vaihe 5) ei mahdu sovelluspakettiin (Applen mobiilidatalataus-raja 200 Mt) — natiivisovelluskin tarvitsisi siis palvelimen/CDN:n tiilien tarjoiluun, ellei siirryttäisi kevyempään (vektori-)muotoon.
- Kaksinkertainen ylläpito (tai cross-platform-kompromissi), sovelluskaupparekisteröinnit (Apple Developer $99/v, Google Play $25 kertamaksu), katselmointiviiveet päivityksille (vrt. nykyinen "push ja live parissa minuutissa").
- Esilaskentaputki (`backend/score_engine.py`, `backend/pipeline.py`, rasterio/OpenCV) pysyy joka tapauksessa ennallaan taustalla — natiivisovellus on vain toisenlainen asiakas samalle datalle, ei korvaa laskentaa.

### Maantieteellisen laajuuden vaatimus (koko Suomenlahti + Suomen länsirannikko + koko Ahvenanmaa)
Käyttäjä on vahvistanut tavoitteeksi kattaa koko Suomenlahti, Suomen länsirannikko ja koko Ahvenanmaan alue melontakäyttöön. Tämä on karkeasti **50–100× nykyisen 11-tiilen alueen** (n. 18×30 km) rantaviivan pituus.

**Havaittu ongelma**: nykyinen "esilaske kaikki paksuus×prosentti-yhdistelmät kokonaiskuvina" -strategia (ks. Vaihe 5, ~600 Mt / 11 tiiltä) EI skaalaudu tähän laajuuteen — suoraan skaalattuna tuottaisi kymmeniä–satoja gigatavuja, mikä ei ole ylläpidettävissä GitHub Pagesilla eikä sovelluksen mukana jaeltavaksi. Tämä on riippumaton web/natiivi-valinnasta ja pitäisi ratkaista ensin joka tapauksessa.

**Harkittu ratkaisu (EI vielä toteutettu)**: siirtyminen vektoritiiliin + ajonaikaiseen tyylitykseen (esim. MapLibre-tyyliset vektoritiilet) sen sijaan että pistemäärä/rantaviivageometria + paksuus/prosenttikynnys esilasketaan erillisiksi rasterikuviksi. Paksuus ja prosenttivalinta olisivat pelkkiä tyyliparametreja jotka sovelletaan ajonaikaisesti — poistaa koko yhdistelmäräjähdyksen (5×10=50 kuvaa → 1 vektoridatasetti + tyylisääntö), pienentää datamäärän murto-osaan, JA mahdollistaisi portaattoman paksuus-/prosenttisäädön uudelleen. Toimisi identtisesti web:ssä (MapLibre GL JS) ja natiivissa (MapLibre Native) samalla tyylimäärittelyllä — tämä on tärkeä syy harkita vektoritiilipohjaista arkkitehtuuria jo NYT web-kehityksessä, koska se palvelisi sekä laajuusongelmaa että tulevaa natiivisiirtymää samalla kertaa.

**Alueellinen offline-lataus**: koko alueen täydellinen offline-tuki ei liene mahdollinen edes vektoritiilillä (edelleen iso kokonaisdatamäärä), mutta käyttäjä voisi valita ja ladata pienempiä, itselleen relevantteja alueita offline-käyttöön (precedentti: OsmAnd, Gaia GPS, Maps.me). Vaatisi: alueiden nimeämisen/rajauksen (nimetyt saaristo-/rannikkoalueet TAI kiintoruudukko), koon arvioinnin ennen latausta, ladattujen alueiden hallinnan (poisto levytilan vapauttamiseksi).

**Hostaus laajemmassa mittakaavassa**: data ei enää mahdu järkevästi git-repoon/GitHub Pagesiin — tarvitsisi CDN:n/objektivaraston (esim. Cloudflare R2, AWS S3+CloudFront). Tämä on silti STAATTISTA dataa (ei laskentapalvelinta), vain isompi/parempi staattinen hostaus.

### Ohjenuora web-kehitykselle nyt (kunnes natiivi mahdollisesti aloitetaan)
- Vältä arkkitehtuuripäätöksiä jotka tekisivät tulevasta vektoritiilimigraatiosta tarpeettoman vaikean — pidä pisteytys/esilaskentaputki (`score_engine.py`/`pipeline.py`) käsitteellisesti erillään renderöintimuodosta, jotta sama data voisi periaatteessa syöttää sekä nykyistä rasterikuvantuotantoa että tulevaa vektoritiilituotantoa.
- Muista ettei "täysin itsenäinen staattinen sivusto" -ominaisuus (ks. Vaihe 5) säily sellaisenaan jos/kun alue laajenee koko Suomenlahti+länsirannikko+Ahvenanmaa-tasolle — CDN/objektivarasto on silloin todennäköisesti edessä joka tapauksessa, riippumatta natiivipäätöksestä.
