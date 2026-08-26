#!/usr/bin/env python3
"""Saaristodatan kuvat: 17 222 ehdokasta x 28 ulottuvuutta.

ERILLINEN DEMO. Lukee demo-oma/:n binaarit, kirjoittaa vain demo-viz/kuvat/.
"""
import json
import pathlib
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from sklearn.decomposition import PCA

JUURI = pathlib.Path(__file__).resolve().parent.parent
LAHDE = JUURI / "demo-oma"
KUVAT = JUURI / "demo-viz" / "kuvat"
DPI = 110

# RdYlGn, sama asteikko kuin demossa - lukija tunnistaa varit.
CMAP = matplotlib.colors.LinearSegmentedColormap.from_list(
    "rdylgn", ["#d73027", "#fc8d59", "#fee08b", "#ffffbf",
               "#d9ef8b", "#91cf60", "#1a9850"])


def lataa():
    m = json.loads((LAHDE / "piirteet.json").read_text())
    q = (np.fromfile(LAHDE / "piirteet.bin", dtype=np.uint8)
         .reshape(m["n"], m["d"]).astype(np.float64) / 255.0)
    xy = np.fromfile(LAHDE / "koordinaatit.bin", dtype=np.int32).reshape(m["n"], 2)
    return m, q, xy


def kuva1_pienoiskartat(m, q, xy):
    """28 pienoiskarttaa: missa kukin ominaisuus on.

    TAMA ON KOKOELMAN TARKEIN. Se nayttaa MISSA ominaisuus on, ja paljastaa
    viat joita luvut eivat nayta: vakioksi jaanyt ulottuvuus, tai kuvio joka
    seuraa tiilirajoja (6 km ruudukko) eika maastoa."""
    n = m["d"]
    sar, riv = 4, int(np.ceil(n / 4))
    fig, ax = plt.subplots(riv, sar, figsize=(sar * 3.0, riv * 2.5))
    ax = ax.ravel()
    for j in range(n):
        a = ax[j]
        # Jarjestetaan niin etta korkeat arvot piirtyvat paalle - muuten
        # harvinainen luokka (1,6 % ehdokkaista) hautautuu nollien alle.
        jarj = np.argsort(q[:, j])
        a.scatter(xy[jarj, 0], xy[jarj, 1], c=q[jarj, j], cmap=CMAP,
                  s=0.6, linewidths=0, vmin=0, vmax=1, rasterized=True)
        # SEKTORIT ERIKSEEN NIMETTYNA. esitys-taulukossa kaikilla 12:lla on
        # sama ryhmaotsikko, mika on oikein selityspaneelissa mutta tekee
        # tasta kuvasta lukukelvottoman: kaksitoista identtista otsikkoa
        # eika mitaan tapaa tietaa mika suunta on kyseessa.
        nimi = m["nimet"][j]
        if nimi.startswith("pyyhkaisy_"):
            k = int(nimi.split("_")[1])
            suunta = ("merelle" if k == 0 else "maalle" if k == 6
                      else f"{k*30}\u00b0")
            otsikko = f"avoimuus {suunta}"
        else:
            otsikko = m["esitys"][nimi]["otsikko"]
        a.set_title(otsikko, fontsize=8, pad=3)
        a.set_aspect("equal"); a.set_xticks([]); a.set_yticks([])
        for s in a.spines.values(): s.set_linewidth(0.4); s.set_color("#bbb")
    for j in range(n, len(ax)):
        ax[j].axis("off")
    fig.suptitle("Missä kukin ominaisuus on — 17 222 rantaehdokasta, "
                 "Helsingin edusta 50 km säteellä", fontsize=11, y=0.998)
    fig.tight_layout(rect=[0, 0, 1, 0.99])
    fig.savefig(KUVAT / "1-pienoiskartat.png", dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return "1-pienoiskartat.png"


def kuva2_korrelaatio(m, q):
    otsikot = [m["esitys"][n]["otsikko"] for n in m["nimet"]]
    C = np.corrcoef(q, rowvar=False)
    fig, a = plt.subplots(figsize=(9.5, 8.4))
    im = a.imshow(C, cmap="RdBu_r", vmin=-1, vmax=1)
    a.set_xticks(range(len(otsikot))); a.set_yticks(range(len(otsikot)))
    a.set_xticklabels(otsikot, rotation=90, fontsize=7)
    a.set_yticklabels(otsikot, fontsize=7)
    a.set_title("Mitkä ulottuvuudet toistavat toisiaan", fontsize=11, pad=10)
    fig.colorbar(im, ax=a, shrink=0.7, label="korrelaatio")
    fig.tight_layout()
    fig.savefig(KUVAT / "2-korrelaatio.png", dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    parit = [(otsikot[i], otsikot[j], C[i, j])
             for i in range(len(C)) for j in range(i + 1, len(C))
             if abs(C[i, j]) > 0.9]
    return "2-korrelaatio.png", parit


def kuva3_pca(q):
    X = q - q.mean(0)
    _, s, _ = np.linalg.svd(X, full_matrices=False)
    var = s ** 2 / (s ** 2).sum(); kum = np.cumsum(var)
    k95 = int(np.searchsorted(kum, 0.95) + 1)
    fig, a = plt.subplots(figsize=(7, 4))
    a.bar(range(1, len(var) + 1), 100 * var, color="#7fa8c9", label="komponentin osuus")
    a2 = a.twinx()
    a2.plot(range(1, len(kum) + 1), 100 * kum, color="#c25e12", marker="o",
            ms=3, lw=1.4, label="kumulatiivinen")
    a2.axhline(95, color="#888", ls="--", lw=1)
    a2.axvline(k95, color="#c25e12", ls=":", lw=1)
    a2.annotate(f"{k95} komponenttia\n95 %:iin", (k95, 95), xytext=(k95 + 1.5, 70),
                fontsize=9, color="#c25e12",
                arrowprops=dict(arrowstyle="->", color="#c25e12", lw=1))
    a.set_xlabel("pääkomponentti"); a.set_ylabel("osuus vaihtelusta (%)")
    a2.set_ylabel("kumulatiivinen (%)"); a2.set_ylim(0, 102)
    a.set_title("Aineisto on aidosti moniulotteinen", fontsize=11)
    fig.tight_layout()
    fig.savefig(KUVAT / "3-pca.png", dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return "3-pca.png", k95


def kuva4_harvinaisuus(m, q):
    otsikot = [m["esitys"][n]["otsikko"] for n in m["nimet"]]
    var = q.var(axis=0)
    jarj = np.argsort(var)
    fig, a = plt.subplots(figsize=(7.5, 6.5))
    varit = ["#c25e12" if var[j] < 0.03 else "#7fa8c9" for j in jarj]
    a.barh(range(len(jarj)), var[jarj], color=varit)
    a.axvline(1/12, color="#444", ls="--", lw=1)
    a.annotate("1/12 = 0,083\ntasajakauman varianssi", (1/12, 2), xytext=(0.05, 4),
               fontsize=9, arrowprops=dict(arrowstyle="->", lw=1))
    a.set_yticks(range(len(jarj)))
    a.set_yticklabels([otsikot[j] for j in jarj], fontsize=8)
    a.set_xlabel("varianssi kvantiilimuunnoksen jälkeen")
    a.set_title("Miksi 1/12-oletus rikkoutui", fontsize=11)
    fig.tight_layout()
    fig.savefig(KUVAT / "4-harvinaisuus.png", dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return "4-harvinaisuus.png", float(var.min()), otsikot[int(np.argmin(var))]


def kuva5_klusterit(m, q, xy):
    d = np.load(JUURI / "demo-viz" / "klusterit.npz")
    nimiot, k, sil = d["nimiot"], int(d["k"]), float(d["siluetti"])
    varit = plt.get_cmap("tab10")(np.arange(k) % 10)
    p2 = PCA(n_components=2, random_state=20260826).fit_transform(q - q.mean(0))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 6))
    for c in range(k):
        s = nimiot == c
        a1.scatter(xy[s, 0], xy[s, 1], s=1.2, color=varit[c], linewidths=0,
                   label=f"{c} ({s.sum()})", rasterized=True)
        a2.scatter(p2[s, 0], p2[s, 1], s=1.2, color=varit[c], linewidths=0,
                   rasterized=True)
    a1.set_aspect("equal"); a1.set_xticks([]); a1.set_yticks([])
    a1.set_title("Klusterit kartalla", fontsize=11)
    a1.legend(markerscale=8, fontsize=8, loc="lower left", framealpha=0.9)
    a2.set_title(f"Sama kahdessa pääkomponentissa — siluetti {sil:.3f}", fontsize=11)
    a2.set_xlabel("PC1"); a2.set_ylabel("PC2")
    fig.suptitle("Rantatyypit: rajat ovat pehmeät", fontsize=12)
    fig.tight_layout()
    fig.savefig(KUVAT / "5-klusterit.png", dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return "5-klusterit.png", k, sil


def main():
    KUVAT.mkdir(parents=True, exist_ok=True)
    m, q, xy = lataa()
    print(f"{m['n']:,} ehdokasta x {m['d']} ulottuvuutta\n")
    print(" ", kuva1_pienoiskartat(m, q, xy))
    f, parit = kuva2_korrelaatio(m, q)
    print(" ", f, f"| |r|>0,9: {parit if parit else 'ei yhtaan paria'}")
    f, k95 = kuva3_pca(q); print(" ", f, f"| 95 % vaatii {k95} komponenttia")
    f, vmin, vnimi = kuva4_harvinaisuus(m, q)
    print(" ", f, f"| pienin varianssi {vmin:.4f} ({vnimi})")
    f, k, sil = kuva5_klusterit(m, q, xy)
    print(" ", f, f"| k={k}, siluetti {sil:.3f}")
    yht = sum(p.stat().st_size for p in KUVAT.glob("*.png"))
    print(f"\nkuvia yhteensa {yht/1e6:.2f} Mt (base64 n. {yht*1.34/1e6:.2f} Mt)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
