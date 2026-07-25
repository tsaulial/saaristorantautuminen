"""
Vaihe 2: OpenCV-suodatin karttarasterille.

Lukee MML-karttakuvan (PNG + .pgw), muuntaa sen HSV-varitilaan ja tunnistaa
rantaviivan (#0080ff) ja kallion (#d1c7d1) binaarimaskeina.

Kartta on paletti-PNG (max 255 tasaista varia) - varit eivat liuku, mutta
ruskeat korkeuskayrat ja mustat karttamerkit voivat peittaa yksittaisia
kallio-alueen pikseleita ja pilkkoa sen katkonaiseksi. Siksi kalliomaskille
tehdaan morphological closing (cv2.morphologyEx) aukkojen silloittamiseksi.

Kayttoesimerkki:
    python3 -m backend.raster_filters \
        --map karttakuva-mll/L3123R.png \
        --out output/L3123R_vaihe2.npz \
        --preview
"""

import argparse
from pathlib import Path

import cv2
import numpy as np
import rasterio
from rasterio.windows import from_bounds

# HSV-rajat on kalibroitu naytetiilesta (L3123R) mitatuista paletin
# varvarvoista, ks. instructions.md kohdat C ja D.
#
# Rantaviiva #0080ff -> HSV(105,255,255): tayspunkyllainen, kirkas sininen.
# Naapurivari #0099ff (HSV h=102) on eri karttamerkki (esim. puro/vesiviiva),
# joten sävytoleranssi pidetaan tiukkana jotta se ei sekoitu rantaviivaan.
SHORELINE_HSV_LOWER = np.array([103, 200, 200], dtype=np.uint8)
SHORELINE_HSV_UPPER = np.array([107, 255, 255], dtype=np.uint8)

# Kallio #d1c7d1 -> HSV(150,12,209): lahes harmaa, hyvin matala saturaatio.
# Saturaatio (5-40) erottaa sen puhtaista harmaista (S=0, esim. varjostus)
# ja sävyikkuna (125-175) erottaa sen muista vaaleista pastellivareista
# (esim. vesi-/suoaluevarjostus H=90, maastovarjostus H=15).
ROCK_HSV_LOWER = np.array([125, 5, 180], dtype=np.uint8)
ROCK_HSV_UPPER = np.array([175, 40, 230], dtype=np.uint8)

# Suo #ffff40 -> HSV(30,191,255). Palettissa on useita lahellisia keltaisia
# savyja (esim. #ffd980 H=21 maastovarjostus, #fff266 H=27/S=153 luultavasti
# toinen varjostustaso, #d1cc40 H=29/V=209 luultavasti suon rajaviiva) -
# tiukka S- ja V-ikkuna erottaa tarkalleen #ffff40:n ja sen lahimmat
# antialiasoidut reunapikselit niista.
SWAMP_HSV_LOWER = np.array([29, 180, 250], dtype=np.uint8)
SWAMP_HSV_UPPER = np.array([31, 255, 255], dtype=np.uint8)


def load_map_raster(png_path):
    """Lukee kartta-PNG:n pikselidatan (BGR, paletti puretaan automaattisesti)
    ja georeferoinnin (.pgw-tiedostosta, sama nimi vieressa)."""
    bgr = cv2.imread(str(png_path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(f"Karttakuvaa ei voitu lukea: {png_path}")

    with rasterio.open(png_path) as ds:
        transform = ds.transform
        bounds = ds.bounds

    return bgr, transform, bounds


def load_map_window(png_path, bbox):
    """Lukee vain bbox:aa (minx,miny,maxx,maxy) vastaavan ikkunan karttakuvasta
    palettidatana ja purkaa sen BGR:ksi itse - valttaa koko 12000x12000-tiilen
    lataamisen muistiin kun tarvitaan vain yhta DEM-tiilen kokoista aluetta."""
    with rasterio.open(png_path) as ds:
        window = from_bounds(*bbox, transform=ds.transform).round_offsets().round_lengths()
        index = ds.read(1, window=window)
        colormap = ds.colormap(1)
        transform = ds.window_transform(window)

    lut = np.zeros((256, 3), dtype=np.uint8)
    for idx, (r, g, b, _a) in colormap.items():
        lut[idx] = (b, g, r)
    bgr = lut[index]
    return bgr, transform


def _color_mask(hsv_img, lower, upper, close_kernel_px):
    mask = cv2.inRange(hsv_img, lower, upper)
    if close_kernel_px > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (close_kernel_px, close_kernel_px)
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask.astype(bool)


def detect_shoreline_mask(bgr_img, hsv_img=None, close_kernel_px=3):
    if hsv_img is None:
        hsv_img = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2HSV)
    return _color_mask(hsv_img, SHORELINE_HSV_LOWER, SHORELINE_HSV_UPPER, close_kernel_px)


def detect_rock_mask(bgr_img, hsv_img=None, close_kernel_px=9):
    if hsv_img is None:
        hsv_img = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2HSV)
    return _color_mask(hsv_img, ROCK_HSV_LOWER, ROCK_HSV_UPPER, close_kernel_px)


def detect_swamp_mask(bgr_img, hsv_img=None, close_kernel_px=9):
    if hsv_img is None:
        hsv_img = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2HSV)
    return _color_mask(hsv_img, SWAMP_HSV_LOWER, SWAMP_HSV_UPPER, close_kernel_px)


def run(map_path, out_path, preview=False, rock_close_px=9, shoreline_close_px=3, swamp_close_px=9):
    bgr, transform, bounds = load_map_raster(map_path)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    shoreline_mask = detect_shoreline_mask(bgr, hsv, close_kernel_px=shoreline_close_px)
    rock_mask_raw = detect_rock_mask(bgr, hsv, close_kernel_px=0)
    rock_mask = detect_rock_mask(bgr, hsv, close_kernel_px=rock_close_px)
    swamp_mask_raw = detect_swamp_mask(bgr, hsv, close_kernel_px=0)
    swamp_mask = detect_swamp_mask(bgr, hsv, close_kernel_px=swamp_close_px)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path,
        shoreline_mask=shoreline_mask,
        rock_mask=rock_mask,
        swamp_mask=swamp_mask,
        transform=np.array(transform)[:6],
        bounds=np.array(bounds),
    )

    n_px = shoreline_mask.size
    filled_by_closing = rock_mask.sum() - rock_mask_raw.sum()
    swamp_filled_by_closing = swamp_mask.sum() - swamp_mask_raw.sum()
    print(f"Kartta: {map_path}  koko: {bgr.shape[:2]}  bounds: {bounds}")
    print(
        f"Rantaviiva-pikseleita: {shoreline_mask.sum()} "
        f"({100 * shoreline_mask.sum() / n_px:.3f} %)"
    )
    print(
        f"Kallio-pikseleita: {rock_mask.sum()} ({100 * rock_mask.sum() / n_px:.2f} %) "
        f"- joista {filled_by_closing} px (closing-operaation tayttamia aukkoja)"
    )
    print(
        f"Suo-pikseleita: {swamp_mask.sum()} ({100 * swamp_mask.sum() / n_px:.2f} %) "
        f"- joista {swamp_filled_by_closing} px (closing-operaation tayttamia aukkoja)"
    )
    print(f"Tallennettu: {out_path}")

    if preview:
        stem = out_path.with_suffix("")
        preview_img = bgr.copy()
        preview_img[rock_mask] = (255, 0, 255)  # magenta = kallio
        preview_img[swamp_mask] = (0, 200, 200)  # oliivi/kelta = suo
        preview_img[shoreline_mask] = (0, 0, 255)  # punainen = rantaviiva
        cv2.imwrite(f"{stem}_preview.png", preview_img)
        cv2.imwrite(f"{stem}_rock_mask.png", (rock_mask * 255).astype(np.uint8))
        cv2.imwrite(f"{stem}_shoreline_mask.png", (shoreline_mask * 255).astype(np.uint8))
        cv2.imwrite(f"{stem}_swamp_mask.png", (swamp_mask * 255).astype(np.uint8))
        print(
            f"Esikatselukuvat: {stem}_preview.png, {stem}_rock_mask.png, "
            f"{stem}_shoreline_mask.png, {stem}_swamp_mask.png"
        )


def main():
    parser = argparse.ArgumentParser(description="Vaihe 2: rantaviiva- ja kalliosuodatin")
    parser.add_argument("--map", required=True, help="Polku karttakuva-PNG:hen (.pgw vieressa)")
    parser.add_argument("--out", required=True, help="Polku tulos-npz-tiedostolle")
    parser.add_argument("--preview", action="store_true", help="Kirjoita myos PNG-esikatselukuvat")
    parser.add_argument(
        "--rock-close-px", type=int, default=9, help="Morphological closing -kernelin koko kalliolle"
    )
    parser.add_argument(
        "--shoreline-close-px",
        type=int,
        default=3,
        help="Morphological closing -kernelin koko rantaviivalle",
    )
    args = parser.parse_args()

    run(
        map_path=args.map,
        out_path=args.out,
        preview=args.preview,
        rock_close_px=args.rock_close_px,
        shoreline_close_px=args.shoreline_close_px,
    )


if __name__ == "__main__":
    main()
