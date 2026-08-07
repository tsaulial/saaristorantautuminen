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


def _aja_era(process, inputs_fn, era, kansio, key, mita):
    """Ajaa yhden eran ja OHITTAA lehdet joita aineistossa ei ole.

    MML hylkaa koko eran jos yksikaan lehti puuttuu, ja nimeaa puuttuvat
    virheviestissa. Puuttuvia on aidosti: 1:10 000 karttaa ei ole pelkkaa
    avomerta olevalle ruudulle, ja 40 km kaytava Saaristomerella osuu
    sellaisiin. Ilman tata 149 lehden haku kaatui kolmen takia.

    Puuttuva lehti EI OLE VIRHE vaan tieto: siella ei ole karttaa. Tiili
    jaa silloin pois rekisterista (tiles.build_registry vaatii seka DEM:n
    etta kartan), mika on oikein - avomerella ei ole rantaviivaa."""
    ohitetut = []
    while era:
        try:
            return mml.run_job(process, inputs_fn(era), key=key), ohitetut
        except mml.LataustyoVirhe as e:
            puuttuvat = [x for x in e.puuttuvat_lehdet() if x in era]
            if not puuttuvat:
                raise
            ohitetut += puuttuvat
            era = [x for x in era if x not in puuttuvat]
            print(f"    {mita}: {len(puuttuvat)} lehtea ei ole aineistossa "
                  f"({', '.join(puuttuvat[:6])}"
                  f"{'...' if len(puuttuvat) > 6 else ''}), ohitetaan",
                  flush=True)
    return [], ohitetut


def lataa_korkeusmalli(lehdet, key=None):
    """DEM 2 m -lehdet TIFF-muodossa. Palauttaa ladatut polut."""
    key = key or mml.api_key()
    puuttuu = _puuttuvat(lehdet, DEM_DIR, ".tif")
    print(f"korkeusmalli: {len(lehdet)} lehtea, joista puuttuu {len(puuttuu)}")
    ulos = []
    for i, era in enumerate(_erat(puuttuu), 1):
        print(f"  era {i}: {len(era)} lehtea...")
        tulokset, _ohi = _aja_era(
            DEM_PROCESS,
            lambda e: {"mapSheetInput": e, "fileFormatInput": "TIFF"},
            era, DEM_DIR, key, "korkeusmalli")
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
        tulokset, _ohi = _aja_era(
            MAP_PROCESS,
            lambda e: {"mapSheetInput": e, "fileFormatInput": "PNG",
                       "dataSetInput": MAP_DATASET},
            era, MAP_DIR, key, "kartta")
        for item in tulokset:
            ulos.append(mml.download_to(item, MAP_DIR, key))
    return ulos


# --- TAUSTAKARTTA: PIENTEN MITTAKAAVOJEN OMA TUOTE ---
#
# Uloimmat zoomit piirrettiin 1:10 000 kartasta pienentamalla, mika on kaksi
# kertaa vaarin.
#
# KARTOGRAFISESTI: 1:10k on suunniteltu luettavaksi metrien tarkkuudella.
# 16-kertaisesti pienennettyna sen viivat, tekstit ja symbolit muuttuvat
# kohinaksi - kartta nayttaa sotkuiselta juuri silloin kun kayttaja haluaa
# yleiskuvan.
#
# KOOLTAAN: painettu kartta on 214 VARIA, ja siihen perustuu haviottoman
# pakkauksen teho (0,33 bittia/px). Alinaytteistys keskiarvoistaa
# naapurivareja ja tuottaa niita kymmenia tuhansia - mitattuna 20 307 varia
# ja 1,05 bittia/px (2 m/px) seka 53 110 varia ja 2,49 bittia/px (4 m/px).
# Siksi NELJA KERTAA harvempi ruudukko maksaa vain 23 % vahemman tavuja, ja
# peruskartta on 73 % koko docs/-hakemistosta.
#
# Taustakartta on MML:n oma yleistys pieniin mittakaavoihin, ja kun se
# haetaan NATIIVIRESOLUUTIOSSA, alinaytteistysta ei tehda missaan ja
# palettirakenne sailyy.
#
# Resoluutiosaanto m/px = mittakaava / 10 000 (esim. 1:10k -> 1 m/px) on
# TODENNETTAVA ennen joukkolatausta - ks. todenna_taustakartta().
TAUSTA_PROCESS = "taustakartta_rasteri_karttalehti"

# taso -> (aineisto, lehtijako, m/px)
#
# RESOLUUTIO ON MITATTU, EI PAATELTY. Ensin oletin saannon
# m/px = mittakaava / 10 000 (koska maastokartta_rasteri_10k on 1 m/px).
# Se on VAARIN taustakartalle: 1:160 000 osoittautui 32 m/px:ksi, eli
# jakaja on 5 000. Lehtijako sen sijaan osui metrilleen jo ensimmaisella
# yrityksella. Tama on tasan se syy miksi tuote todennetaan yhdella
# lehdella ennen joukkolatausta.
TAUSTAKARTAT = {
    "near":     ("taustakartta_rasteri_10k", "kartta", 2.0),
    "mid":      ("taustakartta_rasteri_20k", "tausta20k", 4.0),
    "overview": ("taustakartta_rasteri_80k", "tausta80k", 16.0),
}

TAUSTA_DIR = ROOT / "taustakartta-mml"


def tausta_kansio(taso):
    return TAUSTA_DIR / taso


def lataa_taustakartta(lehdet, taso, key=None):
    """Taustakarttarasterit yhdelle resoluutiotasolle."""
    aineisto, _jako, _mpp = TAUSTAKARTAT[taso]
    key = key or mml.api_key()
    kansio = tausta_kansio(taso)
    puuttuu = _puuttuvat(lehdet, kansio, ".png")
    print(f"taustakartta {taso} ({aineisto}): {len(lehdet)} lehtea, "
          f"joista puuttuu {len(puuttuu)}")
    ulos = []
    for i, era in enumerate(_erat(puuttuu), 1):
        print(f"  era {i}: {len(era)} lehtea...", flush=True)
        tulokset, _ohi = _aja_era(
            TAUSTA_PROCESS,
            lambda e: {"mapSheetInput": e, "fileFormatInput": "PNG",
                       "dataSetInput": aineisto},
            era, kansio, key, f"taustakartta {taso}")
        for item in tulokset:
            ulos.append(mml.download_to(item, kansio, key))
    return ulos


def todenna_taustakartta(taso="mid", lehti=None, key=None):
    """Lataa YHDEN lehden ja tarkistaa etta lehtijako ja resoluutio ovat
    sita mita oletamme.

    Sama kaytanto kuin todenna_kartta(): tuote todennetaan ennen kuin
    ladataan satoja megatavuja sen varassa. Aiemmin tama loysi 0 eroavaa
    pikselia 144 miljoonasta - halpa tarkistus, kallis virhe."""
    import rasterio
    from . import karttalehti

    aineisto, jako, mpp = TAUSTAKARTAT[taso]
    lehti = lehti or karttalehti.sheet_name(385000.0, 6670000.0, jako)
    odotettu = karttalehti.sheet_bounds(lehti)
    print(f"todennus: {taso} / {aineisto} / lehti {lehti}")
    print(f"  odotettu rajaus {tuple(round(v) for v in odotettu)}")
    print(f"  odotettu resoluutio {mpp} m/px")

    polut = lataa_taustakartta([lehti], taso, key=key)
    if not polut:
        polut = [p for p in tausta_kansio(taso).glob(f"{lehti}.*") if p.suffix == ".png"]
    if not polut:
        raise RuntimeError("lehtea ei saatu ladattua")
    p = polut[0]

    with rasterio.open(p) as ds:
        b = (ds.bounds.left, ds.bounds.bottom, ds.bounds.right, ds.bounds.top)
        todellinen_mpp = abs(ds.transform.a)
        muoto = (ds.width, ds.height)
    raja_ok = all(abs(a - c) < 1.0 for a, c in zip(odotettu, b))
    mpp_ok = abs(todellinen_mpp - mpp) < 0.01
    print(f"  todellinen rajaus {tuple(round(v) for v in b)}  "
          f"{'OK' if raja_ok else 'ERI'}")
    print(f"  todellinen resoluutio {todellinen_mpp} m/px, koko {muoto[0]}x{muoto[1]}  "
          f"{'OK' if mpp_ok else 'ERI'}")
    print(f"  tiedostokoko {p.stat().st_size / 1e6:.2f} MB")
    return raja_ok and mpp_ok


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
# Nimissa on LASKENTA_VERSIO (ks. pipeline.LASKENTA_VERSIO), joten kuviot
# paattyvat jokeriin - muuten mitatointi ei osuisi versionnoston jalkeen
# vanhoihin tiedostoihin, ja ne jaisivat levylle ikuisiksi ajoiksi.
POISTETTAVAT_TIEDOSTOT = (
    "_vaylat.json", "_suojelualueet.json", "_palvelut.json",
)
POISTETTAVAT_KUVIOT = (
    "_sea_mosaic_v*.npz", "_height_mosaic_v*.npy", "_fetch_global_v*.npz",
    "_water_global_v*.npz", "_global_tiebreak_sorted_v*.npy",
    "_factor_thresholds_v*.json", "_prime_thresholds_v*.json",
    "_shelter_thresholds_v*.json", "_shoreline_stats_v*.json",
    "_global_threshold_p*_v*.json",
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
    "*_top*.png", "*_factors*.png", "*_tiebreak*.png",
    # Vanhat versioimattomat nimet (ennen LASKENTA_VERSIOta) siivotaan pois.
    "_sea_mosaic.npz", "_height_mosaic.npy", "_fetch_global.npz",
    "_water_global.npz", "_global_tiebreak_sorted.npy",
    "_factor_thresholds.json", "_prime_thresholds.json",
    "_shelter_thresholds.json", "_shoreline_stats.json",
    "_global_threshold_p[0-9]*.json",
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
    ap.add_argument("--ahvenanmaa", action="store_true",
                    help="Lisaa Ahvenanmaan haara (Nauvo-Eckero) rannikkoon")
    ap.add_argument("--valilta", metavar="NIMI",
                    help="Rajaa rannikko reittipisteesta, esim. Helsinki")
    ap.add_argument("--asti", metavar="NIMI",
                    help="...tahan reittipisteeseen, esim. Nauvo")
    ap.add_argument("--todenna-kartta", metavar="LEHTI", nargs="?", const="",
                    help="Vertaa rajapinnan karttalehti olemassa olevaan")
    ap.add_argument("--taustakartta", action="store_true",
                    help="Lataa MYOS taustakartta karkeille zoom-tasoille")
    ap.add_argument("--vain-taustakartta", action="store_true",
                    help="Lataa VAIN taustakartta nykyiselle tiilistolle")
    ap.add_argument("--todenna-taustakartta", action="store_true",
                    help="Lataa yksi lehti per taso ja tarkista jako ja resoluutio")
    ap.add_argument("--mitatoi", action="store_true",
                    help="Poista mosaiikkiin sidotut valimuistit")
    ap.add_argument("--kuiva", action="store_true", help="Nayta mita tehtaisiin")
    ap.add_argument("--ei-rakennuksia", action="store_true")
    args = ap.parse_args()

    if args.todenna_kartta is not None:
        tulos = todenna_kartta(args.todenna_kartta or None)
        return 0 if tulos["kelpaa"] else 1
    if args.vain_taustakartta:
        # Lehdet johdetaan TIILISTOSTA eika bboxista tai kaytavasta: silloin
        # ladataan tasan se mita rakennetut tiilet tarvitsevat, eika mitaan
        # muuta aineistoa kosketa. --rannikko --taustakartta lataisi myos
        # hydrografian ja rakennukset uudelleen, mika veisi tunteja turhaan.
        from . import tiles
        key = mml.api_key()
        reg = tiles.get_registry()
        print(f"{len(reg)} tiilta rekisterissa")
        for taso, (aineisto, jako, mpp) in TAUSTAKARTAT.items():
            lehdet = set()
            for t in reg.values():
                lehdet |= set(karttalehti.sheets_for_bbox(t.bounds, jako))
            lataa_taustakartta(sorted(lehdet), taso, key=key)
        return 0
    if args.todenna_taustakartta:
        kaikki_ok = True
        for taso in TAUSTAKARTAT:
            kaikki_ok &= bool(todenna_taustakartta(taso))
            print()
        return 0 if kaikki_ok else 1
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
        vali = None
        if args.valilta or args.asti:
            if not (args.valilta and args.asti):
                ap.error("--valilta ja --asti annetaan yhdessa")
            vali = rannikko.valilinja(args.valilta, args.asti)
        dem, kartta = rannikko.lehdet(args.leveys, osa, args.ahvenanmaa, vali)
        vektoribboxit = rannikko.vektoripalat(args.leveys, osa=osa,
                                              ahvenanmaa=args.ahvenanmaa,
                                              linja=vali)
        print(f"RANNIKKO, kaytavan leveys {args.leveys/1000:.0f} km"
              + (f", {args.valilta}-{args.asti}" if vali else "")
              + (", Ahvenanmaan haara mukana" if args.ahvenanmaa else "")
              + (f", osa {args.osa}" if args.osa else ""))
    else:
        bbox = tuple(args.bbox)
        dem = karttalehti.sheets_for_bbox(bbox, "dem")
        kartta = karttalehti.sheets_for_bbox(bbox, "kartta")
        vektoribboxit = [bbox]
        print(f"Alue {bbox}")

    if args.taustakartta:
        # Taustakartan lehtijako on ERI kuin DEM:n ja peruskartan, ja eri
        # jokaisella mittakaavalla - siksi lehdet lasketaan tasoittain
        # samasta alueesta eika johdeta toisistaan.
        tausta = {}
        for taso, (_a, jako, _m) in TAUSTAKARTAT.items():
            lehdet = set()
            for b in vektoribboxit:
                lehdet |= set(karttalehti.sheets_for_bbox(b, jako))
            tausta[taso] = sorted(lehdet)
        print("  taustakartta: "
              + ", ".join(f"{t} {len(v)} lehtea" for t, v in tausta.items()))

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
    if args.taustakartta:
        for taso, lehdet in tausta.items():
            lataa_taustakartta(lehdet, taso, key=key)
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
