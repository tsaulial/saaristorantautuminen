"""
Rakentaa taysin staattisen dist/-hakemiston (esim. GitHub Pagesia varten).

Ajaa kaiken GIS-laskennan kertaalleen paikallisen output/cache/-valimuistin
kautta (backend/pipeline.py) ja kopioi tulokset seka kevennetyn, staattisia
polkuja kayttavan frontendin dist/-kansioon. Tuotannossa ei tarvita Pythonia,
GDAL:ia eika mitaan palvelinta - pelkat staattiset tiedostot.

dist/-kansio tyhjennetaan ja luodaan uudelleen joka ajolla.

Kaynnistys projektin juuresta:
    python3 build_static.py
"""

import json
import shutil
from pathlib import Path

from backend import pipeline, tiles

ROOT = Path(__file__).resolve().parent
BUILDINGS_PATH = ROOT / "rakennukset-mll" / "rakennukset.gpkg"
DIST_DIR = ROOT / "dist"
DIST_CACHE_DIR = DIST_DIR / "cache"

# frontend/index.html kayttaa naita tarkkoja /api/-polkuja - build-skripti
# korvaa ne staattisilla, suhteellisilla poluilla (toimivat myos GitHub
# Pagesin ali-URLissa, esim. https://user.github.io/repo/).
URL_REPLACEMENTS = {
    "fetch('/api/tiles')": "fetch('tiles.json')",
    "`/api/basemap/${tile.tile_id}.png`": "`cache/${tile.tile_id}_base.png`",
    "`/api/overlay/${tile.tile_id}.png`": "`cache/${tile.tile_id}.png`",
    "`/api/overlay/${tile.tile_id}/top.png`": "`cache/${tile.tile_id}_top.png`",
    "const tileList = await res.json();": "const tileList = (await res.json()).tiles;",
}


def build():
    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    DIST_CACHE_DIR.mkdir(parents=True)

    registry = tiles.get_registry()
    print(f"{len(registry)} tiilta rekisterissa")

    tile_entries = []
    for tile_id in registry:
        print(f"  {tile_id}...")

        base_bytes = pipeline.get_or_compute_basemap(tile_id)
        overlay_bytes, meta = pipeline.get_or_compute_overlay(tile_id, str(BUILDINGS_PATH))
        top_bytes = pipeline.get_or_compute_top(tile_id, str(BUILDINGS_PATH))

        (DIST_CACHE_DIR / f"{tile_id}_base.png").write_bytes(base_bytes)
        (DIST_CACHE_DIR / f"{tile_id}.png").write_bytes(overlay_bytes)
        (DIST_CACHE_DIR / f"{tile_id}_top.png").write_bytes(top_bytes)

        tile_entries.append({"tile_id": tile_id, "bounds_wgs84": meta["bounds_wgs84"]})

    threshold = pipeline.compute_global_threshold(str(BUILDINGS_PATH))

    tiles_json = {
        "tiles": tile_entries,
        "top_percentile": pipeline.TOP_PERCENTILE,
        "top_threshold": threshold,
    }
    (DIST_DIR / "tiles.json").write_text(json.dumps(tiles_json, indent=2))

    write_static_index_html()

    # Estaa GitHub Pagesia ajamasta Jekylla-prosessointia staattisten
    # tiedostojen paalla (nopeampi julkaisu, ei yllatyksia tiedostonimissa).
    (DIST_DIR / ".nojekyll").touch()

    total_size = sum(f.stat().st_size for f in DIST_DIR.rglob("*") if f.is_file())
    print(f"\nValmis: {DIST_DIR} ({total_size / 1e6:.1f} MB, {len(tile_entries)} tiilta)")
    print(f"Paras 15% -kynnysarvo: {threshold:.4f} (persentiili {pipeline.TOP_PERCENTILE})")


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

    (DIST_DIR / "index.html").write_text(out)


if __name__ == "__main__":
    build()
