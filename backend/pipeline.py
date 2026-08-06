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

import hashlib
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np
from rasterio.crs import CRS
from rasterio.transform import Affine, array_bounds, from_origin
from rasterio.warp import Resampling as WarpResampling
from rasterio.warp import reproject
from scipy.ndimage import (binary_dilation, distance_transform_edt,
                           label as ndimage_label, minimum_filter)

from backend import lidar, raster_filters, score_engine, tiles, vesisto

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

# --- MERI JA RANTAVIIVA TULEVAT VEKTORIAINEISTOSTA ---
#
# Ne luettiin aiemmin peruskartan VAREISTA, ja se vaati kolme heuristiikkaa:
# 50 ha kokosuodatus (meri ja jarvi ovat samanvarisia), morfologinen
# sulkeminen (tiet ja vaylaviivat katkaisivat tayton) ja erillissaanto
# tiilirajoille. Kaikki kolme on nyt poistettu - ks. backend/vesisto.py.
#
# Ratkaiseva syy oli etta peruskartta on IHMISELLE PIIRRETTY KUVA: vesistojen
# nimet on painettu tasan samalla sinisella kuin rantaviiva, joten sanat
# "Purolahti" ja "Backviken" tulkittiin rantaviivaksi keskella lahtea. Viisi
# eri erottelijaa kokeiltiin, eika yksikaan erottanut niita luotettavasti.
#
# Maastotietokannassa meri on oma tasonsa. Todennettu vanhaa vasten:
# Ahvenanmaalla (L3123F) uusi rantaviiva on samassa paikassa kuin vanha
# (mediaanietaisyys 0,0 m), Helsingissa (L4133D) mediaani 2,8 m mutta 90 %
# piste 281 m - ero on tasan ne lahdet jotka rasteripolku hukkasi.

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
    # TYHJA RANTAVIIVA ON KASITELTAVA ERIKSEEN. distance_transform_edt mittaa
    # etaisyyden lahimpaan NOLLAAN; jos nollia ei ole yhtaan, scipy ei kaadu
    # vaan mittaa etaisyyden HAAMUPISTEESEEN rivilla -1. Tulokseksi tulee
    # taysin uskottavan nakoisia etaisyyksia (1, 2, 3, ... metria ylareunasta),
    # joista osa osuu 5-15 m ikkunaan - eli tiili jossa ei ole rantaviivaa
    # sai 160 puskuripikselia ylareunaansa. Mitattu nelja tallaista tiilta
    # (sisamaan ruudut Helsingin alueella).
    if not shoreline_mask.any():
        return np.zeros(shoreline_mask.shape, dtype=bool)
    non_shore = ~shoreline_mask
    dist_to_shore = distance_transform_edt(non_shore, sampling=(pixel_size, pixel_size))
    return land & (dist_to_shore >= SHORELINE_BUFFER_MIN_M) & (dist_to_shore <= SHORELINE_BUFFER_MAX_M)


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
    swamp_mask = raster_filters.detect_swamp_mask(map_bgr)

    # MERI JA RANTAVIIVA VEKTORIAINEISTOSTA, ei kartan vareista.
    # Peruskartan sininen ei erota rantaviivaa vesistojen NIMISTA eika merta
    # jarvista - ks. backend/vesisto.py. Kallio ja suo luetaan edelleen
    # rasterista, koska niille ei ole tassa vastaavaa ongelmaa.
    shoreline_mask = vesisto.rantaviiva_maski(tile.bounds, map_transform, map_shape)

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

    tiebreak = compute_tiebreak(slope_deg, dist_m)
    rank_score = total_score + TIEBREAK_EPSILON * tiebreak

    buffer_mask = compute_shoreline_buffer(shoreline_mask, dem, pixel_size)

    return {
        "score": total_score,
        "rank_score": rank_score,
        "buffer_mask": buffer_mask,
        # Osatekijat erikseen: KAYTTAJA voi valita mitka niista osallistuvat
        # pisteytykseen (ks. FACTOR_BITS ja get_or_compute_factor_png) - siksi
        # yhdistetty total_score ei yksin riita, vaan komponentit on
        # sailytettava jotta pistemaara voidaan koota uudelleen selaimessa.
        "slope_score": slope_score,
        "dist_score": dist_score,
        "rock_mask": rock_mask,
        "swamp_mask": swamp_mask,
        "tiebreak": tiebreak,
        # Rantaviiva ja maa/vesi-raja sailytetaan, jotta karkipaikkojen
        # laajempi arviointivyohyke (ks. PRIME_ZONE_MAX_M) ja poikki-
        # leikkausryhmittely voidaan laskea ilman koko tiilen uudelleen-
        # laskentaa. Molemmat ovat boolean-maskeja eli pakkautuvat hyvin -
        # DEM:ia kokonaisuudessaan EI tallenneta (144 MB/tiili).
        "shoreline_mask": shoreline_mask,
        "land_mask": dem > 0.0,
        "map_transform": map_transform,
        "n_buildings": v1["n_buildings"],
        "rock_pct": 100 * rock_mask.mean(),
        "swamp_pct": 100 * swamp_mask.mean(),
        "shoreline_px": int(shoreline_mask.sum()),
        "buffer_px": int(buffer_mask.sum()),
    }


# --- LAHTEEN SORMENJALKI ---
#
# Valimuisti tarkisti aiemmin VAIN etta tiedosto on olemassa. Jos MML julkaisi
# uuden korkeusmallilehden tai korjatun rantaviivan, putki kaytti vanhaa
# tulosta HILJAA - eika mikaan kaatunut. Ainoa keino oli mitatoida kaikki.
#
# Nyt jokaisen tiilen valimuistiin talletetaan sen LAHTEIDEN sormenjalki.
# Jos se eroaa, tiili lasketaan uudelleen automaattisesti.
#
# Sormenjalki on koko + muokkausaika, EI sisallon tiiviste: lahteet ovat
# satoja megatavuja ja tiivisteen laskenta per tiili maksaisi enemman kuin
# koko uudelleenlaskenta. Hinta on etta tiedoston kopiointi ilman
# aikaleiman sailytysta nayttaa muutokselta ja aiheuttaa turhan laskennan.
# Se on oikea suunta erehtya: turha tyo on halvempi kuin vaara tulos.
_SORMENJALKI_VALIMUISTI = {}


def _tiedoston_sormenjalki(polku):
    polku = str(polku)
    if polku not in _SORMENJALKI_VALIMUISTI:
        try:
            st = os.stat(polku)
            _SORMENJALKI_VALIMUISTI[polku] = f"{st.st_size}:{st.st_mtime_ns}"
        except OSError:
            _SORMENJALKI_VALIMUISTI[polku] = "puuttuu"
    return _SORMENJALKI_VALIMUISTI[polku]


def lahde_sormenjalki(tile, buildings_path):
    """Tiilen kaikkien lahteiden sormenjalki yhtena merkkijonona."""
    osat = [
        _tiedoston_sormenjalki(tile.dem_path),
        _tiedoston_sormenjalki(tile.map_path),
        _tiedoston_sormenjalki(buildings_path),
        _tiedoston_sormenjalki(vesisto.GPKG),
    ]
    return "|".join(osat)


# --- TIILISTOSTA RIIPPUVAT VALIMUISTIT ---
#
# Osa valimuisteista ei kuvaa YHTA tiilta vaan koko aineistoa: "parhaat X %"
# -kynnykset, tasapelin globaali jarjestysluku, rantaviivan jakauma ja
# vektoritasot (jotka haetaan tiilien peittamalle alueelle). Ne vanhenevat
# kun tiilia lisataan tai poistetaan - HILJAA: mikaan ei kaadu, kartta vain
# varitetaan vaaraa jakaumaa vasten.
#
# Mosaiikit eivat ole talla listalla: ne tarkistavat oman geometriansa
# rekisteria vasten (ks. get_or_compute_sea_mosaic), mika on tarkempi kuin
# hajautusarvo. Myoskaan _fetch_global / _water_global eivat ole: ne on
# avainnettu GLOBAALEILLA solutunnisteilla juuri siksi, etta ne sailyvat
# tiiliston muuttuessa.
REKISTERISTA_RIIPPUVAT = (
    "_global_threshold_p*.json",
    "_global_tiebreak_sorted.npy",
    "_factor_thresholds.json", "_prime_thresholds.json",
    "_shelter_thresholds.json", "_shoreline_stats.json",
    "_vaylat.json", "_suojelualueet.json", "_palvelut.json",
    # Naiden kuvien arvot on kvantisoitu globaalia jakaumaa vasten.
    "*_top*.png", "*_factors.png", "*_tiebreak.png",
)


def rekisterin_sormenjalki():
    """Tiiliston sormenjalki: tunnisteet ja rajat."""
    reg = tiles.get_registry()
    osat = [f"{t}:{tuple(reg[t].bounds)}" for t in sorted(reg)]
    return hashlib.sha1("|".join(osat).encode()).hexdigest()


def varmista_rekisteri():
    """Mitatoi tiilistosta riippuvat valimuistit jos tiilisto on muuttunut.

    Kutsutaan buildin alussa. Ilman tata Helsingin ajon paalle lisatty
    Ahvenanmaa perisi Helsingin kynnysarvot ja vektoritasot."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    polku = CACHE_DIR / "_rekisteri.json"
    nyt = rekisterin_sormenjalki()
    ennen = json.loads(polku.read_text()).get("sormenjalki") if polku.exists() else None
    if ennen == nyt:
        return 0

    poistetut = []
    if ennen is None:
        # ENSIMMAINEN AJO taman tarkistuksen kanssa: sormenjalkea ei ole,
        # joten ei ole nayttoa vanhentumisesta. Olemassa oleva valimuisti on
        # miltei varmasti rakennettu nykyiselle tiilistolle, ja sen
        # heittaminen pois maksaisi tunteja ilman syyta - kirjataan vain.
        print(f"  tiilisto kirjattu ({len(tiles.get_registry())} tiilta)", flush=True)
    else:
        for kuvio in REKISTERISTA_RIIPPUVAT:
            poistetut += sorted(CACHE_DIR.glob(kuvio))
        for p in poistetut:
            p.unlink()
        print(f"  TIILISTO MUUTTUNUT: mitatoitiin {len(poistetut)} tiilistosta "
              f"riippuvaa valimuistia", flush=True)
    polku.write_text(json.dumps({"sormenjalki": nyt,
                                 "tiilia": len(tiles.get_registry())}))
    return len(poistetut)


def get_or_compute_raw(tile_id, buildings_path, force=False):
    """Palauttaa tiilen raa'an pistemaara+puskurimaski-tuloksen peruskartan
    omalla ruudukolla (EPSG:3067), levyvalimuistilla. Kayttaa seka yksittaisen
    tiilen piirtoa etta globaalia persentiililaskentaa - lasketaan siis vain
    kerran per tiili riippumatta kummasta tarpeesta se ensin tulee."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    npz_path = CACHE_DIR / f"{tile_id}_raw.npz"
    tile = tiles.get_registry()[tile_id]
    sormenjalki = lahde_sormenjalki(tile, buildings_path)

    def _kelpaa(d):
        # "shoreline_mask" on skeeman VERSIOTARKISTUS: taulukoita on lisatty
        # kahdesti, joten vanha valimuisti on laskettava uudelleen vaikka se
        # muuten olisi ehja. Sormenjalki puolestaan tarkistaa LAHTEET.
        if "shoreline_mask" not in d.files:
            return False
        vanha = str(d["sormenjalki"]) if "sormenjalki" in d.files else None
        if vanha != sormenjalki:
            print(f"  {tile_id}: lahdeaineisto muuttunut, lasketaan uudelleen", flush=True)
            return False
        return True

    if not force and npz_path.exists() and _kelpaa(data := np.load(npz_path)):
        return {
            "score": data["score"],
            "rank_score": data["rank_score"],
            "buffer_mask": data["buffer_mask"].astype(bool),
            "slope_score": data["slope_score"],
            "dist_score": data["dist_score"],
            "rock_mask": data["rock_mask"].astype(bool),
            "swamp_mask": data["swamp_mask"].astype(bool),
            "tiebreak": data["tiebreak"],
            "shoreline_mask": data["shoreline_mask"].astype(bool),
            "land_mask": data["land_mask"].astype(bool),
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
        slope_score=result["slope_score"].astype(np.float32),
        dist_score=result["dist_score"].astype(np.float32),
        rock_mask=result["rock_mask"],
        swamp_mask=result["swamp_mask"],
        tiebreak=result["tiebreak"].astype(np.float32),
        shoreline_mask=result["shoreline_mask"],
        land_mask=result["land_mask"],
        map_transform=np.array(result["map_transform"])[:6],
        n_buildings=result["n_buildings"],
        rock_pct=result["rock_pct"],
        swamp_pct=result["swamp_pct"],
        shoreline_px=result["shoreline_px"],
        buffer_px=result["buffer_px"],
        sormenjalki=sormenjalki,
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


# --- KAYTTAJAN VALITTAVAT TEKIJAT / SELAINPUOLEN PISTEYTYS ---
#
# VANHA toteutus (get_or_compute_overlay/get_or_compute_top, sailytetty
# vertailua varten) esilaskee JOKAISEN paksuus x prosentti -yhdistelman omaksi
# valmiiksi varitetyksi kuvakseen. Se ei voi tukea kayttajan valittavia
# tekijoita lainkaan: 4 valintaruutua = 15 eri pisteytysta, jotka kertautuisivat
# paksuuden (5) ja prosentin (10) kanssa ~37 000 tiedostoksi (~9 GB).
#
# TAMA toteutus koodaa per tiili KAKSI kuvaa, joissa pisteytyksen OSATEKIJAT
# ovat erillaan toisistaan, ja jattaa seka pistemaaran KOKOAMISEN etta
# varityksen/kynnyksen/paksuuden selaimen Canvas-pikselikasittelyn vastuulle
# (ks. frontend/index.html: renderFactorTile). Datamaara ei siis riipu
# lainkaan valintojen maarasta - viides tekija ei kasvattaisi sita ollenkaan
# (vrt. instructions.md kohta 6: sama syy miksi vektoritiilia harkitaan
# maantieteellista laajentumista varten).
#
# Resoluutio on karkeampi kuin peruskartan 1 m (NEW_PIXEL_FACTOR), jotta
# jatkuva variliuku rantaviivaa pitkin ei vaadi vektorigeometriaa.
#
# 3,5 m osoittautui LIIAN karkeaksi kun se jai ainoaksi toteutukseksi:
# mitattuna rantakaistale leveni 44 % ja reunat porrastuivat nakyvasti
# lahizoomilla, vaikka pistemaarat olivat kaytannossa samat (mediaaniero
# 0,003 asteikolla 0-1, variluokka vaihtui 3,2 %:ssa pikseleista).
#
# 2,0 m puolittaa porrastuksen. Koko ei ole este: mitattuna factors+tiebreak
# on 1,22 Mt/tiili (3,5 m: 0,57), ja koko docs/ on silti PIENEMPI kuin
# vanhalla toteutuksella, koska sailyy vain kaksi kuvaa per tiili eika 50.
#
# KUVA 1, "{tile}_factors.png" (BGRA cv2-jarjestyksessa taulukossa, RGBA
# tiedostossa):
#   R = jyrkkyyspisteet (slope_score) 0-255
#   G = etaisyyspisteet rakennuksiin (dist_score) 0-255
#   B = bittikentta: bitti 0 = kallio, bitti 1 = suo
#   A = puskurivyohyke-peittomaski (0/255)
#
# KUVA 2, "{tile}_tiebreak.png":
#   R = tasapelinpurku (ks. TIEBREAK_EPSILON) globaalisti jarjestysluvuksi
#       kvantisoituna 0-255
#   A = 255 KAIKKIALLA. Tama on tahallista: selaimen Canvas sailoo pikselit
#       esikerrottuna alfalla, joten A<255 pyoristaa RGB-arvoja ja A=0 nollaa
#       ne kokonaan getImageData:ssa. Kuvassa 1 se ei haittaa (A=0 vain
#       puskurin ulkopuolella, jota ei kayteta), mutta tasapelinpurku
#       tarvitaan tarkkana - siksi oma kuva taysalfalla.
#
# **Havaittu ja korjattu ongelma ("outo punainen reunus")**: aiempi versio
# downsamplasi natiivit taulukot suoralla aluekeskiarvolla, mika sekoitti
# puskurivyohykkeen ULKOPUOLISIA arvoja (esim. aivan rantaviivan tuntumassa,
# jossa DEM:n maa/vesi-raja voi aiheuttaa keinotekoisen jyrkan pistemaaran)
# mukaan reunaruutujen keskiarvoon. Korjattu (1) PEITTOPAINOTETULLA
# keskiarvolla (_masked_downsample: vain oikeasti puskurivyohykkeella olevat
# natiivipikselit vaikuttavat ruudun arvoon) ja (2) muuttamalla selaimen
# dilataatio kayttamaan lahimman OIKEAN puskuripikselin varia (ks.
# frontend/index.html: dilateWithNearestColor).
NEW_PIXEL_FACTOR = 2.0

# Kayttajan valittavat tekijat (ks. frontend/settings.html). Bittimaski
# yksiloi valinnan: sama luku kaytetaan avaimena esilasketuissa
# kynnysarvoissa (compute_factor_thresholds) ja lasketaan selaimessa
# identtisesti (frontend/index.html: factorMask).
FACTOR_SLOPE = 1
FACTOR_DIST = 2
FACTOR_ROCK = 4
FACTOR_SWAMP = 8
FACTOR_BITS = {"slope": FACTOR_SLOPE, "dist": FACTOR_DIST, "rock": FACTOR_ROCK, "swamp": FACTOR_SWAMP}
# NO_SHELTER_MASK / ALL_FACTORS_MASK maaritellaan suojaisuuslohkossa
# tiedoston lopussa (suojaisuus on viides tekija, bitti 16).


def _resize_new_grid(arr, native_shape, factor):
    """Resamploi (INTER_AREA, aluekeskiarvo) taulukon selainpuolen ruudukolle
    (ks. NEW_PIXEL_FACTOR) - factor voi olla EI-kokonaisluku, toisin kuin
    jaetut downsample_image/downsample_mask (VANHAN toteutuksen kayttamat,
    vain kokonaislukukertoimet - ei muutettu tassa)."""
    h, w = native_shape
    new_h = max(1, round(h / factor))
    new_w = max(1, round(w / factor))
    return cv2.resize(arr, (new_w, new_h), interpolation=cv2.INTER_AREA)


def _masked_downsample(arr, buffer_native_f, native_shape, weight_small):
    """Peittopainotettu aluekeskiarvo (ks. moduulin "outo punainen reunus"
    -kommentti): ruudun arvo lasketaan VAIN puskurivyohykkeella olevista
    natiivipikseleista. weight_small on _resize_new_grid(buffer_native_f)
    kertaalleen laskettuna (sama jokaiselle kanavalle)."""
    masked = _resize_new_grid(arr.astype(np.float32) * buffer_native_f, native_shape, NEW_PIXEL_FACTOR)
    return masked / np.maximum(weight_small, 1e-6)


def downsampled_components(tile_id, buildings_path, force=False):
    """Pisteytyksen osatekijat selainpuolen ruudukolla (ks. NEW_PIXEL_FACTOR),
    peittopainotettuna. Palauttaa dict:n jossa kaikki taulukot ovat samaa
    muotoa - 'buffer' kertoo mitka ruudut ovat lainkaan naytettavia."""
    raw = get_or_compute_raw(tile_id, buildings_path, force=force)
    native_shape = raw["score"].shape
    buffer_native_f = raw["buffer_mask"].astype(np.float32)
    weight_small = _resize_new_grid(buffer_native_f, native_shape, NEW_PIXEL_FACTOR)

    return {
        "slope": _masked_downsample(raw["slope_score"], buffer_native_f, native_shape, weight_small),
        "dist": _masked_downsample(raw["dist_score"], buffer_native_f, native_shape, weight_small),
        "rock": _masked_downsample(raw["rock_mask"], buffer_native_f, native_shape, weight_small),
        "swamp": _masked_downsample(raw["swamp_mask"], buffer_native_f, native_shape, weight_small),
        "tiebreak": _masked_downsample(raw["tiebreak"], buffer_native_f, native_shape, weight_small),
        "buffer": weight_small > 0.0,
        "raw": raw,
    }


def _global_tiebreak_sorted(buildings_path, force=False):
    """Lajiteltu taulukko KAIKKIEN tiilien tasapelinpurkuarvoista puskuri-
    vyohykkeella (selainpuolen ruudukolla). Kaytetaan kvantisoimaan
    tasapelinpurku 0-255 GLOBAALIKSI JARJESTYSLUVUKSI: tasavalinen
    kvantisointi hukkaisi tarkkuutta, koska arvot kasautuvat jakauman
    ylapaahan - juuri sinne missa tasapelit ratkotaan."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "_global_tiebreak_sorted.npy"
    if not force and cache_path.exists():
        return np.load(cache_path)

    # Tama kaynnistyy LAISKASTI ensimmaisen tiilen kohdalla ja kay silti
    # kaikki tiilet lapi, joten ilman raportointia ajo nayttaa jumiutuvan
    # heti riville "[1/N]".
    values = []
    for tid in tiilet_edistymisella("Globaali tasapelinpurku"):
        comp = downsampled_components(tid, buildings_path, force=force)
        if comp["buffer"].any():
            values.append(comp["tiebreak"][comp["buffer"]])

    sorted_values = np.sort(np.concatenate(values)) if values else np.array([0.0], dtype=np.float32)
    np.save(cache_path, sorted_values)
    return sorted_values


def _rank_byte(values, sorted_global):
    """0-255-kvantisoitu globaali jarjestysluku (255 = suurin) binaarihaulla."""
    positions = np.searchsorted(sorted_global, values, side="right")
    return np.clip(positions / len(sorted_global) * 255.0, 0, 255).astype(np.uint8)


def score_from_components(slope_b, dist_b, rock_bit, swamp_bit, factor_mask,
                          fetch_level=None, wind_speed=None, fetch_m=None,
                          obstacle_h=0.0):
    """Pistemaara 0-1 valituista tekijoista, 8-bittisiksi kvantisoiduista
    osatekijoista. **Tama on backendin puoli sopimuksesta** - selaimen
    (frontend/index.html: scoreFromComponents) on laskettava TASAN samoin,
    samassa jarjestyksessa, tai esilasketut kynnysarvot (ks.
    compute_factor_thresholds) eivat vastaa naytettya kuvaa.

    Valitsematta jaanyt tekija EI saa painoa 0 vaan poistuu kokonaan, ja
    jaljelle jaavat painot normalisoidaan summaksi 1 - muuten esim. pelkka
    jyrkkyys tuottaisi korkeintaan 0.50 pistetta ja koko kartta nayttaisi
    punaiselta. Suo on kertova rangaistus (ei painotettu termi), ja jos
    VAIN suo on valittuna, pohjapistemaara on 1.0 (= "kaikki on hyvaa paitsi
    suo") - muuten valinnalle ei olisi mielekasta tulkintaa."""
    total = np.zeros(slope_b.shape, dtype=np.float64)
    weight_sum = 0.0
    if factor_mask & FACTOR_SLOPE:
        total = total + score_engine.SLOPE_WEIGHT * (slope_b / 255.0)
        weight_sum += score_engine.SLOPE_WEIGHT
    if factor_mask & FACTOR_DIST:
        total = total + score_engine.DIST_WEIGHT * (dist_b / 255.0)
        weight_sum += score_engine.DIST_WEIGHT
    if factor_mask & FACTOR_ROCK:
        total = total + ROCK_WEIGHT * np.where(rock_bit, ROCK_SCORE_YES, ROCK_SCORE_NO)
        weight_sum += ROCK_WEIGHT
    if factor_mask & FACTOR_SHELTER:
        if wind_speed is None or (fetch_level is None and fetch_m is None):
            raise ValueError("Suojaisuustekija vaatii pyyhkaisymatkan ja wind_speed-arvon")
        # fetch_m (metrit) on selaimen kayttama polku: se interpoloi kahden
        # sektorin valilla eika kvantisoi tulosta takaisin tasoksi.
        shelter = (shelter_score_from_fetch(fetch_m, wind_speed, obstacle_h)
                   if fetch_m is not None
                   else shelter_score_from_level(fetch_level, wind_speed, obstacle_h))
        total = total + SHELTER_WEIGHT * shelter
        weight_sum += SHELTER_WEIGHT

    score = total / weight_sum if weight_sum > 0 else np.ones(slope_b.shape, dtype=np.float64)
    if factor_mask & FACTOR_SWAMP:
        score = np.where(swamp_bit, score * SWAMP_PENALTY_FACTOR, score)
    return np.clip(score, 0.0, 1.0)


def rank_from_components(slope_b, dist_b, rock_bit, swamp_bit, tiebreak_b, factor_mask,
                         fetch_level=None, wind_speed=None, fetch_m=None,
                         obstacle_h=0.0):
    """Pistemaara + tasapelinpurku - kaytetaan VAIN "parhaat X %" -valintaan,
    ei varitykseen (sama jako kuin score/rank_score, ks. TIEBREAK_EPSILON)."""
    score = score_from_components(slope_b, dist_b, rock_bit, swamp_bit, factor_mask,
                                  fetch_level=fetch_level, wind_speed=wind_speed, fetch_m=fetch_m,
                                  obstacle_h=obstacle_h)
    return score + TIEBREAK_EPSILON * (tiebreak_b / 255.0)


def get_or_compute_factor_arrays(tile_id, buildings_path, force=False):
    """Kvantisoidut (8-bittiset) osatekijataulukot yhdelle tiilelle - tasan
    ne arvot jotka selain lukee kuvista."""
    comp = downsampled_components(tile_id, buildings_path, force=force)
    sorted_tiebreak = _global_tiebreak_sorted(buildings_path, force=force)

    return {
        "slope_b": np.clip(comp["slope"] * 255.0, 0, 255).astype(np.uint8),
        "dist_b": np.clip(comp["dist"] * 255.0, 0, 255).astype(np.uint8),
        # Kallio/suo ovat natiivisti binaarisia; peittopainotetun keskiarvon
        # jalkeen ruutu luetaan kallioksi/suoksi jos YLI PUOLET sen
        # puskurivyohykkeen pinta-alasta on sita.
        "rock_bit": comp["rock"] >= 0.5,
        "swamp_bit": comp["swamp"] >= 0.5,
        "tiebreak_b": _rank_byte(comp["tiebreak"], sorted_tiebreak),
        "buffer": comp["buffer"],
        "raw": comp["raw"],
    }


def get_or_compute_factor_png(tile_id, buildings_path, part="factors", force=False):
    """Palauttaa (png_bytes, meta_dict) osatekijakuvalle. part="factors" tai
    "tiebreak" (ks. moduulin kanavakuvaus). Yksi kuvapari per tiili
    riippumatta tekijavalinnoista, paksuudesta ja prosentista."""
    if part not in ("factors", "tiebreak"):
        raise ValueError(f"Tuntematon part: {part}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}_{part}.png"
    meta_path = CACHE_DIR / f"{tile_id}.json"

    if not force and png_path.exists() and meta_path.exists():
        return png_path.read_bytes(), json.loads(meta_path.read_text())

    registry = tiles.get_registry()
    if tile_id not in registry:
        raise KeyError(f"Tuntematon tile_id: {tile_id}")

    arrays = get_or_compute_factor_arrays(tile_id, buildings_path, force=force)
    raw = arrays["raw"]

    if part == "factors":
        r = arrays["slope_b"]
        g = arrays["dist_b"]
        b = (arrays["rock_bit"].astype(np.uint8) | (arrays["swamp_bit"].astype(np.uint8) << 1))
        a = np.where(arrays["buffer"], 255, 0).astype(np.uint8)
    else:
        r = arrays["tiebreak_b"]
        g = np.zeros_like(r)
        b = np.zeros_like(r)
        a = np.full_like(r, 255)

    ok, encoded = cv2.imencode(".png", np.dstack([b, g, r, a]))
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
    if not meta_path.exists():
        meta_path.write_text(json.dumps(meta, indent=2))

    return png_bytes, meta


def tiilet_edistymisella(vaihe, jono=None):
    """Kayy tiilirekisterin (tai annetun jonon) lapi ja RAPORTOI edistymisen.

    Kynnysarvovaiheet kayvat kaikki tiilet lapi mutta eivat tulostaneet
    mitaan, jolloin ajo nayttti jumiutuneen pitkaksi aikaa heti
    vektoritasojen jalkeen - siina kohdassa kun kaikki muut vaiheet
    raportoivat rivi kerrallaan. Hitain naista on compute_shoreline_stats,
    joka laskee compute_prime_components(native=True):n uudelleen jokaiselle
    tiilelle: mitattuna 5-6 s tiilta, eli 27 tiilella nelisen minuuttia
    tayttaa hiljaisuutta. Kaytetaan naissa kaikissa, jottei mikaan
    kokonaisten tiilien yli kayva silmukka ole enaa aanetön."""
    kohteet = list(tiles.get_registry() if jono is None else jono)
    n = len(kohteet)
    print(f"  {vaihe}: {n} tiilta", flush=True)
    t0 = time.perf_counter()
    for i, kohde in enumerate(kohteet, 1):
        yield kohde
        kulunut = time.perf_counter() - t0
        arvio = kulunut / i * (n - i)
        # Jokaista tiilta ei tulosteta isolla aineistolla: 900 tiilta
        # tayttaisi ruudun. Riittaa etta rivi tulee tarpeeksi tiheasti
        # jotta jumin erottaa etenemisesta.
        if n <= 40 or i % 10 == 0 or i == n:
            nimi = kohde if isinstance(kohde, str) else getattr(kohde, "tile_id", i)
            print(f"    [{i}/{n}] {nimi}  ({kulunut / 60:.1f} min kulunut, "
                  f"n. {arvio / 60:.0f} min jaljella)", flush=True)


def compute_factor_thresholds(buildings_path, force=False):
    """"Parhaat X %" -kynnysarvot JOKAISELLE tekijayhdistelmalle (15 kpl) ja
    jokaiselle prosenttiesiasetukselle: {"<bittimaski>": {"<prosentti>": kynnys}}.

    Kynnys on pakko laskea taalla eika selaimessa, koska se on GLOBAALI (koko
    aineiston yli) - selain nakee kerrallaan vain nakymassa olevat tiilet.
    Laskenta tehdaan TASAN samoista 8-bittisista arvoista jotka selain lukee
    kuvista (ei natiiveista liukuluvuista), jotta kynnys ja naytetty kuva
    vastaavat toisiaan pikselilleen."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "_factor_thresholds.json"
    if not force and cache_path.exists():
        return json.loads(cache_path.read_text())

    parts = []
    for tid in tiilet_edistymisella("Tekijakynnykset"):
        arrays = get_or_compute_factor_arrays(tid, buildings_path, force=force)
        buf = arrays["buffer"]
        if buf.any():
            parts.append(
                (
                    arrays["slope_b"][buf],
                    arrays["dist_b"][buf],
                    arrays["rock_bit"][buf],
                    arrays["swamp_bit"][buf],
                    arrays["tiebreak_b"][buf],
                )
            )

    slope_b, dist_b, rock_bit, swamp_bit, tiebreak_b = (np.concatenate(c) for c in zip(*parts))

    thresholds = {}
    for factor_mask in range(1, NO_SHELTER_MASK + 1):
        rank = rank_from_components(slope_b, dist_b, rock_bit, swamp_bit, tiebreak_b, factor_mask)
        thresholds[str(factor_mask)] = {
            str(pct): float(np.percentile(rank, top_percent_to_percentile(pct)))
            for pct in TOP_PERCENT_PRESETS
        }

    cache_path.write_text(json.dumps(thresholds, indent=2))
    return thresholds

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


# --- RANTAVIIVAN JAKAUMA (asetussivun kuvaaja) ---
#
# Kuinka suuri osa rantaviivasta on helppoa ja kuinka suuri vaikeaa
# rantautua. Jakauma lasketaan NATIIVILLA 1m/px-ruudukolla (ei 3,5m
# selainruudukolla), jotta metrimaarat vastaavat tarkalleen todellista
# puskurivyohykkeen pinta-alaa - osatekijat kvantisoidaan silti samoiksi
# 8-bittisiksi arvoiksi kuin kuviin, jotta pistemaarat vastaavat sita mita
# kartta nayttaa.
SHORELINE_HIST_BINS = 25

# Rantaviivan kokonaispituus metreina. **Tama on ARVIO, ei mitattu tarkka
# arvo.** Puskurivyohykkeen PINTA-ALA tiedetaan tarkalleen (pikselilaskenta,
# 1 px = 1 m2), mutta pituus ei: lahde on peruskartan RASTEROITU rantaviiva,
# ei vektorigeometriaa, joten pituus riippuu seka maskin kohinasta etta
# mittakaavasta (rantaviivaparadoksi - 1 m tarkkuudella saaristo tuottaa
# aina pidemman luvun kuin 10 m tarkkuudella).
#
# Mitattu kolmella tavalla, joiden systemaattiset vinoumat osoittavat
# vastakkaisiin suuntiin:
#   - puskurin ala / mitattu leveys (10,2 m):   601 km  (ALIARVIO: kapeilla
#     kannaksilla vastarantojen vyohykkeet sulautuvat, ala ei kasva pituuden
#     mukana)
#   - rantaviivamaskin aariviiva / 2:           899 km  (YLIARVIO: rasterin
#     porrastus ja HSV-kynnyksen rosoinen reuna kasvattavat piiria)
#   - Zhang-Suen-ohennus, ketjukoodipituus:    1405 km  (selva yliarvio:
#     porrasaskeleet lasketaan taysmittaisina, ohennus tuottaa haaroja)
# Kaksi ensimmaista rajaavat totuuden valiinsa -> ~700 km, haarukka 600-900.
#
# HUOM kuvaajan tulkinnassa: jakauman MUOTO ja suhteelliset osuudet ovat
# tarkkoja (suoria pikselilaskentoja), vain metriasteikon absoluuttinen taso
# kantaa tata epavarmuutta.
SHORELINE_LENGTH_M = 700_000
SHORELINE_LENGTH_M_LOW = 600_000
SHORELINE_LENGTH_M_HIGH = 900_000


def compute_shoreline_stats(buildings_path, force=False):
    """Rantaviivan jakauma rantautumiskelpoisuuden mukaan, per
    tekijayhdistelma: metria rantaviivaa kussakin pistemaaraluokassa.

    Palauttaa myos "parhaat X %" -rajan sijainnin pistemaara-asteikolla,
    jotta asetussivun kuvaaja voi merkita sen - se lasketaan TASTA SAMASTA
    jakaumasta, jolloin merkki ja pylvaat ovat keskenaan tasmalleen
    yhtapitavia."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "_shoreline_stats.json"
    if not force and cache_path.exists():
        return json.loads(cache_path.read_text())

    bins = SHORELINE_HIST_BINS
    edges = np.linspace(0.0, 1.0, bins + 1)
    counts = {mask: np.zeros(bins, dtype=np.float64) for mask in range(1, NO_SHELTER_MASK + 1)}
    prime_counts = {mask: np.zeros(bins, dtype=np.float64) for mask in range(1, NO_SHELTER_MASK + 1)}
    ranks = {mask: [] for mask in range(1, NO_SHELTER_MASK + 1)}
    total_px = 0

    for tid in tiilet_edistymisella("Rantaviivan jakauma"):
        raw = get_or_compute_raw(tid, buildings_path, force=force)
        buf = raw["buffer_mask"]
        if not buf.any():
            continue
        total_px += int(buf.sum())

        # Sama kvantisointi kuin kuviin (ks. get_or_compute_factor_arrays),
        # mutta ilman downsamplausta.
        slope_b = np.clip(raw["slope_score"][buf] * 255.0, 0, 255).astype(np.uint8)
        dist_b = np.clip(raw["dist_score"][buf] * 255.0, 0, 255).astype(np.uint8)
        rock_bit = raw["rock_mask"][buf]
        swamp_bit = raw["swamp_mask"][buf]
        tiebreak_b = np.clip(raw["tiebreak"][buf] * 255.0, 0, 255).astype(np.uint8)

        # Karkipaikkojen osatekijat SAMALLA natiiviruudukolla, jotta kayrat
        # ovat keskenaan vertailukelpoisia (ks. compute_prime_components).
        prime = compute_prime_components(tid, buildings_path, force=force, native=True)
        p_slope_b = np.clip(prime["slope"][buf] * 255.0, 0, 255).astype(np.uint8)
        p_dist_b = np.clip(prime["dist"][buf] * 255.0, 0, 255).astype(np.uint8)
        p_rock_bit = prime["rock"][buf] >= 0.5
        p_swamp_bit = prime["not_swamp"][buf] < 0.5
        del prime

        for mask in counts:
            score = score_from_components(slope_b, dist_b, rock_bit, swamp_bit, mask)
            counts[mask] += np.histogram(score, bins=edges)[0]
            prime_score = score_from_components(p_slope_b, p_dist_b, p_rock_bit, p_swamp_bit, mask)
            prime_counts[mask] += np.histogram(prime_score, bins=edges)[0]
            # Persentiilit lasketaan lopuksi koko aineistosta; kerataan
            # otos muistin saastamiseksi (jakauma on niin suuri ettei
            # tarkkaa lajittelua tarvita merkkiviivan sijaintiin).
            step = max(1, len(score) // 20000)
            ranks[mask].append(
                (score + TIEBREAK_EPSILON * (tiebreak_b / 255.0))[::step].astype(np.float32)
            )

    metres_per_px = SHORELINE_LENGTH_M / total_px if total_px else 0.0

    histograms = {}
    prime_histograms = {}
    top_markers = {}
    for mask in counts:
        histograms[str(mask)] = [round(float(c) * metres_per_px, 1) for c in counts[mask]]
        prime_histograms[str(mask)] = [round(float(c) * metres_per_px, 1) for c in prime_counts[mask]]
        sample = np.concatenate(ranks[mask])
        top_markers[str(mask)] = {
            str(pct): float(np.percentile(sample, top_percent_to_percentile(pct)))
            for pct in TOP_PERCENT_PRESETS
        }

    stats = {
        "bin_edges": [round(float(e), 4) for e in edges],
        "histograms_m": histograms,
        "prime_histograms_m": prime_histograms,
        "top_markers": top_markers,
        "buffer_px": total_px,
        "buffer_km2": round(total_px / 1e6, 3),
        "length_m": SHORELINE_LENGTH_M,
        "length_m_low": SHORELINE_LENGTH_M_LOW,
        "length_m_high": SHORELINE_LENGTH_M_HIGH,
    }
    cache_path.write_text(json.dumps(stats))
    return stats


# --- KARKIPAIKAT: "koko rantakaistaleen on oltava hyva" ---
#
# ONGELMA jonka tama ratkaisee: "Parhaat rantautumispaikat" valitsee parhaat
# X % PIKSELI KERRALLAAN. Siksi loiva kohta aivan vesirajassa saa taydet
# pisteet vaikka 8 m sisamaahan olisi kelvoton kallio - paikkaan ei
# kaytannossa pysty rantautumaan. Lisaksi valituksi tulee muutaman metrin
# siivuja joihin ei mahdu venetta eika kajakkia.
#
# RATKAISU on kaksivaiheinen aggregointi, joka nojaa siihen etta
# compute_shoreline_buffer laskee jo etaisyysmuunnoksen rantaviivaan.
# Sama muunnos return_indices=True-lipulla antaa jokaiselle pikselille
# LAHIMMAN RANTAVIIVAPIKSELIN indeksin, eli valmiin ryhmittelyn
# poikkileikkauksiin - uutta geometriaa ei tarvita:
#   1. POIKITTAIN (rantaviivasta sisamaahan): jokaiselle poikkileikkaukselle
#      sen arvojen ALIN KYMMENYS koko PRIME_ZONE_MAX_M:iin asti.
#   2. RANTAVIIVAN SUUNTAAN: minimi +-PRIME_ALONGSHORE_RADIUS_M ikkunassa,
#      jolloin kelpuutettu kohta vaatii yhtajaksoisen hyvan jakson
#      ymparilleen eivatka yksittaiset siivut paase lapi.
#
# Miksi ALIN KYMMENYS eika tiukka minimi: korkeusmalli on 2 m ruudukolta
# resamploitu 1 m:iin, joten yksittaisia virheellisen jyrkkia pikseleita
# esiintyy. Minimi antaisi niiden pudottaa muuten moitteettomia paikkoja.
#
# Miksi aggregoidaan OSATEKIJAT eika pistemaaraa: pistemaara riippuu
# kayttajan tekijavalinnoista (15 yhdistelmaa), joten valmiiksi laskettu
# pistemaara vaatisi 15 muunnelmaa ja rikkoisi "yksi kuva per tiili"
# -periaatteen. Osatekijat sen sijaan ovat tekijavalinnasta riippumattomia,
# ja selain kokoaa niista pistemaaran SAMALLA scoreFromComponents-
# funktiolla kuin muuallakin.
#
# Kallio ja suo ovat binaarisia, ja niihin patee sama "alin kymmenys"
# -saanto: kallio kelpaa jos VAHINTAAN 90 % kaistaleesta on kalliota, ja
# suo lasketaan haitaksi jos VAHINTAAN 10 % kaistaleesta on suota. Suo
# kasitellaan kaannettyna ("ei suota"), jolloin molempiin kay sama
# persentiili 10 ja sama minimi rantaviivan suunnassa.
PRIME_ZONE_MIN_M = 5.0
PRIME_ZONE_MAX_M = 30.0
PRIME_CROSS_PERCENTILE = 10
PRIME_ALONGSHORE_RADIUS_M = 10.0


def _grouped_percentile(anchor, values, percentile):
    """Ryhmitelty persentiili ilman silmukkaa: lajitellaan (ryhma, arvo)
    -parit ja poimitaan kustakin ryhmasta halutun kohdan alkio. Palauttaa
    (ryhmatunnisteet, arvot) - ryhmatunnisteet ovat nousevassa jarjestyksessa,
    jolloin niista voi hakea searchsorted:lla."""
    order = np.lexsort((values, anchor))
    sorted_anchor = anchor[order]
    sorted_values = values[order]

    uniq, starts = np.unique(sorted_anchor, return_index=True)
    ends = np.append(starts[1:], len(sorted_anchor))
    sizes = ends - starts
    picks = starts + ((sizes - 1) * (percentile / 100.0)).astype(np.int64)
    return uniq, sorted_values[picks]


def _alongshore_min(anchor_ids, values, shape, radius_px):
    """Minimi rantaviivan suunnassa: sirotellaan poikkileikkausarvot
    2D-taulukkoon jonka tayte on +inf ja ajetaan minimum_filter. +inf ei
    koskaan voita minimia, joten ikkuna huomioi vain oikeat
    rantaviivapikselit - erillista "vain naiden pikselien yli" -logiikkaa
    ei tarvita."""
    arr = np.full(shape, np.inf, dtype=np.float32)
    ys, xs = np.unravel_index(anchor_ids, shape)
    arr[ys, xs] = values
    filtered = minimum_filter(arr, size=2 * radius_px + 1, mode="nearest")
    return filtered[ys, xs]


def compute_prime_components(tile_id, buildings_path, force=False, native=False):
    """Karkipaikkojen osatekijat selainruudukolla (ks. NEW_PIXEL_FACTOR).
    Arviointi tehdaan LEVEAMMALLA PRIME_ZONE-vyohykkeella, mutta tulos
    naytetaan NYKYISELLA 5-15 m puskurivyohykkeella, jotta kerros asettuu
    tarkalleen samaan kohtaan kuin muut kerrokset.

    native=True palauttaa taulukot downsamplaamattomina (1 m/px) -
    rantaviivan jakauman laskentaa varten, jotta karkipaikkojen jakauma on
    laskettu TASMALLEEN samalla ruudukolla kuin tavallinen jakauma
    (compute_shoreline_stats) ja kayrat ovat vertailukelpoisia."""
    raw = get_or_compute_raw(tile_id, buildings_path, force=force)
    shoreline = raw["shoreline_mask"]
    shape = shoreline.shape
    pixel_size = abs(raw["map_transform"].a)

    # RANTAVIIVATON TIILI: ei rantaa, ei karkipaikkoja. Ilman tata haaraa
    # return_indices palauttaa indeksin -1 (ks. compute_shoreline_buffer),
    # jolloin litistetty poikkileikkaustunniste on -leveys ja unravel_index
    # kaatuu "index -6000 is out of bounds". Nain kavi Helsingin sisamaan
    # ruudulle L4131F kolmen tunnin ajon lopussa.
    if not shoreline.any():
        # Muoto johdetaan samoilla apufunktioilla kuin normaalihaarassa,
        # jottei tyhjan tiilen taulukko voi olla eri kokoinen kuin muiden.
        nolla_native = np.zeros(shape, dtype=np.float32)
        pieni = _resize_new_grid(nolla_native, shape, NEW_PIXEL_FACTOR)
        tyhja = nolla_native if native else np.zeros_like(pieni)
        return {
            "slope": tyhja, "dist": tyhja.copy(), "rock": tyhja.copy(),
            "not_swamp": tyhja.copy(),
            "buffer": np.zeros(tyhja.shape, dtype=bool),
            "raw": raw,
        }

    dist, indices = distance_transform_edt(
        ~shoreline, sampling=(pixel_size, pixel_size), return_indices=True
    )
    prime_zone = raw["land_mask"] & (dist >= PRIME_ZONE_MIN_M) & (dist <= PRIME_ZONE_MAX_M)
    del dist

    # Poikkileikkauksen tunniste = lahimman rantaviivapikselin litistetty indeksi.
    # int32 riittaa: suurin indeksi on H*W = 36 milj. << 2^31, ja int64
    # kaksinkertaistaisi 288 MB:n taulukon turhaan.
    anchor_full = (indices[0].astype(np.int32) * np.int32(shape[1]) + indices[1].astype(np.int32))
    del indices

    anchor_zone = anchor_full[prime_zone]
    # Nayttopikselit (5-15 m) ovat aina PRIME_ZONE:n osajoukko, joten niiden
    # poikkileikkaustunnisteet loytyvat aina ryhmien joukosta.
    buffer_mask = raw["buffer_mask"]
    anchor_display = anchor_full[buffer_mask]
    del anchor_full

    radius_px = meters_to_px(PRIME_ALONGSHORE_RADIUS_M, pixel_size)
    buffer_native_f = buffer_mask.astype(np.float32)
    weight_small = _resize_new_grid(buffer_native_f, shape, NEW_PIXEL_FACTOR)

    def aggregate(values_full):
        """Kaksivaiheinen aggregointi + downsamplaus yhtena askeleena -
        natiivikokoinen valitulos vapautetaan heti, jottei neljaa 144 MB:n
        taulukkoa ole yhtaaikaa muistissa."""
        uniq, per_transect = _grouped_percentile(
            anchor_zone, values_full[prime_zone], PRIME_CROSS_PERCENTILE
        )
        along = _alongshore_min(uniq, per_transect, shape, radius_px)
        pos = np.searchsorted(uniq, anchor_display)
        out = np.zeros(shape, dtype=np.float32)
        out[buffer_mask] = along[pos]
        if native:
            return out
        return _masked_downsample(out, buffer_native_f, shape, weight_small)

    return {
        "slope": aggregate(raw["slope_score"].astype(np.float32)),
        "dist": aggregate(raw["dist_score"].astype(np.float32)),
        # Kallio: persentiili 10 nolla/ykkos-taulukosta = tosi vain jos
        # vahintaan 90 % kaistaleesta on kalliota.
        "rock": aggregate(raw["rock_mask"].astype(np.float32)),
        # Suo on NEGATIIVINEN tekija, joten sita kasitellaan kaannettyna
        # "ei suota" -hyvyytena. Silloin sama persentiili 10 ja sama
        # _alongshore_min osuvat oikeaan suuntaan: tulos on 0 (= suota) jos
        # vahintaan 10 % kaistaleesta on suota tai jos suota on lahistolla
        # rantaviivan suunnassa.
        "not_swamp": aggregate((~raw["swamp_mask"]).astype(np.float32)),
        "buffer": buffer_mask if native else (weight_small > 0.0),
        "raw": raw,
    }


def get_or_compute_prime_arrays(tile_id, buildings_path, force=False):
    """Kvantisoidut (8-bittiset) karkipaikka-osatekijat - tasan ne arvot
    jotka selain lukee kuvasta."""
    comp = compute_prime_components(tile_id, buildings_path, force=force)
    return {
        "slope_b": np.clip(comp["slope"] * 255.0, 0, 255).astype(np.uint8),
        "dist_b": np.clip(comp["dist"] * 255.0, 0, 255).astype(np.uint8),
        "rock_bit": comp["rock"] >= 0.5,
        "swamp_bit": comp["not_swamp"] < 0.5,
        "buffer": comp["buffer"],
        "raw": comp["raw"],
    }


def get_or_compute_prime_png(tile_id, buildings_path, force=False):
    """Palauttaa (png_bytes, meta_dict) karkipaikkakuvalle. Kanavat kuten
    factors-kuvassa (R=jyrkkyys, G=etaisyys, B=kallio/suo-bitit,
    A=puskurimaski), mutta arvot ovat kaistaleen yli aggregoituja."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}_prime.png"
    meta_path = CACHE_DIR / f"{tile_id}.json"

    if not force and png_path.exists() and meta_path.exists():
        return png_path.read_bytes(), json.loads(meta_path.read_text())

    if tile_id not in tiles.get_registry():
        raise KeyError(f"Tuntematon tile_id: {tile_id}")

    arrays = get_or_compute_prime_arrays(tile_id, buildings_path, force=force)
    raw = arrays["raw"]

    r = arrays["slope_b"]
    g = arrays["dist_b"]
    b = arrays["rock_bit"].astype(np.uint8) | (arrays["swamp_bit"].astype(np.uint8) << 1)
    a = np.where(arrays["buffer"], 255, 0).astype(np.uint8)

    ok, encoded = cv2.imencode(".png", np.dstack([b, g, r, a]))
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
    if not meta_path.exists():
        meta_path.write_text(json.dumps(meta, indent=2))

    return png_bytes, meta


def compute_prime_thresholds(buildings_path, force=False):
    """"Parhaat X %" -kynnysarvot KARKIPAIKOILLE, per tekijayhdistelma ja
    prosentti. Persentiili lasketaan SAMASTA populaatiosta (puskurivyohykkeen
    pikselit) kuin compute_factor_thresholds, jotta "parhaat 7 %" tarkoittaa
    samaa osuutta rantaviivasta molemmissa kerroksissa - ero on siina MITKA
    7 % valitaan."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "_prime_thresholds.json"
    if not force and cache_path.exists():
        return json.loads(cache_path.read_text())

    parts = []
    for tid in tiilet_edistymisella("Karkipaikkakynnykset"):
        prime = get_or_compute_prime_arrays(tid, buildings_path, force=force)
        # Tasapelinpurku otetaan SAMASTA kuvasta kuin top-kerroksessa:
        # aggregoitu pistemaara saturoituu sekin, ja jarjestys tasapelien
        # sisalla on ratkaistava jotenkin.
        factors = get_or_compute_factor_arrays(tid, buildings_path, force=force)
        buf = prime["buffer"]
        if buf.any():
            parts.append(
                (
                    prime["slope_b"][buf],
                    prime["dist_b"][buf],
                    prime["rock_bit"][buf],
                    prime["swamp_bit"][buf],
                    factors["tiebreak_b"][buf],
                )
            )

    slope_b, dist_b, rock_bit, swamp_bit, tiebreak_b = (np.concatenate(c) for c in zip(*parts))

    thresholds = {}
    for factor_mask in range(1, NO_SHELTER_MASK + 1):
        rank = rank_from_components(slope_b, dist_b, rock_bit, swamp_bit, tiebreak_b, factor_mask)
        thresholds[str(factor_mask)] = {
            str(pct): float(np.percentile(rank, top_percent_to_percentile(pct)))
            for pct in TOP_PERCENT_PRESETS
        }

    cache_path.write_text(json.dumps(thresholds, indent=2))
    return thresholds


# --- SUOJAISUUS: PYYHKAISYMATKA (FETCH) ---
#
# Aallokon suuruus rannassa riippuu siita, kuinka pitkalta avovedelta tuuli
# ehtii sita kasvattaa. Lasketaan siis jokaiselle rantaruudulle 12 ilman-
# suuntaan (30 asteen sektorit) matka avovetta pitkin ensimmaiseen maahan.
#
# LASKETTAVA KAIKKIEN TIILIEN YLI KERRALLA, ei tiili kerrallaan: sade voi
# kulkea naapuritiilen lapi. Siksi merimaskeista kootaan yksi karkea
# (FETCH_GRID_M) mosaiikki. Tiilisto on 3x5 ruudukko josta 4 ruutua
# puuttuu, joten mosaiikissa on aukkoja.
#
# AUKOT JA REUNAT = TUNTEMATON = ALTIS. Sade joka poistuu aineistosta tai
# kulkee aukon lapi ei osu maahan, jolloin fetch juoksee MAX_FETCH_M-kattoon
# ja ranta merkitaan alttiiksi. Tama on tahallista ja konservatiivista: emme
# vaita paikkaa suojaisaksi silloin kun emme tieda. Erillista aukkologiikkaa
# ei siis tarvita.
#
# MERIMASKI EIKA ~land_mask: jalkimmainen on DEM-pohjainen, jolloin korkeus-
# mallin nodata-alueet tulkittaisiin vedeksi ja synnyttaisivat valekaytavia,
# ja sisamaan lammet menisivat mukaan. Merimaski tulee Maastotietokannan
# meri-tasosta, jossa jarvet ovat erikseen (ks. backend/vesisto.py).
#
# Merimosaiikki lasketaan SUORAAN vektoriaineistosta (ei raakavalimuistin
# kautta): se ei tarvitse DEM:ia eika rakennuksia, joten raakavalimuistin
# versiota ei tarvitse nostaa tama takia.
FETCH_SECTORS = 12
FETCH_GRID_M = 10.0
MAX_FETCH_M = 15000.0
FETCH_LEVELS = 16  # 4 bittia
# Pienin erottuva pyyhkaisymatka: tata lyhyemmat ovat kaytannossa tyynta.
MIN_FETCH_M = 50.0
# Kuinka monta ruutua sade saa kulkea maalla ennen kuin sen katsotaan
# osoittavan sisamaahan. Puskurivyohyke ulottuu 15 m rantaviivasta ja
# rantaviivamaski on muutaman metrin levyinen, joten 4 ruutua (40 m) riittaa
# tavoittamaan veden - pidempi sallisi kapean kannaksen lapi "vuotavan"
# vaaran altistuksen.
MAX_INITIAL_LAND_STEPS = 4


def sector_bearing(sector):
    """Sektorin keskisuunta asteina (0 = pohjoinen, kasvaa myotapaivaan) -
    sama kulmasopimus kuin tuulen suunnalla saatiedoissa."""
    return sector * (360.0 / FETCH_SECTORS)


# --- GLOBAALI SOLUTUNNISTE ---
#
# Rantaruudut talletettiin aiemmin muodossa rivi * mosaiikin_leveys + sarake.
# Se sitoi valimuistin mosaiikin muotoon: kun tiilia lisattiin, leveys muuttui
# ja JOKAINEN tunniste tarkoitti eri ruutua. Koko globaali laskenta oli siis
# uusittava, mika tekee kasvavasta aineistosta neliollisen: 244 tiilen
# kaytava 30 tiilen erissa maksaisi kahdeksan taytta pyyhkaisymatkalaskentaa.
#
# Tunniste lasketaan nyt KIINTEASTA globaalista ruudukosta, joka ei riipu
# siita mita tiilia sattuu olemaan mukana. Sama ruutu saa saman tunnisteen
# riippumatta aineiston laajuudesta, joten valimuisti sailyy.
#
# Origo on Suomen ulkopuolella pohjoisessa ja lannessa, jotta kaikki
# EPSG:3067-koordinaatit tuottavat ei-negatiiviset indeksit. Suurin tunniste
# on n. 1,2e10 eli mahtuu int64:aan reilusti.
GLOBAL_ORIGIN_X = 0.0
GLOBAL_ORIGIN_Y = 7_800_000.0
GLOBAL_COLS = 100_000          # 1000 km / FETCH_GRID_M


def global_cell_ids(x, y):
    """Koordinaatit -> globaalit solutunnisteet (int64)."""
    gcol = np.rint((np.asarray(x, dtype=np.float64) - GLOBAL_ORIGIN_X) / FETCH_GRID_M).astype(np.int64)
    grow = np.rint((GLOBAL_ORIGIN_Y - np.asarray(y, dtype=np.float64)) / FETCH_GRID_M).astype(np.int64)
    return grow * GLOBAL_COLS + gcol


def global_cell_coords(gid):
    """Globaalit solutunnisteet -> koordinaatit (ruudun keskipiste-indeksi)."""
    grow, gcol = np.divmod(np.asarray(gid, dtype=np.int64), GLOBAL_COLS)
    return (GLOBAL_ORIGIN_X + gcol * FETCH_GRID_M,
            GLOBAL_ORIGIN_Y - grow * FETCH_GRID_M)


# Mosaiikkia levennetaan tiilijoukon ymparilta talla verran.
#
# SYY ON MITATTU VIKA: _march_ray rajaa esteenetsinnan np.clipilla taulukon
# reunaan, joten reunalla katse leikkautuu takaisin aineiston sisaan ja poimii
# maata sielta missa sita ei ole. Kun demo laajeni Ahvenanmaalta Helsinkiin,
# vanhan alueen estekorkeuksista muuttui 0,53 % ja JOKAINEN muuttunut arvo oli
# pienempi - eli pieni mosaiikki oli yliarvioinut esteet reunoillaan.
#
# MAX_FETCH_M riittaa tasmalleen: sitä kauempaa sade ei nae mitaan.
MOSAIC_PAD_M = MAX_FETCH_M


# Mosaiikin suurin sallittu solumaara yhdella kertaa.
#
# Mosaiikki on TIHEA taulukko tiilien rajaaman suorakaiteen yli, joten se ei
# skaalaudu koko rannikolle: itarajalta Tornioon rajaus olisi 346 x 667 km eli
# 2,8 MILJARDIA solua, ja korkeusmalli float32:na 11,3 Gt. Kaytava itsessaan
# on siita ehka 5 %.
#
# Ratkaisu: laskenta pilkotaan ALUEISIIN. Jokainen alue kattaa osan tiilista
# (ydin) ja lisaksi kaikki alle MAX_FETCH_M paassa olevat tiilet (konteksti),
# jotta reunalla olevat sateet nakevat oikean maan eivatka luule sita mereksi.
# Alueet limittyvat siis tahallaan.
#
# 300 M solua = sea 300 Mt + height 1,2 Gt. Mahtuu mukavasti ja vastaa noin
# 170 x 170 km aluetta.
MAX_MOSAIC_CELLS = 300_000_000


def _sea_mosaic_geometry(bounds_lista=None):
    """Karkean mosaiikin (origo, muoto) annettujen tiilien ylle.

    Origo napsautetaan globaalin ruudukon solurajalle, jotta mosaiikin ruudut
    vastaavat aina samoja globaaleja tunnisteita."""
    if bounds_lista is not None:
        bounds = list(bounds_lista)
    else:
        bounds = [t.bounds for t in tiles.get_registry().values()]
    minx = min(b[0] for b in bounds) - MOSAIC_PAD_M
    miny = min(b[1] for b in bounds) - MOSAIC_PAD_M
    maxx = max(b[2] for b in bounds) + MOSAIC_PAD_M
    maxy = max(b[3] for b in bounds) + MOSAIC_PAD_M
    # Napsautus globaaliin ruudukkoon: origo osuu tasan solun reunalle.
    minx = GLOBAL_ORIGIN_X + np.floor((minx - GLOBAL_ORIGIN_X) / FETCH_GRID_M) * FETCH_GRID_M
    maxy = GLOBAL_ORIGIN_Y - np.floor((GLOBAL_ORIGIN_Y - maxy) / FETCH_GRID_M) * FETCH_GRID_M
    w = int(round((maxx - minx) / FETCH_GRID_M))
    h = int(round((maxy - miny) / FETCH_GRID_M))
    return (float(minx), float(maxy)), (h, w)



def _laskenta_alueet():
    """Jakaa tiilet alueisiin joiden mosaiikki mahtuu muistiin.

    Palauttaa listan (ydin_tiilet, konteksti_tiilet). Ydin on se joukko jonka
    ruudut lasketaan tassa alueessa; konteksti sisaltaa lisaksi kaikki alle
    MAX_FETCH_M paassa olevat tiilet, jotta reunan sateet nakevat oikean maan.

    Yhdella alueella (tavallinen tapaus) palautetaan koko tiilijoukko, jolloin
    kaytos on tasan sama kuin ennen alueellistamista."""
    registry = tiles.get_registry()
    kaikki = list(registry.items())
    _o, (h, w) = _sea_mosaic_geometry()
    if h * w <= MAX_MOSAIC_CELLS or len(kaikki) < 2:
        return [([t for t, _ in kaikki], [t for t, _ in kaikki])]

    # Jaetaan ita-lansi-suunnassa: Suomen rannikko on pitka ja kapea, joten
    # yksi jakosuunta riittaa eika ruudukkoa tarvita.
    kaikki.sort(key=lambda kv: kv[1].bounds[0])
    leveys = max(b.bounds[2] for _, b in kaikki) - min(b.bounds[0] for _, b in kaikki)
    korkeus = max(b.bounds[3] for _, b in kaikki) - min(b.bounds[1] for _, b in kaikki)
    # Kuinka leveaan kaistaan mahtuu MAX_MOSAIC_CELLS kun korkeus on annettu
    kaista_m = MAX_MOSAIC_CELLS * FETCH_GRID_M ** 2 / max(korkeus + 2 * MOSAIC_PAD_M, 1.0)
    kaista_m = max(kaista_m - 2 * MOSAIC_PAD_M, 6000.0)
    n = max(int(np.ceil(leveys / kaista_m)), 1)

    x0 = min(b.bounds[0] for _, b in kaikki)
    alueet = []
    for i in range(n):
        raja0 = x0 + i * leveys / n
        raja1 = x0 + (i + 1) * leveys / n
        ydin = [t for t, b in kaikki if raja0 <= b.bounds[0] < raja1 or
                (i == n - 1 and b.bounds[0] >= raja1)]
        if not ydin:
            continue
        yb = [registry[t].bounds for t in ydin]
        kx0 = min(b[0] for b in yb) - MAX_FETCH_M
        kx1 = max(b[2] for b in yb) + MAX_FETCH_M
        ky0 = min(b[1] for b in yb) - MAX_FETCH_M
        ky1 = max(b[3] for b in yb) + MAX_FETCH_M
        konteksti = [t for t, b in kaikki
                     if b.bounds[2] > kx0 and b.bounds[0] < kx1
                     and b.bounds[3] > ky0 and b.bounds[1] < ky1]
        alueet.append((ydin, konteksti))
    return alueet


def _alueen_mosaiikit(tile_ids, buildings_path):
    """Merimaski ja korkeusmalli annetuille tiilille, ilman levyvalimuistia.

    Kaytetaan kun aineisto ei mahdu yhteen mosaiikkiin. Yhden alueen tapaus
    kayttaa edelleen valimuistitettuja get_or_compute_*-versioita."""
    bounds = [tiles.get_registry()[t].bounds for t in tile_ids]
    (ox, oy), (h, w) = _sea_mosaic_geometry(bounds)
    sea = np.ones((h, w), dtype=bool)
    for tid in tile_ids:
        tile = tiles.get_registry()[tid]
        n = int(round((tile.bounds[2] - tile.bounds[0]) / FETCH_GRID_M))
        m = int(round((tile.bounds[3] - tile.bounds[1]) / FETCH_GRID_M))
        tr = from_origin(tile.bounds[0], tile.bounds[3], FETCH_GRID_M, FETCH_GRID_M)
        small = vesisto.meri_maski(tile.bounds, tr, (m, n))
        col = int(round((tile.bounds[0] - ox) / FETCH_GRID_M))
        row = int(round((oy - tile.bounds[3]) / FETCH_GRID_M))
        sea[row:row + small.shape[0], col:col + small.shape[1]] = small
    height = _height_mosaic_for(tile_ids, sea, (ox, oy), buildings_path)
    return sea, height, (ox, oy)


def get_or_compute_sea_mosaic(force=False):
    """Merimaski karkealla FETCH_GRID_M-ruudukolla kaikkien tiilien ylle.
    True = avovetta (tai aineiston aukko, ks. moduulin kommentti).

    Meri rasteroidaan VEKTORIAINEISTOSTA (ks. backend/vesisto.py). Aiemmin se
    luettiin peruskartan varista, mika vaati kolme kikkaa joita ei enaa ole:
    50 ha kokosuodatus (meri vs jarvi), morfologinen silta siltapenkereille,
    ja erillissaanto tiilirajalla katkeaville lahdille. Maastotietokannassa
    meri on oma tasonsa, joten mitaan naista ei tarvita."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "_sea_mosaic.npz"
    (ox, oy), (h, w) = _sea_mosaic_geometry()

    # GEOMETRIA TARKISTETAAN, ei vain olemassaolo. Mosaiikki kattaa tasan
    # sen tiilijoukon jolla se rakennettiin; jos tiilia lisataan, vanha
    # mosaiikki ei ulotu uusille ja niiden ruudut osuisivat sen ulkopuolelle.
    # Tarkistus on tassa eika mitatointilistassa, koska se johtuu suoraan
    # rekisterista eika voi jaada tekematta.
    if not force and cache_path.exists():
        data = np.load(cache_path)
        if (data["sea"].shape == (h, w)
                and float(data["ox"]) == ox and float(data["oy"]) == oy):
            return data["sea"], (ox, oy)
        print(f"  merimosaiikki: tiilisto muuttunut "
              f"({data['sea'].shape} -> {(h, w)}), rakennetaan uudelleen",
              flush=True)

    # Oletus TOSI = tuntematon kasitellaan avovetena, jolloin sade jatkaa
    # kulkuaan kattoon asti ja ranta tulkitaan alttiiksi.
    sea = np.ones((h, w), dtype=bool)

    for tile in tiilet_edistymisella("Merimosaiikki", tiles.get_registry().values()):
        n = int(round((tile.bounds[2] - tile.bounds[0]) / FETCH_GRID_M))
        m = int(round((tile.bounds[3] - tile.bounds[1]) / FETCH_GRID_M))
        # Rasteroidaan suoraan mosaiikin tarkkuudella: valissa ei ole
        # alinaytteistysta eika enemmistosaantoa, joten kapeat salmet
        # sailyvat sellaisina kuin ne aineistossa ovat.
        tr = from_origin(tile.bounds[0], tile.bounds[3], FETCH_GRID_M, FETCH_GRID_M)
        small = vesisto.meri_maski(tile.bounds, tr, (m, n))

        col = int(round((tile.bounds[0] - ox) / FETCH_GRID_M))
        row = int(round((oy - tile.bounds[3]) / FETCH_GRID_M))
        sea[row:row + small.shape[0], col:col + small.shape[1]] = small

    np.savez_compressed(cache_path, sea=sea, ox=ox, oy=oy)
    return sea, (ox, oy)


def quantise_fetch(fetch_m):
    """Fetch -> 0..FETCH_LEVELS-1 LOGARITMISELLA asteikolla. Aallonkorkeus
    kasvaa pyyhkaisymatkan neliojuuressa, joten lineaarinen kvantisointi
    hukkaisi tarkkuuden juuri lyhyilla matkoilla, joissa suojaisan ja
    alttiin ero ratkeaa."""
    lo, hi = np.log(MIN_FETCH_M), np.log(MAX_FETCH_M)
    t = (np.log(np.clip(fetch_m, MIN_FETCH_M, MAX_FETCH_M)) - lo) / (hi - lo)
    return np.clip(np.rint(t * (FETCH_LEVELS - 1)), 0, FETCH_LEVELS - 1).astype(np.uint8)


def dequantise_fetch(level):
    """quantise_fetch:n kaanteisfunktio - selain (frontend/index.html:
    dequantiseFetch) laskee TASAN saman."""
    lo, hi = np.log(MIN_FETCH_M), np.log(MAX_FETCH_M)
    return np.exp(lo + (level / (FETCH_LEVELS - 1)) * (hi - lo))


# --- SUOJAISUUS VIIDENTENA TEKIJANA ---
#
# Aallonkorkeus arvioidaan tuulen nopeudesta ja pyyhkaisymatkasta
# (JONSWAP, fetch-rajoitettu kasvu):  Hs ~ WAVE_COEFF * U * sqrt(F).
# Tarkistus: 10 m/s ja 5 km -> 0,35 m; 15 km -> 0,62 m. Uskottavia.
#
# Pistemaara on 1 kun aallokko on melojalle merkityksetonta ja 0 kun se
# tekee rantautumisen hankalaksi, valissa lineaarinen.
#
# **Kiristetty mittausten perusteella** (aiemmin 0,10-0,60 m). Vanhoilla
# arvoilla tekija ei kaytannossa erotellut mitaan: pyyhkaisymatkan mediaani
# pahimmassa suunnassa on 2241 m, mista 14 m/s tuottaa 0,34 m aallokon eli
# pistemaaran 0,52 - myrskyssakin puolet saaristosta sai yli puolet
# pisteista, ja vain 8,2 % jai alle 0,5:n. Lisaksi tekijan lisaaminen NOSTI
# kokonaispisteen keskiarvoa (+0,031 viela 14 m/s:ssa), koska suojaisuuden
# keskiarvo oli korkeampi kuin muiden tekijoiden ja painojen normalisointi
# veti pistetta ylospain - suojaisuus paalle teki kartasta vihreamman, ei
# punaisemman. 0,30 m on kajakille realistinen raja, jonka yli kallio-
# rantaan rantautuminen muuttuu hankalaksi.
WAVE_COEFF = 5.1e-4
SHELTER_CALM_M = 0.05
SHELTER_ROUGH_M = 0.30

# Kvantisoitujen fetch-tasojen metrimaarat KIINTEANA TAULUKKONA, ei
# exp/log-laskuna. Syy on tarkkuus: numpy ja selaimen JS kayttavat eri
# matematiikkakirjastoja, eivatka exp/log ole niissa taatusti bitilleen
# samoja. Taulukko + sqrt (joka on IEEE-tarkka molemmissa) pitaa Pythonin ja
# selaimen pistemaarat identtisina, mika on koko kynnysarvojarjestelman ehto.
# Arvot ovat dequantise_fetch(0..15) - jos MIN/MAX_FETCH_M tai FETCH_LEVELS
# muuttuu, tama taulukko on luotava uudelleen (ja sama JS-puolelle).
FETCH_LEVEL_METRES = np.array([
    50.0000, 73.1327, 106.9677, 156.4567,
    228.8420, 334.7165, 489.5742, 716.0773,
    1047.3729, 1531.9435, 2240.7024, 3277.3709,
    4793.6576, 7011.4594, 10255.3348, 15000.0000,
], dtype=np.float64)

FACTOR_SHELTER = 16
# Suojaisuuden mukaan ottava maski. ALL_FACTORS_MASK kasvaa 15 -> 31, joten
# TUULESTA RIIPPUMATTOMAT laskennat (compute_factor_thresholds,
# compute_prime_thresholds, compute_shoreline_stats) kayttavat
# NO_SHELTER_MASK:ia - muuten ne yrittaisivat laskea tuulesta riippuvia
# maskeja ilman tuulta.
NO_SHELTER_MASK = FACTOR_SLOPE | FACTOR_DIST | FACTOR_ROCK | FACTOR_SWAMP  # 15
ALL_FACTORS_MASK = NO_SHELTER_MASK | FACTOR_SHELTER  # 31
FACTOR_BITS["shelter"] = FACTOR_SHELTER

# Tuulelle altis ranta on melojalle yhta ratkaiseva kuin maaston jyrkkyys.
# Nostettu 0,40 -> 0,70 samasta syysta kuin SHELTER_ROUGH_M kiristettiin:
# 0,40 antoi suojaisuudelle vain 28,6 %:n osuuden viiden tekijan kesken,
# jolloin nollapistekaan ei pudottanut kokonaispistetta kuin 0,286.
SHELTER_WEIGHT = 0.70

def fetch_at_bearing(levels, bearing_deg):
    """Pyyhkaisymatka metreina mielivaltaiselle tuulen suunnalle: kahden
    lahimman sektorin lineaarinen interpolointi. Tama on mahdollista vasta
    sen jalkeen kun kynnysarvot irrotettiin tuulesta (ks.
    compute_shelter_thresholds) - aiemmin suunta oli pakko pyoristaa
    sektoriin, jotta esilasketut kynnykset vastasivat naytettya pistemaaraa.

    levels on taulukko jonka viimeinen ulottuvuus on FETCH_SECTORS."""
    step = 360.0 / FETCH_SECTORS
    pos = (bearing_deg % 360.0) / step
    lo = int(np.floor(pos)) % FETCH_SECTORS
    hi = (lo + 1) % FETCH_SECTORS
    frac = pos - np.floor(pos)
    a = FETCH_LEVEL_METRES[levels[..., lo]]
    b = FETCH_LEVEL_METRES[levels[..., hi]]
    return a + (b - a) * frac


# Este hiljentaa tuulta takanaan sita enemman mita korkeampi se on ja mita
# lahempana ollaan. Matala luoto ei siis tuota tuulensuojaa lainkaan, vaikka
# se katkaiseekin aallon - juuri tama erottelu puuttui aiemmin.
#
# Tarkistus lahtoarvoilla (0,6 ja 8): 2 m luoto 200 m paassa -> kerroin 1,00
# (ei suojaa); 10 m saari 200 m paassa -> 0,83; 10 m saari 50 m paassa -> 0,56.
WIND_SHELTER_MAX = 0.6
WIND_SHELTER_K = 8.0


def sheltered_wind(wind_speed, fetch_m, obstacle_h):
    """Esteen takana vaimentunut tuuli. Etaisyydeksi otetaan pyyhkaisymatkan
    PUOLIVALI, koska aallokko kasvaa koko matkan varrella eika vain rannassa."""
    h = np.maximum(np.asarray(obstacle_h, dtype=np.float64), 0.0)
    x = np.asarray(fetch_m, dtype=np.float64) / 2.0
    # h=0 (ei estetta tai aineiston reuna) -> ei vaimennusta.
    scale = WIND_SHELTER_K * np.maximum(h, 1e-9)
    factor = 1.0 - WIND_SHELTER_MAX * np.exp(-x / scale)
    return wind_speed * np.where(h > 0.0, factor, 1.0)


def shelter_score_from_fetch(fetch_m, wind_speed, obstacle_h=0.0):
    """Suojaisuuspistemaara 0-1 pyyhkaisymatkasta (metreina), tuulen
    nopeudesta ja esteen korkeudesta. **Selaimen
    (frontend/index.html: shelterScoreFromFetch) on laskettava tasan samoin.**

    HUOM: tassa on ainoa exp() koko jaetussa sopimuksessa, eivatka numpy ja
    JS takaa sille bitilleen samaa tulosta (ero luokkaa 1e-16). Se ei haittaa,
    koska KYNNYSARVOT lasketaan tuulennopeudella 0, jolloin koko
    tuulensuojatermi on merkityksetön - ristiintarkistus sallii siksi
    hiuksenhienon eron vain talta osin."""
    u_eff = sheltered_wind(wind_speed, fetch_m, obstacle_h)
    hs = WAVE_COEFF * u_eff * np.sqrt(fetch_m)
    return np.clip((SHELTER_ROUGH_M - hs) / (SHELTER_ROUGH_M - SHELTER_CALM_M), 0.0, 1.0)


def shelter_score_from_level(fetch_level, wind_speed, obstacle_h=0.0):
    """Kuten yllä, mutta kvantisoidusta tasosta. Selain interpoloi kahden
    sektorin valilla ja kayttaa siksi metriversiota - se on mahdollista
    koska kynnysarvot lasketaan TYYNESSA eivatka riipu tuulesta lainkaan
    (ks. compute_shelter_thresholds)."""
    return shelter_score_from_fetch(FETCH_LEVEL_METRES[np.asarray(fetch_level, dtype=np.int64)],
                                    wind_speed, obstacle_h)


def get_or_compute_fetch_png(tile_id, buildings_path, part="a", force=False):
    """Pyyhkaisymatkat kahtena kuvana per tiili. 12 sektoria a 4 bittia = 6
    tavua, jotka mahtuvat tasmalleen kahden kuvan RGB-kanaviin:

        part "a": R=[S0|S1] G=[S2|S3] B=[S4|S5]
        part "b": R=[S6|S7] G=[S8|S9] B=[S10|S11]

    Ylempi puolitavu on parillinen sektori. ALFA ON 255 KAIKKIALLA eika sita
    kayteta datalle: Canvas sailoo pikselit alfalla esikerrottuina, jolloin
    A<255 pyoristaisi arvoja ja A=0 nollaisi ne (sama syy joka pakotti
    tasapelinpurun omaan kuvaansa). Peittomaskina toimii factors-kuvan alfa,
    joka on selaimessa jo ladattu."""
    if part not in ("a", "b", "obsa", "obsb"):
        raise ValueError(f"Tuntematon part: {part}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}_fetch{part}.png"
    meta_path = CACHE_DIR / f"{tile_id}.json"
    if not force and png_path.exists() and meta_path.exists():
        return png_path.read_bytes(), json.loads(meta_path.read_text())

    if tile_id not in tiles.get_registry():
        raise KeyError(f"Tuntematon tile_id: {tile_id}")

    fetch_levels, obs_levels, _buffer = get_or_compute_fetch_levels(
        tile_id, buildings_path, force=force
    )
    levels = obs_levels if part.startswith("obs") else fetch_levels
    base = 6 if part.endswith("b") else 0
    chan = [
        (levels[:, :, base + 2 * i] << 4) | levels[:, :, base + 2 * i + 1]
        for i in range(3)
    ]
    r, g, b = chan
    a = np.full_like(r, 255)

    ok, encoded = cv2.imencode(".png", np.dstack([b, g, r, a]))
    if not ok:
        raise RuntimeError("PNG-enkoodaus epaonnistui")
    png_bytes = encoded.tobytes()

    raw = get_or_compute_raw(tile_id, buildings_path)
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
    if not meta_path.exists():
        meta_path.write_text(json.dumps(meta, indent=2))
    return png_bytes, meta


def compute_shelter_thresholds(buildings_path, force=False):
    """"Parhaat X %" -kynnysarvot niille tekijayhdistelmille joissa
    SUOJAISUUS on mukana: {"normal"|"prime": {"<maski>": {"<prosentti>": kynnys}}}.

    **Kynnys lasketaan TYYNESSA** (tuulennopeus 0, jolloin suojaisuuspiste on
    1 kaikkialla) ja sita sovelletaan sellaisenaan kaikkiin tuuliin. "Parhaat
    7 %" tarkoittaa siis "paikat jotka olisivat parhaan 7 %:n joukossa
    tyynella", ja tuuli voi vain PUDOTTAA paikkoja pois - ei koskaan tuoda
    lisaa.

    Aiemmin kynnys laskettiin erikseen jokaiselle tuulisuunnalle ja
    -nopeudelle, jolloin valittuja oli aina tasan X % saasta riippumatta.
    Valinta kylla kiristyi oikein (14 m/s:ssa valittujen pyyhkaisymatkan
    mediaani oli 50 m eli pienin mahdollinen), mutta koska MAARA ei
    muuttunut, kartta nayttti yhta taydelta myrskyssa kuin tyynella eika
    tuulen voimakkuus nakynyt kayttajalle lainkaan.

    Kaksi seurausta: kynnystiedosto kutistuu 493 kt -> parisen kilotavua, ja
    koska kynnys ei enaa riipu tuulesta, selaimen ei tarvitse pyoristaa
    tuulen suuntaa sektoriin eika nopeutta luokkaan - se voi interpoloida
    pyyhkaisymatkan sektorien valilla ja kayttaa tarkkaa nopeutta."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "_shelter_thresholds.json"
    if not force and cache_path.exists():
        return json.loads(cache_path.read_text())

    normal, prime, tiebreak = [], [], []
    for tid in tiilet_edistymisella("Suojaisuuskynnykset"):
        f = get_or_compute_factor_arrays(tid, buildings_path, force=force)
        p = get_or_compute_prime_arrays(tid, buildings_path, force=force)
        buf = f["buffer"]
        if not buf.any():
            continue
        normal.append((f["slope_b"][buf], f["dist_b"][buf], f["rock_bit"][buf], f["swamp_bit"][buf]))
        prime.append((p["slope_b"][buf], p["dist_b"][buf], p["rock_bit"][buf], p["swamp_bit"][buf]))
        tiebreak.append(f["tiebreak_b"][buf])

    def merge(parts):
        return tuple(np.concatenate(c) for c in zip(*parts))

    tb_all = np.concatenate(tiebreak)
    shelter_masks = [m for m in range(1, ALL_FACTORS_MASK + 1) if m & FACTOR_SHELTER]
    out = {"normal": {}, "prime": {}}
    for layer, parts in (("normal", normal), ("prime", prime)):
        s, d, r, w = merge(parts)
        for mask in shelter_masks:
            # Tuulennopeus 0 -> suojaisuuspiste 1 kaikkialla, jolloin
            # pyyhkaisymatkalla ei ole merkitysta ja kynnys kuvaa
            # nimenomaan tyynen olosuhteen parhaita.
            rank = rank_from_components(s, d, r, w, tb_all, mask,
                                        fetch_level=np.zeros(len(s), dtype=np.int64),
                                        wind_speed=0.0)
            out[layer][str(mask)] = {
                str(pct): float(np.percentile(rank, top_percent_to_percentile(pct)))
                for pct in TOP_PERCENT_PRESETS
            }

    cache_path.write_text(json.dumps(out))
    return out




# --- ESTEEN TEHOLLINEN KORKEUS ---
#
# Kaikki maa ei ole samanlainen este. Matala luoto katkaisee aallon mutta EI
# tuulta - sen takana tuulee yhta kovaa kuin edessa. Korkea, puustoinen saari
# taas hiljentaa myos tuulen. Erottelu vaatii esteen korkeuden.
#
# MML:n korkeusmalli on MAANPINTAMALLI: puusto ei sisally siihen lainkaan,
# joten matala mutta puustoinen saari nayttaisi luodolta. Kayttajan antama
# saanto: suurilla saarilla peruskartan valkoinen voi olla metsaa, pienilla
# ei. Kasvillisuuslisa annetaan siksi SAAREN KOON perusteella - ei
# varitunnistuksella, jota ei tassa saaristossa voi luottaa.
#
# Mitattu perustelu koolle: alle 0,1 ha luodot ovat mediaanikorkeudeltaan
# 0,9 m, 0,5-2 ha saaret 2,2 m ja yli 10 ha saaret 6,8 m. Saaren koko siis
# ennustaa korkeutta vahvasti. 900 maakomponentista 67 % on alle 1 ha, mutta
# ne kattavat vain 1,8 % maapinta-alasta.
#
# Paljas kallio ja avosuo eivat saa kasvillisuuslisaa vaikka olisivat
# suurella saarella - molemmat maskit ovat jo raakavalimuistissa, joten
# uutta varikynnysta ei tarvitse virittaa.
#
# ─── TAMA ARVAUS ON NYT VARAJARJESTELMA, EI PAALAHDE ───
#
# Kasvillisuuslisa on VAKIO, joka annetaan saaren koon perusteella. Se on
# kaytossa enaa siella minne laserkeilausta ei ole ladattu (ks. backend/lidar.py
# ja get_or_compute_height_mosaic alla) - mitattu latvusto voittaa aina.
#
# Miksi arvaus ei riittanyt: se on KAKSIHUIPPUINEN eika jatkuva. Mitattuna
# 54 % maaruuduista sai tasan 12,0 m ja 46 % tasan 0 m, kun mittaus antaa
# jatkuvan jakauman mediaanilla 7,5 m. Keskiarvon ero (+1,0 m) on siis kahden
# vastakkaisen korjauksen summa: 24 % ruuduista laski yli 3 m ja 38 % nousi
# yli 3 m. Pieni keskiarvomuutos ei tarkoita pienta muutosta.
#
# Virheen suuruus tuulensuojaan (sheltered_wind, 4 m vs 12 m puusto):
#   pyyhkaisymatka  50 m -> 26 % ero tuulennopeudessa
#                  100 m -> 26 %
#                  200 m -> 19 %
#                  400 m ->  7 %
# Virhe on suurin 50-200 m matkoilla eli tasan niissa suojaisissa poukamissa
# joihin rantaudutaan.
#
# Korvaaja on MML:n laserkeilausaineisto (0,5 p/m2, CC BY 4.0, EPSG:3067 eli
# sama koordinaatisto kuin talla projektilla). Aineisto on valmiiksi
# luokiteltu: 2 = maanpinta, 3 = matala kasvillisuus (0-0,5 m), 4 = keskikorkea
# (0,5-2 m), 5 = korkea (2-50 m). Tiheys riittaa hyvin: 10 m ruudussa on
# ~50 pistetta ja maksimi tarvitsee vain muutaman. Todennettu maanpintaa
# vastaan: LiDARin maanpinta vs DEM mediaani +0,13 m, 99 % alle 1 m erolla.
#
# Arvaus on eristetty omaksi funktiokseen (vegetation_height_m), jotta
# sekakaytto olisi luettavissa yhdesta kohtaa: uudelle alueelle riittaa ladata
# keilaus, eika muuhun putkeen tarvitse koskea.
#
# HUOM vuodenajasta: LiDAR mittaa KORKEUDEN oikein vuodenajasta riippumatta,
# koska lehdeton puu on yhta korkea kuin lehtipuinen. Kausivaihtelu koskee
# latvuston HUOKOISUUTTA, jota tama malli ei esita lainkaan - suojan
# voimakkuus on kiintea WIND_SHELTER_MAX = 0,6. Sovellus on kesakayttoon ja
# 0,6 vastaa jokseenkin tiheaa kesalatvustoa, joten kausivaihtelu ei ole
# esteena tuulimallin parantamiselle. Se olisi olennainen vasta jos
# aluskasvillisuus otettaisiin rantautumiskelpoisuuden tekijaksi.
MIN_VEG_ISLAND_HA = 1.0
VEG_HEIGHT_M = 12.0


def vegetation_height_m(sea, veg_ok, land):
    """Kasvillisuuden lisa esteen korkeuteen (m) ruuduittain - ARVAUS saaren
    koon perusteella.

    Kaytetaan enaa VARALLA: get_or_compute_height_mosaic lisaa taman vain
    niihin ruutuihin joissa laserkeilausta ei ole. Palauttaa saman muotoisen
    taulukon kuin maski; nollaa siella missa kasvillisuutta ei oleteta.

    Ks. yllaoleva kommentti siita, miten kaksihuippuinen tama arvaus on
    mitattuun latvustoon verrattuna."""
    labels, n = ndimage_label(land, structure=np.ones((3, 3), dtype=bool))
    if not n:
        return np.zeros(sea.shape, dtype=np.float32)
    sizes = np.bincount(labels.ravel())
    min_cells = MIN_VEG_ISLAND_HA * 10000.0 / (FETCH_GRID_M ** 2)
    big = np.zeros(len(sizes), dtype=bool)
    big[1:] = sizes[1:] >= min_cells
    return VEG_HEIGHT_M * (big[labels] & veg_ok & land)


def get_or_compute_height_mosaic(buildings_path, force=False):
    """Esteen tehollinen korkeus (m) samalla karkealla ruudukolla kuin
    merimosaiikki. Ruudun arvo on DEM:n MAKSIMI ruudussa - este on
    korkeimman kohtansa mittainen tuulen kannalta - plus mahdollinen
    kasvillisuuslisa."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "_height_mosaic.npy"
    if not force and cache_path.exists():
        # Sama geometriatarkistus kuin merimosaiikilla - ne jakavat ruudukon.
        # Muoto luetaan mmapilla, jottei gigatavun taulukkoa ladata pelkan
        # tarkistuksen takia.
        _o, muoto = _sea_mosaic_geometry()
        kurkistus = np.load(cache_path, mmap_mode="r")
        if kurkistus.shape == muoto:
            del kurkistus
            return np.load(cache_path)
        del kurkistus
        print(f"  korkeusmosaiikki: tiilisto muuttunut, rakennetaan uudelleen",
              flush=True)

    sea, origo = get_or_compute_sea_mosaic(force=force)
    height = _height_mosaic_for(list(tiles.get_registry()), sea, origo,
                                buildings_path, force=force)
    np.save(cache_path, height)
    return height


def _height_mosaic_for(tile_ids, sea, origin, buildings_path, force=False):
    """Korkeusmosaiikin runko annetuille tiilille ja valmiille merimaskille.

    Erotettu omaksi funktiokseen, jotta alueellinen laskenta voi kayttaa sita
    ilman levyvalimuistia (ks. _alueen_mosaiikit)."""
    ox, oy = origin
    h, w = sea.shape
    height = np.zeros((h, w), dtype=np.float32)
    veg_ok = np.zeros((h, w), dtype=bool)

    for tid in tile_ids:
        tile = tiles.get_registry()[tid]
        dem, transform, _crs, nodata, pixel_size = score_engine.read_dem(str(tile.dem_path))
        dem = np.where(dem == nodata, 0.0, dem).astype(np.float32)
        factor = int(round(FETCH_GRID_M / pixel_size))
        hh, ww = dem.shape[0] // factor, dem.shape[1] // factor
        # Maksimi ruudussa (reshape-lohkotus on nopein tapa)
        dem_small = dem[:hh * factor, :ww * factor].reshape(hh, factor, ww, factor).max(axis=(1, 3))

        raw = get_or_compute_raw(tile.tile_id, buildings_path, force=force)
        native_factor = int(round(FETCH_GRID_M / abs(raw["map_transform"].a)))
        bare = raw["rock_mask"] | raw["swamp_mask"]
        bh, bw = bare.shape[0] // native_factor, bare.shape[1] // native_factor
        bare_small = (bare[:bh * native_factor, :bw * native_factor]
                      .reshape(bh, native_factor, bw, native_factor).mean(axis=(1, 3))) >= 0.5

        col = int(round((tile.bounds[0] - ox) / FETCH_GRID_M))
        row = int(round((oy - tile.bounds[3]) / FETCH_GRID_M))
        hh2, ww2 = min(hh, bh), min(ww, bw)
        target = (slice(row, row + hh2), slice(col, col + ww2))
        height[target] = np.maximum(height[target], dem_small[:hh2, :ww2])
        veg_ok[target] |= ~bare_small[:hh2, :ww2]

    # --- MITATTU LATVUSTO ARVAUKSEN TILALLE ---
    #
    # Missa laserkeilaus on kaytettavissa, esteen huippu otetaan SUORAAN
    # mittauksesta: se on jo N2000-korkeus eli sama vertailutaso kuin DEM:lla,
    # joten latvuskorkeutta ei tarvitse laskea erikseen eika maanpinnan
    # vertailutasoa valita. Maksimi DEM:n kanssa on turvaverkko: jos keilaus
    # on jostain syysta matalampi kuin maastomalli, maasto voittaa.
    #
    # Arvaus jaa voimaan vain siella minne mittausta ei ole. Mitattuna arvaus
    # yliarvioi keskimaarin +4,2 m ja yli 6 m 41 %:ssa ruuduista, joten
    # sekakaytto on tarkoituksella jarjestetty niin etta MITTAUS VOITTAA aina
    # kun se on olemassa.
    land = ~sea
    mitattu = np.zeros(sea.shape, dtype=bool)
    for tid in tile_ids:
        tile = tiles.get_registry()[tid]
        if not lidar.have_lidar(tile.tile_id):
            continue
        top, ok = lidar.surface_top(tile.tile_id)
        col = int(round((tile.bounds[0] - ox) / FETCH_GRID_M))
        row = int(round((oy - tile.bounds[3]) / FETCH_GRID_M))
        hh2, ww2 = min(top.shape[0], h - row), min(top.shape[1], w - col)
        if hh2 <= 0 or ww2 <= 0:
            continue
        dst = (slice(row, row + hh2), slice(col, col + ww2))
        src = (slice(0, hh2), slice(0, ww2))
        kelpo = ok[src] & land[dst]
        height[dst] = np.where(kelpo, np.maximum(height[dst], top[src]), height[dst])
        mitattu[dst] |= kelpo

    # Arvaus vain mittaamattomiin ruutuihin.
    height = height + np.where(mitattu, 0.0, vegetation_height_m(sea, veg_ok, land))

    return height.astype(np.float32)



# Yksi sade per sektori antoi pienelle luodolle liian pitkan suojan: 30 m
# levea luoto 200 m paassa peittaa vain noin 8 astetta, mutta yksi sade
# katkesi siihen kuin se olisi peittanyt koko sektorin. Aallokko saapuu
# leveammalta sektorilta sen ohi ja taitse.
#
# Korvattu SAVILLEN TEHOLLISELLA PYYHKAISYMATKALLA, joka on merenkulun
# vakiokaytanto: jokaiselle sektorille lasketaan 13 sadetta +-45 asteen
# haarukassa ja niista painotettu keskiarvo
#
#     F_eff = summa(F_i * cos^2 a_i) / summa(cos a_i)
#
# Kapea luoto poistaa naista vain yhden tai kaksi, jolloin tehollinen matka
# pysyy pitkana; levea saari peittaa kaikki. Tama on samalla halvempi kuin
# aallon lapaisyn mallintaminen, koska tallennettava data ei kasva.
#
# Sama sadehaarukka antaa esteen korkeuden: se painotetaan samoin, jolloin
# yhden kapean sateen taakse jaava korkea saari ei tuota tuulensuojaa
# leveasti - mika on fysikaalisesti oikein.
RAY_SPREAD_DEG = 45.0
RAY_STEP_DEG = 7.5
RAY_OFFSETS = np.arange(-RAY_SPREAD_DEG, RAY_SPREAD_DEG + 0.001, RAY_STEP_DEG)

# Esteen korkeus luetaan vasta osuman TAKAA: rantaviiva itsessaan on aina
# matala, ja pelkka osumapiste antoi mittauksissa mediaaniksi 1,1 m kun
# oikea arvo on 4,1 m.
OBSTACLE_LOOKAHEAD_M = 100.0
OBSTACLE_MAX_M = 20.0
OBSTACLE_LEVELS = 16


def quantise_obstacle(height_m):
    """Esteen korkeus -> 0..OBSTACLE_LEVELS-1, LINEAARISESTI (toisin kuin
    pyyhkaisymatka): tuulensuoja riippuu korkeudesta suunnilleen lineaarisesti
    eika tarvitse logaritmista tarkkuutta matalassa paassa."""
    t = np.clip(height_m, 0.0, OBSTACLE_MAX_M) / OBSTACLE_MAX_M
    return np.clip(np.rint(t * (OBSTACLE_LEVELS - 1)), 0, OBSTACLE_LEVELS - 1).astype(np.uint8)


def dequantise_obstacle(level):
    """quantise_obstacle:n kaanteisfunktio - selain laskee tasan saman
    (pelkkaa kerto- ja jakolaskua, joten bitilleen sama molemmissa)."""
    return np.asarray(level, dtype=np.float64) * (OBSTACLE_MAX_M / (OBSTACLE_LEVELS - 1))


def _march_ray(rows, cols, sea, height, bearing_deg, max_fetch_m):
    """Yksi sade: palauttaa (pyyhkaisymatka, esteen korkeus) jokaiselle
    pisteelle. Ks. vaiheiden A ja B selitys alla - sade lahtee maalta, joten
    ensin on edettava veteen."""
    h, w = sea.shape
    n = len(rows)
    steps = int(round(max_fetch_m / FETCH_GRID_M))
    look = int(round(OBSTACLE_LOOKAHEAD_M / FETCH_GRID_M))
    bearing = np.radians(bearing_deg)
    dr, dc = -np.cos(bearing), np.sin(bearing)

    # --- TYOJOUKKO TIIVISTETAAN KUN SATEET VALMISTUVAT ---
    #
    # Mitattuna 100 askeleen jalkeen enaa 22 % sateista on matkalla ja 800
    # askeleen jalkeen 6,5 %, mutta silmukka indeksoi 1 500 askelta. Ilman
    # tiivistysta noin 90 % taulukko-operaatioista tehdaan sateille jotka ovat
    # jo osuneet maahan.
    #
    # Tama on myos syy siihen miksi rinnakkaistaminen ei auttanut: mitattuna
    # kahdeksan prosessia kaytti 687 % CPU:sta mutta nopeutti vain 1,5x -
    # ytimet olivat varattuja mutta tekivat turhaa tyota.
    #
    # "live" on indeksit ALKUPERAISIIN taulukoihin. Kaikki tyoarrayt ovat
    # tyojoukon mittaisia, ja tulokset kirjoitetaan livein kautta.
    live = np.arange(n)
    r0 = np.ascontiguousarray(rows)
    c0 = np.ascontiguousarray(cols)
    entered = np.zeros(n, dtype=bool)
    start_k = np.zeros(n, dtype=np.int64)
    fetch = np.full(n, MIN_FETCH_M, dtype=np.float32)
    obstacle = np.zeros(n, dtype=np.float32)
    seuraava_tiivistys = n // 2

    for k in range(1, steps + 1):
        dk_r, dk_c = int(round(k * dr)), int(round(k * dc))
        rr = r0 + dk_r
        cc = c0 + dk_c
        valid = (rr >= 0) & (rr < h) & (cc >= 0) & (cc < w)
        # Aineiston ulkopuoli tulkitaan mereksi (ks. moduulin kommentti).
        is_sea = ~valid
        if valid.any():
            is_sea[valid] = sea[rr[valid], cc[valid]]

        newly = ~entered & is_sea
        if newly.any():
            entered |= newly
            start_k[newly] = k

        pidä = None
        if k == MAX_INITIAL_LAND_STEPS:
            # Sade joka ei ole viela vedessa osoittaa sisamaahan. Se pudotetaan
            # ja jaa oletusarvoonsa MIN_FETCH_M.
            pidä = entered

        hit = entered & ~is_sea
        if hit.any():
            osuneet = live[hit]
            fetch[osuneet] = (k - start_k[hit]) * FETCH_GRID_M
            # Esteen katse VAIN osumille - aiemmin tama laskettiin koko
            # tyojoukolle ja indeksoitiin vasta lopuksi.
            rh, ch = r0[hit], c0[hit]
            hv = np.zeros(len(osuneet), dtype=np.float32)
            for j in range(look + 1):
                r2 = np.clip(rh + int(round((k + j) * dr)), 0, h - 1)
                c2 = np.clip(ch + int(round((k + j) * dc)), 0, w - 1)
                np.maximum(hv, height[r2, c2], out=hv)
            obstacle[osuneet] = hv
            pidä = ~hit if pidä is None else (pidä & ~hit)

        if pidä is not None:
            live = live[pidä]
            if len(live) == 0:
                break
            r0, c0 = r0[pidä], c0[pidä]
            entered, start_k = entered[pidä], start_k[pidä]
            seuraava_tiivistys = min(seuraava_tiivistys, len(live))

    # Maahan osumattomat sateet kulkevat aineiston ulkopuolelle tai
    # avomerelle: ne saavat KATON eivatka oletusarvoa, ja este on 0.
    fetch[live] = max_fetch_m
    return np.clip(fetch, MIN_FETCH_M, max_fetch_m), obstacle


# Rinnakkaisajon konteksti. Asetetaan ENNEN prosessien luontia, jolloin fork
# jakaa isot taulukot copy-on-writena eika niita tarvitse siirtaa.
_MARCH_CTX = None


def _march_ray_worker(bearing_deg):
    rows, cols, sea, height, max_fetch_m = _MARCH_CTX
    return _march_ray(rows, cols, sea, height, bearing_deg, max_fetch_m)


def _sateiden_tyontekijat():
    """Rinnakkaisten prosessien maara, 1 = sarjassa."""
    import multiprocessing
    if "fork" not in multiprocessing.get_all_start_methods():
        return 1                      # spawn kopioisi mosaiikit joka prosessiin
    return max(1, min(os.cpu_count() or 1, 16))



def compute_fetch_and_obstacle(points_rc, sea, height, max_fetch_m=MAX_FETCH_M):
    """Tehollinen pyyhkaisymatka ja esteen korkeus jokaiselle pisteelle ja
    sektorille: (n, FETCH_SECTORS) x 2."""
    rows, cols = points_rc
    n = len(rows)
    fetch_out = np.zeros((n, FETCH_SECTORS), dtype=np.float32)
    obs_out = np.zeros((n, FETCH_SECTORS), dtype=np.float32)

    cos1 = np.cos(np.radians(RAY_OFFSETS))
    cos2 = cos1 ** 2
    denom_f = cos1.sum()
    denom_h = cos2.sum()

    # Sama suunta toistuu eri sektoreissa, joten sateet lasketaan kerran
    # per uniikki suunta ja yhdistetaan sen jalkeen.
    bearings = {}
    for sector in range(FETCH_SECTORS):
        for off in RAY_OFFSETS:
            bearings.setdefault(round((sector_bearing(sector) + off) % 360.0, 3), None)
    # --- RINNAKKAISAJO ILMANSUUNNITTAIN ---
    #
    # Suunnat ovat toisistaan riippumattomia. Rinnakkaisuus EI auttanut ennen
    # tyojoukon tiivistysta (mitattuna 8 prosessia = 687 % CPU mutta vain
    # 1,5x nopeutus, koska ytimet tekivat turhaa tyota); tiivistyksen jalkeen
    # sama mittaus antaa 4,2x.
    #
    # fork jakaa merimaskin ja korkeusmallin copy-on-writena, joten gigatavun
    # taulukoita ei kopioida. Muilla alustoilla (spawn) kopiointi maksaisi
    # enemman kuin rinnakkaisuus tuo, joten silloin ajetaan sarjassa.
    import sys
    import time as _time
    t0 = _time.perf_counter()
    yht = len(bearings)
    suunnat = list(bearings)

    def _raportoi(i):
        kulunut = _time.perf_counter() - t0
        jaljella = kulunut / i * (yht - i)
        print(f"      suunta {i:2d}/{yht}  {kulunut / 60:5.1f} min kulunut, "
              f"n. {jaljella / 60:5.1f} min jaljella", flush=True)

    tyontekijat = _sateiden_tyontekijat()
    if tyontekijat > 1:
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        global _MARCH_CTX
        _MARCH_CTX = (rows, cols, sea, height, max_fetch_m)
        try:
            with ProcessPoolExecutor(max_workers=tyontekijat,
                                     mp_context=multiprocessing.get_context("fork")) as ex:
                for i, (b, tulos) in enumerate(
                        zip(suunnat, ex.map(_march_ray_worker, suunnat, chunksize=1)), 1):
                    bearings[b] = tulos
                    if i % max(yht // 8, 1) == 0 or i == yht:
                        _raportoi(i)
        finally:
            _MARCH_CTX = None
    else:
        for i, b in enumerate(suunnat, 1):
            bearings[b] = _march_ray(rows, cols, sea, height, b, max_fetch_m)
            if i % max(yht // 8, 1) == 0 or i == yht:
                _raportoi(i)
        sys.stdout.flush()

    for sector in range(FETCH_SECTORS):
        acc_f = np.zeros(n, dtype=np.float64)
        acc_h = np.zeros(n, dtype=np.float64)
        for off, c1, c2 in zip(RAY_OFFSETS, cos1, cos2):
            f_i, h_i = bearings[round((sector_bearing(sector) + off) % 360.0, 3)]
            acc_f += f_i * c2
            acc_h += h_i * c2
        fetch_out[:, sector] = acc_f / denom_f
        obs_out[:, sector] = acc_h / denom_h

    return np.clip(fetch_out, MIN_FETCH_M, max_fetch_m), obs_out


# Valimuistin muoto. Nosta kun tallennettu sisalto ei ole enaa vertailukelpoista
# aiemman kanssa - silloin koko laskenta uusitaan automaattisesti.
#   2 = globaalit solutunnisteet + MOSAIC_PAD_M
GLOBAL_CACHE_VERSION = 2


def _dirty_mask(gid, muuttuneet_tiilet):
    """Mitka solut on laskettava uudelleen kun tiilijoukko muuttuu.

    Vain ne jotka ovat alle MAX_FETCH_M etaisyydella muuttuneesta tiilesta:
    kauempaa sade ei nae uutta maata eika mosaiikin laajeneminen vaikuta,
    koska mosaiikki ulottuu MOSAIC_PAD_M verran tiilien ohi (ks. siella)."""
    if not muuttuneet_tiilet:
        return np.zeros(len(gid), dtype=bool)
    x, y = global_cell_coords(gid)
    likainen = np.zeros(len(gid), dtype=bool)
    for b in muuttuneet_tiilet:
        dx = np.maximum.reduce([b[0] - x, np.zeros_like(x), x - b[2]])
        dy = np.maximum.reduce([b[1] - y, np.zeros_like(y), y - b[3]])
        likainen |= (dx * dx + dy * dy) <= MAX_FETCH_M ** 2
    return likainen


def _global_inkrementaalisesti(cache_path, gid, sea, height, origin, otsikko,
                               buildings_path):
    """Yhteinen runko pyyhkaisymatkojen ja vesiruudukon laskennalle.

    Laskee vain ne solut joita ei ole valimuistissa tai jotka ovat muuttuneen
    tiilen lahella. Ilman tata koko globaali laskenta uusittaisiin joka kerta
    kun tiilia lisataan, jolloin kasvava aineisto maksaa neliollisesti.

    MUUTTUNUT tarkoittaa seka lisattya/poistettua tiilta ETTA tiilta jonka
    lahdeaineisto on paivittynyt (ks. lahde_sormenjalki). Ilman jalkimmaista
    MML:n uusi karttalehti ei koskaan paivittaisi pyyhkaisymatkoja."""
    registry = tiles.get_registry()
    nyt_tiilet = {tid: t.bounds for tid, t in registry.items()}
    nyt_sormenjaljet = {tid: lahde_sormenjalki(t, buildings_path)
                        for tid, t in registry.items()}

    vanha_gid = vanha_fetch = vanha_obs = None
    muuttuneet = list(nyt_tiilet.values())          # oletus: kaikki
    if cache_path.exists():
        d = np.load(cache_path, allow_pickle=True)
        if int(d.get("versio", 0)) == GLOBAL_CACHE_VERSION:
            vanha_gid = d["cells"]
            vanha_fetch, vanha_obs = d["fetch"], d["obstacle"]
            oli = set(str(s) for s in d["tiilet"])
            nyt = set(nyt_tiilet)
            vanhat_sj = {}
            if "sormenjaljet" in d.files:
                vanhat_sj = dict(zip((str(x) for x in d["tiilet"]),
                                     (str(x) for x in d["sormenjaljet"])))
            muuttuneet_idt = set(nyt - oli)
            for tid in nyt & oli:
                if vanhat_sj.get(tid) != nyt_sormenjaljet[tid]:
                    muuttuneet_idt.add(tid)
            muuttuneet = [nyt_tiilet[t] for t in muuttuneet_idt]
            if muuttuneet_idt:
                print(f"    muuttuneita tiilia: {len(muuttuneet_idt)}", flush=True)
            if oli - nyt:
                muuttuneet = list(nyt_tiilet.values())   # poisto: varmin on laskea kaikki

    # Tulos on vanhan ja pyydetyn joukon YHDISTE, ei pelkka pyydetty. Ilman
    # tata alueittain ajettu laskenta kirjoittaisi vain viimeisen alueen solut
    # ja pyyhkisi muut pois.
    if vanha_gid is not None and len(vanha_gid):
        kaikki_gid = np.union1d(vanha_gid, gid)
    else:
        kaikki_gid = gid

    fetch = np.zeros((len(kaikki_gid), FETCH_SECTORS), dtype=np.float32)
    obstacle = np.zeros((len(kaikki_gid), FETCH_SECTORS), dtype=np.float32)

    # Lasketaan vain PYYDETYT solut; muut kopioidaan vanhasta sellaisenaan.
    pyydetty = np.isin(kaikki_gid, gid, assume_unique=True)
    laskettava = pyydetty.copy()
    if vanha_gid is not None and len(vanha_gid):
        pos = np.searchsorted(vanha_gid, kaikki_gid)
        pos = np.clip(pos, 0, len(vanha_gid) - 1)
        loytyi = vanha_gid[pos] == kaikki_gid
        fetch[loytyi] = vanha_fetch[pos[loytyi]]
        obstacle[loytyi] = vanha_obs[pos[loytyi]]
        laskettava = pyydetty & (~loytyi | _dirty_mask(kaikki_gid, muuttuneet))
    gid = kaikki_gid

    n = int(laskettava.sum())
    print(f"  {otsikko}: {len(gid)} ruutua, laskettava {n} "
          f"({100.0 * n / max(len(gid), 1):.0f} %)")
    if n:
        rows, cols = _gid_to_mosaic_rc(gid[laskettava], origin, sea.shape)
        f, o = compute_fetch_and_obstacle((rows, cols), sea, height)
        fetch[laskettava] = f
        obstacle[laskettava] = o

    jarjestys = sorted(nyt_tiilet)
    np.savez_compressed(cache_path, cells=gid, fetch=fetch, obstacle=obstacle,
                        tiilet=np.array(jarjestys, dtype=object),
                        sormenjaljet=np.array([nyt_sormenjaljet[t] for t in jarjestys],
                                              dtype=object),
                        versio=GLOBAL_CACHE_VERSION)
    return gid, fetch, obstacle



def _alueittain(cache_path, solut_fn, buildings_path, force, otsikko):
    """Ajaa globaalin laskennan alue kerrallaan ja yhdistaa tuloksen.

    Yhdella alueella (aineisto mahtuu yhteen mosaiikkiin) kaytetaan
    levyvalimuistitettuja mosaiikkeja, jolloin kaytos on tasan sama kuin
    ennen alueellistamista. Useammalla alueella mosaiikit rakennetaan
    lennossa - muuten koko rannikon korkeusmalli olisi 11 Gt."""
    alueet = _laskenta_alueet()
    if len(alueet) == 1:
        ydin, _konteksti = alueet[0]
        sea, origo = get_or_compute_sea_mosaic(force=force)
        height = get_or_compute_height_mosaic(buildings_path, force=force)
        return _global_inkrementaalisesti(cache_path, solut_fn(ydin), sea, height,
                                          origo, otsikko, buildings_path)

    print(f"  {otsikko}: aineisto jaetaan {len(alueet)} alueeseen "
          f"(mosaiikki ei mahdu kerralla muistiin)", flush=True)
    for i, (ydin, konteksti) in enumerate(alueet, 1):
        print(f"  alue {i}/{len(alueet)}: {len(ydin)} tiilta ydinta, "
              f"{len(konteksti)} kontekstia", flush=True)
        sea, height, origo = _alueen_mosaiikit(konteksti, buildings_path)
        gid = solut_fn(ydin)
        _global_inkrementaalisesti(cache_path, gid, sea, height, origo,
                                   f"{otsikko} alue {i}", buildings_path)
        del sea, height
    d = np.load(cache_path, allow_pickle=True)
    return d["cells"], d["fetch"], d["obstacle"]


def get_or_compute_fetch_global(buildings_path, force=False):
    """Laskee pyyhkaisymatkat ja esteiden korkeudet KAIKKIEN tiilien
    rantaruuduille. Globaali siksi, etta sade kulkee tiilirajojen yli - ja
    koska sadehaarukka on 13-kertainen, sama suunta kannattaa laskea vain
    kerran koko aineistolle.

    Tulos on inkrementaalinen: tiilien lisaaminen laskee uudelleen vain
    lisayksen laheiset ruudut."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "_fetch_global.npz"
    if force and cache_path.exists():
        cache_path.unlink()

    def solut(tile_ids):
        osat = [_tile_mosaic_cells(t, buildings_path, force=force)[0] for t in tile_ids]
        return np.unique(np.concatenate(osat)) if osat else np.array([], dtype=np.int64)

    return _alueittain(cache_path, solut, buildings_path, force, "rantaruudut")


def _tile_mosaic_cells(tile_id, buildings_path, force=False):
    """Tiilen naytettavien ruutujen GLOBAALIT solutunnisteet.

    Palauttaa (gid, (rr, cc, muoto)). Tunniste ei riipu mosaiikin muodosta,
    joten valimuisti kestaa tiilien lisaamisen - ks. global_cell_ids."""
    factors = get_or_compute_factor_arrays(tile_id, buildings_path, force=force)
    buffer_small = factors["buffer"]
    raw = factors["raw"]
    transform = raw["map_transform"]
    native_h, native_w = raw["score"].shape
    small_h, small_w = buffer_small.shape

    rr, cc = np.nonzero(buffer_small)
    x = transform.c + (cc + 0.5) * (native_w / small_w) * abs(transform.a)
    y = transform.f - (rr + 0.5) * (native_h / small_h) * abs(transform.e)
    return global_cell_ids(x, y), (rr, cc, buffer_small.shape)


def _gid_to_mosaic_rc(gid, origin, mosaic_shape):
    """Globaalit tunnisteet -> nykyisen mosaiikin (rivi, sarake).

    Mosaiikki kattaa tiilijoukon plus MOSAIC_PAD_M, joten tiilien ruudut ovat
    aina sisalla; clip on silti varmuuden vuoksi eika hiljainen oletus."""
    ox, oy = origin
    x, y = global_cell_coords(gid)
    col = np.clip(np.rint((x - ox) / FETCH_GRID_M).astype(np.int64), 0, mosaic_shape[1] - 1)
    row = np.clip(np.rint((oy - y) / FETCH_GRID_M).astype(np.int64), 0, mosaic_shape[0] - 1)
    return row, col


def get_or_compute_fetch_levels(tile_id, buildings_path, force=False):
    """Kvantisoidut pyyhkaisymatkat JA esteiden korkeudet tiilen
    selainruudukolla (ks. NEW_PIXEL_FACTOR) - tasan ne arvot jotka selain
    lukee kuvista. Palauttaa (fetch_levels, obstacle_levels, buffer)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{tile_id}_fetch.npz"

    factors = get_or_compute_factor_arrays(tile_id, buildings_path, force=force)
    buffer_small = factors["buffer"]
    if not force and cache_path.exists():
        data = np.load(cache_path)
        return data["fetch"], data["obstacle"], buffer_small

    # Ei mosaiikkia: solutunnisteet lasketaan koordinaateista, joten tama
    # toimii riippumatta siita miten iso aineisto on.
    cells, fetch_all, obs_all = get_or_compute_fetch_global(buildings_path, force=force)
    flat, (rr, cc, shape) = _tile_mosaic_cells(tile_id, buildings_path, force=force)

    pos = np.searchsorted(cells, flat)
    # Jokaisen tiilen ruudun ON loydyttava globaalista joukosta - se on koottu
    # tasan naista. Jos ei loydy, indeksointi antaisi vaaran naapurin arvot
    # HILJAA, joten se tarkistetaan.
    if len(cells) == 0 or not np.array_equal(cells[np.clip(pos, 0, len(cells) - 1)], flat):
        raise RuntimeError(
            f"{tile_id}: {int((cells[np.clip(pos, 0, len(cells) - 1)] != flat).sum())} "
            "ruutua ei loydy globaalista joukosta - valimuisti on epasynkassa")
    fetch_levels = np.zeros((*shape, FETCH_SECTORS), dtype=np.uint8)
    obs_levels = np.zeros((*shape, FETCH_SECTORS), dtype=np.uint8)
    fetch_levels[rr, cc] = quantise_fetch(fetch_all[pos])
    obs_levels[rr, cc] = quantise_obstacle(obs_all[pos])

    np.savez_compressed(cache_path, fetch=fetch_levels, obstacle=obs_levels)
    return fetch_levels, obs_levels, buffer_small


# --- MELOTTAVUUS: OLOSUHTEET VEDEN PAALLA ---
#
# Tahan asti arvioitiin vain rantaa. Matkamelojalle olennainen kysymys on
# kuitenkin, paaseeko sinne: merenselan ylitys 12 m/s puuskissa on eri asia
# kuin sama matka suojaisia salmia pitkin.
#
# Sama pyyhkaisymatkakoneisto (compute_fetch_and_obstacle) kelpaa
# sellaisenaan - vain pistejoukko vaihtuu rantaruuduista vesiruutuihin.
#
# OMA KARKEAMPI RUUDUKKO: merta on mosaiikissa 84,7 % eli 4,6 miljoonaa
# 10 m ruutua, mika olisi liikaa. Vesipisteet otetaan 50 m valein, jolloin
# niita on saman verran kuin rantaruutuja. Aallokkokentta on veden paalla
# sileä, joten 50 m riittaa hyvin - ja tallennettavat kuvat ovat vain
# 120x120 ruutua per tiili.
WATER_GRID_M = 50.0

# Melonnan vaikeusrajat. AALLOKKO ja PUUSKA pidetaan erillaan, koska ne
# vaikuttavat eri asioihin: aallokko tulee jatkuvasta tuulesta ja maaraa
# veneen liikkeen, puuska maaraa kasiteltavyyden (kaatumisriski, kurssissa
# pysyminen). Sama 0,4 m aallokko on eri asia tasaisessa 8 m/s tuulessa kuin
# 6 m/s tuulessa jossa on 13 m/s puuskia.
#
# Vaikeus on HUONOMMAN mukaan: kumpi tahansa yksin riittaa tekemaan
# melonnasta vaativaa.
#
# Rajat ovat arvioita eivatka julkaistu standardi - kalibroitava mittaamalla
# kuten suojaisuuden parametrit.
PADDLE_WAVE_LIMITS = (0.20, 0.40, 0.70)   # helppo | kohtalainen | vaativa | ei suositella
PADDLE_GUST_LIMITS = (8.0, 12.0, 16.0)
PADDLE_CLASSES = ("helppo", "kohtalainen", "vaativa", "ei suositella")


def _piecewise_class(value, limits):
    """Jatkuva 0-3 luokka-asteikko annetuilla rajoilla: 0 kun arvo on 0,
    1/2/3 rajojen kohdalla, valissa lineaarinen. Jatkuva eika porrastettu,
    jotta kartasta tulee sileä eika laikukas."""
    v = np.asarray(value, dtype=np.float64)
    a, b, c = limits
    out = np.where(v < a, v / a,
          np.where(v < b, 1.0 + (v - a) / (b - a),
          np.where(v < c, 2.0 + (v - b) / (c - b),
                   3.0)))
    return np.clip(out, 0.0, 3.0)


def paddle_difficulty(fetch_m, wind_speed, gust_speed, obstacle_h, met_wave_m=None):
    """Melonnan vaikeus 0-1 (0 = helppo, 1 = ei suositella).
    **Selaimen (frontend/index.html: paddleDifficulty) on laskettava tasan
    samoin.**

    met_wave_m: MET Norwayn aallokkoennuste, jos saatavilla. Sekoitetaan oman
    mallin arvioon painolla joka kertoo kuinka lahella pyyhkaisymatkan KATTOA
    ollaan: suojaisassa poukamassa luotetaan omaan 10 m malliin (MET:n
    muutaman kilometrin hila ei nae sinne), avomerella MET:iin, koska oma
    malli katkaisee matkan MAX_FETCH_M:iin ja aliarvioi siella aallokkoa
    n. 25 % (mitattu MET:ia vastaan)."""
    u_eff = sheltered_wind(wind_speed, fetch_m, obstacle_h)
    hs = WAVE_COEFF * u_eff * np.sqrt(fetch_m)
    if met_wave_m is not None:
        w = np.minimum(np.asarray(fetch_m, dtype=np.float64) / MAX_FETCH_M, 1.0)
        hs = (1.0 - w) * hs + w * met_wave_m
    gust_eff = sheltered_wind(gust_speed, fetch_m, obstacle_h)
    wave = _piecewise_class(hs, PADDLE_WAVE_LIMITS)
    gust = _piecewise_class(gust_eff, PADDLE_GUST_LIMITS)
    return np.maximum(wave, gust) / 3.0


def _tile_water_mask(tile):
    """Tiilen vesiruudukon (WATER_GRID_M) merimaski ja koko.

    Rasteroidaan suoraan vektoriaineistosta eika lueta globaalista
    mosaiikista: nain per-tiili -laskenta ei riipu siita kuinka laaja koko
    aineisto on, mika on edellytys rannikon mittaiselle alueelle."""
    n = int(round((tile.bounds[2] - tile.bounds[0]) / WATER_GRID_M))
    tr = from_origin(tile.bounds[0], tile.bounds[3], WATER_GRID_M, WATER_GRID_M)
    return vesisto.meri_maski(tile.bounds, tr, (n, n)), n


def _tile_water_gids(tile):
    """Tiilen vesiruudukon GLOBAALIT solutunnisteet (kaikki ruudut, myos maa).

    Lasketaan koordinaateista eika mosaiikin indekseista, jotta tunniste on
    riippumaton mosaiikin muodosta - sama peruste kuin _tile_mosaic_cells."""
    n = int(round((tile.bounds[2] - tile.bounds[0]) / WATER_GRID_M))
    # Sama (i, j) -> (rivi, sarake) -jarjestys kuin _tile_water_maskissa:
    # i kasvaa etelaan (y pienenee), j kasvaa itaan (x kasvaa).
    x = tile.bounds[0] + np.arange(n) * WATER_GRID_M
    y = tile.bounds[3] - np.arange(n) * WATER_GRID_M
    xx, yy = np.meshgrid(x, y, indexing="xy")
    return global_cell_ids(xx, yy)


def get_or_compute_water_global(buildings_path, force=False):
    """Pyyhkaisymatkat ja esteiden korkeudet KAIKKIEN tiilien vesiruuduille
    kerralla - sama peruste kuin rantaruuduilla: sade kulkee tiilirajojen yli
    ja sadehaarukka on 13-kertainen."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "_water_global.npz"
    if force and cache_path.exists():
        cache_path.unlink()

    def solut(tile_ids):
        osat = []
        for t in tile_ids:
            tile = tiles.get_registry()[t]
            water, _n = _tile_water_mask(tile)
            osat.append(_tile_water_gids(tile)[water].ravel())
        return np.unique(np.concatenate(osat)) if osat else np.array([], dtype=np.int64)

    return _alueittain(cache_path, solut, buildings_path, force, "vesiruudut")


def get_or_compute_water_levels(tile_id, buildings_path, force=False):
    """Kvantisoidut pyyhkaisymatkat ja esteiden korkeudet tiilen
    vesiruudukolla. Palauttaa (fetch_levels, obstacle_levels, water_mask),
    kaikki muotoa (n, n, ...) missa n = 6000 m / WATER_GRID_M."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{tile_id}_water.npz"
    if not force and cache_path.exists():
        data = np.load(cache_path)
        return data["fetch"], data["obstacle"], data["water"].astype(bool)

    registry = tiles.get_registry()
    if tile_id not in registry:
        raise KeyError(f"Tuntematon tile_id: {tile_id}")
    tile = registry[tile_id]

    cells, fetch_all, obs_all = get_or_compute_water_global(buildings_path, force=force)
    water, n = _tile_water_mask(tile)

    fetch_levels = np.zeros((n, n, FETCH_SECTORS), dtype=np.uint8)
    obs_levels = np.zeros((n, n, FETCH_SECTORS), dtype=np.uint8)
    flat = _tile_water_gids(tile)[water]
    pos = np.searchsorted(cells, flat)
    if len(cells) == 0 or not np.array_equal(cells[np.clip(pos, 0, len(cells) - 1)], flat):
        raise RuntimeError(f"{tile_id}: vesiruutuja ei loydy globaalista joukosta")
    fetch_levels[water] = quantise_fetch(fetch_all[pos])
    obs_levels[water] = quantise_obstacle(obs_all[pos])

    np.savez_compressed(cache_path, fetch=fetch_levels, obstacle=obs_levels, water=water)
    return fetch_levels, obs_levels, water


def get_or_compute_water_png(tile_id, buildings_path, part="a", force=False):
    """Vesiruudukon pyyhkaisymatkat ja esteiden korkeudet kuvina, sama
    nibble-pakkaus kuin rantadatassa. part: "a"/"b" = pyyhkaisymatka,
    "obsa"/"obsb" = esteen korkeus.

    Alfa on tassa MERKITSEVA (0 = maata, ei arvioitavaa vetta) toisin kuin
    fetch-kuvissa: vesiruudukossa maa-alueet on rajattava pois, ja koska
    kuvat ovat vain 120x120, esikerrotun alfan pyoristys ei ole ongelma -
    arvot luetaan vain vesiruuduista."""
    if part not in ("a", "b", "obsa", "obsb"):
        raise ValueError(f"Tuntematon part: {part}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}_water{part}.png"
    meta_path = CACHE_DIR / f"{tile_id}.json"
    if not force and png_path.exists() and meta_path.exists():
        return png_path.read_bytes(), json.loads(meta_path.read_text())

    fetch_levels, obs_levels, water = get_or_compute_water_levels(
        tile_id, buildings_path, force=force
    )
    levels = obs_levels if part.startswith("obs") else fetch_levels
    base = 6 if part.endswith("b") else 0
    chan = [(levels[:, :, base + 2 * i] << 4) | levels[:, :, base + 2 * i + 1] for i in range(3)]
    r, g, b = chan
    a = np.where(water, 255, 0).astype(np.uint8)

    ok, encoded = cv2.imencode(".png", np.dstack([b, g, r, a]))
    if not ok:
        raise RuntimeError("PNG-enkoodaus epaonnistui")
    png_bytes = encoded.tobytes()

    raw = get_or_compute_raw(tile_id, buildings_path)
    meta = {
        "tile_id": tile_id,
        "bounds_epsg3067": bounds_tuple_to_dict(array_bounds(*raw["score"].shape, raw["map_transform"])),
        "n_buildings": raw["n_buildings"],
        "rock_pct": raw["rock_pct"],
        "swamp_pct": raw["swamp_pct"],
        "shoreline_px": raw["shoreline_px"],
        "buffer_px": raw["buffer_px"],
    }
    png_path.write_bytes(png_bytes)
    if not meta_path.exists():
        meta_path.write_text(json.dumps(meta, indent=2))
    return png_bytes, meta
