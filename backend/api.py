"""
Vaihe 3: FastAPI-rajapinta.

Tarjoaa saatavilla olevien tiilien listan (WGS84-rajoineen Leafletia varten)
ja kunkin tiilen pistemaaraoverlayn PNG:na. Overlay lasketaan ensimmaisella
pyynnolla ja valimuistetaan levylle (backend/pipeline.py).

Kaynnistys:
    uvicorn backend.api:app --reload
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from backend import pipeline, tiles

BUILDINGS_PATH = Path(__file__).resolve().parent.parent / "rakennukset-mll" / "rakennukset.gpkg"

app = FastAPI(title="Saaristorantautuminen - Score API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/tiles")
def list_tiles():
    """Kaikki saatavilla olevat tiilet ja niiden WGS84-rajat."""
    registry = tiles.get_registry()
    return [
        {
            "tile_id": tile_id,
            "bounds_wgs84": pipeline.get_tile_bounds_wgs84(tile),
        }
        for tile_id, tile in registry.items()
    ]


@app.get("/api/overlay/{tile_id}.png")
def get_overlay_png(tile_id: str):
    """Tiilen pistemaaraoverlay RGBA-PNG:na (lasketaan/valimuistetaan tarvittaessa)."""
    try:
        png_bytes, _meta = pipeline.get_or_compute_overlay(tile_id, str(BUILDINGS_PATH))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tuntematon tile_id: {tile_id}")
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/basemap/{tile_id}.png")
def get_basemap_png(tile_id: str):
    """Taustakartaksi tarkoitettu MML-karttakuvaleikkaus samoille rajoille kuin overlay."""
    try:
        png_bytes = pipeline.get_or_compute_basemap(tile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tuntematon tile_id: {tile_id}")
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/overlay/{tile_id}/top.png")
def get_overlay_top_png(tile_id: str):
    """Erillinen kerros: nayttaa vain parhaat 20% (koko aineiston 80.
    persentiili) rantautumispisteista. Ensimmainen pyynto voi olla hidas,
    koska se laskee kaikkien tiilien raa'an pistemaaran globaalia kynnysarvoa
    varten jos sita ei viela ole valimuistissa."""
    try:
        png_bytes = pipeline.get_or_compute_top(tile_id, str(BUILDINGS_PATH))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tuntematon tile_id: {tile_id}")
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/threshold")
def get_threshold():
    """Nykyinen 'paras 20%' -kynnysarvo ja sen laskentaperuste."""
    threshold = pipeline.compute_global_threshold(str(BUILDINGS_PATH))
    return {"percentile": pipeline.TOP_PERCENTILE, "threshold": threshold}


@app.get("/api/overlay/{tile_id}/meta")
def get_overlay_meta(tile_id: str):
    """Tiilen WGS84-rajat ja tunnuslukuja (rakennusmaara, kallio-%, jne)."""
    try:
        _png_bytes, meta = pipeline.get_or_compute_overlay(tile_id, str(BUILDINGS_PATH))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tuntematon tile_id: {tile_id}")
    return meta


@app.get("/api/viewport")
def get_viewport_tiles(min_lon: float, min_lat: float, max_lon: float, max_lat: float):
    """Palauttaa nakymaan (WGS84-bbox) osuvat tiilet - frontend kutsuu tata
    kun kayttaja panoroi karttaa, ja lataa vain palautetut overlayt."""
    registry = tiles.get_registry()
    hits = []
    for tile_id, tile in registry.items():
        b = pipeline.get_tile_bounds_wgs84(tile)
        overlaps = not (
            b["east"] < min_lon or b["west"] > max_lon or b["north"] < min_lat or b["south"] > max_lat
        )
        if overlaps:
            hits.append({"tile_id": tile_id, "bounds_wgs84": b})
    return hits


@app.get("/api/health")
def health():
    return {"status": "ok", "tiles": len(tiles.get_registry())}


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
