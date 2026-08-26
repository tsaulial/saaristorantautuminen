#!/usr/bin/env python3
"""Kyselykaava, validointi ja sanaston tarkistus.

KAAVA ON SULJETTU. Malli saa palauttaa vain naita kenttia ja vain
valkolistalla olevia ulottuvuuksia. Kaikki muu hylataan - myos silloin kun
se nayttaa jarkevalta. Nain hallusinaatio ei rakenteellisesti VOI tuottaa
vaaraa vaitetta rannasta: pahin mahdollinen virhe on vaarin ymmarretty
kysymys, ja se nakyy kayttajalle koska kysely naytetaan takaisin.
"""
import json
import pathlib
import sys

import numpy as np

JUURI = pathlib.Path(__file__).resolve().parent.parent
ULOS = JUURI / "demo-botti"
OPERAATTORIT = {"<", ">", "<=", ">="}
DYNAAMISET_SUUREET = {"aallonkorkeus", "suojaisuus"}
MAX_SUODATTIMIA = 6


def lataa():
    m = json.loads((ULOS / "piirteet.json").read_text())
    s = json.loads((ULOS / "sanasto.json").read_text())
    return m, s


def raaka_arvot(m):
    """Kaikki ehdokkaat oikeissa yksikoissa, kvantiilitaulun kautta."""
    q = (np.fromfile(ULOS / "piirteet.bin", dtype=np.uint8)
         .reshape(m["n"], m["d"]).astype(np.float64) / 255.0)
    kv = np.fromfile(ULOS / "kvantiilit.bin", dtype=np.float32).reshape(m["d"], 101)
    ulos = np.empty_like(q)
    for j in range(m["d"]):
        x = q[:, j] * 100
        a = np.clip(x.astype(int), 0, 100)
        b = np.clip(a + 1, 0, 100)
        t = x - a
        ulos[:, j] = kv[j, a] * (1 - t) + kv[j, b] * t
    return ulos


def validoi(kysely, m):
    """Palauttaa (ok, virheet). Ei koskaan korjaa - vain hyvaksyy tai hylkaa."""
    virheet = []
    if not isinstance(kysely, dict):
        return False, ["kysely ei ole olio"]
    tuntemattomat = set(kysely) - {"suodattimet", "jarjestys", "aika",
                                   "dynaaminen", "tulkinta", "raja"}
    if tuntemattomat:
        virheet.append(f"tuntemattomia kenttia: {sorted(tuntemattomat)}")

    sd = kysely.get("suodattimet") or []
    if len(sd) > MAX_SUODATTIMIA:
        virheet.append(f"yli {MAX_SUODATTIMIA} suodatinta")
    for f in sd:
        if f.get("ulottuvuus") == "suunta":
            v = f.get("valissa")
            if not (isinstance(v, list) and len(v) == 2
                    and all(isinstance(x, (int, float)) and 0 <= x <= 360 for x in v)):
                virheet.append(f"suunta: valissa oltava [alku, loppu] 0-360, sai {v}")
            continue
        if f.get("ulottuvuus") not in m["nimet"]:
            virheet.append(f"tuntematon ulottuvuus: {f.get('ulottuvuus')!r}")
        if f.get("op") not in OPERAATTORIT:
            virheet.append(f"tuntematon operaattori: {f.get('op')!r}")
        if not isinstance(f.get("arvo"), (int, float)):
            virheet.append(f"arvo ei ole luku: {f.get('arvo')!r}")

    for f in kysely.get("dynaaminen") or []:
        if f.get("suure") not in DYNAAMISET_SUUREET:
            virheet.append(f"tuntematon dynaaminen suure: {f.get('suure')!r}")
        if f.get("op") not in OPERAATTORIT:
            virheet.append(f"tuntematon operaattori: {f.get('op')!r}")

    j = kysely.get("jarjestys")
    if j and j.get("ulottuvuus") not in m["nimet"]:
        virheet.append(f"tuntematon jarjestysulottuvuus: {j.get('ulottuvuus')!r}")

    a = kysely.get("aika")
    if a is not None and not isinstance(a.get("tunti"), int):
        virheet.append("aika.tunti ei ole kokonaisluku")
    return not virheet, virheet


def suunta_valissa(kulmat, alku, loppu):
    """Kaarto nollan yli hoidettu: [315, 45] tarkoittaa pohjoista."""
    k = np.mod(kulmat, 360)
    if alku <= loppu:
        return (k >= alku) & (k <= loppu)
    return (k >= alku) | (k <= loppu)


def suorita(kysely, m, raaka, kulmat):
    """Staattinen suodatus. Dynaaminen tehdaan selaimessa (tarvitsee saan)."""
    valinta = np.ones(m["n"], dtype=bool)
    for f in kysely.get("suodattimet") or []:
        if f["ulottuvuus"] == "suunta":
            valinta &= suunta_valissa(kulmat, *f["valissa"])
            continue
        x = raaka[:, m["nimet"].index(f["ulottuvuus"])]
        op, v = f["op"], f["arvo"]
        valinta &= (x < v if op == "<" else x > v if op == ">"
                    else x <= v if op == "<=" else x >= v)
    return valinta


def tarkista_varret(s):
    """Osuuko lyhyt varsi TOISEN sanaston sanan nimeen?

    Suomessa lyhyt varsi osuu naapurisanaan helposti, ja seuraus on hiljainen:
    kysely saa mukaansa suodattimen jota kayttaja ei pyytanyt. Ensimmainen
    versio kaytti varta "suo", joka osui sanaan "suojaisa" - "suojaisa ranta"
    haki suorantoja. Sama tarkistus loysi myohemmin viela kaksi: "jyrka" osui
    sanaan "jyrkanteinen" ja "rauhall" sanaan "rauhallinen".
    """
    def norm(t):
        return t.lower().replace("\u00e4", "a").replace("\u00e5", "a").replace("\u00f6", "o")
    varret = {}
    for ryhma in ("ominaisuudet", "suunnat", "dynaaminen"):
        for sana, v in s[ryhma].items():
            for o in v.get("osumat", []):
                varret.setdefault(norm(o), []).append(sana)
    ongelmat = []
    for varsi, omistajat in sorted(varret.items()):
        for ryhma in ("ominaisuudet", "suunnat", "dynaaminen"):
            for sana in s[ryhma]:
                if sana not in omistajat and varsi in norm(sana):
                    ongelmat.append((varsi, omistajat, sana))
    return len(varret), ongelmat


def tarkista_sanasto():
    """Jokaisen sanan valitsema osuus - mitattuna, ei arvattuna."""
    m, s = lataa()
    raaka = raaka_arvot(m)
    kulmat = np.fromfile(ULOS / "suunta.bin", dtype=np.uint16).astype(float)
    print(f"{'sana':20s} {'ulottuvuus':24s} {'ehto':>14s} {'osuus':>8s}")
    for sana, d in s["ominaisuudet"].items():
        ok, v = validoi({"suodattimet": [d]}, m)
        if not ok:
            print(f"{sana:20s} VALIDOINTI HYLKASI: {v}"); continue
        osuus = suorita({"suodattimet": [d]}, m, raaka, kulmat).mean()
        merkki = " <-- harvinainen" if osuus < 0.005 else ""
        yks = d.get("yksikko", "")
        arvo = d["arvo"] * (100 if yks == "%" else 1)
        print(f"{sana:20s} {d['ulottuvuus']:24s} "
              f"{d['op'] + ' ' + f'{arvo:g}' + yks:>14s} {100*osuus:7.1f} %{merkki}")
    print()
    for sana, d in s["suunnat"].items():
        a, b = d["valissa"]
        osuus = suunta_valissa(kulmat, a, b).mean()
        print(f"{sana:20s} {'suunta':24s} {f'{a}-{b}astetta':>14s} {100*osuus:7.1f} %")

    n, ongelmat = tarkista_varret(s)
    print(f"\nvarsien tormaystarkistus: {n} varta")
    for varsi, omistajat, sana in ongelmat:
        print(f"  TORMAYS: varsi {varsi!r} ({', '.join(omistajat)}) osuu sanaan {sana!r}")
    print("  " + ("ei tormayksia" if not ongelmat
                  else f"{len(ongelmat)} tormaysta - korjaa ennen kayttoa"))
    return 0 if not ongelmat else 1


if __name__ == "__main__":
    sys.exit(tarkista_sanasto())
