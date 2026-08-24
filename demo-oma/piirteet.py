#!/usr/bin/env python3
"""Vaihe 1: piirrevektori rantaehdokkaille (clusterplan.md).

DEMO. Ei kosketa tuotantoon: lukee vain docs/cache/-kuvia ja kirjoittaa
tulokset demo-oma/-hakemistoon.

Ehdokkaat sijoitetaan EHDOKAS_VALI_M valein rantaviivaa pitkin, ja piirteet
luetaan ehdokkaan ymparilta PUSKURIVYOHYKKEELTA - ei yhdesta pikselista.
Yksi pikseli olisi kohinaa: 2 m/px ruudukolla se on satunnainen naytepiste
rantakaistaleesta joka on 5-15 m leveä.

Lahdeaineisto on tasan se mita selain jo lataa:
  {tiili}_factors.png   R=jyrkkyys  G=etaisyys  B=bitit(0=kallio,1=suo)  A=puskuri
  {tiili}_tiebreak.png  R=tasapelinpurku
  {tiili}_fetcha/b.png  12 pyyhkaisysektoria 4-bittisina puolitavuina
"""
import json
import math
import pathlib
import sys

import cv2
import numpy as np

JUURI = pathlib.Path(__file__).resolve().parent.parent
CACHE = JUURI / "docs" / "cache"
ULOS = JUURI / "demo-oma"

KESKIPISTE = (386385.0, 6671827.0)      # Helsingin Kauppatori, EPSG:3067
SADE_M = 50000.0
EHDOKAS_VALI_M = 50.0
IKKUNA_SADE_M = 25.0                    # piirreikkunan sade
PX_M = 2.0                              # docs/cache-kuvien resoluutio
FETCH_SEKTOREITA = 12

# Pyyhkaisysektorin keskisuunta asteina, 0 = pohjoinen, kasvaa myotapaivaan
# (pipeline.sector_bearing). Sama sopimus kuin tuulen suunnalla.
SEKTORIN_ASTE = 360.0 / FETCH_SEKTOREITA

PIIRTEET = (
    ["jyrkkyys", "etaisyys_rakennuksiin", "kallio", "suo",
     "tasapelinpurku", "puskurin_leveys"]
    + [f"pyyhkaisy_{i}" for i in range(FETCH_SEKTOREITA)]
)


def testialueen_tiilet():
    """Tiilet SADE_M etaisyydella keskipisteesta, joilla on puskuria."""
    tl = json.loads((JUURI / "docs" / "tiles.json").read_text())["tiles"]
    ulos = []
    for t in tl:
        b = t["bounds_epsg3067"]
        dx = max(b["minx"] - KESKIPISTE[0], 0.0, KESKIPISTE[0] - b["maxx"])
        dy = max(b["miny"] - KESKIPISTE[1], 0.0, KESKIPISTE[1] - b["maxy"])
        if math.hypot(dx, dy) <= SADE_M and (CACHE / f"{t['tile_id']}_factors.png").exists():
            ulos.append((t["tile_id"],
                         (b["minx"], b["miny"], b["maxx"], b["maxy"])))
    return ulos


def _lue(nimi):
    im = cv2.imread(str(CACHE / nimi), cv2.IMREAD_UNCHANGED)
    if im is None:
        raise FileNotFoundError(nimi)
    return im


def tiilen_kanavat(tile_id):
    """Purkaa tiilen kuvat taulukoiksi. None jos tiilessa ei ole puskuria."""
    f = _lue(f"{tile_id}_factors.png")           # cv2 lukee BGRA
    alfa = f[:, :, 3]
    if not alfa.any():
        return None
    tb = _lue(f"{tile_id}_tiebreak.png")
    fa = _lue(f"{tile_id}_fetcha.png")
    fb = _lue(f"{tile_id}_fetchb.png")

    # Tiedostossa RGBA, cv2:n taulukossa BGRA -> R on indeksi 2.
    sektorit = np.empty(f.shape[:2] + (FETCH_SEKTOREITA,), dtype=np.uint8)
    for kuva, base in ((fa, 0), (fb, 6)):
        for i in range(3):
            kanava = kuva[:, :, 2 - i]           # R, G, B
            sektorit[:, :, base + 2 * i] = kanava >> 4
            sektorit[:, :, base + 2 * i + 1] = kanava & 0x0F
    return {
        "puskuri": alfa > 0,
        "jyrkkyys": f[:, :, 2],
        "etaisyys": f[:, :, 1],
        "bitit": f[:, :, 0],
        "tasapeli": tb[:, :, 2],
        "sektorit": sektorit,
    }


def rantaviivat(tiilet):
    """Vesialueiden reunat testialueella yhtena geometriana."""
    from shapely import union_all
    from shapely.geometry import box
    import pyogrio
    from backend import vesisto

    x0 = min(b[0] for _, b in tiilet); y0 = min(b[1] for _, b in tiilet)
    x1 = max(b[2] for _, b in tiilet); y1 = max(b[3] for _, b in tiilet)
    palat = []
    for taso in vesisto.VESI_TASOT:
        try:
            df = pyogrio.read_dataframe(vesisto.GPKG, layer=taso,
                                        bbox=(x0, y0, x1, y1))
        except Exception:
            continue
        if len(df):
            palat.extend(df.geometry.values)
    if not palat:
        raise RuntimeError("vesialueita ei loytynyt")
    vesi = union_all(palat)
    return vesi, vesi.boundary.intersection(box(x0, y0, x1, y1))


def ehdokkaat(reuna, vesi):
    """Pisteet EHDOKAS_VALI_M valein + merensuuntainen kulma asteina."""
    from shapely.geometry import LineString, MultiLineString, Point
    from shapely.prepared import prep
    valmis = prep(vesi)
    viivat = ([reuna] if isinstance(reuna, LineString)
              else list(reuna.geoms) if isinstance(reuna, MultiLineString)
              else [g for g in getattr(reuna, "geoms", []) if isinstance(g, LineString)])
    xs, ys, kulmat = [], [], []
    for viiva in viivat:
        L = viiva.length
        if L < EHDOKAS_VALI_M:
            continue
        for d in np.arange(EHDOKAS_VALI_M / 2, L, EHDOKAS_VALI_M):
            p = viiva.interpolate(d)
            e = min(5.0, L / 4)
            a = viiva.interpolate(max(d - e, 0.0))
            b = viiva.interpolate(min(d + e, L))
            tx, ty = b.x - a.x, b.y - a.y
            n = math.hypot(tx, ty)
            if n < 1e-9:
                continue
            # Normaali on tangentti kaannettyna 90 astetta. Kumpi puoli on
            # MERTA ratkaistaan kokeilemalla - polygonin kiertosuuntaan ei
            # voi luottaa, koska reunoja on seka ulko- etta sisakehilta.
            nx, ny = -ty / n, tx / n
            if valmis.contains(Point(p.x + nx * 5.0, p.y + ny * 5.0)):
                pass
            elif valmis.contains(Point(p.x - nx * 5.0, p.y - ny * 5.0)):
                nx, ny = -nx, -ny
            else:
                continue                       # kumpikaan puoli ei ole vetta
            xs.append(p.x); ys.append(p.y)
            kulmat.append(math.degrees(math.atan2(nx, ny)) % 360.0)
    return np.array(xs), np.array(ys), np.array(kulmat)


def poimi_piirteet(tiilet, xs, ys, kulmat):
    """Piirteet ehdokkaille. Palauttaa (piirteet, kelpaa)."""
    n = len(xs)
    d = len(PIIRTEET)
    ulos = np.full((n, d), np.nan, dtype=np.float32)
    r_px = int(round(IKKUNA_SADE_M / PX_M))
    yy, xx = np.mgrid[-r_px:r_px + 1, -r_px:r_px + 1]
    kiekko = (yy * yy + xx * xx) <= r_px * r_px
    ikkunan_halkaisija_m = 2 * IKKUNA_SADE_M

    for k, (tile_id, b) in enumerate(tiilet, 1):
        osuu = np.where((xs >= b[0]) & (xs < b[2]) & (ys >= b[1]) & (ys < b[3]))[0]
        if not len(osuu):
            continue
        kanavat = tiilen_kanavat(tile_id)
        if kanavat is None:
            continue
        H, W = kanavat["puskuri"].shape
        cc = ((xs[osuu] - b[0]) / PX_M).astype(np.int32)
        rr = ((b[3] - ys[osuu]) / PX_M).astype(np.int32)
        for j, i in enumerate(osuu):
            r0, r1 = rr[j] - r_px, rr[j] + r_px + 1
            c0, c1 = cc[j] - r_px, cc[j] + r_px + 1
            if r0 < 0 or c0 < 0 or r1 > H or c1 > W:
                continue
            m = kanavat["puskuri"][r0:r1, c0:c1] & kiekko
            npx = int(m.sum())
            if npx < 8:                       # liian vahan rantaa ikkunassa
                continue
            ulos[i, 0] = kanavat["jyrkkyys"][r0:r1, c0:c1][m].mean()
            ulos[i, 1] = kanavat["etaisyys"][r0:r1, c0:c1][m].mean()
            bitit = kanavat["bitit"][r0:r1, c0:c1][m]
            ulos[i, 2] = float((bitit & 1 > 0).mean())
            ulos[i, 3] = float((bitit & 2 > 0).mean())
            ulos[i, 4] = kanavat["tasapeli"][r0:r1, c0:c1][m].mean()
            # Puskurin leveys: kaistale halkoo ikkunan, joten
            # pinta-ala / halkaisija on sen keskimaarainen leveys.
            ulos[i, 5] = npx * PX_M * PX_M / ikkunan_halkaisija_m
            sekt = kanavat["sektorit"][r0:r1, c0:c1][m].mean(axis=0)
            # SEKTORIT KIERRETAAN RANTAVIIVAN NORMAALIN MUKAAN, ei kompassin.
            # Ilman tata kaksi identtista poukamaa saaren eri puolilla
            # nayttavat taysin erilaisilta - ks. clusterplan.md vaihe 1.
            siirto = int(round(kulmat[i] / SEKTORIN_ASTE)) % FETCH_SEKTOREITA
            ulos[i, 6:] = np.roll(sekt, -siirto)
        print(f"  [{k}/{len(tiilet)}] {tile_id}: {len(osuu)} ehdokasta", flush=True)

    kelpaa = np.isfinite(ulos).all(axis=1)
    return ulos, kelpaa


def kvantiloi(piirteet):
    """Jokainen piirre kvantiiliksi [0,1] koko aineiston yli.

    Etaisyydet tulevat vertailukelpoisiksi yksikoista riippumatta ja tulos on
    tulkittava: 0,3 tarkoittaa 30 prosenttiyksikkoa. Kestaa myos poikkeamat
    toisin kuin z-pisteytys."""
    ulos = np.empty_like(piirteet)
    for j in range(piirteet.shape[1]):
        s = piirteet[:, j]
        jarjestys = np.argsort(s, kind="stable")
        sijat = np.empty(len(s), dtype=np.float64)
        sijat[jarjestys] = np.arange(len(s))
        # Sidokset samaan arvoon: keskimaarainen sija, muuten kvantiili
        # riippuisi lajittelun sattumasta.
        arvot, alku, maara = np.unique(s, return_index=True, return_counts=True)
        for a, m in zip(alku, maara):
            if m > 1:
                osuma = jarjestys[a:a + m]
                sijat[osuma] = sijat[osuma].mean()
        ulos[:, j] = sijat / max(len(s) - 1, 1)
    return ulos


def main():
    sys.path.insert(0, str(JUURI))
    tiilet = testialueen_tiilet()
    print(f"testialue: {len(tiilet)} tiilta")
    vesi, reuna = rantaviivat(tiilet)
    print(f"rantaviivaa: {reuna.length/1000:.0f} km")
    xs, ys, kulmat = ehdokkaat(reuna, vesi)
    print(f"ehdokkaita {EHDOKAS_VALI_M:.0f} m valein: {len(xs):,}")
    piirteet, kelpaa = poimi_piirteet(tiilet, xs, ys, kulmat)
    print(f"\npiirteet saatu {int(kelpaa.sum()):,} ehdokkaalle "
          f"({100*kelpaa.mean():.1f} %)")

    xs, ys, kulmat = xs[kelpaa], ys[kelpaa], kulmat[kelpaa]
    raaka = piirteet[kelpaa]
    q = kvantiloi(raaka)

    ULOS.mkdir(exist_ok=True)
    np.savez_compressed(ULOS / "ehdokkaat_raaka.npz",
                        x=xs, y=ys, kulma=kulmat, piirteet=raaka,
                        nimet=np.array(PIIRTEET))
    (ULOS / "piirteet.bin").write_bytes(
        np.clip(np.rint(q * 255), 0, 255).astype(np.uint8).tobytes())
    np.stack([np.rint(xs), np.rint(ys)], axis=1).astype(np.int32).tofile(
        ULOS / "koordinaatit.bin")
    (ULOS / "piirteet.json").write_text(json.dumps({
        "n": int(len(xs)), "d": len(PIIRTEET), "nimet": PIIRTEET,
        "vali_m": EHDOKAS_VALI_M, "ikkuna_m": IKKUNA_SADE_M,
        "keskipiste": KESKIPISTE, "sade_m": SADE_M,
    }, indent=1))
    print(f"kirjoitettu {ULOS}: piirteet.bin "
          f"{len(xs)*len(PIIRTEET)/1e6:.1f} Mt, koordinaatit.bin "
          f"{len(xs)*8/1e6:.1f} Mt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
