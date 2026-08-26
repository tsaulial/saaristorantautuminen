#!/usr/bin/env python3
"""RISTIINTARKISTUS: Python ja JS laskevat saman suojaisuuden.

Botti on jaetun sopimuksen KOLMAS kopio. Kolme kopiota on velka, ja tama
testi on sen ainoa suoja. JOS TAMA EI OLE VIHREA, BOTTI EI SAA NAYTTAA
YHTAAN AALLONKORKEUTTA.

TOLERANSSI EI OLE NOLLA, ja syy on kirjattu pipeline.py:hyn: tassa on ainoa
exp() koko jaetussa sopimuksessa, eivatka numpy ja JS takaa sille bitilleen
samaa tulosta (ero luokkaa 1e-16).
"""
import json
import pathlib
import subprocess
import sys

import numpy as np

JUURI = pathlib.Path(__file__).resolve().parent.parent
ULOS = JUURI / "demo-botti"
TOLERANSSI = 1e-12


def main():
    sys.path.insert(0, str(JUURI))
    from backend import pipeline as P

    V = json.loads((ULOS / "piirteet.json").read_text())["vakiot"]
    # Vakioiden ON oltava samat kuin pipeline.py:ssa - muuten koko vertailu
    # mittaa vaaraa asiaa.
    for k, v in V.items():
        oikea = getattr(P, k)
        if abs(v - oikea) > 0:
            print(f"VIRHE: vakio {k} = {v}, pipeline.py sanoo {oikea}")
            return 1
    print(f"vakiot tasmaavat pipeline.py:hyn ({len(V)} kpl)")

    fetch = np.array([50, 120, 300, 800, 2000, 5000, 9000, 15000], float)
    tuuli = np.array([0, 2, 5, 8, 12, 17, 25], float)
    este = np.array([0, 0.5, 2, 5, 9, 14, 20], float)
    F, T, E = np.meshgrid(fetch, tuuli, este, indexing="ij")
    F, T, E = F.ravel(), T.ravel(), E.ravel()
    py = P.shelter_score_from_fetch(F, T, E)
    hs_py = P.WAVE_COEFF * P.sheltered_wind(T, F, E) * np.sqrt(F)

    js = f"""
    const {{ shelterScoreFromFetch, aallonkorkeus }} = require("{ULOS/'sopimus.js'}");
    const V = {json.dumps(V)};
    const F = {json.dumps(F.tolist())}, T = {json.dumps(T.tolist())}, E = {json.dumps(E.tolist())};
    const s = [], h = [];
    for (let i = 0; i < F.length; i++) {{
      s.push(shelterScoreFromFetch(F[i], T[i], E[i], V));
      h.push(aallonkorkeus(F[i], T[i], E[i], V));
    }}
    console.log(JSON.stringify({{s, h}}));
    """
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    if r.returncode:
        print("node epaonnistui:\n", r.stderr[:600]); return 1
    ulos = json.loads(r.stdout)
    d_s = np.abs(np.array(ulos["s"]) - py).max()
    d_h = np.abs(np.array(ulos["h"]) - hs_py).max()

    print(f"\n{len(F):,} yhdistelmaa (fetch x tuuli x este)")
    print(f"  suojaisuuspistemaara: suurin ero {d_s:.3e}")
    print(f"  aallonkorkeus:        suurin ero {d_h:.3e}")
    ok = d_s < TOLERANSSI and d_h < TOLERANSSI
    print(f"\n{'SOPIMUS PITAA' if ok else 'SOPIMUS RIKKI'} "
          f"(toleranssi {TOLERANSSI:.0e})")
    if not ok:
        print("  Botti EI saa nayttaa aallonkorkeuksia ennen kuin tama on vihrea.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
