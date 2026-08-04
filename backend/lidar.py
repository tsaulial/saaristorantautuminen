"""Latvuskorkeus MML:n laserkeilausaineistosta (0,5p, CC BY 4.0).

Korvaa kasvillisuuden ARVAUKSEN (saaren koko -> kiintea 12 m) MITTAUKSELLA.
Mitattuna arvaus yliarvioi yli 6 m 81 %:ssa maaruuduista - ks. instructions.md
kohta 5b.

RAAKA-AINEISTO ON VIRTA, EI VARASTO. Rannikkoalueella LAZ-tiedostoja olisi
kymmenia gigatavuja, joten jokainen lehti ladataan, muunnetaan ruudukoksi ja
POISTETAAN heti. Levylle jaa vain 10 m rasteri, joka on noin sadasosa raa'an
aineiston koosta.

KAIKKI SUUREET LASKETAAN YHDELLA LUKUKERRALLA. Uudelleenlataus maksaisi
kymmenia gigatavuja, joten samasta pistepilvesta irrotetaan kerralla myos ne
suureet joita ei viela kayteta (latvuspeittavyys, aluskasvillisuus,
pistetiheys, keilauspaiva). Niiden lisaaminen jalkikateen tarkoittaisi koko
aineiston lataamista uudestaan.

API-avain luetaan tiedostosta ~/.mml-api-key eika sita kirjoiteta lokiin
missaan tilanteessa - se on salaisuus.
"""

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from . import tiles

OGC_BASE = "https://avoin-paikkatieto.maanmittauslaitos.fi/tiedostopalvelu/ogcproc/v1"
PROCESS_ID = "laserkeilausaineisto_05_karttalehti"
API_KEY_PATH = Path.home() / ".mml-api-key"

CACHE_DIR = Path(__file__).resolve().parent.parent / "output" / "cache"
LAZ_TMP_DIR = Path(__file__).resolve().parent.parent / "output" / "lidar_tmp"

# Sama ruudukko kuin merimosaiikilla ja korkeusmosaiikilla, jotta tulos voi
# korvata kasvillisuuslisan suoraan ilman uudelleennaytteistysta.
GRID_M = 10.0

# 1:10000 karttalehti (6x6 km, esim. L3123E) jakautuu neljaan 1:5000 lehteen
# numeroilla 1-4. Neljannesten numerointia EI oleteta: jokainen lehti
# sijoitetaan sen OMIEN otsikkorajojen mukaan, joten vaara oletus ei voi
# aiheuttaa hiljaista virhetta.
SHEET_SUFFIXES = ("1", "2", "3", "4")

# Melu pois: luokat 2-5 ovat maanpinta ja kasvillisuus. Luokittelemattomia (1)
# ei oteta, koska niissa voi olla linnut ja ilmapisteet - ja este maaraytyy
# MAKSIMIKORKEUDESTA, jolloin yksi virheellinen korkea piste pilaisi ruudun.
SURFACE_CLASSES = (2, 3, 4, 5)
GROUND_CLASS = 2
LOW_VEG_CLASSES = (3, 4)      # 0-2 m: aluskasvillisuus, kuljettavuus
CANOPY_CLASSES = (3, 4, 5)

# KAKSI ERI KELPOISUUSKRITEERIA, koska suureet tarvitsevat eri asioita:
#
#   Esteen korkeus tarvitsee vain PINNAN eli mita tahansa kaikuja. Tihea
#   latvus estaa maakaiut juuri siella missa puusto on korkeinta, joten
#   maapisteiden vaatiminen hylkaisi parhaat metsaruudut.
#
#   Latvuskorkeus (korkeus maanpinnasta) tarvitsee lisaksi maanpinnan, eli
#   maapisteita. Ilman niita ei ole vertailutasoa.
#
# Mitattu tiheys maalla oli 0,38 p/m2 eli ~38 pistetta 10 m ruudussa, joten
# kolme on hyvin varovainen alaraja ja karsii lahinna vesirajan reunaruudut.
MIN_POINTS = 3
MIN_GROUND_POINTS = 3


def _api_key():
    """API-avain tiedostosta. Ei koskaan tulosteta eika palauteta lokiin."""
    if not API_KEY_PATH.exists():
        raise RuntimeError(
            f"API-avainta ei loydy: {API_KEY_PATH}. Luo se OmaTilissa ja "
            f"tallenna:  printf '%s' 'AVAIN' > {API_KEY_PATH} && chmod 600 {API_KEY_PATH}"
        )
    return API_KEY_PATH.read_text().strip()


def _request(url, key, data=None, timeout=120):
    req = urllib.request.Request(url, data=data)
    # Basic auth: avain kayttajatunnuksena, salasana tyhja (MML:n ohje).
    # Ei URL-parametrina, koska URL:t paatyvat palvelinlokeihin.
    import base64
    token = base64.b64encode(f"{key}:".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def sheet_names(tile_id):
    """1:5000 lehtinimet 1:10000 tiilelle."""
    return [f"{tile_id}{s}" for s in SHEET_SUFFIXES]


def download_sheets(sheet_ids, dest_dir, key=None):
    """Lataa lehdet LAZ-muodossa. Palauttaa {lehtinimi: polku}.

    Rajapinta on asynkroninen: POST luo tyon, jonka tilaa kysellaan kunnes se
    on valmis. Yksi kysely voi sisaltaa 100 lehtea, mutta lataamme pienemmissa
    erissa jotta osittainenkin tulos on kaytettavissa jos jokin katkeaa."""
    key = key or _api_key()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    body = json.dumps({
        "id": PROCESS_ID,
        "inputs": {
            "mapSheetInput": list(sheet_ids),
            "fileFormatInput": "LAZ",
            # "Uusin" valitsee tuoreimman keilauskerran, mika on oikein:
            # vanhentunut puustotieto on huonompi kuin tuore.
            "dataSetInput": "Uusin",
        },
    }).encode()

    job = json.loads(_request(f"{OGC_BASE}/processes/{PROCESS_ID}/execution", key, data=body))
    job_id = job["jobID"]

    for _ in range(120):                    # enintaan ~10 min
        st = json.loads(_request(f"{OGC_BASE}/jobs/{job_id}", key))
        status = st.get("status")
        if status in ("successful", "failed", "dismissed"):
            break
        time.sleep(5)
    if status != "successful":
        raise RuntimeError(f"Lataustyo epaonnistui: {status} {st.get('message','')}")

    res = json.loads(_request(f"{OGC_BASE}/jobs/{job_id}/results", key))
    out = {}
    # Listassa on lehtien lisaksi yksi yhteenvetoalkio ILMAN path-kenttaa,
    # joten .get() eika [] - suora indeksointi kaatui tahan.
    for item in res.get("results", []):
        url = item.get("path")
        if not url:
            continue
        path = dest_dir / url.rsplit("/", 1)[-1]
        path.write_bytes(_request(url, key, timeout=900))
        # Rajapinnan metatieto on TARKEMPI kuin LAS-otsake: dateOfScanning
        # antaa keilauspaivan ja project-nimi sisaltaa usein vuodenajan
        # ("..._kesa"). Tama ratkaisee lehdellinen/lehdeton-kysymyksen
        # ilman paattelya pistepilvesta.
        out[path.stem] = {
            "path": path,
            "sheet": item.get("mapSheetNumber", path.stem),
            "scan_date": item.get("dateOfScanning", ""),
            "project": item.get("project", ""),
            "density": item.get("pointDensity", ""),
            "elevation_system": item.get("elevationSystem", ""),
        }
    return out


def sheet_to_grid(laz_path):
    """Yhden lehden pistepilvi 10 m ruudukoksi.

    Palauttaa sanakirjan, jossa kaikki suureet yhdelta lukukerralta seka
    ruudukon origo ja koko. Korkeus on PINNAN KORKEUS MERENPINNASTA (N2000),
    ei latvuskorkeus maanpinnasta: tuulensuoja riippuu esteen korkeudesta sen
    pinnan ylapuolella jota pitkin tuuli kulkee, ja aineiston z on jo N2000.
    Nain valtetaan myos maanpintareferenssin valinta rinteissa."""
    import laspy

    with laspy.open(str(laz_path)) as fh:
        hdr = fh.header
        pts = fh.read()

    x = np.asarray(pts.x)
    y = np.asarray(pts.y)
    z = np.asarray(pts.z)
    cls = np.asarray(pts.classification)

    # Ruudukko lehden OMISTA rajoista, pyoristettyna GRID_M:n monikertaan.
    x0 = np.floor(hdr.mins[0] / GRID_M) * GRID_M
    y1 = np.ceil(hdr.maxs[1] / GRID_M) * GRID_M
    nx = int(np.ceil((hdr.maxs[0] - x0) / GRID_M))
    ny = int(np.ceil((y1 - hdr.mins[1]) / GRID_M))

    col = np.clip(((x - x0) / GRID_M).astype(np.int64), 0, nx - 1)
    row = np.clip(((y1 - y) / GRID_M).astype(np.int64), 0, ny - 1)
    idx = row * nx + col
    n = nx * ny

    surface = np.isin(cls, SURFACE_CLASSES)
    ground = cls == GROUND_CLASS
    canopy = np.isin(cls, CANOPY_CLASSES)
    lowveg = np.isin(cls, LOW_VEG_CLASSES)

    # -inf alustus EIKA NaN: np.maximum(nan, x) on nan, mika nollaisi koko
    # tuloksen hiljaisesti. Tama virhe tehtiin kerran jo kokeiluvaiheessa.
    NEG = np.float32(-1e9)
    top = np.full(n, NEG, dtype=np.float32)
    np.maximum.at(top, idx[surface], z[surface].astype(np.float32))

    ground_top = np.full(n, NEG, dtype=np.float32)
    np.maximum.at(ground_top, idx[ground], z[ground].astype(np.float32))

    n_all = np.bincount(idx, minlength=n).astype(np.int32)
    n_ground = np.bincount(idx[ground], minlength=n).astype(np.int32)
    n_canopy = np.bincount(idx[canopy], minlength=n).astype(np.int32)
    n_low = np.bincount(idx[lowveg], minlength=n).astype(np.int32)

    valid = n_ground >= MIN_GROUND_POINTS          # latvuskorkeutta varten
    has_surface = n_all >= MIN_POINTS                  # esteen korkeutta varten
    surface_top = np.where(top > NEG / 2, top, 0.0).astype(np.float32)
    terrain_top = np.where(ground_top > NEG / 2, ground_top, 0.0).astype(np.float32)

    return {
        "x0": x0, "y1": y1, "nx": nx, "ny": ny,
        "surface_top": surface_top.reshape(ny, nx),     # latvuston/maan huippu, N2000
        "terrain_top": terrain_top.reshape(ny, nx),     # pelkka maanpinta, N2000
        "valid": valid.reshape(ny, nx),
        "has_surface": has_surface.reshape(ny, nx),
        "n_all": n_all.reshape(ny, nx),
        "canopy_cover": np.where(n_all > 0, n_canopy / np.maximum(n_all, 1), 0.0
                                 ).astype(np.float32).reshape(ny, nx),
        "undergrowth": np.where(n_all > 0, n_low / np.maximum(n_all, 1), 0.0
                                ).astype(np.float32).reshape(ny, nx),
        "scan_date": str(hdr.creation_date) if hdr.creation_date else "",
    }


def get_or_compute_tile_surface(tile_id, force=False, keep_laz=False):
    """Tiilen pintamalli laserkeilauksesta, 10 m ruudukolla.

    Lataa neljä 1:5000 lehteä, laskee suureet ja POISTAA LAZ-tiedostot.
    Valimuistissa on vain rasteri."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{tile_id}_lidar.npz"
    if not force and cache_path.exists():
        return dict(np.load(cache_path, allow_pickle=True))

    tile = tiles.get_registry()[tile_id]
    x0 = float(tile.bounds[0])
    y1 = float(tile.bounds[3])
    nx = int(round((tile.bounds[2] - tile.bounds[0]) / GRID_M))
    ny = int(round((tile.bounds[3] - tile.bounds[1]) / GRID_M))

    surface = np.zeros((ny, nx), dtype=np.float32)
    terrain = np.zeros((ny, nx), dtype=np.float32)
    valid = np.zeros((ny, nx), dtype=bool)
    has_surface = np.zeros((ny, nx), dtype=bool)
    n_points = np.zeros((ny, nx), dtype=np.int32)
    cover = np.zeros((ny, nx), dtype=np.float32)
    under = np.zeros((ny, nx), dtype=np.float32)
    dates = []

    tmp = LAZ_TMP_DIR / tile_id
    try:
        got = download_sheets(sheet_names(tile_id), tmp)
        for name, meta in sorted(got.items()):
            path = meta["path"]
            g = sheet_to_grid(path)
            # Sijoitus lehden OMIEN rajojen mukaan - ei oleteta neljannesten
            # numerointia, joten vaara oletus ei voi mennä lapi huomaamatta.
            c0 = int(round((g["x0"] - x0) / GRID_M))
            r0 = int(round((y1 - g["y1"]) / GRID_M))
            c1, r1 = c0 + g["nx"], r0 + g["ny"]
            sc0, sr0 = max(c0, 0), max(r0, 0)
            sc1, sr1 = min(c1, nx), min(r1, ny)
            if sc1 <= sc0 or sr1 <= sr0:
                continue
            dst = (slice(sr0, sr1), slice(sc0, sc1))
            src = (slice(sr0 - r0, sr1 - r0), slice(sc0 - c0, sc1 - c0))
            surface[dst] = np.maximum(surface[dst], g["surface_top"][src])
            terrain[dst] = np.maximum(terrain[dst], g["terrain_top"][src])
            valid[dst] |= g["valid"][src]
            has_surface[dst] |= g["has_surface"][src]
            n_points[dst] = np.maximum(n_points[dst], g["n_all"][src])
            cover[dst] = np.maximum(cover[dst], g["canopy_cover"][src])
            under[dst] = np.maximum(under[dst], g["undergrowth"][src])
            # Rajapinnan paivays ensisijainen, LAS-otsake varalla.
            pvm = meta.get("scan_date") or g["scan_date"]
            dates.append(f"{meta.get('sheet', name)}:{pvm}:{meta.get('project','')}")
            path.unlink(missing_ok=True)     # LAZ pois heti - virta, ei varasto
    finally:
        if not keep_laz:
            shutil.rmtree(tmp, ignore_errors=True)

    out = {
        "surface_top": surface, "terrain_top": terrain, "valid": valid,
        "has_surface": has_surface, "n_points": n_points,
        "canopy_cover": cover, "undergrowth": under,
        "scan_dates": np.array(dates, dtype=object),
    }
    np.savez_compressed(cache_path, **out)
    return out


def canopy_height(tile_id, force=False):
    """Latvuskorkeus maanpinnasta (m). Nolla siella missa mittausta ei ole."""
    d = get_or_compute_tile_surface(tile_id, force=force)
    h = d["surface_top"] - d["terrain_top"]
    return np.where(d["valid"], np.clip(h, 0.0, 50.0), 0.0).astype(np.float32)


def surface_top(tile_id, force=False):
    """Esteen huipun korkeus merenpinnasta (N2000, m) ja kelpoisuusmaski.

    Tama on se suure jota tuulensuoja tarvitsee: latvuston tai maanpinnan
    huippu sen pinnan ylapuolella jota pitkin tuuli kulkee. Ei vaadi
    maapisteita, joten tihea metsa ei putoa pois."""
    d = get_or_compute_tile_surface(tile_id, force=force)
    return d["surface_top"].astype(np.float32), d["has_surface"].astype(bool)


def have_lidar(tile_id):
    return (CACHE_DIR / f"{tile_id}_lidar.npz").exists()
