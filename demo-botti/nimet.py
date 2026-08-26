#!/usr/bin/env python3
"""Paikannimet ehdokkaille MML:n nimistosta.

LAHIN NIMI EI OLE SAMA KUIN OIKEA NIMI. Ranta 200 m paassa "Kalliosaaresta"
ei valttamatta ole Kalliosaaren rantaa - se voi olla naapurisaari jolla ei
ole nimea. Siksi etaisyys tallennetaan ja NAYTETAAN AINA, ja botti sanoo
"lahella: Kalliosaari (180 m)" eika "Kalliosaaren rannalla".

Lahde on nimisto_koko_suomi / karttanimet_25k, GML, 1,27 Gt purettuna.
Luetaan VIRTANA suoraan zipista: koko tiedosto ei mahdu muistiin.
"""
import json
import pathlib
import sys
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

JUURI = pathlib.Path(__file__).resolve().parent.parent
ULOS = JUURI / "demo-botti"
ZIP = JUURI / "nimisto-mml" / "mapnames_25_2026_05.zip"
XML = "mapnames_25.xml"
NS = "{http://xml.nls.fi/geographic-names/2019/02}"
GML = "{http://www.opengis.net/gml}"
MARGINAALI_M = 3000.0        # nimi voi olla alueen ulkopuolella mutta lahella


def lue_nimet(raja):
    x0, y0, x1, y1 = raja
    nimet = []
    with zipfile.ZipFile(ZIP) as z, z.open(XML) as f:
        for tapahtuma, el in ET.iterparse(f, events=("end",)):
            if el.tag != NS + "MapName":
                continue
            teksti = el.findtext(NS + "text")
            pos = el.find(f"{NS}placeLocation/{GML}Point/{GML}pos")
            if teksti and pos is not None and pos.text:
                x, y = (float(v) for v in pos.text.split()[:2])
                if x0 <= x <= x1 and y0 <= y <= y1:
                    nimet.append((teksti, x, y,
                                  el.findtext(NS + "placeType") or ""))
            # MUISTI VAPAUTETTAVA. Ilman clear():ia puu kasvaa 1,27 Gt:hen
            # riippumatta siita etta valtaosa alkioista hylataan heti.
            el.clear()
    return nimet


def main():
    d = np.load(JUURI / "demo-oma" / "ehdokkaat_raaka.npz", allow_pickle=True)
    xs, ys = d["x"], d["y"]
    raja = (xs.min() - MARGINAALI_M, ys.min() - MARGINAALI_M,
            xs.max() + MARGINAALI_M, ys.max() + MARGINAALI_M)
    print(f"luetaan {ZIP.name} virtana, rajaus "
          f"{raja[0]:.0f} {raja[1]:.0f} {raja[2]:.0f} {raja[3]:.0f}", flush=True)
    nimet = lue_nimet(raja)
    print(f"  {len(nimet):,} nimea testialueella")
    if not nimet:
        print("VIRHE: yhtaan nimea ei loytynyt"); return 1

    from scipy.spatial import cKDTree
    nx = np.array([n[1] for n in nimet]); ny = np.array([n[2] for n in nimet])
    puu = cKDTree(np.column_stack([nx, ny]))
    et, idx = puu.query(np.column_stack([xs, ys]))

    (ULOS / "nimet.json").write_text(json.dumps(
        {"nimet": [n[0] for n in nimet],
         "tyypit": [n[3] for n in nimet],
         "_varoitus": ("Lahin nimi ei ole sama kuin oikea nimi. Etaisyys on "
                       "naytettava aina.")},
        ensure_ascii=False))
    np.clip(idx, 0, 65535).astype(np.uint16).tofile(ULOS / "nimi_indeksi.bin")
    np.clip(np.rint(et), 0, 65535).astype(np.uint16).tofile(ULOS / "nimi_etaisyys.bin")

    print(f"\netaisyys lahimpaan nimeen:")
    for p in (10, 50, 90, 99):
        print(f"  p{p:<3d} {np.percentile(et, p):6.0f} m")
    print(f"  yli 500 m: {100*(et > 500).mean():.1f} %  "
          f"(naissa nimi on todennakoisesti VAARA)")
    yleisimmat = {}
    for i in idx:
        yleisimmat[nimet[i][0]] = yleisimmat.get(nimet[i][0], 0) + 1
    print("\nyleisimmat nimet:")
    for n, k in sorted(yleisimmat.items(), key=lambda kv: -kv[1])[:8]:
        print(f"  {n}: {k} ehdokasta")
    return 0


if __name__ == "__main__":
    sys.exit(main())
