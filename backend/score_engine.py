"""
Vaihe 1: GIS-pisteutusmoottori.

Lukee DEM-korkeusmallin (GeoTIFF) ja rakennukset.gpkg-tiedoston, ja laskee
jyrkkyys- ja rakennusetaisyyspisteytysmatriisit samalle pikseliruudukolle.

Kayttoesimerkki:
    python3 -m backend.score_engine \
        --dem korkeusmalli-mml/L3123F.tif \
        --buildings rakennukset-mll/rakennukset.gpkg \
        --out output/L3123F_vaihe1.npz \
        --preview
"""

import argparse
from pathlib import Path

import cv2
import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import Affine, array_bounds
from scipy.ndimage import distance_transform_edt

SLOPE_FLAT_DEG = 5.0
SLOPE_REJECT_DEG = 20.0
SLOPE_WEIGHT = 0.50

DIST_REJECT_M = 20.0
DIST_IDEAL_M = 150.0
DIST_WEIGHT = 0.35

BUILDING_LAYERS = ("rakennus", "rakennelma")


def read_dem(dem_path):
    with rasterio.open(dem_path) as ds:
        dem = ds.read(1)
        transform = ds.transform
        crs = ds.crs
        nodata = ds.nodata
        pixel_size = ds.res[0]
    return dem, transform, crs, nodata, pixel_size


def compute_slope_score(dem, nodata, pixel_size):
    valid = dem != nodata if nodata is not None else np.ones(dem.shape, dtype=bool)
    dzdy, dzdx = np.gradient(dem, pixel_size)
    slope_deg = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))

    score = np.where(
        slope_deg <= SLOPE_FLAT_DEG,
        1.0,
        np.where(
            slope_deg >= SLOPE_REJECT_DEG,
            0.0,
            1.0 - (slope_deg - SLOPE_FLAT_DEG) / (SLOPE_REJECT_DEG - SLOPE_FLAT_DEG),
        ),
    )
    score = np.where(valid, score, np.nan)
    return score, slope_deg


def load_buildings(gpkg_path, bbox, crs, layers=BUILDING_LAYERS, pad_m=DIST_IDEAL_M):
    minx, miny, maxx, maxy = bbox
    padded_bbox = (minx - pad_m, miny - pad_m, maxx + pad_m, maxy + pad_m)

    frames = []
    for layer in layers:
        gdf = gpd.read_file(gpkg_path, layer=layer, bbox=padded_bbox)
        if gdf.crs != crs:
            gdf = gdf.to_crs(crs)
        if len(gdf):
            frames.append(gdf[["geometry"]])

    if not frames:
        return gpd.GeoDataFrame(geometry=[], crs=crs)
    return gpd.GeoDataFrame(pd_concat(frames), crs=crs)


def pd_concat(frames):
    import pandas as pd

    return pd.concat(frames, ignore_index=True)


def compute_distance_score(buildings, shape, transform, pixel_size, pad_m=DIST_IDEAL_M):
    """Etaisyys lahimpaan rakennukseen, laskettu tiilen ulkopuolelle paddatulla
    ruudukolla jotta reunalla olevat rakennukset naapuritiilesta vaikuttavat
    oikein etaisyyksiin tiilen reunapikseleissa."""
    pad_px = int(np.ceil(pad_m / pixel_size))
    rows, cols = shape

    padded_shape = (rows + 2 * pad_px, cols + 2 * pad_px)
    padded_transform = transform * Affine.translation(-pad_px, -pad_px)

    if len(buildings):
        building_mask = rasterize(
            [(geom, 1) for geom in buildings.geometry if geom is not None],
            out_shape=padded_shape,
            transform=padded_transform,
            fill=0,
            dtype="uint8",
        ).astype(bool)
    else:
        building_mask = np.zeros(padded_shape, dtype=bool)

    non_building = ~building_mask
    dist_padded = distance_transform_edt(non_building, sampling=(pixel_size, pixel_size))
    dist_m = dist_padded[pad_px : pad_px + rows, pad_px : pad_px + cols]

    score = np.where(
        dist_m < DIST_REJECT_M,
        0.0,
        np.where(
            dist_m > DIST_IDEAL_M,
            1.0,
            (dist_m - DIST_REJECT_M) / (DIST_IDEAL_M - DIST_REJECT_M),
        ),
    )
    return score, dist_m


def score_to_preview(score):
    """Pisteet 0..1 -> punainen-keltainen-vihrea BGR-esikatselukuva.
    NaN-pikselit (esim. DEM nodata) piirretaan mustana."""
    valid = ~np.isnan(score)
    clipped = np.clip(np.nan_to_num(score, nan=0.0), 0.0, 1.0)

    hsv = np.zeros((*score.shape, 3), dtype=np.uint8)
    hsv[..., 0] = (clipped * 60).astype(np.uint8)  # 0=punainen, 60=vihrea (cv2-asteikko 0-179)
    hsv[..., 1] = 255
    hsv[..., 2] = np.where(valid, 255, 0).astype(np.uint8)

    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return bgr


def compute(dem_path, buildings_path, layers=BUILDING_LAYERS):
    """Laskee jyrkkyys- ja rakennusetaisyyspisteytyksen yhdelle DEM-tiilelle.
    Palauttaa dictin, jota seka CLI (run) etta Vaihe 3:n pipeline voivat kayttaa."""
    dem, transform, crs, nodata, pixel_size = read_dem(dem_path)
    bounds = array_bounds(dem.shape[0], dem.shape[1], transform)

    slope_score, slope_deg = compute_slope_score(dem, nodata, pixel_size)

    buildings = load_buildings(buildings_path, bounds, crs, layers=layers)
    dist_score, dist_m = compute_distance_score(buildings, dem.shape, transform, pixel_size)

    weight_sum = SLOPE_WEIGHT + DIST_WEIGHT
    partial_score = (SLOPE_WEIGHT * slope_score + DIST_WEIGHT * dist_score) / weight_sum

    return {
        "dem": dem,
        "nodata": nodata,
        "pixel_size": pixel_size,
        "transform": transform,
        "crs": crs,
        "bounds": bounds,
        "slope_score": slope_score,
        "slope_deg": slope_deg,
        "dist_score": dist_score,
        "dist_m": dist_m,
        "partial_score": partial_score,
        "n_buildings": len(buildings),
    }


def run(dem_path, buildings_path, out_path, preview=False, layers=BUILDING_LAYERS):
    result = compute(dem_path, buildings_path, layers=layers)
    transform, crs, bounds = result["transform"], result["crs"], result["bounds"]
    slope_score, slope_deg = result["slope_score"], result["slope_deg"]
    dist_score, dist_m = result["dist_score"], result["dist_m"]
    partial_score = result["partial_score"]

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path,
        slope_score=slope_score.astype(np.float32),
        dist_score=dist_score.astype(np.float32),
        partial_score=partial_score.astype(np.float32),
        slope_deg=slope_deg.astype(np.float32),
        dist_m=dist_m.astype(np.float32),
        transform=np.array(transform)[:6],
        crs=str(crs),
        bounds=np.array(bounds),
    )

    print(f"DEM: {dem_path}  koko: {result['dem'].shape}  crs: {crs}  bounds: {bounds}")
    print(f"Rakennuksia/rakennelmia paddatulla alueella: {result['n_buildings']}")
    print(
        "slope_score  min/mean/max: "
        f"{np.nanmin(slope_score):.3f}/{np.nanmean(slope_score):.3f}/{np.nanmax(slope_score):.3f}"
    )
    print(
        "dist_score   min/mean/max: "
        f"{np.nanmin(dist_score):.3f}/{np.nanmean(dist_score):.3f}/{np.nanmax(dist_score):.3f}"
    )
    print(
        "partial_score min/mean/max: "
        f"{np.nanmin(partial_score):.3f}/{np.nanmean(partial_score):.3f}/{np.nanmax(partial_score):.3f}"
    )
    print(f"Tallennettu: {out_path}")

    if preview:
        stem = out_path.with_suffix("")
        cv2.imwrite(f"{stem}_slope.png", score_to_preview(slope_score))
        cv2.imwrite(f"{stem}_dist.png", score_to_preview(dist_score))
        cv2.imwrite(f"{stem}_partial.png", score_to_preview(partial_score))
        print(f"Esikatselukuvat: {stem}_slope.png, {stem}_dist.png, {stem}_partial.png")


def main():
    parser = argparse.ArgumentParser(description="Vaihe 1: jyrkkyys- ja rakennusetaisyyspisteytys")
    parser.add_argument("--dem", required=True, help="Polku DEM GeoTIFF-tiedostoon")
    parser.add_argument(
        "--buildings",
        default="rakennukset-mll/rakennukset.gpkg",
        help="Polku rakennukset.gpkg-tiedostoon",
    )
    parser.add_argument(
        "--layers",
        default=",".join(BUILDING_LAYERS),
        help="Pilkulla eroteltu lista gpkg-layereista (oletus: rakennus,rakennelma)",
    )
    parser.add_argument("--out", required=True, help="Polku tulos-npz-tiedostolle")
    parser.add_argument(
        "--preview", action="store_true", help="Kirjoita myos PNG-esikatselukuvat"
    )
    args = parser.parse_args()

    run(
        dem_path=args.dem,
        buildings_path=args.buildings,
        out_path=args.out,
        preview=args.preview,
        layers=tuple(args.layers.split(",")),
    )


if __name__ == "__main__":
    main()
