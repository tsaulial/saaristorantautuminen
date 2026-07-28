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
from scipy.ndimage import distance_transform_edt, label as ndimage_label, minimum_filter

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

# Peruskartalla puro/oja piirretaan rantaviiva-varisena viivana MUTTA ILMAN
# vesialueen tayttoa (liian kapea nakyakseen tallä mittakaavalla) - siksi
# rantaviiva-vari yksinaan ei erota merenrantaa sisamaan puron rannasta
# (havaittu ongelma: sovellus ehdotti rantautumista purojen varsilta).
# Erottelu tehdaan vesialueen tayton (WATER_FILL_HSV_*) yhtenaisten alueiden
# koon perusteella: meri on aina valtava yhtenainen alue (havaittu
# aineistossa >2000 ha per 6x6km tiili), puro/lampi/pieni jarvi pieni (alle
# ~10 ha). SEA_MIN_AREA_M2 on asetettu selvalla marginaalilla naiden valiin.
#
# Ennen ryhmittelya tayttomaskille tehdaan morphological closing
# (SEA_CLOSING_RADIUS_M): tiet, laivavaylaviivat yms. kartan symbolit voivat
# katkaista tayton varin kapeista salmista/lahdista, jolloin aidosti
# merellinen alue pilkkoutuisi virheellisesti moneksi pieneksi
# komponentiksi (havaittu esimerkki: tie katkaisi 18 ha:n suojaisan lahden
# yhteyden avomereen). 10m sulkee nama katkokset mutta ei yhdista aidosti
# erillisia sisamaan lampia/jarvia mereen.
SEA_CLOSING_RADIUS_M = 10.0
SEA_MIN_AREA_M2 = 500_000.0  # 50 ha
SEA_ADJACENCY_M = 5.0  # kuinka lahella meripintaa rantaviivapikselin pitaa olla sailyakseen

# Puskurivyohyke on todellisuudessa vain muutaman metrin levyinen eika erotu
# ulompana zoomitasolla. Paksunnetaan sita PELKASTAAN nakymista varten
# (dilataatio). Sama pikselisade kaytetaan JOKAISELLA resoluutiotasolla (ks.
# LEVEL_FACTORS) - ei metrisade - jotta viiva nayttaa yhta paksulta ruudulla
# riippumatta zoomaustasosta. Karkeammalla tasolla downsampletusta ohuesta
# maskista tulisi muuten resize-pehmennyksessa nakymatta.
#
# Paksuus on kayttajan saadettavissa (liukusaadin, ks. frontend/index.html)
# muutaman kiintean esiasetuksen valilla - EI portaattomasti selaimessa
# (esim. SVG feMorphology-suodattimella): kokeiltiin, mutta se osoittautui
# liian raskaaksi (Chromiumin feMorphology on hidas suurilla sateilla ja
# aiheutti pätkimista pan/zoomin aikana). Jokainen esiasetus lasketaan siis
# kertaalleen build-vaiheessa omaksi kuvakseen (ks. get_or_compute_overlay/
# get_or_compute_top: thickness_px sisaltyy tiedostonimeen), joten
# ajonaikaista kulua ei ole - vaihto on pelkka toisen valmiin kuvan pyynto.
THICKNESS_PRESETS = [1, 5, 10, 15, 20]
DEFAULT_THICKNESS_PX = 10

# "Parhaat rantautumispaikat" -kynnys on kayttajan saadettavissa (liukusaadin,
# ks. frontend/index.html) valilta 1-10 (%) - eli kuinka suuri osuus koko
# aineiston (kaikkien tiilien) puskurivyohykkeen pisteista korostetaan.
# Pieni prosentti (esim. 1%) naytaa vain kapean kaistaleen parasta rantaa;
# suurempi (esim. 10%) laajemman joukon. Persentiili numpy.percentile:lle on
# aina 100-top_percent (top_percent=7 -> persentiili 93). Jokainen esiasetus
# lasketaan kertaalleen build-vaiheessa omaksi kuvakseen (ks. get_or_compute_top
# ja THICKNESS_PRESETS-kommentti - sama "ei ajonaikaista laskentaa" -periaate).
TOP_PERCENT_PRESETS = list(range(1, 11))  # 1, 2, ..., 10
DEFAULT_TOP_PERCENT = 7

# Jyrkkyys ($S_{slope}$) vaihtelee hyvin paikallisesti, joten huonot pisteet
# (keltainen/punainen) nakyvat pienina, hajanaisina taplina. Korostetaan
# niita PELKASTAAN renderoinnissa: kunkin pikselin variksi otetaan
# lahiymparistonsa (sade pikseleina, sama joka tasolla ks. yllä) PIENIN
# pistemaara (minimum_filter), jolloin huono kohta "leviaa" nakyvaan kuvaan
# laajemmalle. Tilastot, suo-rangaistus ja top-persentiilin kynnysarvo
# perustuvat yha tarkkaan, suodattamattomaan pisteeseen.
LOW_SCORE_EMPHASIS_PX = 6

# Moniresoluutioiset "tasot" ulompia zoomauksia varten: jokainen taso on
# kokonaislukukerroin peruskartan natiivista 1m/px-ruudukosta, jolloin
# downsamplaus on halpa lohkokeskiarvo (cv2.INTER_AREA) ilman geodeettista
# resamplausta. "detail" (kerroin 1) on peruskartan natiivi resoluutio ja
# sailyttaa aiemman tiedostonimikaytannon (ei suffiksia); "near"/"mid"/
# "overview" ovat kevyempia yleisnakymia joita frontend nayttaa ulompana
# zoomattuna nopean alkulatauksen vuoksi.
LEVEL_FACTORS = {"detail": 1, "near": 2, "mid": 4, "overview": 16}
LEVEL_SUFFIXES = {"detail": "", "near": "_near", "mid": "_mid", "overview": "_overview"}


def parse_tile_key(tile_key):
    """Purkaa API-polusta tulevan '{tile_id}[_level][_t{thickness}][_p{percent}]'
    -avaimen (tile_id, level, thickness_px, top_percent) -nelikoksi. Kaikki
    paate-osat ovat valinnaisia (oletus 'detail' / DEFAULT_THICKNESS_PX /
    DEFAULT_TOP_PERCENT) - kayttokelpoinen seka tiili- etta 'variant'-
    avaimille (ks. api.py: get_overlay_top_png). '_p{percent}' purkaan
    ENSIN, koska se on aina viimeisena (vain top-avaimissa, ks.
    get_or_compute_top)."""
    top_percent = DEFAULT_TOP_PERCENT
    for p in TOP_PERCENT_PRESETS:
        suffix = f"_p{p}"
        if tile_key.endswith(suffix):
            tile_key = tile_key[: -len(suffix)]
            top_percent = p
            break

    thickness_px = DEFAULT_THICKNESS_PX
    for t in THICKNESS_PRESETS:
        suffix = f"_t{t}"
        if tile_key.endswith(suffix):
            tile_key = tile_key[: -len(suffix)]
            thickness_px = t
            break

    for level, suffix in LEVEL_SUFFIXES.items():
        if suffix and tile_key.endswith(suffix):
            return tile_key[: -len(suffix)], level, thickness_px, top_percent
    return tile_key, "detail", thickness_px, top_percent


def downsample_image(img, factor):
    """Pienentaa kuvan/maskin 'factor'-kertaa per akseli aluekeskiarvolla
    (cv2.INTER_AREA) - sopii seka jatkuville kuville etta 0/1-maskeille
    (jalkimmaiselle kynnystetaan > 0, ks. downsample_mask)."""
    if factor <= 1:
        return img
    h, w = img.shape[:2]
    new_w, new_h = max(1, w // factor), max(1, h // factor)
    return cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)


def downsample_mask(mask, factor):
    """Kuten downsample_image, mutta True jos kohdepikselin alueella oli
    YHTAAN True-pikselia lahteessa - ohut maski ei katoa kokonaan."""
    if factor <= 1:
        return mask
    small = downsample_image(mask.astype(np.float32), factor)
    return small > 0.0

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


def compute_sea_mask(water_fill_mask, pixel_size):
    """Erottaa meren (yhtenainen, suuri vesialue) sisamaan puroista/lammista/
    pienista jarvista (pienia, erillisia vesialueita) - ks. SEA_* -vakioiden
    kommentit. Palauttaa maskin niista vesitaytto-pikseleista jotka kuuluvat
    riittavan suureen yhtenaiseen alueeseen."""
    close_radius_px = meters_to_px(SEA_CLOSING_RADIUS_M, pixel_size)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * close_radius_px + 1, 2 * close_radius_px + 1))
    closed = cv2.morphologyEx(water_fill_mask.astype(np.uint8), cv2.MORPH_CLOSE, kernel).astype(bool)

    labels, n = ndimage_label(closed, structure=np.ones((3, 3), dtype=bool))
    if n == 0:
        return np.zeros_like(water_fill_mask)

    sizes = np.bincount(labels.ravel())
    min_area_px = SEA_MIN_AREA_M2 / (pixel_size ** 2)
    sea_labels = np.flatnonzero(sizes >= min_area_px)
    sea_labels = sea_labels[sea_labels != 0]
    return np.isin(labels, sea_labels)


# "Paras rannat %" -valinta (ks. TOP_PERCENT_PRESETS) osoittautui olevan
# rikki pienilla prosenteilla: total_score SATUROITUU tarkalleen arvoon 1.0
# heti kun jyrkkyys<=5 astetta JA etaisyys rakennuksiin>150m JA kallio -
# tama toteutuu n. 8 %:lla KAIKISTA puskurivyohykkeen pikseleista (mitattu
# koko aineistosta), jolloin persentiilit 93-99 (top 1-7 %) tuottavat KAIKKI
# saman kynnysarvon 1.0 ja siis TASAN saman korostetun alueen - "top 1 %"
# ei siis ollut sen suppeampi kuin "top 7 %". Korjattu lisaamalla erittain
# pieni, JATKUVA "tasapelin purku" (compute_tiebreak: suosii tasaisempaa
# maastoa ja kauempana rakennuksista olevia pikseleita MYOS 1.0-tason
# ylapuolella/sisalla) rank_score-nimiseen apusuureeseen, jota kaytetaan
# VAIN persentiilikynnyksen laskennassa ja "top X %" -maskin valinnassa -
# nakyva pistemaara/varikoodaus (score) pysyy talta osin muuttumattomana.
# Epsilon on niin pieni ettei se voi koskaan kaantaa kahden aidosti eri
# total_score-arvon jarjestysta (0.5/0.35/0.15-painotteiset erot ovat aina
# suurempia kuin epsilon*[0,1]), se vain jarjestaa tasapelit.
TIEBREAK_EPSILON = 1e-4


def compute_tiebreak(slope_deg, dist_m):
    """Jatkuva, rajoittamaton apusuure edella kuvattujen tasapelien
    purkamiseen - normalisoitu karkeasti [0,1]-valille (ei tarvitse olla
    tarkka, vain jarjestysta varten). Painotettu samoilla suhteellisilla
    painoilla kuin varsinainen pistemaara (SLOPE_WEIGHT/DIST_WEIGHT) -
    kallio ei mukana, koska se on jo binaarinen eika tarvitse tarkennusta."""
    slope_component = np.clip(1.0 - slope_deg / 90.0, 0.0, 1.0)
    dist_component = np.clip(dist_m / 1000.0, 0.0, 1.0)
    weight_sum = score_engine.SLOPE_WEIGHT + score_engine.DIST_WEIGHT
    return (score_engine.SLOPE_WEIGHT * slope_component + score_engine.DIST_WEIGHT * dist_component) / weight_sum


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
    shoreline_mask_raw = raster_filters.detect_shoreline_mask(map_bgr)
    swamp_mask = raster_filters.detect_swamp_mask(map_bgr)
    water_fill_mask = raster_filters.detect_water_fill_mask(map_bgr)

    # Rantaviiva-vari piirretaan seka merenrannalle etta sisamaan puroille -
    # rajataan huomioon vain se osa joka on lahella oikeaa, riittavan suurta
    # vesialuetta (= merta), ks. SEA_*-vakioiden kommentit.
    sea_mask = compute_sea_mask(water_fill_mask, pixel_size)
    sea_mask_near = dilate_mask(sea_mask, meters_to_px(SEA_ADJACENCY_M, pixel_size))
    shoreline_mask = shoreline_mask_raw & sea_mask_near

    slope_score = resample_to_grid(v1["slope_score"], v1["transform"], map_transform, map_shape)
    dist_score = resample_to_grid(v1["dist_score"], v1["transform"], map_transform, map_shape)
    dem = resample_to_grid(v1["dem"], v1["transform"], map_transform, map_shape)
    slope_deg = resample_to_grid(v1["slope_deg"], v1["transform"], map_transform, map_shape)
    dist_m = resample_to_grid(v1["dist_m"], v1["transform"], map_transform, map_shape)

    rock_score = np.where(rock_mask, ROCK_SCORE_YES, ROCK_SCORE_NO)
    total_score = slope_score * score_engine.SLOPE_WEIGHT
    total_score = total_score + dist_score * score_engine.DIST_WEIGHT
    total_score = total_score + rock_score * ROCK_WEIGHT
    total_score = np.where(swamp_mask, total_score * SWAMP_PENALTY_FACTOR, total_score)

    rank_score = total_score + TIEBREAK_EPSILON * compute_tiebreak(slope_deg, dist_m)

    buffer_mask = compute_shoreline_buffer(shoreline_mask, dem, pixel_size)

    return {
        "score": total_score,
        "rank_score": rank_score,
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

    if not force and npz_path.exists() and "rank_score" in (data := np.load(npz_path)).files:
        return {
            "score": data["score"],
            "rank_score": data["rank_score"],
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
        rank_score=result["rank_score"].astype(np.float32),
        buffer_mask=result["buffer_mask"],
        map_transform=np.array(result["map_transform"])[:6],
        n_buildings=result["n_buildings"],
        rock_pct=result["rock_pct"],
        swamp_pct=result["swamp_pct"],
        shoreline_px=result["shoreline_px"],
        buffer_px=result["buffer_px"],
    )
    return result


def top_percent_to_percentile(top_percent):
    """'Paras rannan %' (ks. TOP_PERCENT_PRESETS) -> numpy.percentile-parametri
    (esim. top_percent=7 -> persentiili 93)."""
    return 100 - top_percent


def compute_global_threshold(buildings_path, percentile, force=False):
    """Laskee rank_score:n (ks. TIEBREAK_EPSILON-kommentti - EI nakyvaa
    score:a, koska se saturoituu 1.0:aan liian monella pikselilla) percentile:n
    kaikkien tiilien puskurivyohykkeen pikseleiden yli. Valimuistetaan
    levylle PER PERSENTIILI (yksi esiasetus = yksi kynnysarvo), koska vaatii
    kaikkien tiilien raa'an laskennan (~2s/tiili, kertaalleen - tuloksena
    ei-persentiilikohtainen get_or_compute_raw on jo omalla valimuistillaan)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    threshold_path = CACHE_DIR / f"_global_threshold_p{percentile}.json"

    if not force and threshold_path.exists():
        return json.loads(threshold_path.read_text())["threshold"]

    registry = tiles.get_registry()
    all_scores = []
    for tid in registry:
        raw = get_or_compute_raw(tid, buildings_path, force=force)
        buf = raw["buffer_mask"]
        if buf.any():
            all_scores.append(raw["rank_score"][buf])

    threshold = float(np.percentile(np.concatenate(all_scores), percentile)) if all_scores else 1.0

    threshold_path.write_text(
        json.dumps({"percentile": percentile, "threshold": threshold, "n_tiles": len(registry)}, indent=2)
    )
    return threshold


def get_or_compute_overlay(tile_id, buildings_path, level="detail", thickness_px=DEFAULT_THICKNESS_PX, force=False):
    """Palauttaa (png_bytes, meta_dict) pisteytysoverlaylle halutulla
    resoluutiotasolla (ks. LEVEL_FACTORS) ja rantaviivan paksuudella (ks.
    THICKNESS_PRESETS). "detail" ei resamplaa mitaan - pistemaara on jo
    laskettu peruskartan omalle ruudukolle (compute_tile). "mid"/"overview"
    downsamplaavat raa'an pistemaaran/puskurimaskin ja paksuntavat sen
    SAMALLA pikselisateella kuin detail, jotta viiva pysyy nakyvana. Kayttaa
    levyvalimuistia (yksi PNG per level+thickness-yhdistelma)."""
    suffix = LEVEL_SUFFIXES[level]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}{suffix}_t{thickness_px}.png"
    meta_path = CACHE_DIR / f"{tile_id}.json"

    if not force and png_path.exists() and meta_path.exists():
        return png_path.read_bytes(), json.loads(meta_path.read_text())

    registry = tiles.get_registry()
    if tile_id not in registry:
        raise KeyError(f"Tuntematon tile_id: {tile_id}")

    raw = get_or_compute_raw(tile_id, buildings_path, force=force)
    factor = LEVEL_FACTORS[level]
    score = downsample_image(raw["score"].astype(np.float32), factor)
    buffer_mask = downsample_mask(raw["buffer_mask"], factor)

    visible_mask = dilate_mask(buffer_mask, thickness_px)
    visual_score = emphasize_low_scores(score, LOW_SCORE_EMPHASIS_PX)
    rgba = score_to_rgba(visual_score, visible_mask)

    ok, encoded = cv2.imencode(".png", rgba)
    if not ok:
        raise RuntimeError("PNG-enkoodaus epaonnistui")
    png_bytes = encoded.tobytes()

    bounds_3067 = array_bounds(*raw["score"].shape, raw["map_transform"])
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


def get_or_compute_top(
    tile_id,
    buildings_path,
    level="detail",
    thickness_px=DEFAULT_THICKNESS_PX,
    top_percent=DEFAULT_TOP_PERCENT,
    force=False,
):
    """Palauttaa PNG-tavuina erillisen kerroksen, joka nayttaa VAIN parhaat
    top_percent % (ks. TOP_PERCENT_PRESETS) puskurivyohykkeen pisteista,
    halutulla resoluutiotasolla ja rantaviivan paksuudella (ks.
    get_or_compute_overlay)."""
    suffix = LEVEL_SUFFIXES[level]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}_top{suffix}_t{thickness_px}_p{top_percent}.png"

    if not force and png_path.exists():
        return png_path.read_bytes()

    registry = tiles.get_registry()
    if tile_id not in registry:
        raise KeyError(f"Tuntematon tile_id: {tile_id}")

    raw = get_or_compute_raw(tile_id, buildings_path, force=force)
    threshold = compute_global_threshold(buildings_path, top_percent_to_percentile(top_percent), force=force)
    top_mask = raw["buffer_mask"] & (raw["rank_score"] >= threshold)

    factor = LEVEL_FACTORS[level]
    top_mask = downsample_mask(top_mask, factor)
    top_mask_visible = dilate_mask(top_mask, thickness_px)

    bgra = np.zeros((*top_mask_visible.shape, 4), dtype=np.uint8)
    bgra[top_mask_visible, 0:3] = TOP_HIGHLIGHT_BGR
    bgra[top_mask_visible, 3] = TOP_HIGHLIGHT_ALPHA

    ok, encoded = cv2.imencode(".png", bgra)
    if not ok:
        raise RuntimeError("PNG-enkoodaus epaonnistui")
    png_bytes = encoded.tobytes()

    png_path.write_bytes(png_bytes)
    return png_bytes


def get_or_compute_basemap(tile_id, level="detail", force=False):
    """Palauttaa taustakartaksi tarkoitetun karttakuva-leikkauksen PNG-tavuina
    halutulla resoluutiotasolla, levyvalimuistilla. "detail" on
    MUUTTUMATON (ei resamplausta/reprojisointia); "mid"/"overview" ovat
    kevyita downsamplattuja yleisnakymia nopeaa alkulatausta varten."""
    suffix = LEVEL_SUFFIXES[level]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}_base{suffix}.png"

    if not force and png_path.exists():
        return png_path.read_bytes()

    registry = tiles.get_registry()
    if tile_id not in registry:
        raise KeyError(f"Tuntematon tile_id: {tile_id}")
    tile = registry[tile_id]

    map_bgr, _map_transform = raster_filters.load_map_window(str(tile.map_path), tile.bounds)
    map_bgr = downsample_image(map_bgr, LEVEL_FACTORS[level])

    ok, encoded = cv2.imencode(".png", map_bgr)
    if not ok:
        raise RuntimeError("PNG-enkoodaus epaonnistui")
    png_bytes = encoded.tobytes()

    png_path.write_bytes(png_bytes)
    return png_bytes


def get_tile_bounds(tile):
    transform, shape = get_map_grid(tile)
    return bounds_tuple_to_dict(array_bounds(*shape, transform))
