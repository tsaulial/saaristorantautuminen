#!/usr/bin/env python3
"""Esteenkorkeudet 12 sektorille - suojaisuuslaskennan puuttuva palanen.

MIKSI TAMA TARVITAAN. Suojaisuus on pipeline.shelter_score_from_fetch(
fetch_m, wind_speed, obstacle_h): maasto tuulen ja rannan valissa vaimentaa
tuulta. demo-oma/piirteet.py poimi pyyhkaisymatkat mutta EI esteenkorkeuksia
(fetchobs: 0 osumaa), joten ilman tata suojaisuus laskettaisiin kuin maastoa
ei olisi lainkaan - mika yliarvioi altistuksen jarjestelmallisesti.

ABSOLUUTTISET SEKTORIT, EI KIERRETYT. Piirrevektorissa sektorit on kierretty
rantaviivan normaalin mukaan, koska samankaltaisuus vaatii sita. Suojaisuus
vaatii paivastoin ABSOLUUTTISEN suunnan: tuuli tulee kompassisuunnasta, ei
rannan suunnasta. Tallennetaan siksi kiertamattomina.

ERILLINEN DEMO: lukee docs/cache/-kuvia ja demo-oma/:n ehdokaslistan,
kirjoittaa vain demo-botti/:iin.
"""
import pathlib
import sys

import numpy as np

JUURI = pathlib.Path(__file__).resolve().parent.parent
LAHDE = JUURI / "demo-oma"
ULOS = JUURI / "demo-botti"
PX_M = 2.0
IKKUNA_SADE_M = 25.0
SEKTOREITA = 12


def main():
    sys.path.insert(0, str(JUURI))
    sys.path.insert(0, str(LAHDE))
    import cv2
    from piirteet import testialueen_tiilet, CACHE

    d = np.load(LAHDE / "ehdokkaat_raaka.npz", allow_pickle=True)
    xs, ys = d["x"], d["y"]
    ulos = np.full((len(xs), SEKTOREITA), np.nan, dtype=np.float32)

    r_px = int(round(IKKUNA_SADE_M / PX_M))
    yy, xx = np.mgrid[-r_px:r_px + 1, -r_px:r_px + 1]
    kiekko = (yy * yy + xx * xx) <= r_px * r_px

    tiilet = testialueen_tiilet()
    for k, (tile_id, b) in enumerate(tiilet, 1):
        osuu = np.where((xs >= b[0]) & (xs < b[2]) & (ys >= b[1]) & (ys < b[3]))[0]
        if not len(osuu):
            continue
        f = cv2.imread(str(CACHE / f"{tile_id}_factors.png"), cv2.IMREAD_UNCHANGED)
        if f is None or not (f[:, :, 3] > 0).any():
            continue
        puskuri = f[:, :, 3] > 0
        # Sama purkulogiikka kuin pyyhkaisymatkoilla: kaksi kuvaa, kolme
        # kanavaa, kaksi 4-bittista sektoria per kanava.
        sekt = np.empty(puskuri.shape + (SEKTOREITA,), dtype=np.uint8)
        for nimi, base in ((f"{tile_id}_fetchobsa.png", 0),
                           (f"{tile_id}_fetchobsb.png", 6)):
            kuva = cv2.imread(str(CACHE / nimi), cv2.IMREAD_UNCHANGED)
            if kuva is None:
                raise FileNotFoundError(nimi)
            for i in range(3):
                kanava = kuva[:, :, 2 - i]          # tiedostossa RGBA
                sekt[:, :, base + 2 * i] = kanava >> 4
                sekt[:, :, base + 2 * i + 1] = kanava & 0x0F

        H, W = puskuri.shape
        cc = ((xs[osuu] - b[0]) / PX_M).astype(np.int32)
        rr = ((b[3] - ys[osuu]) / PX_M).astype(np.int32)
        for j, i in enumerate(osuu):
            r0, r1 = rr[j] - r_px, rr[j] + r_px + 1
            c0, c1 = cc[j] - r_px, cc[j] + r_px + 1
            if r0 < 0 or c0 < 0 or r1 > H or c1 > W:
                continue
            m = puskuri[r0:r1, c0:c1] & kiekko
            if m.sum() < 8:
                continue
            ulos[i] = sekt[r0:r1, c0:c1][m].mean(axis=0)
        print(f"  [{k}/{len(tiilet)}] {tile_id}: {len(osuu)}", flush=True)

    ulos = np.nan_to_num(ulos, nan=0.0)
    np.savez_compressed(ULOS / "esteet.npz", tasot=ulos)
    print(f"\n{len(ulos):,} ehdokasta x {SEKTOREITA} sektoria (tasot 0-15)")
    print(f"  keskiarvo {ulos.mean():.2f}, mediaani {np.median(ulos):.2f}, "
          f"max {ulos.max():.2f}")
    print(f"  nollia (ei estetta): {100*(ulos == 0).mean():.1f} %")
    return 0


if __name__ == "__main__":
    sys.exit(main())
