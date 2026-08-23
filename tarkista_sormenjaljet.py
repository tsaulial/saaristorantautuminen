#!/usr/bin/env python3
"""Kertoo ENNEN ajoa, montako tiilta laskettaisiin uudelleen.

Tarkistus on halpa (n. 26 ms/tiili) ja ajo on tunteja, joten tama
kannattaa ajaa aina ennen laajennuksen rakentamista. Ilman sita
vaarin mennyt siirtyma nakyisi vasta kun tunti on jo kulunut.

EI LASKE MITAAN. Lukee vain _raw.npz:n sormenjaljen ja vertaa sita
nykyiseen tasan samalla funktiolla jota pipeline kayttaa.

Kaytto:
    .venv/bin/python3 tarkista_sormenjaljet.py
"""
import sys
import time
import numpy as np

from backend import pipeline as P, mml_lataus, tiles


def main():
    reg = tiles.get_registry()
    bp = mml_lataus.BUILDINGS_PATH
    print(f"{len(reg)} tiilta rekisterissa, valimuisti {P.CACHE_DIR}")

    ei_valimuistia, tasmaa, siirtyma, laskettaisiin = [], [], [], []
    t0 = time.time()
    for i, (tid, tile) in enumerate(sorted(reg.items()), 1):
        polku = P.CACHE_DIR / f"{tid}_raw.npz"
        if not polku.exists():
            ei_valimuistia.append(tid)
            continue
        try:
            with np.load(polku, allow_pickle=True) as d:
                if "shoreline_mask" not in d.files:
                    laskettaisiin.append((tid, "vanha sisalto"))
                    continue
                vanha = str(d["sormenjalki"]) if "sormenjalki" in d.files else None
        except Exception as e:
            laskettaisiin.append((tid, f"lukuvirhe: {type(e).__name__}"))
            continue

        uusi = P.lahde_sormenjalki(tile, bp)
        if vanha == uusi:
            tasmaa.append(tid)
        elif P.sormenjalki_kelpaa(vanha, uusi):
            siirtyma.append(tid)
        elif not vanha:
            # Sormenjalkikenttaa ei ole lainkaan: valimuisti on vanhempi
            # kuin koko kentta. Ei ole naytto tuoreudesta, joten se
            # lasketaan - eri asia kuin "lahde muuttunut", ja sekoittaminen
            # saisi tuloksen nayttamaan vialta vaikka se on odotettu.
            laskettaisiin.append((tid, "ei sormenjalkea (vanha valimuisti)"))
        else:
            laskettaisiin.append((tid, "lahde oikeasti muuttunut"))

        if i % 200 == 0:
            print(f"  {i}/{len(reg)} ({time.time()-t0:.0f} s)", flush=True)

    print(f"\ntarkistettu {len(reg)} tiilta {time.time()-t0:.0f} sekunnissa\n")
    print(f"  tasmaa tarkalleen        {len(tasmaa):5d}")
    print(f"  kelpaa siirtymasaannolla {len(siirtyma):5d}")
    print(f"  ei valimuistia (UUSI)    {len(ei_valimuistia):5d}")
    print(f"  LASKETTAISIIN UUDELLEEN  {len(laskettaisiin):5d}")

    if ei_valimuistia:
        print(f"\nuudet tiilet: {' '.join(sorted(ei_valimuistia)[:20])}"
              + (" ..." if len(ei_valimuistia) > 20 else ""))

    if laskettaisiin:
        syyt = {}
        for _tid, syy in laskettaisiin:
            syyt[syy] = syyt.get(syy, 0) + 1
        print("\nsyyt uudelleenlaskennalle:")
        for syy, n in sorted(syyt.items(), key=lambda kv: -kv[1]):
            print(f"  {n:5d}  {syy}")
        print("  esimerkkeja: "
              + " ".join(t for t, _s in laskettaisiin[:10]))

    # Odotus: uudelleen lasketaan vain uudet tiilet ja kourallinen
    # vanhoja. Jos muita on satoja, siirtyma ei purrut.
    #
    # KYNNYS ON ALARAJALTAAN KIINTEA. Pelkka prosenttiosuus varoittaisi
    # pienessa aineistossa kourallisesta tiilia, joka on taysin normaali
    # tulos - ja jos varoitus huutaa turhaan, se lakkaa merkitsemasta
    # mitaan silloin kun se on aiheellinen.
    raja = max(20, len(reg) // 20)
    if len(laskettaisiin) > raja:
        print(f"\nVAROITUS: {len(laskettaisiin)} tiilta laskettaisiin uudelleen "
              f"({100.0*len(laskettaisiin)/len(reg):.0f} %).")
        print("Inkrementaalisuus ei toimi odotetusti - ala aloita ajoa.")
        return 1
    print(f"\nAjossa lasketaan {len(ei_valimuistia) + len(laskettaisiin)} tiilta "
          f"{len(reg)}:sta.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
