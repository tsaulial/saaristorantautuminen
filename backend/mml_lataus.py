"""Lahtoaineiston lataus MML:n tiedostopalvelusta.

Aineisto oli aiemmin ladattu kasin Karttapaikasta, mika riitti yhdelle
18x30 km koealueelle mutta ei laajennukselle. Tama moduuli hakee samat kolme
aineistoa ohjelmallisesti, jolloin uusi alue on yhden komennon paassa.

    korkeusmalli_2m_karttalehti        -> korkeusmalli-mml/*.tif   (6x6 km)
    maastokartta_rasteri_karttalehti   -> karttakuva-mll/*.png     (12x12 km)
    maastotietokanta_bbox              -> rakennukset-mml/*.gpkg

Laserkeilaus on backend/lidar.py:ssa, koska sen kasittely (lataa, hilauta,
poista) on aivan eri luokkaa - raaka aineisto on kymmenia gigatavuja.

KARTTATUOTE ON TODENNETTAVA ENNEN JOUKKOLATAUSTA. Vesimaski tunnistaa meren
VARISTA (raster_filters.WATER_FILL_HSV). Nykyisessa aineistossa meri on
RGB(128,255,255) = HSV(90,127,255), tasan rajojen keskella. Jos rajapinta
antaisi eri variantin (taustavari, ei_pehmennysta), maski hajoaisi HILJAA:
mikaan ei kaatuisi, mutta jokainen pistemaara olisi vaara. Siksi
--todenna-kartta lataa yhden lehden jonka meilla jo on ja vertaa maskit
pikselitasolla.
"""

import argparse
import json
import shutil
from pathlib import Path

from . import karttalehti, mml, rannikko

ROOT = Path(__file__).resolve().parent.parent
DEM_DIR = ROOT / "korkeusmalli-mml"
MAP_DIR = ROOT / "karttakuva-mll"
BUILDINGS_DIR = ROOT / "rakennukset-mll"
BUILDINGS_PATH = BUILDINGS_DIR / "rakennukset.gpkg"
CACHE_DIR = ROOT / "output" / "cache"

DEM_PROCESS = "korkeusmalli_2m_karttalehti"
MAP_PROCESS = "maastokartta_rasteri_karttalehti"
MTK_PROCESS = "maastotietokanta_bbox"

# Painovarinen 1:10000 rasteri on se tuote jonka varit vesimaski tuntee.
MAP_DATASET = "maastokartta_rasteri_10k_painovari"

# maastotietokanta_bbox: suurin sallittu pinta-ala 17 334 km2 per haku.
MTK_MAX_AREA_KM2 = 17000.0


def _erat(jono, koko=mml.MAX_SHEETS_PER_JOB):
    jono = list(jono)
    for i in range(0, len(jono), koko):
        yield jono[i:i + koko]


def _puuttuvat(lehdet, kansio, paate):
    """Lehdet joita ei ole viela levylla - ajo on toistettavissa."""
    return [s for s in lehdet if not (kansio / f"{s}{paate}").exists()]


def lataa_korkeusmalli(lehdet, key=None):
    """DEM 2 m -lehdet TIFF-muodossa. Palauttaa ladatut polut."""
    key = key or mml.api_key()
    puuttuu = _puuttuvat(lehdet, DEM_DIR, ".tif")
    print(f"korkeusmalli: {len(lehdet)} lehtea, joista puuttuu {len(puuttuu)}")
    ulos = []
    for i, era in enumerate(_erat(puuttuu), 1):
        print(f"  era {i}: {len(era)} lehtea...")
        tulokset = mml.run_job(DEM_PROCESS, {
            "mapSheetInput": era,
            "fileFormatInput": "TIFF",
        }, key=key)
        for item in tulokset:
            ulos.append(mml.download_to(item, DEM_DIR, key))
    return ulos


def lataa_kartta(lehdet, key=None):
    """Peruskarttarasterit PNG-muodossa (+ .pgw jos rajapinta antaa)."""
    key = key or mml.api_key()
    puuttuu = _puuttuvat(lehdet, MAP_DIR, ".png")
    print(f"kartta: {len(lehdet)} lehtea, joista puuttuu {len(puuttuu)}")
    ulos = []
    for i, era in enumerate(_erat(puuttuu), 1):
        print(f"  era {i}: {len(era)} lehtea...")
        tulokset = mml.run_job(MAP_PROCESS, {
            "mapSheetInput": era,
            "fileFormatInput": "PNG",
            "dataSetInput": MAP_DATASET,
        }, key=key)
        for item in tulokset:
            ulos.append(mml.download_to(item, MAP_DIR, key))
    return ulos


def lataa_hydrografia(bbox, key=None, dest=None):
    """Maastotietokannan hydrografia: meri, jarvi ym. omina tasoinaan.

    Talta tulee sek MERI ETTA RANTAVIIVA (meri-polygonin reuna). Peruskartan
    varit eivat erota niita vesistojen nimista - ks. backend/vesisto.py."""
    key = key or mml.api_key()
    x0, y0, x1, y1 = bbox
    ala = (x1 - x0) * (y1 - y0) / 1e6
    if ala > MTK_MAX_AREA_KM2:
        raise ValueError(f"Alue {ala:.0f} km2 ylittaa rajapinnan rajan - pilko haku")
    print(f"hydrografia: {ala:.0f} km2")
    tulokset = mml.run_job(MTK_PROCESS, {
        "boundingBoxInput": [x0, y0, x1, y1],
        "themeInput": "hydrografia",
        "fileFormatInput": "GPKG",
    }, key=key)
    dest = Path(dest or ROOT / "vesistot-mml" / "_uusi")
    return [mml.download_to(item, dest, key) for item in tulokset]


def yhdista_hydrografia(uudet_gpkg, kohde=None):
    """Liittaa meri-tason olemassa olevaan hydrografia.gpkg:hen."""
    import geopandas as gpd
    import pandas as pd

    kohde = Path(kohde or ROOT / "vesistot-mml" / "hydrografia.gpkg")
    kohde.parent.mkdir(parents=True, exist_ok=True)
    palat = []
    if kohde.exists():
        palat.append(gpd.read_file(kohde, layer="meri"))
    for polku in uudet_gpkg:
        palat.append(gpd.read_file(polku, layer="meri"))
    palat = [g for g in palat if len(g)]
    if not palat:
        return
    yhd = gpd.GeoDataFrame(pd.concat(palat, ignore_index=True), crs=palat[0].crs)
    yhd.to_file(kohde, layer="meri", driver="GPKG")
    print(f"  meri: {len(yhd)} polygonia, {yhd.area.sum() / 1e6:.0f} km2")


def lataa_rakennukset(bbox, key=None, dest=None):
    """Maastotietokannan rakennusteema GPKG:na yhdelle laatikolle."""
    key = key or mml.api_key()
    x0, y0, x1, y1 = bbox
    ala = (x1 - x0) * (y1 - y0) / 1e6
    if ala > MTK_MAX_AREA_KM2:
        raise ValueError(f"Alue {ala:.0f} km2 ylittaa rajapinnan rajan "
                         f"{MTK_MAX_AREA_KM2:.0f} km2 - pilko haku")
    print(f"rakennukset: {ala:.0f} km2")
    tulokset = mml.run_job(MTK_PROCESS, {
        "boundingBoxInput": [x0, y0, x1, y1],
        "themeInput": "rakennukset",
        "fileFormatInput": "GPKG",
    }, key=key)
    dest = Path(dest or BUILDINGS_DIR / "_uusi")
    return [mml.download_to(item, dest, key) for item in tulokset]


def yhdista_rakennukset(uudet_gpkg, kohde=BUILDINGS_PATH):
    """Liittaa uudet rakennukset olemassa olevaan GPKG:hen tasoittain.

    score_engine.load_buildings lukee nimetyt tasot bbox-suodattimella, joten
    yhdistetty tiedosto toimii sellaisenaan - alueiden ei tarvitse olla
    yhtenaisia."""
    import geopandas as gpd
    import pandas as pd
    import pyogrio

    olemassa = {t: None for t in pyogrio.list_layers(kohde)[:, 0]} if kohde.exists() else {}
    for polku in uudet_gpkg:
        for taso in pyogrio.list_layers(polku)[:, 0]:
            if taso == "layer_styles":
                continue
            uusi = gpd.read_file(polku, layer=taso)
            if not len(uusi):
                continue
            if taso in olemassa and kohde.exists():
                vanha = gpd.read_file(kohde, layer=taso)
                yhd = gpd.GeoDataFrame(pd.concat([vanha, uusi], ignore_index=True),
                                       crs=vanha.crs or uusi.crs)
            else:
                yhd = uusi
            yhd.to_file(kohde, layer=taso, driver="GPKG")
            print(f"  {taso}: +{len(uusi)} -> {len(yhd)}")


# --- VALIMUISTIN MITATOINTI -------------------------------------------------
#
# Uusien tiilien lisaaminen muuttaa merimosaiikin ORIGON JA MUODON
# (pipeline._sea_mosaic_geometry katsoo kaikkien tiilien rajoja). Silloin
# jokainen mosaiikkiin sidottu valimuisti vanhenee HILJAA: vanhat
# soluindeksit osoittaisivat vaaraan paikkaan, eika mikaan kaadu.
#
# Per-tiili _raw.npz ja _lidar.npz SAILYVAT: ne katsovat vain omaa tiiltaan
# (score_engine.compute lukee yhden DEM:n) eivatka riipu mosaiikista. Ne ovat
# laskennan kallein osa, joten nykyisten tiilien tyo ei mene hukkaan.
POISTETTAVAT_TIEDOSTOT = (
    "_sea_mosaic.npz", "_height_mosaic.npy", "_fetch_global.npz",
    "_water_global.npz", "_global_tiebreak_sorted.npy",
    "_factor_thresholds.json", "_prime_thresholds.json",
    "_shelter_thresholds.json", "_shoreline_stats.json",
    "_vaylat.json", "_suojelualueet.json", "_palvelut.json",
)
POISTETTAVAT_KUVIOT = (
    "_global_threshold_p*.json",
    "*_fetch?.png", "*_fetchobs?.png", "*_water?.png", "*_waterobs?.png",
    # Kvantisoidut tasot per tiili: johdettu suoraan _fetch_global /
    # _water_global -tiedostoista, joten ne vanhenevat niiden mukana.
    # Nama JAIVAT ensin listalta pois koska kuviot osuivat vain PNG-nimiin -
    # juuri sellainen hiljainen vanhentuminen jota tama listaus torjuu.
    "*_fetch.npz", "*_water.npz",
    # Nama kuvat on laskettu GLOBAALIA jakaumaa vasten, joka muuttuu kun
    # tiilia lisataan:
    #   top*  - compute_global_threshold ("parhaat X %" kynnys)
    #   factors/tiebreak - _global_tiebreak_sorted (tasapelin ratkaisun rank)
    "*_top*.png", "*_factors.png", "*_tiebreak.png",
)

# EI poisteta, koska nama eivat riipu muiden tiilien olemassaolosta:
#   *_base*.png       pelkka karttakuvan leikkaus
#   *_t{N}.png ym.    varikerros lasketaan vain oman tiilen raw-pisteista
#                     (get_or_compute_overlay ei kayta yhtaan globaalia arvoa)
#   *_prime.png       kanavat ovat tiilikohtaisia; kynnykset ovat erikseen
#                     prime_thresholds.json:ssa, joka poistetaan yllä


def mitatoi(kuiva=False):
    """Poistaa mosaiikkiin sidotut valimuistit. Palauttaa poistettujen maaran."""
    poistetut = []
    for nimi in POISTETTAVAT_TIEDOSTOT:
        p = CACHE_DIR / nimi
        if p.exists():
            poistetut.append(p)
    for kuvio in POISTETTAVAT_KUVIOT:
        poistetut += sorted(CACHE_DIR.glob(kuvio))
    for p in poistetut:
        print(f"  poistetaan {p.name}")
        if not kuiva:
            p.unlink()
    sailyy = len(list(CACHE_DIR.glob("*_raw.npz"))) + len(list(CACHE_DIR.glob("*_lidar.npz")))
    print(f"{'(kuiva) ' if kuiva else ''}poistettu {len(poistetut)}, "
          f"sailytetty {sailyy} per-tiili valimuistia")
    return len(poistetut)


# --- KARTTATUOTTEEN TODENTAMINEN -------------------------------------------


def todenna_kartta(lehti=None, key=None):
    """Lataa yhden lehden jonka meilla jo on ja vertaa vesimaskit.

    Tama on ainoa tapa varmistaa etta rajapinnan tuote on sama kuin kasin
    ladattu - varivirhe ei kaataisi mitaan vaan vaaristaisi kaiken."""
    import numpy as np
    import rasterio
    from . import raster_filters

    vanhat = sorted(MAP_DIR.glob("*.png"))
    if not vanhat:
        raise RuntimeError("Ei vertailulehtea karttakuva-mll/-kansiossa")
    lehti = lehti or vanhat[0].stem
    vanha = MAP_DIR / f"{lehti}.png"
    if not vanha.exists():
        raise RuntimeError(f"Vertailulehtea {lehti} ei ole levylla")

    tilapaa = ROOT / "output" / "_kartta_todennus"
    if tilapaa.exists():
        shutil.rmtree(tilapaa)
    key = key or mml.api_key()
    print(f"Ladataan {lehti} ({MAP_DATASET}) vertailua varten...")
    tulokset = mml.run_job(MAP_PROCESS, {
        "mapSheetInput": [lehti],
        "fileFormatInput": "PNG",
        "dataSetInput": MAP_DATASET,
    }, key=key)
    ladatut = [mml.download_to(item, tilapaa, key) for item in tulokset]
    uusi = next((p for p in ladatut if p.suffix.lower() == ".png"), None)
    if uusi is None:
        raise RuntimeError(f"Rajapinta ei palauttanut PNG:ta: {[p.name for p in ladatut]}")

    with rasterio.open(vanha) as a, rasterio.open(uusi) as b:
        print(f"  vanha {a.width}x{a.height} bounds {tuple(round(v) for v in a.bounds)}")
        print(f"  uusi  {b.width}x{b.height} bounds {tuple(round(v) for v in b.bounds)}")
        sama_geom = (a.width, a.height) == (b.width, b.height) and \
            all(abs(x - y) < 1.0 for x, y in zip(a.bounds, b.bounds))

    # Vesimaski koko lehdelta: tama on se suure jota koodi oikeasti kayttaa.
    tulos = {"lehti": lehti, "sama_geometria": sama_geom}
    maskit = []
    for polku in (vanha, uusi):
        bgr, _tr, _b = raster_filters.load_map_raster(str(polku))
        maskit.append(raster_filters.detect_water_fill_mask(bgr))
    ero = int(np.count_nonzero(maskit[0] != maskit[1]))
    tulos["vesipikseleita_vanha"] = int(maskit[0].sum())
    tulos["vesipikseleita_uusi"] = int(maskit[1].sum())
    tulos["eroavia_pikseleita"] = ero
    tulos["ero_osuus"] = ero / maskit[0].size
    tulos["kelpaa"] = sama_geom and tulos["ero_osuus"] < 1e-4
    print(json.dumps(tulos, indent=2, ensure_ascii=False))
    shutil.rmtree(tilapaa, ignore_errors=True)
    return tulos


def main():
    ap = argparse.ArgumentParser(description="MML-aineiston lataus")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("MINX", "MINY", "MAXX", "MAXY"),
                    help="Ladattava alue EPSG:3067-metreina")
    ap.add_argument("--rannikko", action="store_true",
                    help="Koko Suomen rannikko itarajalta Torniojoelle kaytavana")
    ap.add_argument("--leveys", type=float, default=rannikko.KAYTAVA_LEVEYS_M,
                    help="Kaytavan leveys metreina (oletus 20000)")
    ap.add_argument("--osa", metavar="I/N",
                    help="Aja vain osa I kokonaisuudesta N, esim. 3/8")
    ap.add_argument("--todenna-kartta", metavar="LEHTI", nargs="?", const="",
                    help="Vertaa rajapinnan karttalehti olemassa olevaan")
    ap.add_argument("--mitatoi", action="store_true",
                    help="Poista mosaiikkiin sidotut valimuistit")
    ap.add_argument("--kuiva", action="store_true", help="Nayta mita tehtaisiin")
    ap.add_argument("--ei-rakennuksia", action="store_true")
    args = ap.parse_args()

    if args.todenna_kartta is not None:
        tulos = todenna_kartta(args.todenna_kartta or None)
        return 0 if tulos["kelpaa"] else 1
    if args.mitatoi:
        mitatoi(kuiva=args.kuiva)
        return 0
    if not args.bbox and not args.rannikko:
        ap.error("anna --bbox, --rannikko, --todenna-kartta tai --mitatoi")

    if args.rannikko:
        # KAYTAVA EIKA SUORAKAIDE. Rannikon ympari piirretty suorakaide on
        # 230 782 km2 ja vaatisi 6 496 lehtea (147 Gt); kaytava vaatii 867
        # lehtea (19,7 Gt). Suorakaide ei myoskaan kelpaisi vektorihakuun,
        # koska se ylittaa rajapinnan 17 334 km2 katon 14-kertaisesti.
        osa = None
        if args.osa:
            i, n = (int(v) for v in args.osa.split("/"))
            if not 1 <= i <= n:
                ap.error(f"--osa {args.osa}: I:n oltava valilla 1..N")
            osa = (i, n)
        dem, kartta = rannikko.lehdet(args.leveys, osa)
        vektoribboxit = rannikko.vektoripalat(args.leveys, osa=osa)
        print(f"RANNIKKO, kaytavan leveys {args.leveys/1000:.0f} km"
              + (f", osa {args.osa}" if args.osa else ""))
    else:
        bbox = tuple(args.bbox)
        dem = karttalehti.sheets_for_bbox(bbox, "dem")
        kartta = karttalehti.sheets_for_bbox(bbox, "kartta")
        vektoribboxit = [bbox]
        print(f"Alue {bbox}")

    print(f"  {len(dem)} DEM-lehtea, {len(kartta)} karttalehtea, "
          f"{len(vektoribboxit)} vektoripalaa")
    if args.kuiva:
        print("  DEM:   ", " ".join(dem[:20]), "..." if len(dem) > 20 else "")
        print("  kartta:", " ".join(kartta[:20]), "..." if len(kartta) > 20 else "")
        ala = sum((b[2]-b[0])*(b[3]-b[1]) for b in vektoribboxit) / 1e6
        print(f"  vektorihaut yhteensa {ala:.0f} km2")
        return 0

    key = mml.api_key()
    lataa_kartta(kartta, key=key)
    lataa_korkeusmalli(dem, key=key)
    for i, bb in enumerate(vektoribboxit, 1):
        print(f"vektoripala {i}/{len(vektoribboxit)}")
        uudet_h = lataa_hydrografia(bb, key=key, dest=ROOT / "vesistot-mml" / f"_pala{i}")
        yhdista_hydrografia([p for p in uudet_h if p.suffix.lower() == ".gpkg"])
        if not args.ei_rakennuksia:
            uudet = lataa_rakennukset(bb, key=key, dest=BUILDINGS_DIR / f"_pala{i}")
            yhdista_rakennukset([p for p in uudet if p.suffix.lower() == ".gpkg"])
    print("Lataus valmis. Aja seuraavaksi:  python3 -m backend.mml_lataus --mitatoi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
