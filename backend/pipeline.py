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
# resamplausta. "detail" (kerroin 1) sailyttaa aiemman
# tiedostonimikaytannon (ei suffiksia); "mid"/"overview" ovat kevyempia
# yleisnakymia joita frontend nayttaa ulompana zoomattuna nopean
# alkulatauksen vuoksi.
#
# "near" (2 m/px) POISTETTU. Frontendin LEVELS lakkasi kayttamasta sita
# commitissa 75a0299, mutta kuvat syntyivat yha - 181 Mt kuollutta
# tuotetta docs-hakemistossa. Sen lahde (taustakartta_rasteri_10k) on
# poistettu myos mml_lataus.TAUSTAKARTAT-taulukosta: kun detail
# tallennetaan 2 m/px:na, near olisi tasan sama resoluutio mutta
# yleistetylla, vahanimistoisella sisallolla.
LEVEL_FACTORS = {"detail": 1, "mid": 4, "overview": 16}
LEVEL_SUFFIXES = {"detail": "", "mid": "_mid", "overview": "_overview"}

# PERUSKARTTA TALLENNETAAN 2 m/px:na, ei karttalehden natiivilla 1 m/px:lla.
# Se on 45 % koko docs-hakemistosta, ja Railwaylle ei voi ostaa satoja
# gigatavuja.
#
# PIENENNYS ON NEAREST, EI INTER_AREA. Peruskartta on 237 litteaa varia, ja
# keskiarvoistava pienennys luo niiden valiin tuhansia sanvyja - juuri sen
# rakenteen jonka varassa havioton pakkaus toimii. MITATTU (L4131F,
# tekstirikas kaupunkitiili, havioton WebP):
#
#   maastokartta 1 m/px  6000x6000  237 varia  5637 kt   <- nykyinen
#   NEAREST      2 m/px  3000x3000  236 varia  2037 kt   <- 2,77x pienempi
#   INTER_AREA / haviollinen pakkaus tuottivat ISOMMAN tiedoston.
#
# Hinta on ohuissa korkeuskayrissa ja pikkusymboleissa; nimisto sailyy
# luettavana. Detail-taso alkaa zoomilla -1,5, jossa selain pienentaa
# nykyisenkin kuvan 2,8-kertaisesti - taydesta 1 m/px:sta on hyotya vasta
# zoomilla > 0, joka on jo 1:10 000 -tuotteen oman tarkkuuden ulkopuolella.
PERUSKARTTA_PIENENNYS = 2


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


def maa_maski(dem, meri):
    """Maa = korkeutta YLI nollan JA ei merta vektoriaineiston mukaan.

    PELKKA `dem > 0` EI RIITA, ja se oli pitkaan vaarin. Mitattuna 18
    tiilta 37:sta oli ristiriidassa Maastotietokannan merialueen kanssa;
    pahimmillaan `dem > 0` vaitti tiilta 100-prosenttisesti maaksi kun
    vektoriaineiston mukaan siita oli merta 99,9 % (K4244H, K4244B).
    Korkeusmallin nodata avomerella luetaan positiivisena korkeutena.

    Seuraus oli hiljainen mutta vaara suuntaan: puskurivyohyke
    (compute_shoreline_buffer) rajataan maahan, joten valemaalla se levisi
    rantaviivan MOLEMMIN PUOLIN eli myos veteen - kartta lupasi
    rantautumiskelpoista rantaa siella missa on avovetta. Vika nakyi vasta
    kun karkeat resoluutiotasot paksunsivat kaistaleen nakyvaksi.

    Sama peruste on kirjattu jo pyyhkaisymatkojen puolelle: "MERIMASKI EIKA
    ~land_mask: jalkimmainen on DEM-pohjainen, jolloin korkeusmallin
    nodata-alueet tulkittaisiin vedeksi". Tassa se patee toiseen suuntaan -
    nodata tulkittiin maaksi."""
    return (dem > 0.0) & ~meri


def compute_shoreline_buffer(shoreline_mask, dem, pixel_size, meri):
    """Palauttaa boolean-maskin: True niille pikseleille jotka ovat maalla
    JA 5-15m etaisyydella lahimmasta rantaviivapikselista. Maa/vesi tulee
    maa_maski():sta - EI pelkasta DEM:n 0m-tasosta (ks. sen dokumentaatio)."""
    land = maa_maski(dem, meri)
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
    # Vesialue SAMALTA ruudukolta: maa/vesi-raja ei saa jaada korkeusmallin
    # nodatan varaan (ks. maa_maski). MERI JA JARVI, ks. vesisto.VESI_TASOT -
    # sisavesi kasitellaan tasan samoin kuin meri.
    meri_mask = vesisto.vesi_maski(tile.bounds, map_transform, map_shape)

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

    buffer_mask = compute_shoreline_buffer(shoreline_mask, dem, pixel_size, meri_mask)

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
        "land_mask": maa_maski(dem, meri_mask),
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


# --- LASKENTAVERSIO ---
#
# Sormenjalki kattoi LAHDEAINEISTON mutta ei ALGORITMIA. Kun laskenta
# muuttuu, vanha valimuisti kelpaa yha - hiljaa ja vaarin. Versiointi on
# siihen asti hoidettu ad hoc -merkeilla: get_or_compute_raw tarkisti
# esiintyyko "shoreline_mask" avaimena, ja kynnystiedostoille tehtiin oma
# muototarkistus. Molemmat ovat saman oireen paikkauksia.
#
# NOSTA TATA aina kun compute_tile tai sen kutsuma laskenta muuttaa
# tuloksia. Silloin _raw.npz ja siita johdettu aineisto lasketaan
# uudelleen ilman etta kukaan muistaa pyytaa sita.
#
#   1  lahtotilanne
#   2  maa/vesi-raja vektorimerimaskista, ei pelkasta dem > 0
#      (ks. maa_maski - 18 tiilta 37:sta oli ristiriidassa)
#   3  karkeiden tasojen tiivistys mediaanilla, ei maksimilla
#      (ks. _edustava_lohko - kartta oli liian vihrea ja muuttui
#      punaisemmaksi zoomatessa)
LASKENTA_VERSIO = 3


# Kuinka kaukaa tiilen ulkopuolelta lahdeaineisto vaikuttaa. Rakennusten
# etaisyyspisteytys katsoo DIST_IDEAL_M paahan (ks. score_engine.
# load_buildings pad_m), ja vesi vaikuttaa vain tiilen sisalla. Otetaan
# valjasti sama marginaali molemmille.
SORMENJALKI_MARGINAALI_M = 200.0


def _alueen_sormenjalki(polku, tasot, bbox):
    """Sisaltotunniste sille aineistolle jota TAMA tiili kayttaa.

    Luetaan vain mtk_id ilman geometriaa, jolloin haku kayttaa
    paikkaindeksia eika pura muotoja. Mitattuna 6-10 ms tiilta kohti eli
    noin 20 s koko aineistolle - halpa verrattuna siihen mita se saastaa.

    Tunniste on (maara, tunnusten summa) tasoittain. Se ei ole
    tormaysvarma tiiviste eika tarvitse olla: vaara osuma tarkoittaisi
    ettei tiilta lasketa uudelleen vaikka pitaisi, ja sen todennakoisyys
    on olematon kun sek maara etta summa tasmaavat."""
    import pyogrio
    osat = []
    for taso in tasot:
        try:
            df = pyogrio.read_dataframe(polku, layer=taso, bbox=tuple(bbox),
                                        columns=["mtk_id"], read_geometry=False)
            osat.append(f"{taso}:{len(df)}:{int(df['mtk_id'].sum()) if len(df) else 0}")
        except Exception:
            # Puuttuva taso tai lukuvirhe -> palataan tiedoston
            # sormenjalkeen, joka on turvallinen mutta karkea.
            return None
    return ";".join(osat)


def _vanha_muoto_kelpaa(vanha, uusi):
    """Kelpuuttaa ENNEN sisaltotunnistetta lasketun sormenjaljen.

    Jaetut tiedostot tunnistettiin aiemmin koon ja muokkausajan perusteella
    (esim. "12345:678901234"). Kun tunnistus vaihtui sisaltopohjaiseksi,
    JOKAINEN olemassa oleva _raw.npz olisi nayttanyt vanhentuneelta ja koko
    aineisto - 1126 tiilta, yli 20 tuntia - olisi laskettu uudelleen tasan
    kerran, ilman etta mikaan oikeasti muuttui.

    Siksi vanha muoto kelpaa, JOS versio, DEM ja karttalehti tasmaavat.
    Ne ovat tiilikohtaisia tiedostoja ja siten luotettavia; vain jaettujen
    tiedostojen osuus jatetaan huomiotta.

    HINTA ON KIRJATTAVA: jos tiilen oma vesi- tai rakennusaineisto on
    muuttunut sen jalkeen kun se laskettiin, sita ei huomata. Riski koskee
    vain tata siirtymaa - heti kun tiili lasketaan kerran uudelleen, sen
    sormenjalki on uutta muotoa ja tarkistus on tasmallinen. Vaihtoehto
    olisi ollut laskea kaikki uudelleen, mika on suurempi hinta kuin riski."""
    if not vanha:
        return False
    v, u = vanha.split("|"), uusi.split("|")
    if len(v) != 5 or len(u) != 5:
        return False
    if v[:3] != u[:3]:            # versio, DEM, karttalehti
        return False
    # Vanhassa muodossa jaetut osat ovat "koko:mtime" - ei tasonimia.
    return all(":" in osa and ";" not in osa and not any(c.isalpha() for c in osa)
               for osa in v[3:])


def lahde_sormenjalki(tile, buildings_path):
    """Tiilen kaikkien lahteiden JA laskennan sormenjalki merkkijonona.

    JAETUT TIEDOSTOT TUNNISTETAAN SISALLON MUKAAN, ei koon ja
    muokkausajan. Aiemmin tassa oli _tiedoston_sormenjalki(buildings_path)
    ja saman vesistolle, ja molemmat ovat JAETTUJA: uuden alueen
    lisaaminen kirjoittaa niihin, jolloin JOKAISEN tiilen _raw.npz
    vanheni - myos niiden joiden oma aineisto ei muuttunut lainkaan.
    Paijanteen lisays pakotti nain 1126 tiilen uudelleenlaskennan, josta
    96 % oli turhaa.

    DEM ja karttalehti ovat tiilikohtaisia tiedostoja, joten niille koko
    tiedoston sormenjalki on tasan oikea mitta."""
    osat = [
        f"v{LASKENTA_VERSIO}",
        _tiedoston_sormenjalki(tile.dem_path),
        _tiedoston_sormenjalki(tile.map_path),
    ]
    m = SORMENJALKI_MARGINAALI_M
    laaja = (tile.bounds[0] - m, tile.bounds[1] - m,
             tile.bounds[2] + m, tile.bounds[3] + m)
    rakennukset = _alueen_sormenjalki(str(buildings_path),
                                      score_engine.BUILDING_LAYERS, laaja)
    vesi = _alueen_sormenjalki(str(vesisto.GPKG), vesisto.VESI_TASOT, tile.bounds)
    # Jos sisaltotunnistetta ei saada, palataan vanhaan tapaan. Se laskee
    # liikaa mutta ei koskaan liian vahan.
    osat.append(rakennukset if rakennukset is not None
                else _tiedoston_sormenjalki(buildings_path))
    osat.append(vesi if vesi is not None else _tiedoston_sormenjalki(vesisto.GPKG))
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
    "_global_threshold_p*_v*.json",
    "_global_tiebreak_sorted_v*.npy",
    "_factor_thresholds_v*.json", "_prime_thresholds_v*.json",
    "_shelter_thresholds_v*.json", "_shoreline_stats_v*.json",
    # Rantaviivan pituus mitataan tiilien peittamalta alueelta, joten se
    # muuttuu tiiliston mukana. Ilman tata rivia 48 tiilen aineisto sai
    # 86 tiilen pituuden, ja puskurin leveydeksi tuli 4,2 m vaikka vyohyke
    # on 5-15 m - luku oli mahdoton mutta mikaan ei kaatunut.
    "_shoreline_length_v*.json",
    "_vaylat.json", "_suojelualueet.json", "_palvelut.json",
    # Naiden kuvien arvot on kvantisoitu globaalia jakaumaa vasten.
    # JOKERI SUFFIKSIN KOHDALLA: analyysikerroksella on nyt kolme
    # resoluutiotasoa (_mid, _overview), eika "*_factors.png" osu niihin.
    "*_top*.png", "*_factors*.png", "*_tiebreak*.png",
    # Vanhat versioimattomat nimet: siivotaan kertaalleen pois, muuten ne
    # jaavat levylle ikuisiksi ajoiksi nyt kun nimissa on LASKENTA_VERSIO.
    "_global_threshold_p[0-9]*.json", "_global_tiebreak_sorted.npy",
    "_factor_thresholds.json", "_prime_thresholds.json",
    "_shelter_thresholds.json", "_shoreline_stats.json",
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
        # DICT EIKA LISTA: kuviot menevat paallekkain. Esimerkiksi
        # "_global_threshold_p93_v3.json" osuu seka kuvioon
        # "_global_threshold_p*_v*.json" etta vanhojen nimien siivouskuvioon
        # "_global_threshold_p[0-9]*.json", jolloin sama tiedosto yritettiin
        # poistaa kahdesti ja toinen kerta kaatui FileNotFoundErroriin.
        #
        # SAMA VIKA KORJATTIIN JO mml_lataus.mitatoi():hin, mutta ei tanne -
        # kaksi paikkaa tekee samaa tyota, ja korjaus osui vain toiseen.
        #
        # OSUMA-MUUTTUJA ON NIMELTAAN osuma EIKA polku: tassa funktiossa
        # polku on sormenjalkitiedosto, ja silmukka jatti siihen viimeisen
        # osuman. Sormenjalki kirjoitettiin silloin satunnaisen
        # valimuistitiedoston paalle eika _rekisteri.json:iin - tiilisto
        # nayttti muuttuvan joka ajolla, ja yksi valimuisti korruptoitui
        # hiljaa. Mikaan ei kaatunut ennen kuin joku luki sen sisallon.
        loydetyt = {}
        for kuvio in REKISTERISTA_RIIPPUVAT:
            for osuma in CACHE_DIR.glob(kuvio):
                loydetyt[osuma] = True
        poistetut = sorted(loydetyt)
        for osuma in poistetut:
            osuma.unlink(missing_ok=True)
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
        if vanha == sormenjalki:
            return True
        if _vanha_muoto_kelpaa(vanha, sormenjalki):
            return True
        print(f"  {tile_id}: lahdeaineisto muuttunut, lasketaan uudelleen", flush=True)
        return False

    def _lataa():
        """Valimuisti tai None. VIALLINEN TIEDOSTO KASITELLAAN PUUTTUVANA.

        Keskeytynyt ajo jattaa katkenneen npz:n levylle, ja np.load kaataa
        koko buildin BadZipFilella ennen kuin sormenjalkea ehditaan
        tarkistaa. Tunteja kestava ajo ei saa kaatua siihen etta yksi
        valimuistitiedosto on rikki - se on tasan se tapaus jonka varalta
        valimuisti on olemassa. Loydettiin kun L3114D_raw.npz oli katkennut
        29 Mt:n kohdalta."""
        if force or not npz_path.exists():
            return None
        try:
            data = np.load(npz_path)
            if not _kelpaa(data):
                return None
            # Taulukot puretaan TASSA eika kutsujassa: np.load on laiska ja
            # lukee zipista vasta kun alkiota pyydetaan, joten katkennut
            # tiedosto voi kaatua vasta taman funktion ulkopuolella.
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
        except Exception as e:
            print(f"  {tile_id}: valimuisti viallinen ({type(e).__name__}), "
                  f"lasketaan uudelleen", flush=True)
            return None

    if (valmis := _lataa()) is not None:
        return valmis

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


def _tasokynnykset_kelpaa(d):
    """Onko levylla oleva kynnystiedosto UUTTA, tasokohtaista muotoa?

    Muoto muuttui litteasta {maski: {prosentti: kynnys}} tasokohtaiseksi
    {taso: {maski: {prosentti: kynnys}}}. Ilman tata tarkistusta vanha
    tiedosto luettaisiin sellaisenaan, selain ei loytaisi tasoaan ja
    korostuskerros jaisi tyhjaksi ILMAN VIRHEILMOITUSTA - sama hiljaisen
    vanhentumisen luokka jota vastaan skeemantarkistus on muuallakin
    (ks. get_or_compute_raw: "shoreline_mask")."""
    return isinstance(d, dict) and set(d) == set(ANALYYSI_TASOT)


def kynnykset_esiasetuksille(arvot):
    """{"<prosentti>": kynnys} kaikille TOP_PERCENT_PRESETS-esiasetuksille.

    Persentiilit pyydetaan YHDELLA kutsulla: numpy osittaa taulukon kerran
    viiden sijaan. Mitattuna 23,8 miljoonan rivin populaatiolla 0,76 s ->
    0,10 s, ja arvot ovat tasan samat (sama algoritmi, sama syote). Kolmessa
    kynnysfunktiossa ja rantaviivan jakaumassa yhteensa 75 kutsua, joten
    koko rannikolla saasto on minuutteja."""
    pctit = [top_percent_to_percentile(p) for p in TOP_PERCENT_PRESETS]
    tulos = np.percentile(arvot, pctit)
    return {str(p): float(v) for p, v in zip(TOP_PERCENT_PRESETS, tulos)}


def compute_global_threshold(buildings_path, percentile, force=False):
    """Laskee rank_score:n (ks. TIEBREAK_EPSILON-kommentti - EI nakyvaa
    score:a, koska se saturoituu 1.0:aan liian monella pikselilla) percentile:n
    kaikkien tiilien puskurivyohykkeen pikseleiden yli. Valimuistetaan
    levylle PER PERSENTIILI (yksi esiasetus = yksi kynnysarvo), koska vaatii
    kaikkien tiilien raa'an laskennan (~2s/tiili, kertaalleen - tuloksena
    ei-persentiilikohtainen get_or_compute_raw on jo omalla valimuistillaan)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    threshold_path = CACHE_DIR / f"_global_threshold_p{percentile}_v{LASKENTA_VERSIO}.json"

    if not force and threshold_path.exists():
        return json.loads(threshold_path.read_text())["threshold"]

    # Rannattomat ohitetaan: niiden puskuri on tyhja eivatka ne vaikuta
    # jakaumaan, mutta laskenta maksaisi taydet minuutit (ks.
    # tiles.rannattomat).
    registry = tiles.tuotantotiilet()
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
    kertaalleen laskettuna (sama jokaiselle kanavalle).

    NODATA ON NaN, EIKA NaN * 0 OLE 0. score_engine.compute_slope_score
    merkitsee DEM:n nodata-pikselit tarkoituksella NaN:iksi. Kertolasku
    arr * buffer_native_f antaa niille NaN:in VAIKKA ne olisivat puskurin
    ulkopuolella, ja aluekeskiarvo levittaa sen naapuriruutuun. Ruutu
    paatyy NaN:ina castiin np.uint8 -> 0, mika tarkoittaa jyrkinta
    rinnetta eli PUNAISTA.

    Osuma ei ole satunnainen: nodata alkaa siita mihin DEM loppuu, eli
    puskurivyohykkeen MEREN PUOLEISELTA REUNALTA. Vika maalasi siis
    punaista raitaa tasan rantaviivalle - sama oire kuin moduulin "outo
    punainen reunus" -kommentissa, eri syysta. Lisaksi pilaantuneet nollat
    paatyivat slope_b:n kautta compute_factor_thresholds-populaatioon ja
    vaaristivat globaaleja kynnysarvoja.

    Korjaus: NaN-pikseli ei osallistu osoittajaan eika nimittajaan, jolloin
    ruudun arvo lasketaan sen kelvollisista pikseleista. NOPEA POLKU ON
    TAYSIN ENNALLAAN kun NaN:ia ei ole, joten tiilet joilla on ehjä DEM
    tuottavat tavulleen saman tuloksen kuin ennen.

    JAA JALJELLE: jos ruudun KAIKKI puskuripikselit ovat NaN:ia, arvoksi
    tulee 0 ja buffer (weight_small > 0) sanoo silti True. Ruudulla ei ole
    yhtaan kelvollista mittausta, joten mitaan oikeaa arvoa ei ole
    tarjolla; tapaus vaatisi bufferin laskemisen kanavakohtaisesti."""
    a = np.asarray(arr, dtype=np.float32)
    kelpo = np.isfinite(a)
    if kelpo.all():
        masked = _resize_new_grid(a * buffer_native_f, native_shape, NEW_PIXEL_FACTOR)
        return masked / np.maximum(weight_small, 1e-6)

    paino = buffer_native_f * kelpo
    masked = _resize_new_grid(np.where(kelpo, a, 0.0) * paino, native_shape, NEW_PIXEL_FACTOR)
    kelpo_paino = _resize_new_grid(paino, native_shape, NEW_PIXEL_FACTOR)
    return masked / np.maximum(kelpo_paino, 1e-6)


# Yhden tiilen muisti. Sama tiili kysytaan nyt kolmesti perakkain (kolme
# resoluutiotasoa), ja joka kerta luettaisiin 29 Mt raakadataa levylta ja
# alinaytteistettaisiin viisi 6000x6000 taulukkoa. Kynnyslaskenta ei
# valmistunut kymmenessa minuutissa ennen tata. Yksi paikka riittaa, koska
# silmukat on jarjestetty tiili ulommaksi ja taso sisemmaksi.
_KOMPONENTTIMUISTI = {"avain": None, "arvo": None}


def downsampled_components(tile_id, buildings_path, force=False):
    """Pisteytyksen osatekijat selainpuolen ruudukolla (ks. NEW_PIXEL_FACTOR),
    peittopainotettuna. Palauttaa dict:n jossa kaikki taulukot ovat samaa
    muotoa - 'buffer' kertoo mitka ruudut ovat lainkaan naytettavia."""
    avain = (tile_id, str(buildings_path))
    if not force and _KOMPONENTTIMUISTI["avain"] == avain:
        return _KOMPONENTTIMUISTI["arvo"]
    raw = get_or_compute_raw(tile_id, buildings_path, force=force)
    native_shape = raw["score"].shape
    buffer_native_f = raw["buffer_mask"].astype(np.float32)
    weight_small = _resize_new_grid(buffer_native_f, native_shape, NEW_PIXEL_FACTOR)

    tulos = {
        "slope": _masked_downsample(raw["slope_score"], buffer_native_f, native_shape, weight_small),
        "dist": _masked_downsample(raw["dist_score"], buffer_native_f, native_shape, weight_small),
        "rock": _masked_downsample(raw["rock_mask"], buffer_native_f, native_shape, weight_small),
        "swamp": _masked_downsample(raw["swamp_mask"], buffer_native_f, native_shape, weight_small),
        "tiebreak": _masked_downsample(raw["tiebreak"], buffer_native_f, native_shape, weight_small),
        "buffer": weight_small > 0.0,
        "raw": raw,
    }
    _KOMPONENTTIMUISTI["avain"], _KOMPONENTTIMUISTI["arvo"] = avain, tulos
    return tulos


def _global_tiebreak_sorted(buildings_path, force=False):
    """Lajiteltu taulukko KAIKKIEN tiilien tasapelinpurkuarvoista puskuri-
    vyohykkeella (selainpuolen ruudukolla). Kaytetaan kvantisoimaan
    tasapelinpurku 0-255 GLOBAALIKSI JARJESTYSLUVUKSI: tasavalinen
    kvantisointi hukkaisi tarkkuutta, koska arvot kasautuvat jakauman
    ylapaahan - juuri sinne missa tasapelit ratkotaan."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"_global_tiebreak_sorted_v{LASKENTA_VERSIO}.npy"
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


# --- ANALYYSIKERROKSEN RESOLUUTIOTASOT ---
#
# Analyysikerros laskettiin aina 2 m/px tarkkuudella riippumatta zoomista,
# vaikka peruskartalla on neljä tasoa. Mitattuna uloimmalla zoomilla tiili
# peittaa 28x28 ruutupikselia mutta siita laskettiin 3000x3000 - 11 585
# kertaa liikaa. Yhden tiilen renderointi kesti 1,9-3,8 s ja 38 tiilta
# 74-144 s, jonka ajan selain ei vastannut lainkaan.
#
# Kerroin on NEW_PIXEL_FACTOR:n PAALLE, eli 2 / 8 / 32 m/px.
ANALYYSI_TASOT = {"detail": 1, "mid": 4, "overview": 16}
ANALYYSI_SUFFIKSIT = {"detail": "", "mid": "_mid", "overview": "_overview"}


def _edustava_lohko(jarjestys, taulukot, buffer, k):
    """Tiivistaa lohkoiksi k x k valitsemalla kustakin lohkosta EDUSTAVAN
    PIKSELIN: sen jonka `jarjestys`-arvo on lohkon MEDIAANI.

    EI KESKIARVOA EIKA SYNTEETTISTA PIKSELIA. Osatekijoiden tiivistaminen
    erikseen loisi pikselin jota ei ole olemassa ("tassa on loiva rinne ja
    tassa on etaisyytta rakennuksiin", mutta ei valttamatta samassa
    kohdassa), mika olisi keksittya tietoa. Edustava pikseli on aina oikea
    mittaustulos jostain lohkon kohdasta, ja KAIKKI sen osatekijat tulevat
    samasta kohdasta.

    MEDIAANI EIKA MAKSIMI. Ensin valittiin lohkon paras pikseli, perusteena
    ettei kapea hyva ranta hukkuisi huonon ympariston sekaan. Se peruste
    patee ETSINTAAN ("onko taalla hyva paikka") mutta ei YLEISKUVAAN
    ("millaista taalla on") - ja pistemaarakerros on jalkimmainen. Tulos oli
    etta kartta valehteli johdonmukaisesti liian vihreaksi ja muuttui
    punaisemmaksi kun kayttaja zoomasi lahemmas. Mitattuna punaisen
    (pistemaara alle 0,4) osuus:

        tiili     detail   overview max   overview mediaani
        K4242F     4,7 %       0,6 %            3,6 %
        L4131E     8,8 %       1,3 %            6,4 %

    Mediaani osuu lahes tasmalleen tarkan tason jakaumaan. Hinta on se, etta
    kapea erinomainen kohta laajan keskinkertaisen rannan keskella ei erotu
    ennen kuin zoomaa lahemmas - tietoinen valinta, koska 32 m ruudussa ei
    voi rehellisesti nayttaa 2 m kohdetta.

    RAJOITE: edustava valitaan KAIKKIEN tekijoiden pistemaaralla, koska
    kayttajan tekijavalinta ei ole tiedossa laskenta-aikana. Jos kayttaja on
    kytkenyt tekijoita pois, lohkon edustaja ei ole *sen* valinnan mediaani.
    Karkealla zoomilla se on hyvaksyttava approksimaatio; tarkalla zoomilla
    kaytetaan detail-tasoa jossa approksimaatiota ei ole.

    Palauttaa (tiivistetyt_taulukot, tiivistetty_buffer)."""
    h, w = jarjestys.shape
    ph, pw = (-h) % k, (-w) % k          # taytto tasajaolliseksi

    def lohkoiksi(a, tayte):
        a = np.pad(a, ((0, ph), (0, pw)), constant_values=tayte)
        H, W = a.shape
        return a.reshape(H // k, k, W // k, k).transpose(0, 2, 1, 3).reshape(
            H // k, W // k, k * k)

    # Puskurin ulkopuoliset saavat +inf ja painuvat lajittelussa loppuun,
    # jolloin kelvolliset ovat alussa jarjestyksessa ja n:s pienin loytyy
    # suoraan indeksista. Taytepikselit kasitellaan samoin.
    j = np.where(buffer, jarjestys.astype(np.float32), np.inf)
    jl = lohkoiksi(j, np.inf)
    bl = lohkoiksi(buffer, False)

    lajiteltu = np.argsort(jl, axis=2, kind="stable")
    n = bl.sum(axis=2)                       # kelvollisia lohkossa
    # Parillisella maaralla otetaan ALEMPI keskimmainen: se on oikea pikseli,
    # kun taas kahden keskiarvo olisi synteettinen. Tyhjalle lohkolle indeksi
    # 0 on merkityksetön, koska uusi_buffer on siella False.
    keski = np.clip((n - 1) // 2, 0, k * k - 1)
    idx = np.take_along_axis(lajiteltu, keski[:, :, None], axis=2)[:, :, 0]
    uusi_buffer = n > 0

    ulos = {}
    for nimi, arr in taulukot.items():
        al = lohkoiksi(arr, arr.flat[0] if arr.size else 0)
        ulos[nimi] = np.take_along_axis(al, idx[:, :, None], axis=2)[:, :, 0]
    return ulos, uusi_buffer


def get_or_compute_factor_arrays(tile_id, buildings_path, taso="detail", force=False):
    """Kvantisoidut (8-bittiset) osatekijataulukot yhdelle tiilelle - tasan
    ne arvot jotka selain lukee kuvista.

    taso valitsee resoluution (ks. ANALYYSI_TASOT). Karkeammat tasot
    tiivistetaan detail-tasosta edustavalla pikselilla, jolloin ne ovat
    keskenaan yhtapitavia: karkean tason arvo on aina JONKIN detail-ruudun
    arvo samasta kohdasta."""
    comp = downsampled_components(tile_id, buildings_path, force=force)
    sorted_tiebreak = _global_tiebreak_sorted(buildings_path, force=force)

    if taso != "detail":
        k = ANALYYSI_TASOT[taso]
        # Jarjestysperuste on sama rank_score jolla globaali kynnyskin
        # lasketaan, tuotuna selaimen ruudukolle.
        raw = comp["raw"]
        jarj = _masked_downsample(raw["rank_score"],
                                  raw["buffer_mask"].astype(np.float32),
                                  raw["score"].shape,
                                  _resize_new_grid(raw["buffer_mask"].astype(np.float32),
                                                   raw["score"].shape, NEW_PIXEL_FACTOR))
        pienet, uusi_buffer = _edustava_lohko(
            jarj,
            {"slope": comp["slope"], "dist": comp["dist"], "rock": comp["rock"],
             "swamp": comp["swamp"], "tiebreak": comp["tiebreak"]},
            comp["buffer"], k)
        comp = dict(comp, **pienet, buffer=uusi_buffer)

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


def get_or_compute_factor_png(tile_id, buildings_path, part="factors",
                              taso="detail", force=False):
    """Palauttaa (png_bytes, meta_dict) osatekijakuvalle. part="factors" tai
    "tiebreak" (ks. moduulin kanavakuvaus). Yksi kuvapari per tiili JA TASO
    riippumatta tekijavalinnoista, paksuudesta ja prosentista."""
    if part not in ("factors", "tiebreak"):
        raise ValueError(f"Tuntematon part: {part}")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}_{part}{ANALYYSI_SUFFIKSIT[taso]}.png"
    meta_path = CACHE_DIR / f"{tile_id}.json"

    if not force and png_path.exists() and meta_path.exists():
        return png_path.read_bytes(), json.loads(meta_path.read_text())

    registry = tiles.get_registry()
    if tile_id not in registry:
        raise KeyError(f"Tuntematon tile_id: {tile_id}")

    arrays = get_or_compute_factor_arrays(tile_id, buildings_path, taso=taso, force=force)
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
    # RANNATTOMAT OHITETAAN. Ne eivat sisalla yhtaan puskuripikselia, joten
    # jokainen naista silmukoista hylkaa ne joka tapauksessa ("if buf.any()")
    # - mutta vasta sen jalkeen kun tiili on laskettu kokonaan. Mitattuna
    # 7 % tiilista. Tulos ei muutu, vain tyo vahenee.
    kohteet = list(tiles.tuotantotiilet() if jono is None else jono)
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
    cache_path = CACHE_DIR / f"_factor_thresholds_v{LASKENTA_VERSIO}.json"
    if not force and cache_path.exists():
        vanha = json.loads(cache_path.read_text())
        if _tasokynnykset_kelpaa(vanha):
            return vanha
        print(f"  _factor_thresholds.json: vanha muoto, lasketaan uudelleen", flush=True)

    # KYNNYS LASKETAAN ERIKSEEN JOKAISELLE RESOLUUTIOTASOLLE, jotta
    # "parhaat 7 %" tarkoittaa aina 7 % siita mita ruudulla nakyy. Ilman
    # tata karkealla zoomilla korostuisi selvasti enemman kuin pyydetty
    # osuus: tiivistys valitsee lohkon PARHAAN pikselin, joten karkean
    # tason jakauma on systemaattisesti parempi kuin tarkan.
    # TIILI ULOMPANA, TASO SISEMPANA. Toisin pain lahdeaineisto (29 Mt/tiili)
    # luettaisiin ja alinaytteistettaisiin kolmeen kertaan - mitattuna se ei
    # valmistunut kymmenessa minuutissa 37 tiilella.
    osat = {taso: [] for taso in ANALYYSI_TASOT}
    for tid in tiilet_edistymisella("Tekijakynnykset"):
        for taso in ANALYYSI_TASOT:
            arrays = get_or_compute_factor_arrays(tid, buildings_path, taso=taso,
                                                  force=force)
            buf = arrays["buffer"]
            if buf.any():
                osat[taso].append((arrays["slope_b"][buf], arrays["dist_b"][buf],
                                   arrays["rock_bit"][buf], arrays["swamp_bit"][buf],
                                   arrays["tiebreak_b"][buf]))

    kynnykset_tasoittain = {}
    for taso, parts in osat.items():
        slope_b, dist_b, rock_bit, swamp_bit, tiebreak_b = (
            np.concatenate(c) for c in zip(*parts))
        taso_kynnykset = {}
        for factor_mask in range(1, NO_SHELTER_MASK + 1):
            rank = rank_from_components(slope_b, dist_b, rock_bit, swamp_bit,
                                        tiebreak_b, factor_mask)
            taso_kynnykset[str(factor_mask)] = kynnykset_esiasetuksille(rank)
        kynnykset_tasoittain[taso] = taso_kynnykset

    cache_path.write_text(json.dumps(kynnykset_tasoittain, indent=2))
    return kynnykset_tasoittain

def _taustakartta_lehti(tile, level):
    """(polku, lehden_rajat, m/px) jos taustakarttalehti on ladattu, muuten
    None. HALPA: katsoo vain tiedoston olemassaolon, ei lue kuvaa.

    Erotettu lukemisesta koska valimuistin nimi riippuu lahteesta, ja nimi
    on paatettava ennen kuin tiedetaan tarvitaanko kuvaa lainkaan."""
    from . import mml_lataus
    if level not in mml_lataus.TAUSTAKARTAT:
        return None
    _aineisto, jako, mpp = mml_lataus.TAUSTAKARTAT[level]
    from . import karttalehti
    # Tiili (6x6 km) osuu aina yhden taustakarttalehden sisaan, koska
    # pieninkin niista on 12x12 km ja jako on sisakkainen.
    keski = ((tile.bounds[0] + tile.bounds[2]) / 2,
             (tile.bounds[1] + tile.bounds[3]) / 2)
    lehti = karttalehti.sheet_name(keski[0], keski[1], jako)
    polku = mml_lataus.tausta_kansio(level) / f"{lehti}.png"
    if not polku.exists():
        return None
    return polku, karttalehti.sheet_bounds(lehti), mpp


def _taustakartta_ikkuna(tile, level):
    """Tiilen ikkuna taustakarttalehdelta, tai None jos sita ei ole ladattu.

    Palautuminen maastokarttaan on TAHALLISTA eika virhe: taustakartta on
    esityksen parannus, ei laskennan edellytys. Ilman sita kartta on
    sotkuisempi ja isompi mutta oikea, ja koko putki toimii kuten ennen.
    Latauksen puuttuminen ei siis saa kaataa buildia."""
    tieto = _taustakartta_lehti(tile, level)
    if tieto is None:
        return None
    polku, lb, mpp = tieto
    lehti = polku.stem
    im = cv2.imread(str(polku), cv2.IMREAD_COLOR)
    if im is None:
        print(f"  {lehti}: taustakarttaa ei voitu lukea, kaytetaan maastokarttaa",
              flush=True)
        return None
    c0 = int(round((tile.bounds[0] - lb[0]) / mpp))
    r0 = int(round((lb[3] - tile.bounds[3]) / mpp))
    n = int(round((tile.bounds[2] - tile.bounds[0]) / mpp))
    m = int(round((tile.bounds[3] - tile.bounds[1]) / mpp))
    pala = im[r0:r0 + m, c0:c0 + n]
    if pala.shape[:2] != (m, n):
        print(f"  {lehti}: ikkuna {pala.shape[:2]} != odotettu {(m, n)}, "
              f"kaytetaan maastokarttaa", flush=True)
        return None
    return pala


def get_or_compute_basemap(tile_id, level="detail", force=False):
    """Palauttaa taustakartaksi tarkoitetun karttakuva-leikkauksen PNG-tavuina
    halutulla resoluutiotasolla, levyvalimuistilla. "detail" on
    MUUTTUMATON (ei resamplausta/reprojisointia); "mid"/"overview" ovat
    kevyita downsamplattuja yleisnakymia nopeaa alkulatausta varten."""
    suffix = LEVEL_SUFFIXES[level]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    registry = tiles.get_registry()
    if tile_id not in registry:
        raise KeyError(f"Tuntematon tile_id: {tile_id}")
    tile = registry[tile_id]

    # VALIMUISTIN NIMI KERTOO LAHTEEN, muuten lahteen vaihtuminen jaa
    # huomaamatta. Nain kavi: taustakartta otettiin kayttoon, mutta vanhat
    # maastokartasta tehdyt kuvat kelpasivat edelleen eika ladattuja
    # taustakarttalehtia katsottu kertaakaan - 188 tiilen ajo tuotti
    # tasmalleen saman peruskartan kuin ennen.
    #
    # LASKENTA_VERSIO ei auta tahan: se kattaa LASKENNAN, ja peruskartta on
    # ESITYSTA. Raja on oikea, mutta se tarkoittaa etta esityksen lahde
    # tarvitsee oman tunnisteensa.
    lahdemerkki = "_tk" if _taustakartta_lehti(tile, level) else ""
    # RESOLUUTIO ON OSA NIMEA samasta syysta kuin lahde: ilman sita vanhat
    # 6000x6000 kuvat kelpaisivat edelleen eika muutos nakyisi mitenkaan.
    # Tama on taman projektin toistuva vikaluokka - jokin paatettiin kerran
    # eika merkitty vanhentuneeksi.
    pienennys = PERUSKARTTA_PIENENNYS if level == "detail" else 1
    tarkkuusmerkki = f"_r{pienennys}" if pienennys > 1 else ""
    png_path = CACHE_DIR / f"{tile_id}_base{suffix}{lahdemerkki}{tarkkuusmerkki}.png"

    if not force and png_path.exists():
        return png_path.read_bytes()

    # TAUSTAKARTTA KARKEILLE TASOILLE, jos se on ladattu.
    #
    # 1:10 000 karttaa pienentamalla saatu yleisnakyma on kaksi kertaa
    # vaarin. Kartografisesti: viivat, tekstit ja symbolit on suunniteltu
    # metrien tarkkuudelle ja muuttuvat pienennettyna kohinaksi. Kooltaan:
    # painettu kartta on 214 varia, ja alinaytteistys keskiarvoistaa niita
    # kymmeniksi tuhansiksi - juuri se litteys jonka varassa havioton
    # pakkaus toimii. MITATTU samassa 4 m/px resoluutiossa:
    #
    #   maastokartta pienennettyna  53 110 varia  2,49 bit/px  19,42 kt/km2
    #   taustakartta natiivina         238 varia  1,11 bit/px   8,65 kt/km2
    #
    # Detail-taso pysyy maastokarttana: siita luetaan kallio ja suo
    # (raster_filters), joten sen VARIT ovat osa pisteytysta eivatka vain
    # esitysta. Taustakartta on pelkka tausta.
    lahde = _taustakartta_ikkuna(tile, level) if lahdemerkki else None
    if lahde is not None:
        map_bgr = lahde
    else:
        map_bgr, _map_transform = raster_filters.load_map_window(
            str(tile.map_path), tile.bounds)
        map_bgr = downsample_image(map_bgr, LEVEL_FACTORS[level])

    # Pienennys koskee VAIN detail-tasoa. Karkeat tasot tulevat
    # taustakartasta omalla natiiviresoluutiollaan (4 ja 16 m/px), eika
    # niita saa pienentaa uudestaan.
    if pienennys > 1:
        h, w = map_bgr.shape[:2]
        map_bgr = cv2.resize(map_bgr, (w // pienennys, h // pienennys),
                             interpolation=cv2.INTER_NEAREST)

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

# Rantaviivan kokonaispituus MITATAAN VEKTORIGEOMETRIASTA, ei arvata.
#
# Tassa oli aiemmin kovakoodattu SHORELINE_LENGTH_M = 700 000, joka oli
# kalibroitu rannikolle kolmella rasteripohjaisella mittauksella (601 km /
# 899 km / 1 405 km, joiden vinoumat osoittivat eri suuntiin). Vakio piti
# summan 700 kilometrissa RIIPPUMATTA AINEISTOSTA: kun Paijanne laskettiin
# omanaan, sekin sai 700 km, vaikka sen puskurivyohyke on 18,7 km2 ja
# rannikon 196 km2 - kymmenkertainen ero. Yhdistetyssa aineistossa vakio
# olisi vaara molemmille.
#
# Rasterin ongelmat - maskin kohina, porrastus ja ohennuksen haarat - eivat
# koske vektoria lainkaan: pituus luetaan samasta Maastotietokannan
# geometriasta josta vesialueetkin (ks. backend/vesisto.py).
#
# RANTAVIIVAPARADOKSI EI KATOA, mutta se muuttuu maariteltavaksi: tulos on
# "pituus Maastotietokannan yleistystasolla", ei "pituus jollain
# tuntemattomalla tarkkuudella". Se on toistettava luku eika arvio, joten
# haarukkaa (_LOW/_HIGH) ei enaa ole.


# Rantaviivan mittauksen lohkokoko.
#
# 60 km OSOITTAUTUI LIIAN ISOKSI. Rannikon meri-taso on 9 933 polygonia ja
# 141,8 M pistetta, joten 3 600 km2 lohkoon osuu tuhansia polygoneja ja
# kymmenia miljoonia pisteita - unary_union sellaisesta ei valmistu
# jarkevassa ajassa, eika ajoa voi keskeyttaa Ctrl+C:lla koska suoritus on
# GEOS:n C-koodissa.
#
# 15 km lohkossa on noin kuudestoistaosa siita, ja unionin kustannus kasvaa
# pistemaaran mukana selvasti nopeammin kuin lineaarisesti. Lohkoja tulee
# enemman mutta jokainen on halpa.
SHORELINE_LOHKO_M = 15000.0
# Marginaali jonka verran lohkon ulkopuolelta otetaan mukaan, jotta
# leikkausreunat jaavat lopullisen rajauksen ulkopuolelle.
SHORELINE_MARGINAALI_M = 500.0


def compute_shoreline_length_m(force=False):
    """Vesialueiden reunan pituus metreina tiilirekisterin peittamalla
    alueella.

    POLYGONIT ON YHDISTETTAVA ENNEN REUNAN MITTAAMISTA, ja se on tehtava
    LOHKOITTAIN.

    Ensimmainen versio summasi jokaisen polygonin reunan erikseen ja
    perusteli: "polygonit eivat mene paallekkain". Vaarin. Vierekkaiset
    vesialueet jakavat reunoja, ja rannikon hydrografia.gpkg on lisaksi
    koottu 37 latauspalasta jotka menevat saumoissa paallekkain. Vesi vetta
    vasten ei ole rantaviivaa, joten jaetut reunat kuuluu poistaa - mitattuna
    ne olivat 18 % tuloksesta talla koneella ja Ubuntulla viisinkertaistivat
    sen (392 928 km eli 364 km per 36 km2 tiili).

    Kaksi hylattya yritysta, molemmat mitattuja:
      - yhdistaminen tiileittain: merialue on yksi valtava polygoni, joka
        olisi kasitelty uudelleen kaikilla 1126 tiilella - ajo jumittui
      - yhdistaminen vain saman mtk_id:n kesken: halpa mutta ei riita,
        4 397 km vs. oikea 3 607 km, koska paallekkaisyys ei ole
        kaksoiskappaleita vaan JAETTUJA REUNOJA

    Nyt: polygonit puretaan osiin ja indeksoidaan kerran (STRtree), ja tyo
    tehdaan 60 km lohkoissa. Jokaisessa lohkossa LEIKATAAN ENSIN
    (clip_by_rect, halpa suorakaideleikkaus) ja yhdistetaan vasta sitten,
    jolloin unioni tehdaan aina pienelle joukolle.

    MARGINAALI JA RAJAUS OVAT YHDESSA OLENNAISET. Leikkaus tuottaa
    keinotekoisia reunoja marginaalin rajalle; ne jaavat pois kun tulos
    rajataan lohkoon ILMAN marginaalia. Ja peitto on rajattava lohkoon,
    koska 6 km tiili voi ylittaa lohkorajan - ilman sita tulos oli 1,25 %
    pielessa. Todennettu globaalia yhdistamista vastaan: ero +0,001 %."""
    import shapely
    from shapely import STRtree
    from shapely.geometry import box
    from shapely.ops import unary_union

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"_shoreline_length_v{LASKENTA_VERSIO}.json"
    if not force and cache_path.exists():
        return json.loads(cache_path.read_text())["length_m"]

    # EDISTYMINEN NAKYVIIN HETI. Ilman tata ensimmainen tuloste tuli vasta
    # 20 lohkon jalkeen, eika hidasta ajoa voinut erottaa jumista - ja
    # aineiston luku yksin voi kestaa minuutteja. Vaiheet tulostetaan
    # erikseen, jotta hitaan kohdan nakee ilman arvailua.
    import time as _aika
    _t0 = _aika.perf_counter()

    registry = tiles.get_registry()
    peitto = unary_union([box(*t.bounds) for t in registry.values()])
    x0, y0, x1, y1 = peitto.bounds
    print(f"  rantaviiva: luetaan vesialueet ({len(registry)} tiilta, "
          f"{(x1-x0)/1000:.0f} x {(y1-y0)/1000:.0f} km)...", flush=True)

    # Polygonit puretaan yksittaisiksi: MultiPolygonin osat ovat saaria ja
    # erillisia altaita, ja indeksi loytaa niista vain tarvittavat.
    kaikki = []
    for g in vesisto._polygonit((x0, y0, x1, y1), vesisto.VESI_TASOT):
        if g is None or g.is_empty:
            continue
        kaikki.extend(g.geoms if hasattr(g, "geoms") else [g])
    if not kaikki:
        cache_path.write_text(json.dumps({"length_m": 0.0}))
        return 0.0

    # TARKAT KAKSOISKAPPALEET POIS ENNEN MITAAN MUUTA.
    #
    # hydrografia.gpkg kootaan latauspaloista, ja palat menevat saumoissa
    # paallekkain - sama polygoni tallentuu moneen kertaan tasan samana.
    # Mitattuna rannikolla meri-tasolla oli 8 546 riviä, kun pinta-alaan
    # suhteutettuna odottaisi noin 500. Kaksoiskappaleiden poisto on
    # tarkkaa (sama tavujono = sama geometria) ja halpaa, ja se pienentaa
    # kalliin unionin tyota suoraan.
    nahdyt = set()
    osat = []
    for o in kaikki:
        tunnus = hashlib.blake2b(shapely.to_wkb(o), digest_size=16).digest()
        if tunnus in nahdyt:
            continue
        nahdyt.add(tunnus)
        osat.append(o)
    pisteita = int(sum(shapely.get_num_coordinates(o) for o in osat))
    print(f"  rantaviiva: {len(kaikki)} polygonia -> {len(osat)} ilman "
          f"kaksoiskappaleita, {pisteita:,} pistetta "
          f"({_aika.perf_counter() - _t0:.1f} s)", flush=True)
    puu = STRtree(osat)

    yhteensa = 0.0
    lohkoja = 0
    for lx in range(int(np.floor(x0 / SHORELINE_LOHKO_M)),
                    int(np.floor(x1 / SHORELINE_LOHKO_M)) + 1):
        for ly in range(int(np.floor(y0 / SHORELINE_LOHKO_M)),
                        int(np.floor(y1 / SHORELINE_LOHKO_M)) + 1):
            lohko = box(lx * SHORELINE_LOHKO_M, ly * SHORELINE_LOHKO_M,
                        (lx + 1) * SHORELINE_LOHKO_M, (ly + 1) * SHORELINE_LOHKO_M)
            if not lohko.intersects(peitto):
                continue
            iso = box(*shapely.buffer(lohko, SHORELINE_MARGINAALI_M).bounds)
            leikatut = []
            for i in puu.query(iso):
                c = shapely.clip_by_rect(osat[i], *iso.bounds)
                if not c.is_empty:
                    leikatut.append(c if c.is_valid else shapely.make_valid(c))
            if not leikatut:
                continue
            vesi = unary_union(leikatut)
            yhteensa += vesi.boundary.intersection(peitto).intersection(lohko).length
            lohkoja += 1
            # Ensimmaiset lohkot yksitellen: niista nakee tahdin heti ja voi
            # paattaa kannattaako odottaa. Sen jalkeen harvemmin.
            if lohkoja <= 5 or lohkoja % 20 == 0:
                print(f"  rantaviiva: lohko {lohkoja} -> {yhteensa / 1000:.0f} km "
                      f"({_aika.perf_counter() - _t0:.0f} s)", flush=True)

    print(f"  rantaviiva: {lohkoja} lohkoa, {len(osat)} polygonia -> "
          f"{yhteensa / 1000:.1f} km ({_aika.perf_counter() - _t0:.1f} s)", flush=True)
    cache_path.write_text(json.dumps({"length_m": round(yhteensa, 1)}))
    return round(yhteensa, 1)


def compute_shoreline_stats(buildings_path, force=False):
    """Rantaviivan jakauma rantautumiskelpoisuuden mukaan, per
    tekijayhdistelma: metria rantaviivaa kussakin pistemaaraluokassa.

    Palauttaa myos "parhaat X %" -rajan sijainnin pistemaara-asteikolla,
    jotta asetussivun kuvaaja voi merkita sen - se lasketaan TASTA SAMASTA
    jakaumasta, jolloin merkki ja pylvaat ovat keskenaan tasmalleen
    yhtapitavia."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"_shoreline_stats_v{LASKENTA_VERSIO}.json"
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

    pituus_m = compute_shoreline_length_m(force=force)
    metres_per_px = pituus_m / total_px if total_px else 0.0

    histograms = {}
    prime_histograms = {}
    top_markers = {}
    for mask in counts:
        histograms[str(mask)] = [round(float(c) * metres_per_px, 1) for c in counts[mask]]
        prime_histograms[str(mask)] = [round(float(c) * metres_per_px, 1) for c in prime_counts[mask]]
        sample = np.concatenate(ranks[mask])
        top_markers[str(mask)] = kynnykset_esiasetuksille(sample)

    stats = {
        "bin_edges": [round(float(e), 4) for e in edges],
        "histograms_m": histograms,
        "prime_histograms_m": prime_histograms,
        "top_markers": top_markers,
        "buffer_px": total_px,
        "buffer_km2": round(total_px / 1e6, 3),
        "length_m": pituus_m,
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


# Sama peruste kuin _KOMPONENTTIMUISTI:lla: karkipaikat lasketaan nyt kerran
# per resoluutiotaso, ja laskenta on mitattuna 5-6 s/tiili.
_KARKIMUISTI = {"avain": None, "arvo": None}


def compute_prime_components(tile_id, buildings_path, force=False, native=False):
    """Karkipaikkojen osatekijat selainruudukolla (ks. NEW_PIXEL_FACTOR).
    Arviointi tehdaan LEVEAMMALLA PRIME_ZONE-vyohykkeella, mutta tulos
    naytetaan NYKYISELLA 5-15 m puskurivyohykkeella, jotta kerros asettuu
    tarkalleen samaan kohtaan kuin muut kerrokset.

    native=True palauttaa taulukot downsamplaamattomina (1 m/px) -
    rantaviivan jakauman laskentaa varten, jotta karkipaikkojen jakauma on
    laskettu TASMALLEEN samalla ruudukolla kuin tavallinen jakauma
    (compute_shoreline_stats) ja kayrat ovat vertailukelpoisia."""
    avain = (tile_id, str(buildings_path), bool(native))
    if not force and _KARKIMUISTI["avain"] == avain:
        return _KARKIMUISTI["arvo"]
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
        tulos = {
            "slope": tyhja, "dist": tyhja.copy(), "rock": tyhja.copy(),
            "not_swamp": tyhja.copy(),
            "buffer": np.zeros(tyhja.shape, dtype=bool),
            "raw": raw,
        }
        _KARKIMUISTI["avain"], _KARKIMUISTI["arvo"] = avain, tulos
        return tulos

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

    tulos = {
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
    _KARKIMUISTI["avain"], _KARKIMUISTI["arvo"] = avain, tulos
    return tulos


def get_or_compute_prime_arrays(tile_id, buildings_path, taso="detail", force=False):
    """Kvantisoidut (8-bittiset) karkipaikka-osatekijat - tasan ne arvot
    jotka selain lukee kuvasta. Karkeammat tasot tiivistetaan edustavalla
    pikselilla (ks. _edustava_lohko)."""
    comp = compute_prime_components(tile_id, buildings_path, force=force)
    if taso != "detail":
        # Jarjestysperuste on karkipaikkojen OMA pistemaara, ei tavallinen
        # rank_score: muuten karkeat karkipaikat valittaisiin eri
        # perusteella kuin miten ne naytetaan.
        jarj = (comp["slope"] + comp["dist"] + comp["rock"]
                + comp["not_swamp"]).astype(np.float32)
        pienet, uusi_buffer = _edustava_lohko(
            jarj,
            {"slope": comp["slope"], "dist": comp["dist"],
             "rock": comp["rock"], "not_swamp": comp["not_swamp"]},
            comp["buffer"], ANALYYSI_TASOT[taso])
        comp = dict(comp, **pienet, buffer=uusi_buffer)
    return {
        "slope_b": np.clip(comp["slope"] * 255.0, 0, 255).astype(np.uint8),
        "dist_b": np.clip(comp["dist"] * 255.0, 0, 255).astype(np.uint8),
        "rock_bit": comp["rock"] >= 0.5,
        "swamp_bit": comp["not_swamp"] < 0.5,
        "buffer": comp["buffer"],
        "raw": comp["raw"],
    }


def get_or_compute_prime_png(tile_id, buildings_path, taso="detail", force=False):
    """Palauttaa (png_bytes, meta_dict) karkipaikkakuvalle. Kanavat kuten
    factors-kuvassa (R=jyrkkyys, G=etaisyys, B=kallio/suo-bitit,
    A=puskurimaski), mutta arvot ovat kaistaleen yli aggregoituja."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    png_path = CACHE_DIR / f"{tile_id}_prime{ANALYYSI_SUFFIKSIT[taso]}.png"
    meta_path = CACHE_DIR / f"{tile_id}.json"

    if not force and png_path.exists() and meta_path.exists():
        return png_path.read_bytes(), json.loads(meta_path.read_text())

    if tile_id not in tiles.get_registry():
        raise KeyError(f"Tuntematon tile_id: {tile_id}")

    arrays = get_or_compute_prime_arrays(tile_id, buildings_path, taso=taso, force=force)
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
    cache_path = CACHE_DIR / f"_prime_thresholds_v{LASKENTA_VERSIO}.json"
    if not force and cache_path.exists():
        vanha = json.loads(cache_path.read_text())
        if _tasokynnykset_kelpaa(vanha):
            return vanha
        print(f"  _prime_thresholds.json: vanha muoto, lasketaan uudelleen", flush=True)

    osat = {taso: [] for taso in ANALYYSI_TASOT}
    for tid in tiilet_edistymisella("Karkipaikkakynnykset"):
      for taso in ANALYYSI_TASOT:
        prime = get_or_compute_prime_arrays(tid, buildings_path, taso=taso, force=force)
        # Tasapelinpurku otetaan SAMASTA kuvasta kuin top-kerroksessa:
        # aggregoitu pistemaara saturoituu sekin, ja jarjestys tasapelien
        # sisalla on ratkaistava jotenkin.
        factors = get_or_compute_factor_arrays(tid, buildings_path, taso=taso, force=force)
        buf = prime["buffer"]
        if buf.any():
            osat[taso].append((prime["slope_b"][buf], prime["dist_b"][buf],
                               prime["rock_bit"][buf], prime["swamp_bit"][buf],
                               factors["tiebreak_b"][buf]))

    kynnykset_tasoittain = {}
    for taso, parts in osat.items():
        slope_b, dist_b, rock_bit, swamp_bit, tiebreak_b = (
            np.concatenate(c) for c in zip(*parts))
        taso_kynnykset = {}
        for factor_mask in range(1, NO_SHELTER_MASK + 1):
            rank = rank_from_components(slope_b, dist_b, rock_bit, swamp_bit,
                                        tiebreak_b, factor_mask)
            taso_kynnykset[str(factor_mask)] = kynnykset_esiasetuksille(rank)
        kynnykset_tasoittain[taso] = taso_kynnykset

    cache_path.write_text(json.dumps(kynnykset_tasoittain, indent=2))
    return kynnykset_tasoittain


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



def _konteksti(ydin, kaikki, registry):
    """Ytimen lisaksi kaikki tiilet alle MAX_FETCH_M paassa siita.

    KONTEKSTI HAETAAN KOKO REKISTERISTA, ei ytimen rykelmasta. Rykelmat
    erottaa toisistaan yksi tyhja ruutu eli 6 km, mutta pyyhkaisysade yltaa
    15 km - naapurirykelman maa siis vaikuttaa ja on oltava mukana. Rajaus
    rykelman sisaan tuottaisi liian pitkia sateita rykelmien valissa, eika
    mikaan kaatuisi."""
    yb = [registry[t].bounds for t in ydin]
    kx0 = min(b[0] for b in yb) - MAX_FETCH_M
    kx1 = max(b[2] for b in yb) + MAX_FETCH_M
    ky0 = min(b[1] for b in yb) - MAX_FETCH_M
    ky1 = max(b[3] for b in yb) + MAX_FETCH_M
    return [t for t, b in kaikki
            if b.bounds[2] > kx0 and b.bounds[0] < kx1
            and b.bounds[3] > ky0 and b.bounds[1] < ky1]


def _jaa_kaistoihin(ryhma, registry):
    """Yksi rykelma yhdeksi tai useammaksi ytimeksi muistin mukaan.

    Jaetaan ita-lansi-suunnassa: yhtenainen rykelma on Suomen rannikolla
    pitka ja kapea, joten yksi jakosuunta riittaa sen sisalla."""
    rajat = [registry[t].bounds for t in ryhma]
    _o, (h, w) = _sea_mosaic_geometry(rajat)
    if h * w <= MAX_MOSAIC_CELLS or len(ryhma) < 2:
        return [list(ryhma)]

    ryhma = sorted(ryhma, key=lambda t: registry[t].bounds[0])
    leveys = max(b[2] for b in rajat) - min(b[0] for b in rajat)
    korkeus = max(b[3] for b in rajat) - min(b[1] for b in rajat)
    # Kuinka leveaan kaistaan mahtuu MAX_MOSAIC_CELLS kun korkeus on annettu
    kaista_m = MAX_MOSAIC_CELLS * FETCH_GRID_M ** 2 / max(korkeus + 2 * MOSAIC_PAD_M, 1.0)
    kaista_m = max(kaista_m - 2 * MOSAIC_PAD_M, 6000.0)
    n = max(int(np.ceil(leveys / kaista_m)), 1)

    x0 = min(b[0] for b in rajat)
    ulos = []
    for i in range(n):
        raja0 = x0 + i * leveys / n
        raja1 = x0 + (i + 1) * leveys / n
        ydin = [t for t in ryhma
                if raja0 <= registry[t].bounds[0] < raja1
                or (i == n - 1 and registry[t].bounds[0] >= raja1)]
        if ydin:
            ulos.append(ydin)
    return ulos


def _laskenta_alueet():
    """Jakaa tiilet alueisiin joiden mosaiikki mahtuu muistiin.

    Palauttaa listan (ydin_tiilet, konteksti_tiilet). Ydin on se joukko jonka
    ruudut lasketaan tassa alueessa; konteksti sisaltaa lisaksi kaikki alle
    MAX_FETCH_M paassa olevat tiilet, jotta reunan sateet nakevat oikean maan.

    JAKO ON RYKELMITTAIN, ei suoraan ita-lansi-kaistoihin. Kaistajako oletti
    etta aineisto on yhtenainen ja kapea ("Suomen rannikko"), ja sisamaa
    rikkoi oletuksen: Paijanne on 54 km rannikosta pohjoiseen, joten yksi
    kaista venyi 204 km korkeaksi ja oli enimmakseen tyhjaa maata niiden
    valissa.

    MITATTU yhdistetylla aineistolla (493 + 48 tiilta):

        kaistajako   4 aluetta, suurin 306 M solua, YHTEENSA 842 M
        rykelmajako  3 aluetta, suurin 293 M solua, YHTEENSA 584 M

    HUIPPUMUISTI EI JUURI MUUTU (1,23 -> 1,17 Gt): rannikko on yhtenainen
    rykelma ja yha niin iso etta se jakautuu kahteen kaistaan. Hyoty on
    kokonaistyossa, -31 %, ja siina etta Paijanne lasketaan omanaan
    51 M solussa sen sijaan etta se roikkuisi 204 km korkeassa kaistassa.

    Yhdella yhtenaisella alueella tulos on tasan sama kuin ennen."""
    registry = tiles.get_registry()
    kaikki = list(registry.items())
    if len(kaikki) < 2:
        return [([t for t, _ in kaikki], [t for t, _ in kaikki])]

    alueet = []
    for ryhma in tiles.tiilirykelmat(registry):
        for ydin in _jaa_kaistoihin(ryhma, registry):
            alueet.append((ydin, _konteksti(ydin, kaikki, registry)))
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
        small = vesisto.vesi_maski(tile.bounds, tr, (m, n))
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
    cache_path = CACHE_DIR / f"_sea_mosaic_v{LASKENTA_VERSIO}.npz"
    (ox, oy), (h, w) = _sea_mosaic_geometry()

    # TIILISTO TARKISTETAAN, ei vain geometria. Mosaiikki kattaa tasan sen
    # tiilijoukon jolla se rakennettiin, ja kattamattomat ruudut jaavat
    # oletusarvoon TOSI eli avovedeksi.
    #
    # PELKKA MUOTO EI RIITA, ja tama oli oikea vika. Muoto on tiilien
    # yhteisen laatikon koko. Kemionsaaren puuttuvat ruudut ovat syvalla
    # aineiston sisalla - Ahvenanmaan ja Suomenlahden valissa - joten
    # laatikko ei muutu lainkaan kun ne lisataan. Vanha mosaiikki olisi
    # kelvannut, ja uudet ruudut olisivat jaaneet AVOVEDEKSI: pyyhkaisymatka
    # rajaton, jokainen ranta taysin altis. Mikaan ei olisi kaatunut.
    #
    # Sama koskee mita tahansa lisaysta olemassa olevan laatikon sisalle -
    # eli tasan sita tapausta jota inkrementaalinen laajennus varten on.
    sormenjalki = rekisterin_sormenjalki()
    if not force and cache_path.exists():
        data = np.load(cache_path)
        tallennettu = str(data["tiilisto"]) if "tiilisto" in data.files else None
        if (data["sea"].shape == (h, w)
                and float(data["ox"]) == ox and float(data["oy"]) == oy
                and tallennettu == sormenjalki):
            return data["sea"], (ox, oy)
        syy = ("muoto muuttunut" if data["sea"].shape != (h, w)
               else "ei tiilistotietoa" if tallennettu is None
               else "tiilisto muuttunut")
        print(f"  merimosaiikki: {syy}, rakennetaan uudelleen", flush=True)

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
        small = vesisto.vesi_maski(tile.bounds, tr, (m, n))

        col = int(round((tile.bounds[0] - ox) / FETCH_GRID_M))
        row = int(round((oy - tile.bounds[3]) / FETCH_GRID_M))
        sea[row:row + small.shape[0], col:col + small.shape[1]] = small

    np.savez_compressed(cache_path, sea=sea, ox=ox, oy=oy, tiilisto=sormenjalki)
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


def _fetch_natiiviruudukkoon(fetch_levels, obs_levels, buffer_small):
    """Pyyhkaisymatkat puskuriruudukolta (2 m/px) niiden OMAAN
    natiiviresoluutioon (FETCH_GRID_M, 10 m/px).

    TAMA EI OLE TARKKUUSHAVIO vaan turhan toiston poisto. Pyyhkaisymatkat
    lasketaan globaalilla 10 m ruudukolla (ks. global_cell_ids), ja
    _tile_mosaic_cells monistaa saman arvon puskuriruudukon 2 m soluihin -
    jokainen arvo on siis kuvassa 25 kertaa. Kuvat pienenevat 3000x3000 ->
    600x600 eli 25-kertaisesti, ja selain lukee ne oikein riippumatta
    resoluutiosta (ks. frontend: renderFactorTile skaalaa fetch-ruudukon
    naytettavaan ruudukkoon).

    ARVO OTETAAN PUSKURIPIKSELISTA, ei lohkon keskelta. Puskurivyohyke on
    vain n. 10 m leveä, joten useimmissa 10 m soluissa on seka puskuria
    etta sen ulkopuolta - keskisolu osuisi usein nollaan ja rantaviivaan
    tulisi reikia. Valitaan lahin puskuripikseli lohkon keskipisteesta, ja
    fetch seka este otetaan SAMASTA pikselista jottei pari hajoa."""
    # Montako puskuripikselia mahtuu yhteen natiiviin fetch-soluun.
    k = int(round(FETCH_GRID_M / NEW_PIXEL_FACTOR))
    h, w = buffer_small.shape
    if k <= 1 or h % k or w % k:
        return fetch_levels, obs_levels        # ei jaollinen: jatetaan ennalleen

    n_h, n_w = h // k, w // k
    puskuri = np.asarray(buffer_small, dtype=bool).reshape(n_h, k, n_w, k)
    puskuri = puskuri.transpose(0, 2, 1, 3).reshape(n_h, n_w, k * k)

    # Lohkon solut etaisyysjarjestykseen keskipisteesta: ensimmainen osuva
    # puskuripikseli voittaa, joten valinta on keskeinen eika satunnainen.
    ii, jj = np.meshgrid(np.arange(k), np.arange(k), indexing="ij")
    keskus = (k - 1) / 2.0
    jarjestys = np.argsort(((ii - keskus) ** 2 + (jj - keskus) ** 2).ravel(), kind="stable")

    lajiteltu = puskuri[:, :, jarjestys]
    on_puskuria = lajiteltu.any(axis=2)
    valinta = np.take(jarjestys, lajiteltu.argmax(axis=2))     # lohkon sisainen indeksi

    ulos = []
    for taso in (fetch_levels, obs_levels):
        lohkot = taso.reshape(n_h, k, n_w, k, taso.shape[2])
        lohkot = lohkot.transpose(0, 2, 1, 3, 4).reshape(n_h, n_w, k * k, taso.shape[2])
        otettu = np.take_along_axis(lohkot, valinta[:, :, None, None], axis=2)[:, :, 0, :]
        ulos.append(np.where(on_puskuria[:, :, None], otettu, 0).astype(taso.dtype))
    return ulos[0], ulos[1]


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

    fetch_levels, obs_levels, buffer_small = get_or_compute_fetch_levels(
        tile_id, buildings_path, force=force
    )
    fetch_levels, obs_levels = _fetch_natiiviruudukkoon(
        fetch_levels, obs_levels, buffer_small)
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
    cache_path = CACHE_DIR / f"_shelter_thresholds_v{LASKENTA_VERSIO}.json"
    if not force and cache_path.exists():
        vanha = json.loads(cache_path.read_text())
        if _tasokynnykset_kelpaa(vanha):
            return vanha
        print(f"  _shelter_thresholds.json: vanha muoto, lasketaan uudelleen", flush=True)

    osat = {taso: ([], [], []) for taso in ANALYYSI_TASOT}
    for tid in tiilet_edistymisella("Suojaisuuskynnykset"):
      for taso in ANALYYSI_TASOT:
        f = get_or_compute_factor_arrays(tid, buildings_path, taso=taso, force=force)
        p = get_or_compute_prime_arrays(tid, buildings_path, taso=taso, force=force)
        buf = f["buffer"]
        if not buf.any():
            continue
        normal, prime, tiebreak = osat[taso]
        normal.append((f["slope_b"][buf], f["dist_b"][buf], f["rock_bit"][buf], f["swamp_bit"][buf]))
        prime.append((p["slope_b"][buf], p["dist_b"][buf], p["rock_bit"][buf], p["swamp_bit"][buf]))
        tiebreak.append(f["tiebreak_b"][buf])

    kynnykset_tasoittain = {}
    for taso, (normal, prime, tiebreak) in osat.items():
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
              out[layer][str(mask)] = kynnykset_esiasetuksille(rank)
      kynnykset_tasoittain[taso] = out

    cache_path.write_text(json.dumps(kynnykset_tasoittain))
    return kynnykset_tasoittain




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
    cache_path = CACHE_DIR / f"_height_mosaic_v{LASKENTA_VERSIO}.npy"
    # TIILISTO OMASSA SIVUTIEDOSTOSSAAN: .npy kantaa vain taulukon, joten
    # tunniste ei mahdu itse tiedostoon niin kuin merimosaiikin .npz:aan.
    tiilisto_path = CACHE_DIR / f"_height_mosaic_v{LASKENTA_VERSIO}.tiilisto"
    sormenjalki = rekisterin_sormenjalki()
    if not force and cache_path.exists():
        # Sama tarkistus kuin merimosaiikilla - ne jakavat ruudukon, ja
        # samasta syysta: pelkka muoto ei huomaa laatikon SISALLE lisattyja
        # tiilia, jolloin niiden esteenkorkeus jaisi nollaan eli maasto ei
        # suojaisi niita lainkaan.
        # Muoto luetaan mmapilla, jottei gigatavun taulukkoa ladata pelkan
        # tarkistuksen takia.
        _o, muoto = _sea_mosaic_geometry()
        kurkistus = np.load(cache_path, mmap_mode="r")
        muoto_ok = kurkistus.shape == muoto
        del kurkistus
        tallennettu = tiilisto_path.read_text() if tiilisto_path.exists() else None
        if muoto_ok and tallennettu == sormenjalki:
            return np.load(cache_path)
        syy = ("muoto muuttunut" if not muoto_ok
               else "ei tiilistotietoa" if tallennettu is None
               else "tiilisto muuttunut")
        print(f"  korkeusmosaiikki: {syy}, rakennetaan uudelleen", flush=True)

    sea, origo = get_or_compute_sea_mosaic(force=force)
    height = _height_mosaic_for(list(tiles.get_registry()), sea, origo,
                                buildings_path, force=force)
    np.save(cache_path, height)
    tiilisto_path.write_text(sormenjalki)
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


def _globaalin_tila(cache_path, gid, buildings_path):
    """Paattaa MITA on laskettava - ilman mosaiikkia.

    Erotettu omaksi funktiokseen siksi, etta paatos on halpa (levyvalimuistin
    luku) mutta mosaiikin rakentaminen on kallis (161 tiilen merimaski ja
    korkeusmalli). Aiemmin mosaiikki rakennettiin ENSIN ja vasta sitten
    todettiin ettei laskettavaa ole: mitattuna koko rannikon ajossa 27
    minuuttia tiilta kohti tyota jonka tulos oli "laskettava 0 (0 %)".
    188 tiilella se on 85 tuntia tyhjaa."""
    registry = tiles.get_registry()
    nyt_tiilet = {tid: t.bounds for tid, t in registry.items()}
    nyt_sormenjaljet = {tid: lahde_sormenjalki(t, buildings_path)
                        for tid, t in registry.items()}

    jarjestys = sorted(nyt_tiilet)
    vanha_gid = vanha_fetch = vanha_obs = None
    muuttuneet = list(nyt_tiilet.values())          # oletus: kaikki
    # Onko levylla oleva METATIETO jo ajan tasalla? Jos on eika laskettavaa
    # tule, tiedostoa ei tarvitse kirjoittaa uudelleen lainkaan - koko
    # rannikolla se on satoja megatavuja pakkausta tiilta kohti.
    metatieto_sama = False
    if cache_path.exists():
        # Ladataan KERRAN: sama d kelpaa seka vanhoihin arvoihin etta
        # metatiedon vertailuun.
        d = np.load(cache_path, allow_pickle=True)
        if int(d.get("versio", 0)) == GLOBAL_CACHE_VERSION:
            vanha_gid = d["cells"]
            vanha_fetch, vanha_obs = d["fetch"], d["obstacle"]
            vanhat_idt = [str(s) for s in d["tiilet"]]
            oli = set(vanhat_idt)
            nyt = set(nyt_tiilet)
            vanhat_sj = {}
            if "sormenjaljet" in d.files:
                vanhat_sj = dict(zip(vanhat_idt, (str(x) for x in d["sormenjaljet"])))
            muuttuneet_idt = set(nyt - oli)
            for tid in nyt & oli:
                if vanhat_sj.get(tid) != nyt_sormenjaljet[tid]:
                    muuttuneet_idt.add(tid)
            muuttuneet = [nyt_tiilet[t] for t in muuttuneet_idt]
            if muuttuneet_idt:
                print(f"    muuttuneita tiilia: {len(muuttuneet_idt)}", flush=True)
            if oli - nyt:
                muuttuneet = list(nyt_tiilet.values())   # poisto: varmin on laskea kaikki
            metatieto_sama = (vanhat_idt == jarjestys and not muuttuneet_idt
                              and bool(vanhat_sj))

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

    # Solujoukko saa kasvaa vain jos tiilia on lisatty; jos se kasvoi, levylla
    # oleva tiedosto ei enaa vastaa tulosta ja on kirjoitettava.
    metatieto_ajantasalla = (metatieto_sama and vanha_gid is not None
                             and len(vanha_gid) == len(kaikki_gid))

    return {"gid": kaikki_gid, "laskettava": laskettava, "fetch": fetch,
            "obstacle": obstacle, "jarjestys": jarjestys,
            "sormenjaljet": nyt_sormenjaljet,
            "metatieto_ajantasalla": metatieto_ajantasalla}


def _talleta_globaali(cache_path, tila):
    np.savez_compressed(
        cache_path, cells=tila["gid"], fetch=tila["fetch"],
        obstacle=tila["obstacle"],
        tiilet=np.array(tila["jarjestys"], dtype=object),
        sormenjaljet=np.array([tila["sormenjaljet"][t] for t in tila["jarjestys"]],
                              dtype=object),
        versio=GLOBAL_CACHE_VERSION)


def _global_inkrementaalisesti(cache_path, gid, sea, height, origin, otsikko,
                               buildings_path):
    """Yhteinen runko pyyhkaisymatkojen ja vesiruudukon laskennalle.

    Laskee vain ne solut joita ei ole valimuistissa tai jotka ovat muuttuneen
    tiilen lahella. Ilman tata koko globaali laskenta uusittaisiin joka kerta
    kun tiilia lisataan, jolloin kasvava aineisto maksaa neliollisesti.

    MUUTTUNUT tarkoittaa seka lisattya/poistettua tiilta ETTA tiilta jonka
    lahdeaineisto on paivittynyt (ks. lahde_sormenjalki). Ilman jalkimmaista
    MML:n uusi karttalehti ei koskaan paivittaisi pyyhkaisymatkoja."""
    tila = _globaalin_tila(cache_path, gid, buildings_path)
    n = int(tila["laskettava"].sum())
    print(f"  {otsikko}: {len(tila['gid'])} ruutua, laskettava {n} "
          f"({100.0 * n / max(len(tila['gid']), 1):.0f} %)")
    _laske_globaali(cache_path, tila, sea, height, origin)
    return tila["gid"], tila["fetch"], tila["obstacle"]


def _laske_globaali(cache_path, tila, sea, height, origin):
    """Laskee tilan osoittamat solut ja tallettaa tuloksen."""
    laskettava = tila["laskettava"]
    if laskettava.any():
        rows, cols = _gid_to_mosaic_rc(tila["gid"][laskettava], origin, sea.shape)
        f, o = compute_fetch_and_obstacle((rows, cols), sea, height)
        tila["fetch"][laskettava] = f
        tila["obstacle"][laskettava] = o
    elif tila["metatieto_ajantasalla"]:
        return
    _talleta_globaali(cache_path, tila)



def _alueittain(cache_path, solut_fn, buildings_path, force, otsikko):
    """Ajaa globaalin laskennan alue kerrallaan ja yhdistaa tuloksen.

    Yhdella alueella (aineisto mahtuu yhteen mosaiikkiin) kaytetaan
    levyvalimuistitettuja mosaiikkeja, jolloin kaytos on tasan sama kuin
    ennen alueellistamista. Useammalla alueella mosaiikit rakennetaan
    lennossa - muuten koko rannikon korkeusmalli olisi 11 Gt."""
    alueet = _laskenta_alueet()
    if len(alueet) == 1:
        ydin, _konteksti = alueet[0]
        # MOSAIIKKI VASTA KUN TIEDETAAN ETTA SITA TARVITAAN. Yhdenkin alueen
        # tapauksessa se on gigatavun lataus levylta.
        tila = _globaalin_tila(cache_path, solut_fn(ydin), buildings_path)
        n = int(tila["laskettava"].sum())
        print(f"  {otsikko}: {len(tila['gid'])} ruutua, laskettava {n} "
              f"({100.0 * n / max(len(tila['gid']), 1):.0f} %)", flush=True)
        if n or not tila["metatieto_ajantasalla"]:
            sea = height = origo = None
            if n:
                sea, origo = get_or_compute_sea_mosaic(force=force)
                height = get_or_compute_height_mosaic(buildings_path, force=force)
            _laske_globaali(cache_path, tila, sea, height, origo)
        return tila["gid"], tila["fetch"], tila["obstacle"]

    print(f"  {otsikko}: aineisto jaetaan {len(alueet)} alueeseen "
          f"(mosaiikki ei mahdu kerralla muistiin)", flush=True)
    for i, (ydin, konteksti) in enumerate(alueet, 1):
        gid = solut_fn(ydin)
        tila = _globaalin_tila(cache_path, gid, buildings_path)
        n = int(tila["laskettava"].sum())
        print(f"  alue {i}/{len(alueet)}: {len(ydin)} tiilta ydinta, "
              f"{len(tila['gid'])} ruutua, laskettava {n} "
              f"({100.0 * n / max(len(tila['gid']), 1):.0f} %)", flush=True)
        if not n:
            # Tassa oli se 27 minuuttia tiilta kohti: _alueen_mosaiikit
            # rakensi 161 tiilen merimaskin ja korkeusmallin ennen kuin
            # kukaan kysyi onko tyota.
            if not tila["metatieto_ajantasalla"]:
                _talleta_globaali(cache_path, tila)
            continue
        print(f"    rakennetaan mosaiikki {len(konteksti)} tiilesta", flush=True)
        sea, height, origo = _alueen_mosaiikit(konteksti, buildings_path)
        _laske_globaali(cache_path, tila, sea, height, origo)
        del sea, height
    d = np.load(cache_path, allow_pickle=True)
    return d["cells"], d["fetch"], d["obstacle"]


# PROSESSIN SISAINEN MUISTI globaaleille tuloksille.
#
# build_static kutsuu naita KERRAN PER TIILI, ja jokainen kutsu kayo lapi
# kaikkien tiilien solutunnisteet (_tile_mosaic_cells lataa jokaisen tiilen
# osatekijataulukot) seka lataa ja mahdollisesti kirjoittaa satojen
# megatavujen valimuistitiedoston. 188 tiilella se on 188 kertaa 188 tiilen
# tyo, vaikka tulos on joka kerta sama.
#
# Levyvalimuisti ei riita: se estaa uudelleenlaskennan mutta ei
# uudelleenlukua eika solutunnisteiden kokoamista.
#
# Lahdeaineisto ei muutu kesken buildin, joten rekisterin sormenjalki
# riittaa avaimeksi.
_GLOBAALI_MUISTI = {}


def get_or_compute_fetch_global(buildings_path, force=False):
    """Laskee pyyhkaisymatkat ja esteiden korkeudet KAIKKIEN tiilien
    rantaruuduille. Globaali siksi, etta sade kulkee tiilirajojen yli - ja
    koska sadehaarukka on 13-kertainen, sama suunta kannattaa laskea vain
    kerran koko aineistolle.

    Tulos on inkrementaalinen: tiilien lisaaminen laskee uudelleen vain
    lisayksen laheiset ruudut."""
    avain = ("fetch", rekisterin_sormenjalki(), str(buildings_path))
    if not force and avain in _GLOBAALI_MUISTI:
        return _GLOBAALI_MUISTI[avain]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"_fetch_global_v{LASKENTA_VERSIO}.npz"
    if force and cache_path.exists():
        cache_path.unlink()

    def solut(tile_ids):
        osat = [_tile_mosaic_cells(t, buildings_path, force=force)[0] for t in tile_ids]
        return np.unique(np.concatenate(osat)) if osat else np.array([], dtype=np.int64)

    tulos = _alueittain(cache_path, solut, buildings_path, force, "rantaruudut")
    _GLOBAALI_MUISTI[avain] = tulos
    return tulos


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
    return vesisto.vesi_maski(tile.bounds, tr, (n, n)), n


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
    avain = ("water", rekisterin_sormenjalki(), str(buildings_path))
    if not force and avain in _GLOBAALI_MUISTI:
        return _GLOBAALI_MUISTI[avain]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"_water_global_v{LASKENTA_VERSIO}.npz"
    if force and cache_path.exists():
        cache_path.unlink()

    def solut(tile_ids):
        osat = []
        for t in tile_ids:
            tile = tiles.get_registry()[t]
            water, _n = _tile_water_mask(tile)
            osat.append(_tile_water_gids(tile)[water].ravel())
        return np.unique(np.concatenate(osat)) if osat else np.array([], dtype=np.int64)

    tulos = _alueittain(cache_path, solut, buildings_path, force, "vesiruudut")
    _GLOBAALI_MUISTI[avain] = tulos
    return tulos


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
