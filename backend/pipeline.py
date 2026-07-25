"""
Vaihe 3: Yhdistaa Vaihe 1:n (jyrkkyys + rakennusetaisyys) ja Vaihe 2:n
(kallio + rantaviiva + suo) yhdeksi kokonaispistemaaraksi S per DEM-tiili, ja
generoi RGBA-overlay-kuvan vain rantaviivan puskurivyohykkeelle
(instructions.md kohta 3, D).

Kuvat reprojisoidaan TM35FIN:sta (EPSG:3067) suoraan WGS84-pikseliruudukolle
(EPSG:4326) ennen PNG-enkoodausta. Pelkka kulmapisteiden koordinaattimuunnos
(2 kulmaa -> "bounds") EI riita: TM35FIN-ruudukko ei ole linjassa todellisen
pohjois-etela-suunnan kanssa kaukana keskimeridiaanista (27E), joten naapuri-
tiilten reunat eivat tasmaisi Leafletissa vaan jattaisivat nakyvia rakoja.
Koko rasterin reprojisointi tuottaa aidosti suorakulmaisen lat/lon-ruudukon,
jolloin vierekkaisten tiilten reunat tasmaavat.

Tulos valimuistetaan levylle (output/cache/) - lasketaan vain kerran per
tiili, ks. Vaihe 1:n suunnittelupaatos ("koko lehti kerralla + cache").

Kaksi laskentavaihetta:
1. get_or_compute_raw(tile_id): raaka pistemaara + puskurimaski DEM:n omalla
   2m/px-ruudukolla (TM35FIN), valimuistettu .npz:na. Sisaltaa jo suo-
   rangaistuksen. Kayttaa seka yksittaisen tiilen piirtoa etta globaalia
   persentiililaskentaa.
2. compute_global_threshold(): kerää KAIKKIEN tiilien puskurivyohykkeen
   pisteet yhteen ja laskee 80. persentiilin - "parhaat 20%" maaritellaan
   koko aineiston, ei yksittaisen tiilen, suhteen. Tama on tietoinen
   arkkitehtuurivalinta: ensimmainen /api/overlay/*/top.png-pyynto laskee
   KAIKKIEN tiilien raa'an pistemaaran jos niita ei viela ole valimuistissa.
"""

import json
from pathlib import Path

import cv2
import numpy as np
from rasterio.crs import CRS
from rasterio.transform import Affine, array_bounds
from rasterio.warp import Resampling as WarpResampling
from rasterio.warp import calculate_default_transform, reproject
from scipy.ndimage import distance_transform_edt, minimum_filter

from backend import raster_filters, score_engine, tiles

ROCK_SCORE_YES = 1.0
ROCK_SCORE_NO = 0.2
ROCK_WEIGHT = 0.15

# Suo on huono rantautumispaikka: kerrotaan koko (jo laskettu) pistemaara
# talla kertoimella niilla pikseleilla joilla suo tunnistettiin. 0.5 = pudottaa
# pisteen suunnilleen puoleen ("keskitason negatiivinen") riippumatta muista
# komponenteista. Saatavilla erikseen saadettavaksi.
SWAMP_PENALTY_FACTOR = 0.5

SHORELINE_BUFFER_MIN_M = 5.0
SHORELINE_BUFFER_MAX_M = 15.0

# Puskurivyohyke on todellisuudessa vain muutaman pikselin levyinen 2m/px
# DEM-ruudukolla eika erotu ulompana zoomitasolla. Paksunnetaan sita PELKASTAAN
# nakymista varten (dilataatio) - taustalla oleva data/tilastot pysyvat tarkkoina.
BUFFER_VISUAL_DILATION_PX = 5

# Jyrkkyys ($S_{slope}$) vaihtelee hyvin paikallisesti 2m/px-ruudukolla, joten
# huonot pisteet (keltainen/punainen) nakyvat pienina, hajanaisina taplina.
# Korostetaan niita PELKASTAAN renderoinnissa: kunkin pikselin variksi otetaan
# lahiymparistonsa (sade radius_px) PIENIN pistemaara (minimum_filter), jolloin
# huono kohta "leviaa" nakyvaan kuvaan laajemmalle. Tilastot, suo-rangaistus ja
# top-20%-kynnysarvo perustuvat yha tarkkaan, suodattamattomaan pisteeseen.
LOW_SCORE_EMPHASIS_PX = 3

# "Parhaat rantautumispaikat" = koko aineiston (kaikki tiilet) pisteiden
# 85. persentiili (= paras 15%) puskurivyohykkeen sisalla.
TOP_PERCENTILE = 85
TOP_HIGHLIGHT_BGR = (255, 0, 255)  # magenta - erottuu selvasti vihrea/keltainen/punainen-asteikosta
TOP_HIGHLIGHT_ALPHA = 230

# Selaimelle lahetettavien kuvien kutakuinkin tavoitesivun mitta pikseleina.
# Nakymassa voi olla useita tiilia yhtaaikaa eika suuremmasta tarkkuudesta
# ole hyotya selaimen zoomaustasolla.
WEB_TARGET_DIM = 2000

CACHE_DIR = Path(__file__).resolve().parent.parent / "output" / "cache"

SRC_CRS = CRS.from_epsg(3067)
DST_CRS = CRS.from_epsg(4326)


def downsample_mask_majority(mask, factor):
    """Pienentaa binaarimaskin 'factor'-kertaa per akseli enemmiston
    perusteella (esim. 1m/px kalliomaski -> 2m/px DEM-ruudukko)."""
    h, w = mask.shape
    h, w = h - h % factor, w - w % factor
    reshaped = mask[:h, :w].reshape(h // factor, factor, w // factor, factor)
    return reshaped.mean(axis=(1, 3)) >= 0.5


def dilate_mask(mask, radius_px=BUFFER_VISUAL_DILATION_PX):
    """Paksuntaa binaarimaskia pelkkaa visualisointia varten."""
    if radius_px <= 0:
        return mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius_px + 1, 2 * radius_px + 1))
    return cv2.dilate(mask.astype(np.uint8), kernel).astype(bool)


def emphasize_low_scores(score, radius_px=LOW_SCORE_EMPHASIS_PX):
    """Palauttaa version pistemaarasta jossa huonot arvot on levitetty
    lahiymparistoonsa (ks. LOW_SCORE_EMPHASIS_PX). Vain visualisointia varten."""
    if radius_px <= 0:
        return score
    return minimum_filter(score, size=2 * radius_px + 1, mode="nearest")


def compute_shoreline_buffer(shoreline_mask_dem_grid, dem, pixel_size):
    """Palauttaa boolean-maskin: True niille pikseleille jotka ovat maalla
    JA 5-15m etaisyydella lahimmasta rantaviivapikselista. Maa/vesi
    eroteltu DEM:n 0m-tason perusteella (instructions.md kohta D)."""
    land = dem > 0.0
    non_shore = ~shoreline_mask_dem_grid
    dist_to_shore = distance_transform_edt(non_shore, sampling=(pixel_size, pixel_size))
    return land & (dist_to_shore >= SHORELINE_BUFFER_MIN_M) & (dist_to_shore <= SHORELINE_BUFFER_MAX_M)


def score_to_rgba(score, visible_mask):
    """Pisteet 0..1 -> punainen-keltainen-vihrea RGBA. Nakymattomat pikselit
    (puskurivyohykkeen ulkopuolella) saavat alfa=0."""
    clipped = np.clip(np.nan_to_num(score, nan=0.0), 0.0, 1.0)
    hsv = np.zeros((*score.shape, 3), dtype=np.uint8)
    hsv[..., 0] = (clipped * 60).astype(np.uint8)
    hsv[..., 1] = 255
    hsv[..., 2] = 255
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

    alpha = np.where(visible_mask, 255, 0).astype(np.uint8)
    bgra = np.dstack([bgr, alpha])
    return bgra


def compute_wgs84_grid(bounds_3067, target_dim=WEB_TARGET_DIM):
    """Laskee yhteisen WGS84-pikseliruudukon (transform, shape, bounds) yhdelle
    tiilelle. Sama ruudukko kaytetaan seka basemapille etta overlaylle, jotta
    ne asettuvat Leafletissa tarkalleen paallekkain."""
    minx, miny, maxx, maxy = bounds_3067
    dst_transform, dst_w, dst_h = calculate_default_transform(
        SRC_CRS, DST_CRS, target_dim, target_dim, minx, miny, maxx, maxy
    )
    bounds_wgs84 = array_bounds(dst_h, dst_w, dst_transform)  # (west, south, east, north)
    return dst_transform, (dst_h, dst_w), bounds_wgs84


def reproject_to_wgs84(img_hwc, src_transform, dst_transform, dst_shape, resampling=WarpResampling.bilinear):
    dst_h, dst_w = dst_shape
    is_2d = img_hwc.ndim == 2
    src = img_hwc[..., None] if is_2d else img_hwc
    dst = np.zeros((dst_h, dst_w, src.shape[2]), dtype=src.dtype)
    for b in range(src.shape[2]):
        reproject(
            source=src[..., b],
            destination=dst[..., b],
            src_transform=src_transform,
            src_crs=SRC_CRS,
            dst_transform=dst_transform,
            dst_crs=DST_CRS,
            resampling=resampling,
        )
    return dst[..., 0] if is_2d else dst


def reproject_coverage_mask(src_shape, src_transform, dst_transform, dst_shape):
    """Kertoo mitka WGS84-ruudukon pikselit sisaltavat oikeaa lahdedataa.
    Reprojisoitu suorakulmainen tiili on alkuperaisessa TM35FIN-ruudukossa
    hieman vino (ks. moduulin dosstring), joten kohde-bounds-suorakulmion
    kulmiin jaa aina kattamatonta aluetta - tama maski erottaa sen oikeasta
    (esim. mustasta) kartta-datasta."""
    dst_h, dst_w = dst_shape
    coverage_src = np.full(src_shape, 255, dtype=np.uint8)
    coverage_dst = np.zeros((dst_h, dst_w), dtype=np.uint8)
    reproject(
        source=coverage_src,
        destination=coverage_dst,
        src_transform=src_transform,
        src_crs=SRC_CRS,
        dst_transform=dst_transform,
        dst_crs=DST_CRS,
        resampling=WarpResampling.nearest,
    )
    return coverage_dst


def bounds_tuple_to_dict(bounds_wgs84):
    west, south, east, north = bounds_wgs84
    return {"west": west, "south": south, "east": east, "north": north}


def compute_tile(tile, buildings_path):
    v1 = score_engine.compute(str(tile.dem_path), buildings_path)
    dem = v1["dem"]
    pixel_size = v1["pixel_size"]

    map_bgr, _map_transform = raster_filters.load_map_window(str(tile.map_path), tile.bounds)
    rock_mask_1m = raster_filters.detect_rock_mask(map_bgr)
    shoreline_mask_1m = raster_filters.detect_shoreline_mask(map_bgr)
    swamp_mask_1m = raster_filters.detect_swamp_mask(map_bgr)

    factor = round(map_bgr.shape[0] / dem.shape[0])
    rock_mask = downsample_mask_majority(rock_mask_1m, factor)
    shoreline_mask = downsample_mask_majority(shoreline_mask_1m, factor)
    swamp_mask = downsample_mask_majority(swamp_mask_1m, factor)

    rock_score = np.where(rock_mask, ROCK_SCORE_YES, ROCK_SCORE_NO)
    total_score = v1["slope_score"] * score_engine.SLOPE_WEIGHT
    total_score = total_score + v1["dist_score"] * score_engine.DIST_WEIGHT
    total_score = total_score + rock_score * ROCK_WEIGHT
    total_score = np.where(swamp_mask, total_score * SWAMP_PENALTY_FACTOR, total_score)

    buffer_mask = compute_shoreline_buffer(shoreline_mask, dem, pixel_size)

    return {
        "score": total_score,
        "buffer_mask": buffer_mask,
        "src_transform": v1["transform"],
        "n_buildings": v1["n_buildings"],
        "rock_pct": 100 * rock_mask.mean(),
        "swamp_pct": 100 * swamp_mask.mean(),
        "shoreline_px": int(shoreline_mask.sum()),
        "buffer_px": int(buffer_mask.sum()),
    }


def get_or_compute_raw(tile_id, buildings_path, force=False):
    """Palauttaa tiilen raa'an pistemaara+puskurimaski-tuloksen DEM:n omalla
    2m/px-ruudukolla (TM35FIN), levyvalimuistilla. Kayttaa seka yksittaisen
    tiilen piirtoa etta globaalia persentiililaskentaa - lasketaan siis vain
    kerran per tiili riippumatta kummasta tarpeesta se ensin tulee."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    npz_path = CACHE_DIR / f"{tile_id}_raw.npz"

    if not force and npz_path.exists():
        data = np.load(npz_path)
        return {
            "score": data["score"],
            "buffer_mask": data["buffer_mask"].astype(bool),
            "src_transform": Affine(*data["src_transform"]),
            "n_buildings": int(data["n_buildings"]),
            "rock_pct": float(data["rock_pct"]),
            "swamp_pct": float(data["swamp_pct"]),
            "shoreline_px": int(data["shoreline_px"]),
            "buffer_px": int(data["buffer_px"]),
        }

    registry = tiles.get_registry()
    if tile_id not in registry:
        raise KeyError(f"Tuntematon tile_id: {tile_id}")
    tile = registry[tile_id]

    result = compute_tile(tile, buildings_path)

    np.savez_compressed(
        npz_path,
        score=result["score"].astype(np.float32),
        buffer_mask=result["buffer_mask"],
        src_transform=np.array(result["src_transform"])[:6],
        n_buildings=result["n_buildings"],
        rock_pct=result["rock_pct"],
        swamp_pct=result["swamp_pct"],
        shoreline_px=result["shoreline_px"],
        buffer_px=result["buffer_px"],
    )
    return result


def compute_global_threshold(buildings_path, percentile=TOP_PERCENTILE, force=False):
    """Laskee pistemaaran percentile:n (oletus 80. eli paras 20%) kaikkien
    tiilien puskurivyohykkeen pikseleiden yli. Valimuistetaan levylle, koska
    vaatii kaikkien tiilien raa'an laskennan (~2s/tiili)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    threshold_path = CACHE_DIR / "_global_threshold.json"

    if not force and threshold_path.exists():
        cached = json.loads(threshold_path.read_text())
        if cached.get("percentile") == percentile:
            return cached["threshold"]

    registry = tiles.get_registry()
    all_scores = []
    for tid in registry:
        raw = get_or_compute_raw(tid, buildings_path, force=force)
        buf = raw["buffer_mask"]
        if buf.any():
            all_scores.append(raw["score"][buf])

    threshold = float(np.percentile(np.concatenate(all_scores), percentile)) if all_scores else 1.0

    threshold_path.write_text(
        json.dumps({"percentile": percentile, "threshold": threshold, "n_tiles": len(registry)}, indent=2)
    )
    return threshold


def get_or_compute_overlay(tile_id, buildings_path, force=False):
    """Palauttaa (png_bytes, meta_dict). Kayttaa levyvalimuistia."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}.png"
    meta_path = CACHE_DIR / f"{tile_id}.json"

    if not force and png_path.exists() and meta_path.exists():
        return png_path.read_bytes(), json.loads(meta_path.read_text())

    registry = tiles.get_registry()
    if tile_id not in registry:
        raise KeyError(f"Tuntematon tile_id: {tile_id}")
    tile = registry[tile_id]

    dst_transform, dst_shape, bounds_wgs84 = compute_wgs84_grid(tile.bounds)

    raw = get_or_compute_raw(tile_id, buildings_path, force=force)
    visible_mask = dilate_mask(raw["buffer_mask"])
    visual_score = emphasize_low_scores(raw["score"])
    rgba = score_to_rgba(visual_score, visible_mask)
    rgba = reproject_to_wgs84(rgba, raw["src_transform"], dst_transform, dst_shape)

    ok, encoded = cv2.imencode(".png", rgba)
    if not ok:
        raise RuntimeError("PNG-enkoodaus epaonnistui")
    png_bytes = encoded.tobytes()

    meta = {
        "tile_id": tile_id,
        "bounds_epsg3067": list(tile.bounds),
        "bounds_wgs84": bounds_tuple_to_dict(bounds_wgs84),
        "n_buildings": raw["n_buildings"],
        "rock_pct": raw["rock_pct"],
        "swamp_pct": raw["swamp_pct"],
        "shoreline_px": raw["shoreline_px"],
        "buffer_px": raw["buffer_px"],
    }

    png_path.write_bytes(png_bytes)
    meta_path.write_text(json.dumps(meta, indent=2))

    return png_bytes, meta


def get_or_compute_top(tile_id, buildings_path, force=False):
    """Palauttaa PNG-tavuina erillisen kerroksen, joka nayttaa VAIN parhaat
    20% (koko aineiston 80. persentiili) puskurivyohykkeen pisteista."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}_top.png"

    if not force and png_path.exists():
        return png_path.read_bytes()

    registry = tiles.get_registry()
    if tile_id not in registry:
        raise KeyError(f"Tuntematon tile_id: {tile_id}")
    tile = registry[tile_id]

    dst_transform, dst_shape, _bounds_wgs84 = compute_wgs84_grid(tile.bounds)

    raw = get_or_compute_raw(tile_id, buildings_path, force=force)
    threshold = compute_global_threshold(buildings_path, force=force)

    top_mask = raw["buffer_mask"] & (raw["score"] >= threshold)
    top_mask_visible = dilate_mask(top_mask)

    bgra = np.zeros((*top_mask_visible.shape, 4), dtype=np.uint8)
    bgra[top_mask_visible, 0:3] = TOP_HIGHLIGHT_BGR
    bgra[top_mask_visible, 3] = TOP_HIGHLIGHT_ALPHA
    bgra = reproject_to_wgs84(bgra, raw["src_transform"], dst_transform, dst_shape)

    ok, encoded = cv2.imencode(".png", bgra)
    if not ok:
        raise RuntimeError("PNG-enkoodaus epaonnistui")
    png_bytes = encoded.tobytes()

    png_path.write_bytes(png_bytes)
    return png_bytes


def get_or_compute_basemap(tile_id, force=False):
    """Palauttaa taustakartaksi tarkoitetun karttakuva-leikkauksen PNG-tavuina,
    reprojisoituna samalle WGS84-ruudukolle kuin overlay, levyvalimuistilla."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}_base.png"

    if not force and png_path.exists():
        return png_path.read_bytes()

    registry = tiles.get_registry()
    if tile_id not in registry:
        raise KeyError(f"Tuntematon tile_id: {tile_id}")
    tile = registry[tile_id]

    dst_transform, dst_shape, _bounds_wgs84 = compute_wgs84_grid(tile.bounds)

    map_bgr, map_transform = raster_filters.load_map_window(str(tile.map_path), tile.bounds)
    coverage = reproject_coverage_mask(map_bgr.shape[:2], map_transform, dst_transform, dst_shape)
    map_bgr = reproject_to_wgs84(map_bgr, map_transform, dst_transform, dst_shape)
    map_bgra = np.dstack([map_bgr, coverage])

    ok, encoded = cv2.imencode(".png", map_bgra)
    if not ok:
        raise RuntimeError("PNG-enkoodaus epaonnistui")
    png_bytes = encoded.tobytes()

    png_path.write_bytes(png_bytes)
    return png_bytes


def get_tile_bounds_wgs84(tile):
    _dst_transform, _dst_shape, bounds_wgs84 = compute_wgs84_grid(tile.bounds)
    return bounds_tuple_to_dict(bounds_wgs84)
