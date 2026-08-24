#!/usr/bin/env python3
"""Vaiheen 2 mittari: poisjattokoe (clusterplan.md).

  "Maarita ideaali n-1 suosikista ja tarkista mihin persentiiliin
   poisjatetty osuu. Jos se ei ole johdonmukaisesti parhaassa 10 %:ssa,
   mitta ei toimi eika ilmakuva korjaa sita."

RIIPPUMATON SIGNAALI. Suosikit valitaan HIEKKARANNOISTA, joita piirrevektori
ei tunne lainkaan - hietikko tulee maastotietokannasta eika ole yksikaan
18 ulottuvuudesta. Jos mitta loytaa toisen hiekkarannan tuntematta hiekkaa,
se on oppinut jotain rannan muodosta joka oikeasti liittyy hiekkaan.

ERILLISET RANNAT. Saman rannan ehdokkaat ovat 50 m paassa toisistaan, joten
naapurin loytaminen olisi triviaalia. Suosikit ja poisjatetty otetaan siksi
ERI RANNOILTA, jotka erotellaan RANTA_ETAISYYS_M:n valein.
"""
import pathlib
import sys

import numpy as np

JUURI = pathlib.Path(__file__).resolve().parent.parent
ULOS = JUURI / "demo-oma"
sys.path.insert(0, str(ULOS))
from piirteet import kvantiloi          # noqa: E402
from pisteytys import pistemaara        # noqa: E402

RANTA_ETAISYYS_M = 500.0
SUOSIKKEJA = 5
TOISTOJA = 400


def rykelmat(xs, ys, idx, etaisyys):
    """Pisteet rykelmiksi: sama rykelma jos alle `etaisyys` toisistaan."""
    jaljella = list(idx)
    ulos = []
    while jaljella:
        siemen = jaljella.pop(0)
        ryhma = [siemen]
        muuttui = True
        while muuttui:
            muuttui = False
            for i in list(jaljella):
                if any(np.hypot(xs[i]-xs[j], ys[i]-ys[j]) < etaisyys for j in ryhma):
                    ryhma.append(i); jaljella.remove(i); muuttui = True
        ulos.append(ryhma)
    return ulos


def koe(q, nimet, ryhmat, rng, toistoja):
    """Palauttaa poisjatetyn persentiilit (0 = paras)."""
    tulokset = []
    for _ in range(toistoja):
        if len(ryhmat) < SUOSIKKEJA + 1:
            break
        valitut = rng.choice(len(ryhmat), SUOSIKKEJA + 1, replace=False)
        otos = [int(rng.choice(ryhmat[k])) for k in valitut]
        suosikit, poisjatetty = otos[:SUOSIKKEJA], otos[SUOSIKKEJA]
        pisteet, _d, _m, _w = pistemaara(q, suosikit, nimet)
        # pistemaara on 1 - persentiili, joten poisjatetyn sijoitus on
        # 1 - pistemaara: 0 = paras, 1 = huonoin.
        tulokset.append(1.0 - pisteet[poisjatetty])
    return np.array(tulokset)


def main():
    d = np.load(ULOS / "ehdokkaat_raaka.npz", allow_pickle=True)
    nimet = [str(x) for x in d["nimet"]]
    xs, ys = d["x"], d["y"]
    q = kvantiloi(d["piirteet"])
    m = np.load(ULOS / "maastoluokat.npz", allow_pickle=True)
    luokat, lnimet = m["luokat"], [str(x) for x in m["nimet"]]
    rng = np.random.default_rng(20260824)

    print(f"{len(q):,} ehdokasta, {len(nimet)} ulottuvuutta, "
          f"{SUOSIKKEJA} suosikkia per koe\n")
    print(f"{'suosikkien lahde':22s} {'rantoja':>8s} {'mediaani':>9s} "
          f"{'top 10 %':>9s} {'top 25 %':>9s}")

    rivit = []
    for luokka, kynnys in (("hietikko", 0.02), ("kivikko", 0.05),
                           ("satama", 0.05), ("hoidettu", 0.05)):
        li = lnimet.index(luokka)
        idx = np.where(luokat[:, li] > kynnys)[0]
        if len(idx) < 20:
            continue
        ryhmat = rykelmat(xs, ys, idx, RANTA_ETAISYYS_M)
        if len(ryhmat) < SUOSIKKEJA + 1:
            print(f"{luokka:22s} vain {len(ryhmat)} erillista rantaa - ohitetaan")
            continue
        t = koe(q, nimet, ryhmat, rng, TOISTOJA)
        rivit.append((luokka, len(ryhmat), t))
        print(f"{luokka:22s} {len(ryhmat):8d} {np.median(t):8.3f} "
              f"{100*(t<0.10).mean():8.1f} % {100*(t<0.25).mean():8.1f} %")

    # NOLLAVERTAILU: satunnaiset suosikit ilman yhteista piirretta. Ilman
    # tata luvut eivat kerro mitaan - jos satunnainenkin osuu top 10 %:iin,
    # mitta ei erottele vaan asteikko on vinossa.
    kaikki = [[i] for i in rng.choice(len(q), 400, replace=False)]
    t0 = koe(q, nimet, kaikki, rng, TOISTOJA)
    print(f"{'SATUNNAINEN (nolla)':22s} {len(kaikki):8d} {np.median(t0):8.3f} "
          f"{100*(t0<0.10).mean():8.1f} % {100*(t0<0.25).mean():8.1f} %")

    print("\nMITTARI (clusterplan): poisjatetyn on osuttava johdonmukaisesti "
          "parhaaseen 10 %:iin.")
    for luokka, _n, t in rivit:
        osuu = 100 * (t < 0.10).mean()
        vertailu = 100 * (t0 < 0.10).mean()
        print(f"  {luokka:14s} {osuu:5.1f} %  vs satunnainen {vertailu:4.1f} %  "
              f"-> {'LAPI' if osuu > 3*vertailu and osuu > 25 else 'EI LAPI'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
