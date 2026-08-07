"""Todentaa etta _alueittain ei rakenna mosaiikkia kun laskettavaa ei ole."""
import numpy as np, pathlib, tempfile, types
from backend import pipeline as P, tiles

Tile = type(next(iter(tiles.get_registry().values())))

def tee_rekisteri(n):
    r = {}
    for i in range(n):
        tid = f"T{i:03d}"
        x = 300000 + i * 6000
        r[tid] = Tile(tile_id=tid, bounds=(x, 6650000, x + 6000, 6656000),
                      dem_path=pathlib.Path("/ei/ole"), map_path=pathlib.Path("/ei/ole"))
    return r

def aja(otsikko, rekisteri, alueita, cache_path, laskuri):
    tiles.get_registry = lambda: rekisteri
    P.lahde_sormenjalki = lambda t, bp: f"sj-{t.tile_id}"
    # Mosaiikit: pienet valearvot, lasketaan kutsut
    H, W = 40, 40
    def sea_mos(force=False):
        laskuri["sea"] += 1
        return np.ones((H, W), dtype=bool), (0.0, 6700000.0)
    def h_mos(bp, force=False):
        laskuri["height"] += 1
        return np.zeros((H, W), dtype=np.float32)
    def alue_mos(tids, bp):
        laskuri["alue"] += 1
        return np.ones((H, W), dtype=bool), np.zeros((H, W), dtype=np.float32), (0.0, 6700000.0)
    P.get_or_compute_sea_mosaic, P.get_or_compute_height_mosaic = sea_mos, h_mos
    P._alueen_mosaiikit = alue_mos
    P._gid_to_mosaic_rc = lambda gid, o, sh: (np.zeros(len(gid), int), np.zeros(len(gid), int))
    def laske(rc, sea, height):
        laskuri["laskenta"] += len(rc[0])
        n = len(rc[0])
        return (np.full((n, P.FETCH_SECTORS), 1234.0, np.float32),
                np.full((n, P.FETCH_SECTORS), 5.0, np.float32))
    P.compute_fetch_and_obstacle = laske
    P._dirty_mask = lambda gid, muuttuneet: np.zeros(len(gid), bool)
    if alueita > 1:
        idt = list(rekisteri)
        puoli = len(idt) // 2
        P._laskenta_alueet = lambda: [(idt[:puoli], idt), (idt[puoli:], idt)]
    else:
        P._laskenta_alueet = lambda: [(list(rekisteri), list(rekisteri))]
    solut = lambda tids: np.arange(len(tids) * 10, dtype=np.int64)
    return P._alueittain(cache_path, solut, "bp", False, otsikko)

for alueita in (1, 2):
    tmp = pathlib.Path(tempfile.mkdtemp()); cp = tmp / "g.npz"
    reg = tee_rekisteri(8)
    l = {"sea":0,"height":0,"alue":0,"laskenta":0}
    print(f"\n===== {alueita} aluetta =====")
    g1, f1, o1 = aja("testi", reg, alueita, cp, l)
    print(f"1. ajo: mosaiikkeja {l['sea']+l['alue']}, laskettuja soluja {l['laskenta']}")
    ekan = dict(l)
    g2, f2, o2 = aja("testi", reg, alueita, cp, l)
    uudet = {k: l[k]-ekan[k] for k in l}
    print(f"2. ajo (muuttumaton): mosaiikkeja {uudet['sea']+uudet['alue']}, "
          f"laskettuja soluja {uudet['laskenta']}")
    print(f"  tulos identtinen: cells {np.array_equal(g1,g2)}, "
          f"fetch {np.array_equal(f1,f2)}, obstacle {np.array_equal(o1,o2)}")
    print(f"  {'OK' if uudet['sea']+uudet['alue']==0 and uudet['laskenta']==0 else 'VIRHE'}")
    # Lisataan tiili -> pitaa laskea uudelleen
    ennen = dict(l)
    reg2 = tee_rekisteri(9)
    g3, f3, o3 = aja("testi", reg2, alueita, cp, l)
    uudet = {k: l[k]-ennen[k] for k in l}
    print(f"3. ajo (tiili lisatty): mosaiikkeja {uudet['sea']+uudet['alue']}, "
          f"laskettuja soluja {uudet['laskenta']}  "
          f"{'OK' if uudet['laskenta']>0 else 'VIRHE'}")
