#!/usr/bin/env python3
"""Rantautumiskelpoisen maan MAARA ehdokkaan ymparilla.

MIKSI TAMA ON ERI ASIA KUIN "rantavyohyketta ymparilla". Puskurivyohyke on
ARVIOITU vyohyke - kaikki maa 5-15 m rantaviivasta - ei kelvollinen. Laaja
mutta jyrkka kallioranta ja laaja loiva hiekkaranta saavat siita saman
arvon, vaikka toiselle voi nousta ja toiselle ei.

Tama mittaa pinta-alan PISTEMAARALLA PAINOTETTUNA: jokainen puskuripikseli
25 m sateella lasketaan mukaan sen mukaan, kuinka hyva rantautumispaikka se
on. Tulos on neliometreja "kelvollista rantaa", ja se kertoo paikan
LAAJUUDESTA - mahtuuko siihen vene ja teltta vai vain jalansija.

Pistemaara lasketaan pipeline.score_from_components:lla eli TASAN samalla
funktiolla kuin kartan varit, ilman suojaisuutta (NO_SHELTER_MASK): tuuli
on dynaaminen eika kuulu staattiseen piirteeseen.
"""
import pathlib
import sys

import numpy as np

JUURI = pathlib.Path(__file__).resolve().parent.parent
ULOS = JUURI / "demo-oma"
PX_M = 2.0
IKKUNA_SADE_M = 25.0


def main():
    sys.path.insert(0, str(JUURI))
    sys.path.insert(0, str(ULOS))
    from backend import pipeline
    from piirteet import testialueen_tiilet, tiilen_kanavat

    maski = pipeline.NO_SHELTER_MASK
    print(f"tekijamaski {maski} (staattiset tekijat ilman suojaisuutta)")

    d = np.load(ULOS / "ehdokkaat_raaka.npz", allow_pickle=True)
    xs, ys = d["x"], d["y"]
    ulos = np.full(len(xs), np.nan, dtype=np.float32)

    r_px = int(round(IKKUNA_SADE_M / PX_M))
    yy, xx = np.mgrid[-r_px:r_px + 1, -r_px:r_px + 1]
    kiekko = (yy * yy + xx * xx) <= r_px * r_px
    pikselin_ala = PX_M * PX_M

    tiilet = testialueen_tiilet()
    for k, (tile_id, b) in enumerate(tiilet, 1):
        osuu = np.where((xs >= b[0]) & (xs < b[2]) & (ys >= b[1]) & (ys < b[3]))[0]
        if not len(osuu):
            continue
        kan = tiilen_kanavat(tile_id)
        if kan is None:
            continue
        pisteet = pipeline.score_from_components(
            kan["jyrkkyys"].astype(np.float64), kan["etaisyys"].astype(np.float64),
            (kan["bitit"] & 1) > 0, (kan["bitit"] & 2) > 0, maski)
        pisteet = np.where(kan["puskuri"], pisteet, 0.0)
        H, W = kan["puskuri"].shape
        cc = ((xs[osuu] - b[0]) / PX_M).astype(np.int32)
        rr = ((b[3] - ys[osuu]) / PX_M).astype(np.int32)
        for j, i in enumerate(osuu):
            r0, r1 = rr[j] - r_px, rr[j] + r_px + 1
            c0, c1 = cc[j] - r_px, cc[j] + r_px + 1
            if r0 < 0 or c0 < 0 or r1 > H or c1 > W:
                continue
            ulos[i] = float(pisteet[r0:r1, c0:c1][kiekko].sum()) * pikselin_ala
        print(f"  [{k}/{len(tiilet)}] {tile_id}: {len(osuu)}", flush=True)

    ulos = np.nan_to_num(ulos, nan=0.0)
    np.savez_compressed(ULOS / "kelpoala.npz", ala=ulos,
                        nimet=np.array(["kelvollista_rantaa"]))
    print(f"\n{len(ulos):,} ehdokasta, kelvollista rantaa 25 m sateella:")
    for p in (0, 10, 25, 50, 75, 90, 100):
        print(f"  p{p:<3d} {np.percentile(ulos, p):7.0f} m2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
