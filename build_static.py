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
# Pagesin ali-URLissa, esim. https://user.github.io/repo/). "${level.suffix}"
# on JS-ajonaikainen muuttuja (ks. pipeline.LEVEL_SUFFIXES) - staattiset
# tiedostot on nimetty samalla suffiksikaytannolla, joten pelkka polun alku
# tarvitsee korvata.
URL_REPLACEMENTS = {
    "fetch('/api/tiles')": "fetch('tiles.json')",
    "`/api/basemap/${tile.tile_id}${level.suffix}.png`": "`cache/${tile.tile_id}_base${level.suffix}.png`",
    "`/api/overlay/${tile.tile_id}${level.suffix}.png`": "`cache/${tile.tile_id}${level.suffix}.png`",
    "`/api/overlay/${tile.tile_id}/top${level.suffix}.png`": "`cache/${tile.tile_id}_top${level.suffix}.png`",
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
            overlay_bytes, level_meta = pipeline.get_or_compute_overlay(tile_id, str(BUILDINGS_PATH), level=level)
            top_bytes = pipeline.get_or_compute_top(tile_id, str(BUILDINGS_PATH), level=level)
            meta = meta or level_meta

            (DOCS_CACHE_DIR / f"{tile_id}_base{suffix}.png").write_bytes(base_bytes)
            (DOCS_CACHE_DIR / f"{tile_id}{suffix}.png").write_bytes(overlay_bytes)
            (DOCS_CACHE_DIR / f"{tile_id}_top{suffix}.png").write_bytes(top_bytes)

        tile_entries.append({"tile_id": tile_id, "bounds_epsg3067": meta["bounds_epsg3067"]})

    threshold = pipeline.compute_global_threshold(str(BUILDINGS_PATH))

    tiles_json = {
        "tiles": tile_entries,
        "top_percentile": pipeline.TOP_PERCENTILE,
        "top_threshold": threshold,
    }
    (DOCS_DIR / "tiles.json").write_text(json.dumps(tiles_json, indent=2))

    write_static_index_html()

    # Estaa GitHub Pagesia ajamasta Jekylla-prosessointia staattisten
    # tiedostojen paalla (nopeampi julkaisu, ei yllatyksia tiedostonimissa).
    (DOCS_DIR / ".nojekyll").touch()

    total_size = sum(f.stat().st_size for f in DOCS_DIR.rglob("*") if f.is_file())
    print(f"\nValmis: {DOCS_DIR} ({total_size / 1e6:.1f} MB, {len(tile_entries)} tiilta)")
    print(f"Paras 7% -kynnysarvo: {threshold:.4f} (persentiili {pipeline.TOP_PERCENTILE})")


def write_static_index_html():
    src = (ROOT / "frontend" / "index.html").read_text()

    out = src
    for old, new in URL_REPLACEMENTS.items():
        if old not in out:
            raise RuntimeError(
                f"Odotettua tekstia ei loytynyt frontend/index.html:sta: {old!r} "
                "- frontend on luultavasti muuttunut, paivita URL_REPLACEMENTS"
            )
        out = out.replace(old, new)

    out = out.replace(
        "<head>",
        "<!-- Staattinen versio - generoitu build_static.py:lla, ala muokkaa suoraan -->\n<head>",
        1,
    )

    (DOCS_DIR / "index.html").write_text(out)


if __name__ == "__main__":
    build()
