#!/usr/bin/env python3
"""clusterplan.md vaihe 3: rantatyypit klusteroimalla.

ERILLINEN DEMO. Lukee demo-oma/:n valmiit binaarit, kirjoittaa vain
demo-viz/:iin. Ei muuta tuotantoa eika oma malli -demoa.

MITTARI ON clusterplan.md:STA EIKA NEUVOTELTAVISSA:
  - siluettipisteet > 0,25
  - klusterit maantieteellisesti mielekkaita (ulkosaaristo erottuu
    sisalahdista) - tama katsotaan kartalta, ei luvusta
  - tyypit nimettavissa ihmiskielella. "Jos paras nimi klusterille on
    'klusteri 4', tulos on mossoa."

Skripti EI NIMEA klustereita. Se tulostaa profiilin - ne ulottuvuudet joissa
klusterin mediaani poikkeaa eniten koko aineiston mediaanista - ja nimeaminen
on ihmisen tyota sen pohjalta.
"""
import json
import pathlib
import sys

import numpy as np
from sklearn.cluster import MiniBatchKMeans
from sklearn.metrics import silhouette_score

JUURI = pathlib.Path(__file__).resolve().parent.parent
LAHDE = JUURI / "demo-oma"
ULOS = JUURI / "demo-viz"
K_VALI = range(6, 13)
SILUETTI_KYNNYS = 0.25
SILUETTI_OTOS = 4000        # O(n^2), joten otos - 17 222 pistetta kaatuisi
SIEMEN = 20260826


def lataa():
    m = json.loads((LAHDE / "piirteet.json").read_text())
    q = (np.fromfile(LAHDE / "piirteet.bin", dtype=np.uint8)
         .reshape(m["n"], m["d"]).astype(np.float64) / 255.0)
    xy = np.fromfile(LAHDE / "koordinaatit.bin", dtype=np.int32).reshape(m["n"], 2)
    return m, q, xy


def painotettu(q, nimet, etuliite):
    """Sektoriryhma skaalataan niin etta se painaa yhden piirteen verran.

    12 pyyhkaisysektoria hukuttaisivat 16 muuta ulottuvuutta pelkalla
    lukumaarallaan: euklidisessa etaisyydessa ryhman osuus kasvaa sen
    alkioiden LUKUMAARAN mukaan. Kerroin 1/sqrt(12) palauttaa ryhman
    yhteispainon yhdeksi. Sama ansa on jo ratkaistu painotuksessa
    (demo-oma/pisteytys.py: RYHMAPAINOTUS ERIKSEEN)."""
    ryhma = [i for i, n in enumerate(nimet) if n.startswith(etuliite)]
    x = q.copy()
    if ryhma:
        x[:, ryhma] /= np.sqrt(len(ryhma))
    return x, ryhma


def profiili(q, nimet, jasenet, esitys, n=6):
    """Ne ulottuvuudet joissa klusteri poikkeaa eniten koko aineistosta."""
    koko = np.median(q, axis=0)
    oma = np.median(q[jasenet], axis=0)
    ero = oma - koko
    jarj = np.argsort(-np.abs(ero))[:n]
    return [(esitys[nimet[j]]["otsikko"], float(oma[j]), float(ero[j])) for j in jarj]


def main():
    m, q, xy = lataa()
    nimet = m["nimet"]
    x, ryhma = painotettu(q, nimet, m["ryhma_etuliite"])
    print(f"{m['n']:,} ehdokasta x {m['d']} ulottuvuutta")
    print(f"sektoriryhma ({len(ryhma)} kpl) skaalattu kertoimella "
          f"1/sqrt({len(ryhma)}) = {1/np.sqrt(len(ryhma)):.3f}\n")

    rng = np.random.default_rng(SIEMEN)
    otos = rng.choice(len(x), min(SILUETTI_OTOS, len(x)), replace=False)

    print(f"{'k':>3} {'siluetti':>9} {'pienin klusteri':>16} {'suurin':>8}")
    tulokset = []
    for k in K_VALI:
        km = MiniBatchKMeans(n_clusters=k, random_state=SIEMEN, n_init=10,
                             batch_size=1024).fit(x)
        s = silhouette_score(x[otos], km.labels_[otos])
        koot = np.bincount(km.labels_, minlength=k)
        print(f"{k:3d} {s:9.3f} {koot.min():16d} {koot.max():8d}")
        tulokset.append((s, k, km))

    paras_s, paras_k, paras_km = max(tulokset, key=lambda t: t[0])
    print(f"\nparas k = {paras_k}, siluetti {paras_s:.3f}")
    if paras_s > SILUETTI_KYNNYS:
        print(f"  MITTARI LAPI (> {SILUETTI_KYNNYS})")
    else:
        print(f"  MITTARI EI LAPI (kynnys {SILUETTI_KYNNYS}) - "
              f"aineistossa ei ole selvia rantatyyppeja")

    nimiot = paras_km.labels_
    print(f"\n{'#':>2} {'kpl':>6}  profiili (poikkeama koko aineiston mediaanista)")
    for c in range(paras_k):
        jas = np.where(nimiot == c)[0]
        p = profiili(q, nimet, jas, m["esitys"])
        rivi = ", ".join(f"{n} {e:+.2f}" for n, _a, e in p[:4])
        print(f"{c:2d} {len(jas):6d}  {rivi}")

    # Klusteri kayttaytyy kuin suosikkijoukko: sen jasenet OVAT suosikit.
    # Talletetaan jasenyys, jolloin ideaali ja painot voidaan laskea
    # myohemmin demo-oma/pisteytys.py:n omilla funktioilla ilman uutta
    # matematiikkaa.
    np.savez_compressed(ULOS / "klusterit.npz", nimiot=nimiot, k=paras_k,
                        siluetti=paras_s, keskipisteet=paras_km.cluster_centers_)
    print(f"\nkirjoitettu {ULOS/'klusterit.npz'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
