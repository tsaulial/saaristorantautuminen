"""
Rakentaa taysin staattisen docs/-hakemiston GitHub Pagesia varten.

Ajaa kaiken GIS-laskennan kertaalleen paikallisen output/cache/-valimuistin
kautta (backend/pipeline.py) ja kopioi tulokset seka kevennetyn, staattisia
polkuja kayttavan frontendin docs/-kansioon. Tuotannossa ei tarvita Pythonia,
GDAL:ia eika mitaan palvelinta - pelkat staattiset tiedostot.

Kansio on nimeltaan "docs" (ei "dist"), koska GitHub Pagesin "Deploy from a
branch" -tila tukee vain juurikansiota tai nimenomaan /docs-kansiota - ei
mielivaltaisia kansionimia.

docs/-kansio tyhjennetaan ja luodaan uudelleen joka ajolla.

Kaynnistys projektin juuresta:
    python3 build_static.py
"""

import hashlib
import io
import json
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image

from backend import pipeline, tiles, vektoritasot

ROOT = Path(__file__).resolve().parent
BUILDINGS_PATH = ROOT / "rakennukset-mll" / "rakennukset.gpkg"
DOCS_DIR = ROOT / "docs"
DOCS_CACHE_DIR = DOCS_DIR / "cache"

# --- VISUAALISET KERROKSET HAVIOTTOMANA WEBP:NA ---
#
# docs/ oli 593 Mt, josta 580 Mt (98 %) oli visuaalisia kerroksia:
# peruskartta ja esilasketut varikerrokset, jotka selain vain NAYTTAA
# (L.imageOverlay). PNG on niille tehoton muoto.
#
# HAVIOTON VOITTI HAVIOLLISEN JOKAISELLA KUVATYYPILLA - mikä on epäintuitiivista
# mutta selittyy sillä, ettei aineistossa ole yhtään valokuvaa: peruskartta on
# rasteroitua kartografiaa ja kerrokset synteettisiä tasavärialueita, joissa
# hävioton ennustus toimii erinomaisesti ja häviöllinen tuhlaa bittejä
# reunojen "soinnutteluun". Mitattu (Pillow, 6000x6000):
#
#   peruskartta   9,84 Mt -> 2,28 Mt (4,3x)   [haviollinen q80: 3,99 Mt]
#   varikerros    6,73 Mt -> 0,46 Mt (14,7x)  [haviollinen q80: 0,77 Mt]
#   top-kerros    0,29 Mt -> 0,04 Mt (8,2x)   [haviollinen q80: 0,22 Mt]
#
# Kuva ei siis huonone lainkaan - visuaalisesta laadusta ei tarvinnut tinkia.
#
# TODENNETTU pikselitasolla lahde-PNG:ta vastaan: alfakanava on identtinen ja
# NAKYVIEN pikselien RGB-ero on tasan 0. Tiedostot eivat silti ole tavu
# tavulta samoja, koska libwebp nollaa RGB:n TAYSIN LAPINAKYVIEN pikselien
# alta (A=0) pakkauksen parantamiseksi - ne arvot eivat paady ruudulle
# koskaan. Juuri tama kaytos olisi kuitenkin tuhonnut DATAKUVAT, joissa
# "lapinakyvakin" pikseli kantaa merkitsevaa dataa - toinen syy sille, etta
# raja kulkee visuaalisen ja datan valissa eika muodon mukavuuden.
#
# DATAKUVAT (_factors, _tiebreak, _prime, _fetch*, _water*) jaavat PNG:ksi.
# Niista selain lukee pikseliarvoja getImageData:lla ja purkaa nibble-pakatut
# kentat, joten muodon on oltava haviotön - havioton WebP kelpaisi sinansa,
# mutta niita on yhteensa vain 12,7 Mt eika muutos toisi mitaan.
#
# Raja kulkee tasan siina, kutsuuko frontend kuvalle loadImageData()
# (= pikseliarvot, PNG) vai L.imageOverlay() (= pelkka esitys, WebP).
WEBP_METHOD = 1   # 0-6; 1 on paras kompromissi (m=0 on 4x nopeampi mutta 20-40 % isompi)

# Enkoodaus maksaa 6000x6000 kuvalla 1-2,7 s ja kuvia on ~2 460, joten
# lampiman buildin kesto olisi noussut sekunneista tunteihin. Kaksi keinoa:
# rinnakkaisajo (ks. write_webp_batch) ja tama valimuisti. Avain on
# PNG-tavujen tiiviste, joten valimuisti on SISALTOOSOITTEINEN: jos lahdekuva
# muuttuu, avain muuttuu eika vanhentunutta tulosta voi vahingossa kayttaa -
# erillista mitatointia ei tarvita.
WEBP_CACHE_DIR = ROOT / "output" / "cache" / "_webp"


def to_webp(png_bytes):
    """PNG-tavut -> haviotön WebP. Vain visuaalisille kerroksille (ks. yllä)."""
    key = hashlib.sha1(png_bytes).hexdigest()
    cached = WEBP_CACHE_DIR / f"{key}_lossless_m{WEBP_METHOD}.webp"
    if cached.exists():
        return cached.read_bytes()

    buf = io.BytesIO()
    Image.open(io.BytesIO(png_bytes)).save(
        buf, "WEBP", lossless=True, method=WEBP_METHOD
    )
    out = buf.getvalue()

    WEBP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(out)
    return out


def _encode_job(job):
    name, png_bytes = job
    return name, to_webp(png_bytes)


def write_webp_batch(jobs, pool):
    """Muuntaa ja kirjoittaa erän visuaalisia kuvia rinnakkain.

    Era on yksi resoluutiotaso yhdesta tiilesta (56 kuvaa), jolloin muistissa
    on kerrallaan korkeintaan muutama kymmenen megatavua PNG-tavuja."""
    for name, webp_bytes in pool.map(_encode_job, jobs):
        (DOCS_CACHE_DIR / name).write_bytes(webp_bytes)

# frontend/index.html kayttaa naita tarkkoja /api/-polkuja - build-skripti
# korvaa ne staattisilla, suhteellisilla poluilla (toimivat myos GitHub
# Pagesin ali-URLissa, esim. https://user.github.io/repo/). "${level.suffix}"
# on JS-ajonaikainen muuttuja (ks. pipeline.LEVEL_SUFFIXES) - staattiset
# tiedostot on nimetty samalla suffiksikaytannolla.
#
# Paksuus ja prosentti EIVAT enaa esiinny poluissa: ne ovat nyt selaimen
# ajonaikaisia parametreja, ei esilaskettujen kuvien nimia.
URL_REPLACEMENTS = {
    "fetch('/api/tiles')": "fetch('tiles.json')",
    "fetch('/api/wind-grid')": "fetch('wind_grid.json')",
    # Kolme visuaalista kerrosta -> .webp (ks. to_webp). Loput jaavat .png:ksi.
    "`/api/basemap/${tile.tile_id}${level.suffix}.png`": "`cache/${tile.tile_id}_base${level.suffix}.webp`",
    # Analyysikerroksella on nyt kolme resoluutiotasoa (ks.
    # pipeline.ANALYYSI_SUFFIKSIT); taso.suffix on ajonaikainen muuttuja
    # samaan tapaan kuin peruskartan level.suffix ylla.
    # "${s}" on tason suffiksi _tasokuvaTaiDetail-apurin sisalla (tyhja =
    # detail). Muoto on tarkka merkkijono, ja build kaatuu jos se ei osu -
    # ks. tarkistus main():ssa.
    "`/api/factors/${tileId}${s}.png`": "`cache/${tileId}_factors${s}.png`",
    "`/api/tiebreak/${tileId}${s}.png`": "`cache/${tileId}_tiebreak${s}.png`",
    "`/api/prime/${tileId}${s}.png`": "`cache/${tileId}_prime${s}.png`",
    "fetch('/api/factor-thresholds')": "fetch('factor_thresholds.json')",
    "`/api/fetch/${tileId}/${part}.png`": "`cache/${tileId}_fetch${part}.png`",
    "`/api/water/${tileId}/${part}.png`": "`cache/${tileId}_water${part}.png`",
    "fetch('/api/prime-thresholds')": "fetch('prime_thresholds.json')",
    "fetch('/api/shelter-thresholds')": "fetch('shelter_thresholds.json')",
    "vaylat: ['/api/vaylat', piirraVaylat],": "vaylat: ['vaylat.json', piirraVaylat],",
    "suojelu: ['/api/suojelualueet', piirraSuojelu],": "suojelu: ['suojelualueet.json', piirraSuojelu],",
    "palvelut: ['/api/palvelut', piirraPalvelut],": "palvelut: ['palvelut.json', piirraPalvelut],",
    "const tileList = await res.json();": "const tileList = (await res.json()).tiles;",
}


# --- TUULIHILA ---
#
# Tuuli haettiin aiemmin YHDESTA kovakoodatusta pisteesta koko kartalle. Se
# riittaa 18x30 km alueella mutta ei tavoitealueella (Suomenlahti +
# lansirannikko + Ahvenanmaa, ~1000x450 km): mitattuna samalla hetkella tuuli
# oli Merenkurkussa 11,6 m/s ja Peramerella 2,2 m/s eli 5,3-kertainen ero.
# Yhdella pisteella kovin keli olisi nakynyt tyynena - virhe olisi ollut pahin
# siella missa se on vaarallisin.
#
# HILA SEURAA AINEISTOA, ei bounding boxia: tiilet niputetaan WIND_GRID_M
# ruutuihin ja vain ruutu jossa on tiilia saa pisteen. Muuten pisteita kuluisi
# tyhjaan mereen ja sisamaahan, ja rajapinnan raja tulisi vastaan turhaan.
#
# Piste kirjoitetaan SEKA EPSG:3067-metreina etta lat/lon-parina, jotta selain
# ei tarvitse projektiokirjastoa: se tyoskentelee raakoina 3067-metreina
# (L.CRS.Simple) ja lat/lon on valmiina rajapintakutsua varten.
WIND_GRID_M = 30000.0


def wind_grid_points(tile_entries):
    """Tuulihilan pisteet tiilien sijainnin perusteella."""
    from pyproj import Transformer

    solut = {}
    for t in tile_entries:
        b = t["bounds_epsg3067"]
        cx = (b["minx"] + b["maxx"]) / 2.0
        cy = (b["miny"] + b["maxy"]) / 2.0
        solut.setdefault((int(cx // WIND_GRID_M), int(cy // WIND_GRID_M)), []).append((cx, cy))

    # Pisteen paikka on siina olevien tiilien keskiarvo, ei ruudun geometrinen
    # keskus: jos ruudussa on vain yksi tiili sen reunalla, ennuste haetaan
    # sielta missa aineisto oikeasti on.
    to_wgs = Transformer.from_crs("EPSG:3067", "EPSG:4326", always_xy=True)
    pisteet = []
    for (gx, gy), keskukset in sorted(solut.items()):
        x = sum(c[0] for c in keskukset) / len(keskukset)
        y = sum(c[1] for c in keskukset) / len(keskukset)
        lon, lat = to_wgs.transform(x, y)
        pisteet.append({"x": round(x, 1), "y": round(y, 1),
                        "lat": round(lat, 4), "lon": round(lon, 4)})
    return pisteet


def build():
    if DOCS_DIR.exists():
        shutil.rmtree(DOCS_DIR)
    DOCS_CACHE_DIR.mkdir(parents=True)

    registry = tiles.get_registry()
    print(f"{len(registry)} tiilta rekisterissa")

    # Tiilistosta riippuvat valimuistit (kynnykset, globaali jarjestysluku,
    # vektoritasot) on mitatoitava ENNEN laskentaa jos tiilisto on muuttunut -
    # muuten uudet tiilet varitettaisiin vanhan aineiston jakaumaa vasten.
    pipeline.varmista_rekisteri()

    # Rinnakkaisajo vain WebP-enkoodaukselle. GIS-laskenta pysyy sarjallisena,
    # koska se lukee ja kirjoittaa yhteista output/cache/-valimuistia.
    pool = ProcessPoolExecutor(max_workers=os.cpu_count())

    tile_entries = []
    import time as _time
    _t0 = _time.perf_counter()
    _n = len(registry)
    for _i, tile_id in enumerate(registry, 1):
        # Edistyminen nakyviin: pitkassa ajossa on tiedettava etta tyo etenee
        # eika ole jumissa. Arvio perustuu jo kasiteltyjen tiilien keskiarvoon.
        _kulunut = _time.perf_counter() - _t0
        _arvio = f", n. {(_kulunut / max(_i - 1, 1)) * (_n - _i + 1) / 60:.0f} min jaljella" if _i > 1 else ""
        print(f"  [{_i}/{_n}] {tile_id}  ({_kulunut / 60:.1f} min kulunut{_arvio})", flush=True)

        # Peruskartta jokaisella tarkkuustasolla. Esilasketut varikerrokset
        # (paksuus x prosentti x taso = 50 kuvaa/tiili) POISTETTIIN: ne olivat
        # 53 % docs/-hakemiston koosta ja 2/3 laskenta-ajasta, ja kartta
        # piirretaan nyt aina osatekijakuvista selaimessa.
        meta = None
        jobs = []
        for level in pipeline.LEVEL_FACTORS:
            suffix = pipeline.LEVEL_SUFFIXES[level]
            jobs.append((f"{tile_id}_base{suffix}.webp",
                         pipeline.get_or_compute_basemap(tile_id, level=level)))
        write_webp_batch(jobs, pool)

        # Osatekijakuvapari (ks. pipeline-moduulin kanavakuvaus) - yksi pari
        # per tiili riippumatta tekijavalinnoista/paksuudesta/prosentista.
        # KOLME RESOLUUTIOTASOA (ks. pipeline.ANALYYSI_TASOT). Karkeat tasot
        # ovat pienia - 750x750 ja 188x188 vs 3000x3000 - joten ne lisaavat
        # docs/-hakemistoon vain n. 7 %, mutta poistavat uloimman zoomin
        # 11 585-kertaisen ylilaskennan.
        for taso, suffiksi in pipeline.ANALYYSI_SUFFIKSIT.items():
            for part in ("factors", "tiebreak"):
                part_bytes, part_meta = pipeline.get_or_compute_factor_png(
                    tile_id, str(BUILDINGS_PATH), part=part, taso=taso
                )
                (DOCS_CACHE_DIR / f"{tile_id}_{part}{suffiksi}.png").write_bytes(part_bytes)
                meta = meta or part_meta

            # Karkipaikat: sama kanavarakenne, mutta arvot aggregoitu koko
            # rantakaistaleen yli (ks. pipeline.get_or_compute_prime_png).
            prime_bytes, prime_meta = pipeline.get_or_compute_prime_png(
                tile_id, str(BUILDINGS_PATH), taso=taso)
            (DOCS_CACHE_DIR / f"{tile_id}_prime{suffiksi}.png").write_bytes(prime_bytes)
            meta = meta or prime_meta

        # Pyyhkaisymatkat ja esteiden korkeudet suojaisuustekijaa varten:
        # 12 sektoria kummallekin, kaksi kuvaa per suure.
        for part in ("a", "b", "obsa", "obsb"):
            fetch_bytes, _ = pipeline.get_or_compute_fetch_png(
                tile_id, str(BUILDINGS_PATH), part=part
            )
            (DOCS_CACHE_DIR / f"{tile_id}_fetch{part}.png").write_bytes(fetch_bytes)

            # Melottavuuden lahtodata vesiruudukolla (50 m, 120x120 per tiili).
            water_bytes, _ = pipeline.get_or_compute_water_png(
                tile_id, str(BUILDINGS_PATH), part=part
            )
            (DOCS_CACHE_DIR / f"{tile_id}_water{part}.png").write_bytes(water_bytes)

        tile_entries.append({"tile_id": tile_id, "bounds_epsg3067": meta["bounds_epsg3067"]})

    pool.shutdown()

    default_percentile = pipeline.top_percent_to_percentile(pipeline.DEFAULT_TOP_PERCENT)
    threshold = pipeline.compute_global_threshold(str(BUILDINGS_PATH), default_percentile)

    tiles_json = {
        "tiles": tile_entries,
        "top_threshold": threshold,
        "thickness_presets": pipeline.THICKNESS_PRESETS,
        "thickness_default": pipeline.DEFAULT_THICKNESS_PX,
        "top_percent_presets": pipeline.TOP_PERCENT_PRESETS,
        "top_percent_default": pipeline.DEFAULT_TOP_PERCENT,
    }
    (DOCS_DIR / "tiles.json").write_text(json.dumps(tiles_json, indent=2))

    hila = wind_grid_points(tile_entries)
    (DOCS_DIR / "wind_grid.json").write_text(json.dumps(hila, indent=2))
    print(f"Tuulihila: {len(hila)} pistetta ({WIND_GRID_M / 1000:.0f} km ruudukko)")

    # Vektoritasot: vaylat, suojelualueet ja palvelut. Haetaan build-vaiheessa
    # eika selaimessa - sovelluksessa ei ole palvelinta ja offline-kaytto on
    # vaatimus (ks. backend/vektoritasot.py).
    print("Vektoritasot:")
    for nimi, hakija in (("vaylat", vektoritasot.get_or_compute_vaylat),
                         ("suojelualueet", vektoritasot.get_or_compute_suojelualueet),
                         ("palvelut", vektoritasot.get_or_compute_palvelut)):
        data = hakija()
        (DOCS_DIR / f"{nimi}.json").write_text(json.dumps(data, separators=(",", ":")))

    # "Parhaat X %" -kynnykset kaikille 15 tekijayhdistelmalle omaan
    # tiedostoonsa (ks. pipeline.compute_factor_thresholds) - selain hakee
    # taman kerran ja valitsee siita valintojaan vastaavan kynnyksen.
    factor_thresholds = pipeline.compute_factor_thresholds(str(BUILDINGS_PATH))
    (DOCS_DIR / "factor_thresholds.json").write_text(json.dumps(factor_thresholds, indent=2))

    prime_thresholds = pipeline.compute_prime_thresholds(str(BUILDINGS_PATH))
    (DOCS_DIR / "prime_thresholds.json").write_text(json.dumps(prime_thresholds, indent=2))

    # Tuulesta riippuvat kynnykset omaan tiedostoonsa - selain hakee sen
    # vasta kun suojaisuustekija otetaan kayttoon.
    shelter_thresholds = pipeline.compute_shelter_thresholds(str(BUILDINGS_PATH))
    (DOCS_DIR / "shelter_thresholds.json").write_text(json.dumps(shelter_thresholds))

    # Rantaviivan jakauma asetussivun kuvaajaa varten (ks.
    # pipeline.compute_shoreline_stats).
    shoreline_stats = pipeline.compute_shoreline_stats(str(BUILDINGS_PATH))
    (DOCS_DIR / "shoreline_stats.json").write_text(json.dumps(shoreline_stats))

    write_static_index_html()
    write_static_settings_html()

    # Estaa GitHub Pagesia ajamasta Jekylla-prosessointia staattisten
    # tiedostojen paalla (nopeampi julkaisu, ei yllatyksia tiedostonimissa).
    (DOCS_DIR / ".nojekyll").touch()

    total_size = sum(f.stat().st_size for f in DOCS_DIR.rglob("*") if f.is_file())
    print(f"\nValmis: {DOCS_DIR} ({total_size / 1e6:.1f} MB, {len(tile_entries)} tiilta)")
    print(f"Paras {pipeline.DEFAULT_TOP_PERCENT}% -kynnysarvo: {threshold:.4f} (persentiili {default_percentile})")


# Asetussivun ainoa API-riippuvuus on rantaviivan jakauma; muut valinnat
# ovat selaimen localStoragessa.
SETTINGS_URL_REPLACEMENTS = {
    "fetch('/api/shoreline-stats')": "fetch('shoreline_stats.json')",
}


def replace_urls(src, replacements, source_name):
    out = src
    for old, new in replacements.items():
        if old not in out:
            raise RuntimeError(
                f"Odotettua tekstia ei loytynyt {source_name}:sta: {old!r} "
                "- frontend on luultavasti muuttunut, paivita korvaustaulukko"
            )
        out = out.replace(old, new)
    return out


def write_static_settings_html():
    src = (ROOT / "frontend" / "settings.html").read_text()
    out = replace_urls(src, SETTINGS_URL_REPLACEMENTS, "frontend/settings.html")
    out = out.replace(
        "<head>",
        "<!-- Staattinen versio - generoitu build_static.py:lla, ala muokkaa suoraan -->\n<head>",
        1,
    )
    (DOCS_DIR / "settings.html").write_text(out)


def write_static_index_html():
    src = (ROOT / "frontend" / "index.html").read_text()
    out = replace_urls(src, URL_REPLACEMENTS, "frontend/index.html")

    out = out.replace(
        "<head>",
        "<!-- Staattinen versio - generoitu build_static.py:lla, ala muokkaa suoraan -->\n<head>",
        1,
    )

    (DOCS_DIR / "index.html").write_text(out)


if __name__ == "__main__":
    build()
