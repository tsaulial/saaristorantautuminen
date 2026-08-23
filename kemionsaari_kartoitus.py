#!/usr/bin/env python3
"""Kartoittaa mita Kemionsaaren alueelta puuttuu tiilistosta.

AJETAAN SILLA KONEELLA JOSSA TUOTANTOAINEISTO ON. Skripti lukee
rekisterin ladatuista DEM-lehdista (backend/tiles.py), joten sen tulos
on tasan se mita kyseisella koneella on - ei arvio toisen koneen
tilanteesta.

Kaytto:
    .venv/bin/python3 kemionsaari_kartoitus.py
"""
import sys
from backend import tiles

# Kemionsaaren kunta suorakaiteena, EPSG:3067. Rajat on johdettu
# aarimmaisista tunnetuista paikoista (Kasnas etelassa, Angelniemi
# pohjoisessa, Hitis lounaassa, Strommaan salmi idassa) ja pyoristetty
# ulospain 6 km ruudukkoon. Kunnan tarkkaa rajaa ei tarvita: liikaa
# ottaminen maksaa vain vektorihaun alassa, liian vahan ottaminen
# jattaisi rantaviivaa pois.
ALUE = (234000, 6642000, 288000, 6696000)
RUUTU = 6000


def main():
    reg = tiles.get_registry()
    on = {(int(t.bounds[0] // RUUTU), int(t.bounds[1] // RUUTU)) for t in reg.values()}
    print(f"{len(reg)} tiilta rekisterissa")

    xs = range(ALUE[0] // RUUTU, ALUE[2] // RUUTU)
    ys = range(ALUE[1] // RUUTU, ALUE[3] // RUUTU)
    ruudut = [(x, y) for x in xs for y in ys]
    puuttuu = [c for c in ruudut if c not in on]

    print(f"\nKemionsaaren alue: {len(ruudut)} ruutua a 36 km2")
    print(f"  katettu: {len(ruudut) - len(puuttuu)}")
    print(f"  puuttuu: {len(puuttuu)}  ({len(puuttuu) * 36} km2)")

    print("\n# = tiili on, . = puuttuu   (pohjoinen ylhaalla)")
    for y in sorted(ys, reverse=True):
        rivi = "".join("#" if (x, y) in on else "." for x in xs)
        print(f"  {y * RUUTU:>8} {rivi}")
    print(f"  {'':>8} " + "".join(str((x * RUUTU // 6000) % 10) for x in xs))

    if not puuttuu:
        print("\nEi puuttuvia ruutuja - alue on jo katettu.")
        return 0

    minx = min(c[0] for c in puuttuu) * RUUTU
    maxx = (max(c[0] for c in puuttuu) + 1) * RUUTU
    miny = min(c[1] for c in puuttuu) * RUUTU
    maxy = (max(c[1] for c in puuttuu) + 1) * RUUTU
    ala = (maxx - minx) * (maxy - miny) / 1e6
    print(f"\nLATAUKSEN BBOX (puuttuvien ruutujen ymparilta):")
    print(f"  {minx} {miny} {maxx} {maxy}")
    print(f"  {(maxx-minx)/1000:.0f} x {(maxy-miny)/1000:.0f} km = {ala:.0f} km2")
    # Vektorirajapinnan katto on 17 334 km2 (ks. mml_lataus.main).
    if ala > 17334:
        print("  VAROITUS: yli vektorihaun 17 334 km2 katon - jaa osiin")
    print(f"\n  .venv/bin/python3 -m backend.mml_lataus \\")
    print(f"      --bbox {minx} {miny} {maxx} {maxy} --taustakartta")
    return 0


if __name__ == "__main__":
    sys.exit(main())
