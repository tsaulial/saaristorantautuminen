#!/usr/bin/env python3
"""Etaisyys kauppamerenkulun vaylaan yhtena piirteena.

MIKSI VAIN VL1-VL2. Vaylaluokkia on kuusi, mutta ero kauppamerenkulun ja
veneilyn valilla on melojalle turvallisuusasia eika luokitteludetalji:
VL1-VL2 ovat vaylia joilla liikkuu satojen metrien rahtialuksia. Sama
erottelu on jo tehty vektoritasot.py:ssa ja viety selaimeen asti - tassa
sita kaytetaan mittana.

SUUNNALLINEN, EI TAVOITTEELLINEN. Etaisyys rakennuksiin on tavoitteellinen,
koska toinen haluaa eramaata ja toinen palveluja. Rahtivayla ei ole
sellainen: kukaan ei rantaudu MIELELLAAN lahemmas ruuhkaista vaylaa, joten
ideaalin ylittaminen ei saa rangaista. Jos suosikkisi ovat kolmen kilometrin
paassa, viiden kilometrin paassa oleva ranta on yhta hyva - ei "liian
kaukana". Luokittelu on silti suunnitteluvalinta eika totuus, ja siksi se
asuu pisteytys.py:n LUOKAT-taulukossa eika kaavassa.

MITTA ON VAYLALINJAAN, ei vaylaalueen reunaan. Vaylaalue olisi tarkempi
kuvaus siita missa alukset oikeasti kulkevat, mutta luokkatieto (VL1-VL6)
on linjoilla eika alueilla, ja alueen leveys on satoja metreja siina missa
etaisyydet ovat kilometreja.
"""
import json
import pathlib
import sys

import numpy as np

JUURI = pathlib.Path(__file__).resolve().parent.parent
ULOS = JUURI / "demo-oma"
KAUPALLISET_LUOKAT = (1, 2)
KATTO_M = 20000.0        # tata kauempana ero ei enaa merkitse mitaan


def kaupalliset_vaylat():
    from shapely.geometry import LineString, MultiLineString
    d = json.loads((JUURI / "docs" / "vaylat.json").read_text())
    ulos = []
    for f in d["tasot"]["vaylat"]:
        if f["p"].get("vaylaluokkakoodi") not in KAUPALLISET_LUOKAT:
            continue
        g = f["g"]
        if g["type"] == "LineString":
            ulos.append(LineString(g["coordinates"]))
        elif g["type"] == "MultiLineString":
            ulos.extend(LineString(o) for o in g["coordinates"] if len(o) > 1)
    return ulos


def main():
    sys.path.insert(0, str(JUURI))
    from shapely import STRtree
    from shapely.geometry import Point

    viivat = kaupalliset_vaylat()
    if not viivat:
        print("VIRHE: kauppamerenkulun vaylia ei loytynyt"); return 1
    puu = STRtree(viivat)

    d = np.load(ULOS / "ehdokkaat_raaka.npz", allow_pickle=True)
    xs, ys = d["x"], d["y"]
    pisteet = [Point(x, y) for x, y in zip(xs, ys)]
    lahin = puu.nearest(pisteet)
    et = np.array([pisteet[i].distance(viivat[lahin[i]]) for i in range(len(pisteet))],
                  dtype=np.float32)
    et = np.minimum(et, KATTO_M)

    np.savez_compressed(ULOS / "vaylaetaisyys.npz", etaisyys=et,
                        nimet=np.array(["etaisyys_rahtivaylaan"]))
    print(f"{len(et):,} ehdokasta, {len(viivat)} vaylalinjaa")
    for p in (0, 5, 25, 50, 75, 95, 100):
        print(f"  p{p:<3d} {np.percentile(et, p):8.0f} m")
    print(f"  alle 500 m:  {100*(et<500).mean():5.1f} %")
    print(f"  alle 1000 m: {100*(et<1000).mean():5.1f} %")
    print(f"  yli 5 km:    {100*(et>5000).mean():5.1f} %")
    return 0


if __name__ == "__main__":
    sys.exit(main())
