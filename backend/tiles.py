"""
Tiilirekisteri: yhdistaa korkeusmalli-mml/-hakemiston DEM-tiilet niita
peittaviin karttakuva-mll/-hakemiston karttalehtiin.

DEM-tiilet (esim. L3114D) ja karttakuvatiilet (esim. L3114L) kayttavat eri
lehtijakoa - yhdistaminen tehdaan koordinaattien (bounds), ei tiedostonimien,
perusteella (ks. instructions.md kohta 2).
"""

from dataclasses import dataclass
from pathlib import Path

import rasterio

DEM_DIR = Path(__file__).resolve().parent.parent / "korkeusmalli-mml"
MAP_DIR = Path(__file__).resolve().parent.parent / "karttakuva-mll"


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


REGISTRY = None


def get_registry():
    global REGISTRY
    if REGISTRY is None:
        REGISTRY = build_registry()
    return REGISTRY


if __name__ == "__main__":
    for tile_id, tile in build_registry().items():
        print(f"{tile_id}: dem={tile.dem_path.name}  map={tile.map_path.name}  bounds={tile.bounds}")
