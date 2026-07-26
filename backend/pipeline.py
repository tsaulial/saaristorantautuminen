"""
Vaihe 3: Yhdistaa Vaihe 1:n (jyrkkyys + rakennusetaisyys) ja Vaihe 2:n
(kallio + rantaviiva + suo) yhdeksi kokonaispistemaaraksi S per DEM-tiili, ja
generoi RGBA-overlay-kuvan vain rantaviivan puskurivyohykkeelle
(instructions.md kohta 3, D).

Peruskartta (karttakuva-mll) on VIITERUUDUKKO: sen pikselit pysyvat
muuttumattomina, tarkalleen sellaisina kuin MML on ne tuottanut (1m/px,
EPSG:3067, ei kiertoa - ks. .pgw-tiedostot). Kaikki muu data (DEM-pohjainen
jyrkkyys+etaisyys, kallio/rantaviiva/suo-maskit, lopullinen pistemaara)
resamploidaan TAMAN saman ruudukon paalle, tallakin CRS:lla (EPSG:3067) -
ei geodeettista reprojisointia mihinkaan suuntaan. Nain peruskartta ja
sen paalle piirrettava pisteytys asettuvat aina tarkalleen paallekkain
ilman minkaanlaista resamplaus- tai saumavirhetta, koska molemmat ovat
samalla pikseliruudukolla.

Nayttopuolella (frontend/index.html) Leaflet kayttaa L.CRS.Simple-CRS:aa:
EPSG:3067-metrit syotetaan suoraan lat/lng-pareina (pohjoinen=lat, ita=lng),
IlmAN mitaan proj4/geodeettista muunnosta - koska karttakuva ei ole kiertynyt
suhteessa ruudukkoonsa, tama riittaa pikselintarkkaan, saumattomaan
esitykseen ilman ylimaaraisia riippuvuuksia.

Tulos valimuistetaan levylle (output/cache/) - lasketaan vain kerran per
tiili, ks. Vaihe 1:n suunnittelupaatos ("koko lehti kerralla + cache").

Kaksi laskentavaihetta:
1. get_or_compute_raw(tile_id): raaka pistemaara + puskurimaski peruskartan
   omalla 1m/px-ruudukolla (EPSG:3067), valimuistettu .npz:na. Sisaltaa jo
   suo-rangaistuksen. Kayttaa seka yksittaisen tiilen piirtoa etta globaalia
   persentiililaskentaa.
2. compute_global_threshold(): kerää KAIKKIEN tiilien puskurivyohykkeen
   pisteet yhteen ja laskee persentiilin - "parhaat X%" maaritellaan koko
   aineiston, ei yksittaisen tiilen, suhteen. Tama on tietoinen arkkitehtuuri-
   valinta: ensimmainen /api/overlay/*/top.png-pyynto laskee KAIKKIEN
   tiilien raa'an pistemaaran jos niita ei viela ole valimuistissa.
"""

import json
from pathlib import Path

import cv2
import numpy as np
from rasterio.crs import CRS
from rasterio.transform import Affine, array_bounds
from rasterio.warp import Resampling as WarpResampling
from rasterio.warp import reproject
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

# Puskurivyohyke on todellisuudessa vain muutaman metrin levyinen eika erotu
# ulompana zoomitasolla. Paksunnetaan sita PELKASTAAN nakymista varten
# (dilataatio, sade metreina - muunnetaan pikseleiksi peruskartan pixel_sizen
# mukaan) - taustalla oleva data/tilastot pysyvat tarkkoina.
BUFFER_VISUAL_DILATION_M = 10.0

# Jyrkkyys ($S_{slope}$) vaihtelee hyvin paikallisesti, joten huonot pisteet
# (keltainen/punainen) nakyvat pienina, hajanaisina taplina. Korostetaan
# niita PELKASTAAN renderoinnissa: kunkin pikselin variksi otetaan
# lahiymparistonsa (sade metreina) PIENIN pistemaara (minimum_filter), jolloin
# huono kohta "leviaa" nakyvaan kuvaan laajemmalle. Tilastot, suo-rangaistus ja
# top-persentiilin kynnysarvo perustuvat yha tarkkaan, suodattamattomaan
# pisteeseen.
LOW_SCORE_EMPHASIS_M = 6.0

# "Parhaat rantautumispaikat" = koko aineiston (kaikki tiilet) pisteiden
# 90. persentiili (= paras 10%) puskurivyohykkeen sisalla.
TOP_PERCENTILE = 90
TOP_HIGHLIGHT_BGR = (255, 0, 255)  # magenta - erottuu selvasti vihrea/keltainen/punainen-asteikosta
TOP_HIGHLIGHT_ALPHA = 230

CACHE_DIR = Path(__file__).resolve().parent.parent / "output" / "cache"

SRC_CRS = CRS.from_epsg(3067)


def meters_to_px(radius_m, pixel_size):
    return max(1, round(radius_m / pixel_size))


def dilate_mask(mask, radius_px):
    """Paksuntaa binaarimaskia pelkkaa visualisointia varten."""
    if radius_px <= 0:
        return mask
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius_px + 1, 2 * radius_px + 1))
    return cv2.dilate(mask.astype(np.uint8), kernel).astype(bool)


def emphasize_low_scores(score, radius_px):
    """Palauttaa version pistemaarasta jossa huonot arvot on levitetty
    lahiymparistoonsa (ks. LOW_SCORE_EMPHASIS_M). Vain visualisointia varten."""
    if radius_px <= 0:
        return score
    return minimum_filter(score, size=2 * radius_px + 1, mode="nearest")


def compute_shoreline_buffer(shoreline_mask, dem, pixel_size):
    """Palauttaa boolean-maskin: True niille pikseleille jotka ovat maalla
    JA 5-15m etaisyydella lahimmasta rantaviivapikselista. Maa/vesi
    eroteltu DEM:n 0m-tason perusteella (instructions.md kohta D)."""
    land = dem > 0.0
    non_shore = ~shoreline_mask
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


def resample_to_grid(img, src_transform, dst_transform, dst_shape, resampling=WarpResampling.bilinear):
    """Resamploi rasterin toiselle ruudukolle SAMAN CRS:n (EPSG:3067) sisalla -
    ei geodeettista reprojisointia, pelkka ruudukon/resoluution muutos (esim.
    DEM:n 2m/px -> peruskartan 1m/px)."""
    dst_h, dst_w = dst_shape
    is_2d = img.ndim == 2
    src = img[..., None] if is_2d else img
    dst = np.zeros((dst_h, dst_w, src.shape[2]), dtype=src.dtype)
    for b in range(src.shape[2]):
        reproject(
            source=src[..., b],
            destination=dst[..., b],
            src_transform=src_transform,
            src_crs=SRC_CRS,
            dst_transform=dst_transform,
            dst_crs=SRC_CRS,
            resampling=resampling,
        )
    return dst[..., 0] if is_2d else dst


def bounds_tuple_to_dict(bounds_3067):
    minx, miny, maxx, maxy = bounds_3067
    return {"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy}


def get_map_grid(tile):
    """Peruskartan oma pikseliruudukko (transform, shape) tiilen rajoihin
    leikattuna - tama on koko pipelinen viiteruudukko, ks. moduulin docstring."""
    return raster_filters.map_window_geometry(str(tile.map_path), tile.bounds)


def compute_tile(tile, buildings_path):
    """Laskee tiilen pistemaaran peruskartan omalla ruudukolla (viiteruudukko,
    ks. moduulin docstring). DEM-pohjaiset jatkuvat kentat (jyrkkyys, etaisyys,
    korkeus) resamploidaan omalta 2m/px-ruudukoltaan tahan ruudukkoon; kartta-
    pohjaiset maskit (kallio, rantaviiva, suo) ovat jo natiivisti silla."""
    v1 = score_engine.compute(str(tile.dem_path), buildings_path)

    map_transform, map_shape = get_map_grid(tile)
    pixel_size = abs(map_transform.a)

    map_bgr, _map_transform = raster_filters.load_map_window(str(tile.map_path), tile.bounds)
    rock_mask = raster_filters.detect_rock_mask(map_bgr)
    shoreline_mask = raster_filters.detect_shoreline_mask(map_bgr)
    swamp_mask = raster_filters.detect_swamp_mask(map_bgr)

    slope_score = resample_to_grid(v1["slope_score"], v1["transform"], map_transform, map_shape)
    dist_score = resample_to_grid(v1["dist_score"], v1["transform"], map_transform, map_shape)
    dem = resample_to_grid(v1["dem"], v1["transform"], map_transform, map_shape)

    rock_score = np.where(rock_mask, ROCK_SCORE_YES, ROCK_SCORE_NO)
    total_score = slope_score * score_engine.SLOPE_WEIGHT
    total_score = total_score + dist_score * score_engine.DIST_WEIGHT
    total_score = total_score + rock_score * ROCK_WEIGHT
    total_score = np.where(swamp_mask, total_score * SWAMP_PENALTY_FACTOR, total_score)

    buffer_mask = compute_shoreline_buffer(shoreline_mask, dem, pixel_size)

    return {
        "score": total_score,
        "buffer_mask": buffer_mask,
        "map_transform": map_transform,
        "n_buildings": v1["n_buildings"],
        "rock_pct": 100 * rock_mask.mean(),
        "swamp_pct": 100 * swamp_mask.mean(),
        "shoreline_px": int(shoreline_mask.sum()),
        "buffer_px": int(buffer_mask.sum()),
    }


def get_or_compute_raw(tile_id, buildings_path, force=False):
    """Palauttaa tiilen raa'an pistemaara+puskurimaski-tuloksen peruskartan
    omalla ruudukolla (EPSG:3067), levyvalimuistilla. Kayttaa seka yksittaisen
    tiilen piirtoa etta globaalia persentiililaskentaa - lasketaan siis vain
    kerran per tiili riippumatta kummasta tarpeesta se ensin tulee."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    npz_path = CACHE_DIR / f"{tile_id}_raw.npz"

    if not force and npz_path.exists():
        data = np.load(npz_path)
        return {
            "score": data["score"],
            "buffer_mask": data["buffer_mask"].astype(bool),
            "map_transform": Affine(*data["map_transform"]),
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
        map_transform=np.array(result["map_transform"])[:6],
        n_buildings=result["n_buildings"],
        rock_pct=result["rock_pct"],
        swamp_pct=result["swamp_pct"],
        shoreline_px=result["shoreline_px"],
        buffer_px=result["buffer_px"],
    )
    return result


def compute_global_threshold(buildings_path, percentile=TOP_PERCENTILE, force=False):
    """Laskee pistemaaran percentile:n (oletus TOP_PERCENTILE) kaikkien
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
    """Palauttaa (png_bytes, meta_dict). Kayttaa levyvalimuistia. Ei
    resamplausta - pistemaara on jo laskettu peruskartan omalle ruudukolle
    (compute_tile), joten se voidaan piirtaa suoraan sellaisenaan."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}.png"
    meta_path = CACHE_DIR / f"{tile_id}.json"

    if not force and png_path.exists() and meta_path.exists():
        return png_path.read_bytes(), json.loads(meta_path.read_text())

    registry = tiles.get_registry()
    if tile_id not in registry:
        raise KeyError(f"Tuntematon tile_id: {tile_id}")
    tile = registry[tile_id]

    raw = get_or_compute_raw(tile_id, buildings_path, force=force)
    pixel_size = abs(raw["map_transform"].a)

    visible_mask = dilate_mask(raw["buffer_mask"], meters_to_px(BUFFER_VISUAL_DILATION_M, pixel_size))
    visual_score = emphasize_low_scores(raw["score"], meters_to_px(LOW_SCORE_EMPHASIS_M, pixel_size))
    rgba = score_to_rgba(visual_score, visible_mask)

    ok, encoded = cv2.imencode(".png", rgba)
    if not ok:
        raise RuntimeError("PNG-enkoodaus epaonnistui")
    png_bytes = encoded.tobytes()

    bounds_3067 = array_bounds(*rgba.shape[:2], raw["map_transform"])
    meta = {
        "tile_id": tile_id,
        "bounds_epsg3067": bounds_tuple_to_dict(bounds_3067),
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
    X% (TOP_PERCENTILE) puskurivyohykkeen pisteista. Peruskartan omalla
    ruudukolla, ei resamplausta."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}_top.png"

    if not force and png_path.exists():
        return png_path.read_bytes()

    registry = tiles.get_registry()
    if tile_id not in registry:
        raise KeyError(f"Tuntematon tile_id: {tile_id}")
    tile = registry[tile_id]

    raw = get_or_compute_raw(tile_id, buildings_path, force=force)
    pixel_size = abs(raw["map_transform"].a)
    threshold = compute_global_threshold(buildings_path, force=force)

    top_mask = raw["buffer_mask"] & (raw["score"] >= threshold)
    top_mask_visible = dilate_mask(top_mask, meters_to_px(BUFFER_VISUAL_DILATION_M, pixel_size))

    bgra = np.zeros((*top_mask_visible.shape, 4), dtype=np.uint8)
    bgra[top_mask_visible, 0:3] = TOP_HIGHLIGHT_BGR
    bgra[top_mask_visible, 3] = TOP_HIGHLIGHT_ALPHA

    ok, encoded = cv2.imencode(".png", bgra)
    if not ok:
        raise RuntimeError("PNG-enkoodaus epaonnistui")
    png_bytes = encoded.tobytes()

    png_path.write_bytes(png_bytes)
    return png_bytes


def get_or_compute_basemap(tile_id, force=False):
    """Palauttaa taustakartaksi tarkoitetun karttakuva-leikkauksen PNG-tavuina,
    MUUTTUMATTOMANA (ei resamplausta/reprojisointia), levyvalimuistilla."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}_base.png"

    if not force and png_path.exists():
        return png_path.read_bytes()

    registry = tiles.get_registry()
    if tile_id not in registry:
        raise KeyError(f"Tuntematon tile_id: {tile_id}")
    tile = registry[tile_id]

    map_bgr, _map_transform = raster_filters.load_map_window(str(tile.map_path), tile.bounds)

    ok, encoded = cv2.imencode(".png", map_bgr)
    if not ok:
        raise RuntimeError("PNG-enkoodaus epaonnistui")
    png_bytes = encoded.tobytes()

    png_path.write_bytes(png_bytes)
    return png_bytes


def get_tile_bounds(tile):
    transform, shape = get_map_grid(tile)
    return bounds_tuple_to_dict(array_bounds(*shape, transform))
