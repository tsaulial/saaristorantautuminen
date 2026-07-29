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

import json
import shutil
from pathlib import Path

from backend import pipeline, tiles

ROOT = Path(__file__).resolve().parent
BUILDINGS_PATH = ROOT / "rakennukset-mll" / "rakennukset.gpkg"
DOCS_DIR = ROOT / "docs"
DOCS_CACHE_DIR = DOCS_DIR / "cache"

# frontend/index.html kayttaa naita tarkkoja /api/-polkuja - build-skripti
# korvaa ne staattisilla, suhteellisilla poluilla (toimivat myos GitHub
# Pagesin ali-URLissa, esim. https://user.github.io/repo/). "${level.suffix}",
# "${currentThickness}" ja "${currentTopPercent}" ovat JS-ajonaikaisia
# muuttujia (ks. pipeline.LEVEL_SUFFIXES, pipeline.THICKNESS_PRESETS ja
# pipeline.TOP_PERCENT_PRESETS) - staattiset tiedostot on nimetty samalla
# suffiksikaytannolla, joten pelkka polun alku tarvitsee korvata.
URL_REPLACEMENTS = {
    "fetch('/api/tiles')": "fetch('tiles.json')",
    "`/api/basemap/${tile.tile_id}${level.suffix}.png`": "`cache/${tile.tile_id}_base${level.suffix}.png`",
    "`/api/overlay/${tile.tile_id}${level.suffix}_t${currentThickness}.png`":
        "`cache/${tile.tile_id}${level.suffix}_t${currentThickness}.png`",
    "`/api/overlay/${tile.tile_id}/top${level.suffix}_t${currentThickness}_p${currentTopPercent}.png`":
        "`cache/${tile.tile_id}_top${level.suffix}_t${currentThickness}_p${currentTopPercent}.png`",
    "`/api/factors/${tileId}.png`": "`cache/${tileId}_factors.png`",
    "`/api/tiebreak/${tileId}.png`": "`cache/${tileId}_tiebreak.png`",
    "fetch('/api/factor-thresholds')": "fetch('factor_thresholds.json')",
    "const tileList = await res.json();": "const tileList = (await res.json()).tiles;",
}


def build():
    if DOCS_DIR.exists():
        shutil.rmtree(DOCS_DIR)
    DOCS_CACHE_DIR.mkdir(parents=True)

    registry = tiles.get_registry()
    print(f"{len(registry)} tiilta rekisterissa")

    tile_entries = []
    for tile_id in registry:
        print(f"  {tile_id}...")

        meta = None
        for level in pipeline.LEVEL_FACTORS:
            suffix = pipeline.LEVEL_SUFFIXES[level]
            base_bytes = pipeline.get_or_compute_basemap(tile_id, level=level)
            (DOCS_CACHE_DIR / f"{tile_id}_base{suffix}.png").write_bytes(base_bytes)

            for thickness_px in pipeline.THICKNESS_PRESETS:
                overlay_bytes, level_meta = pipeline.get_or_compute_overlay(
                    tile_id, str(BUILDINGS_PATH), level=level, thickness_px=thickness_px
                )
                meta = meta or level_meta
                (DOCS_CACHE_DIR / f"{tile_id}{suffix}_t{thickness_px}.png").write_bytes(overlay_bytes)

                for top_percent in pipeline.TOP_PERCENT_PRESETS:
                    top_bytes = pipeline.get_or_compute_top(
                        tile_id,
                        str(BUILDINGS_PATH),
                        level=level,
                        thickness_px=thickness_px,
                        top_percent=top_percent,
                    )
                    (DOCS_CACHE_DIR / f"{tile_id}_top{suffix}_t{thickness_px}_p{top_percent}.png").write_bytes(
                        top_bytes
                    )

        # Osatekijakuvapari (ks. pipeline-moduulin kanavakuvaus) - yksi pari
        # per tiili riippumatta tekijavalinnoista/paksuudesta/prosentista.
        for part in ("factors", "tiebreak"):
            part_bytes, part_meta = pipeline.get_or_compute_factor_png(
                tile_id, str(BUILDINGS_PATH), part=part
            )
            (DOCS_CACHE_DIR / f"{tile_id}_{part}.png").write_bytes(part_bytes)
            meta = meta or part_meta

        tile_entries.append({"tile_id": tile_id, "bounds_epsg3067": meta["bounds_epsg3067"]})

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

    # "Parhaat X %" -kynnykset kaikille 15 tekijayhdistelmalle omaan
    # tiedostoonsa (ks. pipeline.compute_factor_thresholds) - selain hakee
    # taman kerran ja valitsee siita valintojaan vastaavan kynnyksen.
    factor_thresholds = pipeline.compute_factor_thresholds(str(BUILDINGS_PATH))
    (DOCS_DIR / "factor_thresholds.json").write_text(json.dumps(factor_thresholds, indent=2))

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
