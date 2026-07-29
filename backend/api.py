"""
Vaihe 3: FastAPI-rajapinta.

Tarjoaa saatavilla olevien tiilien listan (EPSG:3067-rajoineen, peruskartan
omalla ruudukolla) ja kunkin tiilen pistemaaraoverlayn PNG:na. Overlay
lasketaan ensimmaisella pyynnolla ja valimuistetaan levylle
(backend/pipeline.py).

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
    """Kaikki saatavilla olevat tiilet ja niiden EPSG:3067-rajat (peruskartan
    omalla ruudukolla)."""
    registry = tiles.get_registry()
    return [
        {
            "tile_id": tile_id,
            "bounds_epsg3067": pipeline.get_tile_bounds(tile),
        }
        for tile_id, tile in registry.items()
    ]


@app.get("/api/overlay/{tile_key}.png")
def get_overlay_png(tile_key: str):
    """Tiilen pistemaaraoverlay RGBA-PNG:na halutulla resoluutiotasolla ja
    rantaviivan paksuudella (tile_key on esim. 'L3123F_t10', 'L3123F_mid_t1'
    tai 'L3123F_overview_t20' - ks. pipeline.parse_tile_key), lasketaan/
    valimuistetaan tarvittaessa."""
    tile_id, level, thickness_px, _top_percent = pipeline.parse_tile_key(tile_key)
    try:
        png_bytes, _meta = pipeline.get_or_compute_overlay(
            tile_id, str(BUILDINGS_PATH), level=level, thickness_px=thickness_px
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tuntematon tile_id: {tile_id}")
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/basemap/{tile_key}.png")
def get_basemap_png(tile_key: str):
    """Taustakartaksi tarkoitettu MML-karttakuvaleikkaus samoille rajoille
    kuin overlay, halutulla resoluutiotasolla (ks. get_overlay_png). Ei
    riipu rantaviivan paksuudesta."""
    tile_id, level, _thickness_px, _top_percent = pipeline.parse_tile_key(tile_key)
    try:
        png_bytes = pipeline.get_or_compute_basemap(tile_id, level=level)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tuntematon tile_id: {tile_id}")
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/overlay/{tile_id}/{variant}.png")
def get_overlay_top_png(tile_id: str, variant: str):
    """Erillinen kerros: nayttaa vain parhaat X% rantautumispisteista
    halutulla resoluutiotasolla, rantaviivan paksuudella ja X%-arvolla
    (ks. pipeline.TOP_PERCENT_PRESETS; variant on esim. 'top_t10_p7',
    'top_mid_t1_p1' tai 'top_overview_t20_p10'). Ensimmainen pyynto tietylle
    X%-arvolle voi olla hidas, koska se laskee kaikkien tiilien raa'an
    pistemaaran globaalia kynnysarvoa varten jos sita ei viela ole
    valimuistissa."""
    base, level, thickness_px, top_percent = pipeline.parse_tile_key(variant)
    if base != "top":
        raise HTTPException(status_code=404, detail=f"Tuntematon polku: {variant}")
    try:
        png_bytes = pipeline.get_or_compute_top(
            tile_id, str(BUILDINGS_PATH), level=level, thickness_px=thickness_px, top_percent=top_percent
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tuntematon tile_id: {tile_id}")
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/factors/{tile_id}.png")
def get_factors_png(tile_id: str):
    """Pisteytyksen OSATEKIJAT erillisina kanavina (ks.
    pipeline.get_or_compute_factor_png): R=jyrkkyys, G=etaisyys
    rakennuksiin, B=kallio/suo-bitit, A=puskurimaski, 3,5x3,5m
    resoluutiolla. Selain kokoaa naista pistemaaran kayttajan valitsemista
    tekijoista (frontend/index.html: renderFactorTile)."""
    try:
        png_bytes, _meta = pipeline.get_or_compute_factor_png(tile_id, str(BUILDINGS_PATH), part="factors")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tuntematon tile_id: {tile_id}")
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/tiebreak/{tile_id}.png")
def get_tiebreak_png(tile_id: str):
    """Tasapelinpurku omana kuvanaan (R-kanava, alfa 255 kaikkialla) - ks.
    pipeline-moduulin kanavakuvaus siita miksi tama ei mahdu samaan kuvaan."""
    try:
        png_bytes, _meta = pipeline.get_or_compute_factor_png(tile_id, str(BUILDINGS_PATH), part="tiebreak")
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tuntematon tile_id: {tile_id}")
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/factor-thresholds")
def get_factor_thresholds():
    """"Parhaat X %" -kynnysarvot per tekijayhdistelma ja prosentti (ks.
    pipeline.compute_factor_thresholds) - globaali suure, jota selain ei voi
    itse laskea koska se nakee kerrallaan vain nakymassa olevat tiilet."""
    return pipeline.compute_factor_thresholds(str(BUILDINGS_PATH))


@app.get("/api/shoreline-stats")
def get_shoreline_stats():
    """Rantaviivan jakauma rantautumiskelpoisuuden mukaan per
    tekijayhdistelma (ks. pipeline.compute_shoreline_stats) - asetussivun
    kuvaajan lahde."""
    return pipeline.compute_shoreline_stats(str(BUILDINGS_PATH))


@app.get("/api/threshold")
def get_threshold(top_percent: int = pipeline.DEFAULT_TOP_PERCENT):
    """Kynnysarvo annetulle 'parhaat X%' -arvolle (ks. pipeline.TOP_PERCENT_PRESETS)."""
    percentile = pipeline.top_percent_to_percentile(top_percent)
    threshold = pipeline.compute_global_threshold(str(BUILDINGS_PATH), percentile)
    return {"top_percent": top_percent, "percentile": percentile, "threshold": threshold}


@app.get("/api/overlay/{tile_id}/meta")
def get_overlay_meta(tile_id: str):
    """Tiilen EPSG:3067-rajat ja tunnuslukuja (rakennusmaara, kallio-%, jne)."""
    try:
        _png_bytes, meta = pipeline.get_or_compute_overlay(tile_id, str(BUILDINGS_PATH))
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tuntematon tile_id: {tile_id}")
    return meta


@app.get("/api/viewport")
def get_viewport_tiles(min_x: float, min_y: float, max_x: float, max_y: float):
    """Palauttaa nakymaan (EPSG:3067-bbox, metreina) osuvat tiilet - frontend
    kutsuu tata kun kayttaja panoroi karttaa, ja lataa vain palautetut overlayt."""
    registry = tiles.get_registry()
    hits = []
    for tile_id, tile in registry.items():
        b = pipeline.get_tile_bounds(tile)
        overlaps = not (
            b["maxx"] < min_x or b["minx"] > max_x or b["maxy"] < min_y or b["miny"] > max_y
        )
        if overlaps:
            hits.append({"tile_id": tile_id, "bounds_epsg3067": b})
    return hits


@app.get("/api/health")
def health():
    return {"status": "ok", "tiles": len(tiles.get_registry())}


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
