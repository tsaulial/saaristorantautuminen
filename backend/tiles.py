"""
Tiilirekisteri: yhdistaa korkeusmalli-mml/-hakemiston DEM-tiilet niita
peittaviin karttakuva-mll/-hakemiston karttalehtiin.

DEM-tiilet (esim. L3114D) ja karttakuvatiilet (esim. L3114L) kayttavat eri
lehtijakoa - yhdistaminen tehdaan koordinaattien (bounds), ei tiedostonimien,
perusteella (ks. instructions.md kohta 2).
"""

import json
from dataclasses import dataclass
from pathlib import Path

import rasterio

DEM_DIR = Path(__file__).resolve().parent.parent / "korkeusmalli-mml"
MAP_DIR = Path(__file__).resolve().parent.parent / "karttakuva-mll"
ROOT_OUT = Path(__file__).resolve().parent.parent / "output" / "cache"


@dataclass(frozen=True)
class Tile:
    tile_id: str
    dem_path: Path
    map_path: Path
    bounds: tuple  # (minx, miny, maxx, maxy) EPSG:3067


def _tif_bounds(path):
    with rasterio.open(path) as ds:
        return tuple(ds.bounds)


def _contains(outer, inner, tol=1.0):
    ominx, ominy, omaxx, omaxy = outer
    iminx, iminy, imaxx, imaxy = inner
    return (
        ominx - tol <= iminx
        and ominy - tol <= iminy
        and omaxx + tol >= imaxx
        and omaxy + tol >= imaxy
    )


def build_registry(dem_dir=DEM_DIR, map_dir=MAP_DIR):
    """Palauttaa dictin {tile_id: Tile} kaikille DEM-tiileille, joilla on
    peittava karttakuva-tiili. DEM-tiilet ilman osumaa jatetaan pois ja
    tulostetaan varoitus (ei pitaisi tapahtua nykyisella aineistolla)."""
    map_tiles = [(p, _tif_bounds(p)) for p in sorted(map_dir.glob("*.png"))]

    registry = {}
    for dem_path in sorted(dem_dir.glob("*.tif")):
        dem_bounds = _tif_bounds(dem_path)
        match = next(
            (p for p, b in map_tiles if _contains(b, dem_bounds)),
            None,
        )
        if match is None:
            print(f"VAROITUS: DEM-tiilelle {dem_path.name} ei loytynyt peittavaa karttakuvaa")
            continue
        tile_id = dem_path.stem
        registry[tile_id] = Tile(
            tile_id=tile_id,
            dem_path=dem_path,
            map_path=match,
            bounds=dem_bounds,
        )
    return registry


def tiilirykelmat(registry=None):
    """Tiilet jaettuna YHTENAISIIN rykelmiin.

    Palauttaa listan tile_id-listoja. Kaksi tiilta kuuluvat samaan rykelmaan
    jos ne koskettavat toisiaan reunasta tai kulmasta 6 km ruudukossa.

    MIKSI TAMA ON YHTEINEN. Aineisto ei ole yhtenainen: Ahvenanmaan ja
    Helsingin edustan vali on yli 200 km, ja Paijanne on 54 km lahimmasta
    rannikkotiilesta. Kaksi eri paikkaa tarvitsee taman saman jaon eri
    syista - vektorihaut sailyttaakseen kyselyt pienina
    (vektoritasot.alueen_bboxit) ja pyyhkaisymatkat mahtuakseen muistiin
    (pipeline._laskenta_alueet) - joten se on tassa kerran eika kahdesti.

    Ruudun koko luetaan ensimmaisesta tiilesta, ei oleteta 6 km:ksi."""
    registry = registry if registry is not None else get_registry()
    if not registry:
        return []
    eka = next(iter(registry.values()))
    koko = eka.bounds[2] - eka.bounds[0]
    ruudut = {}
    for tid, t in registry.items():
        ruudut[(int(round(t.bounds[0] / koko)), int(round(t.bounds[1] / koko)))] = tid

    kaymatta = set(ruudut)
    rykelmat = []
    while kaymatta:
        alku = kaymatta.pop()
        pino, ryhma = [alku], [alku]
        while pino:
            gx, gy = pino.pop()
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    naapuri = (gx + dx, gy + dy)
                    if naapuri in kaymatta:
                        kaymatta.remove(naapuri)
                        pino.append(naapuri)
                        ryhma.append(naapuri)
        rykelmat.append([ruudut[r] for r in ryhma])
    # Suurin ensin: lokin lukija nakee heti paaalueen.
    return sorted(rykelmat, key=len, reverse=True)


# Rannattomien tiilien tunnistus.
#
# MITATTU: 7 % tiilista ei sisalla yhtaan rantaviivaa - ne ovat joko
# kokonaan sisamaata tai kokonaan avovetta. Ne vievat taydet 211 Mt
# raakadataa ja tayden laskenta-ajan, eivatka nayta kartalla mitaan.
#
# TAMA EI POISTA NIITA REKISTERISTA, ja se on tietoinen ero. Mosaiikin
# kattamaton alue oletetaan VEDEKSI (ks. pipeline: "Oletus TOSI =
# tuntematon kasitellaan avovetena"), joten kokonaan maalla olevan tiilen
# poistaminen muuttaisi maan mereksi ja pyyhkaisysateet lapaisisivat sen.
# Mitattuna kuudesta rannattomasta VIISI oli kokonaan maalla, joten vika
# olisi ollut tavallinen eika harvinainen - ja hiljainen.
#
# Rekisteri pysyy siis ennallaan laskentaa varten; tuotanto ohittaa nama.
RANNATON_RUUDUKKO = 600          # 10 m/px, sama kuin FETCH_GRID_M
_RANNATTOMAT = None


def _rannattomien_valimuisti():
    from . import vesisto
    polku = ROOT_OUT / "_rannattomat.json"
    tunnus = None
    if vesisto.GPKG.exists():
        tila = vesisto.GPKG.stat()
        tunnus = f"{tila.st_size}:{tila.st_mtime_ns}:{len(get_registry())}"
    return polku, tunnus


def rannattomat():
    """Tiilet joissa ei ole rantaviivaa: kokonaan vetta tai kokonaan maata.

    Testi on halpa (10 m ruudukko, n. 85 ms/tiili) ja tulos tallennetaan,
    koska sita kysytaan monessa kohdassa. Jos vesiaineistoa ei ole, EI
    OHITETA MITAAN - tuntematon ei ole sama kuin rannaton."""
    global _RANNATTOMAT
    from . import vesisto
    if not vesisto.saatavilla():
        return frozenset()

    polku, tunnus = _rannattomien_valimuisti()
    if _RANNATTOMAT is not None and _RANNATTOMAT[0] == tunnus:
        return _RANNATTOMAT[1]
    if polku.exists():
        try:
            tallennettu = json.loads(polku.read_text())
            if tallennettu.get("tunnus") == tunnus:
                _RANNATTOMAT = (tunnus, frozenset(tallennettu["tiilet"]))
                return _RANNATTOMAT[1]
        except (ValueError, KeyError):
            pass

    from rasterio.transform import from_origin
    n = RANNATON_RUUDUKKO
    ulos = []
    reg = get_registry()
    for tile_id, t in reg.items():
        tr = from_origin(t.bounds[0], t.bounds[3],
                         (t.bounds[2] - t.bounds[0]) / n,
                         (t.bounds[3] - t.bounds[1]) / n)
        osuus = vesisto.vesi_maski(t.bounds, tr, (n, n)).mean()
        if osuus == 0.0 or osuus == 1.0:
            ulos.append(tile_id)
    print(f"  rannattomia tiilia {len(ulos)}/{len(reg)} "
          f"({100 * len(ulos) / max(len(reg), 1):.1f} %) - ohitetaan tuotannossa",
          flush=True)
    polku.parent.mkdir(parents=True, exist_ok=True)
    polku.write_text(json.dumps({"tunnus": tunnus, "tiilet": sorted(ulos)}))
    _RANNATTOMAT = (tunnus, frozenset(ulos))
    return _RANNATTOMAT[1]


def tuotantotiilet():
    """Rekisteri ilman rannattomia tiilia: se joukko jolle tuotteet tehdaan.

    Laskenta (mosaiikit, konteksti) kayttaa get_registry():a sellaisenaan."""
    ohita = rannattomat()
    return {k: v for k, v in get_registry().items() if k not in ohita}


REGISTRY = None


def get_registry():
    global REGISTRY
    if REGISTRY is None:
        REGISTRY = build_registry()
    return REGISTRY


if __name__ == "__main__":
    for tile_id, tile in build_registry().items():
        print(f"{tile_id}: dem={tile.dem_path.name}  map={tile.map_path.name}  bounds={tile.bounds}")
