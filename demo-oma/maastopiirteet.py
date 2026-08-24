#!/usr/bin/env python3
"""Maastotietokannan kuvioluokat ehdokkaille.

Vastaa kysymykseen "saako peruskartasta enemman kuin kallion ja suon".
Saa - mutta EI VARISTA vaan vektorista: sama maastotietokanta josta kartta
on piirretty, teema "maasto". Silloin luokat tulevat sellaisina kuin ne on
kirjattu, ilman korkeuskayrien ja karttamerkkien aiheuttamaa rikkoutumista
jota varitunnistus joutuu paikkaamaan morfologisella sulkemisella.
"""
import json
import pathlib
import sys

import numpy as np

JUURI = pathlib.Path(__file__).resolve().parent.parent
ULOS = JUURI / "demo-oma"
# Haetaan komennolla (10 000 km2 = 50 km sade Kauppatorilta, alle rajapinnan
# 17 000 km2 katon):
#     python3 -m backend.mml_lataus  ... teema "maasto"
# ks. demo-oma/README.md
GPKG = JUURI / "maasto-mml" / "maasto.gpkg"

# Rantautumisen kannalta merkitykselliset kuviot. Nama EIVAT ole
# korkeusmallin uudelleenmuotoiluja vaan itsenaisia havaintoja maanpeitteesta
# - juuri sita mita clusterplan.md sanoi vain ilmakuvan voivan tuoda.
LUOKAT = {
    "hietikko": ["hietikko"],
    "kivikko": ["kivikko", "harvalouhikko"],
    "kallio_vektori": ["kallioalue"],
    "suo_vektori": ["suo", "soistuma"],
    "avoin_maa": ["niitty", "muuavoinalue", "maatalousmaa"],
    "hoidettu": ["puisto", "urheilujavirkistysalue", "hautausmaa"],
    "satama": ["satamaalue", "varastoalue"],
    "rakennettu": ["taajaanrakennettualue", "autoliikennealue"],
}
JYRKANNE = "jyrkanne"        # viiva: talletetaan lahisyytena, ei pinta-alana

PX_M = 2.0
IKKUNA_SADE_M = 25.0


def main():
    sys.path.insert(0, str(JUURI))
    import pyogrio
    from rasterio.features import rasterize
    from rasterio.transform import from_origin
    from shapely.geometry import box
    from shapely import union_all
    sys.path.insert(0, str(ULOS))
    from piirteet import testialueen_tiilet

    d = np.load(ULOS / "ehdokkaat_raaka.npz", allow_pickle=True)
    xs, ys = d["x"], d["y"]
    n = len(xs)
    nimet = list(LUOKAT) + ["jyrkanne_lahella"]
    ulos = np.zeros((n, len(nimet)), dtype=np.float32)

    r_px = int(round(IKKUNA_SADE_M / PX_M))
    yy, xx = np.mgrid[-r_px:r_px + 1, -r_px:r_px + 1]
    kiekko = (yy * yy + xx * xx) <= r_px * r_px

    tiilet = testialueen_tiilet()
    for k, (tile_id, b) in enumerate(tiilet, 1):
        osuu = np.where((xs >= b[0]) & (xs < b[2]) & (ys >= b[1]) & (ys < b[3]))[0]
        if not len(osuu):
            continue
        H = W = int(round((b[3] - b[1]) / PX_M))
        tr = from_origin(b[0], b[3], PX_M, PX_M)
        laatikko = box(*b)
        maskit = {}
        for luokka, tasot in LUOKAT.items():
            geom = []
            for taso in tasot:
                try:
                    df = pyogrio.read_dataframe(GPKG, layer=taso, bbox=tuple(b))
                except Exception:
                    continue
                if len(df):
                    geom.extend(df.geometry.values)
            maskit[luokka] = (
                rasterize([(union_all(geom), 1)], out_shape=(H, W), transform=tr,
                          dtype="uint8").astype(bool)
                if geom else np.zeros((H, W), bool))
        try:
            jd = pyogrio.read_dataframe(GPKG, layer=JYRKANNE, bbox=tuple(b))
            jm = (rasterize([(union_all(jd.geometry.values).buffer(10.0), 1)],
                            out_shape=(H, W), transform=tr, dtype="uint8").astype(bool)
                  if len(jd) else np.zeros((H, W), bool))
        except Exception:
            jm = np.zeros((H, W), bool)

        cc = ((xs[osuu] - b[0]) / PX_M).astype(np.int32)
        rr = ((b[3] - ys[osuu]) / PX_M).astype(np.int32)
        for j, i in enumerate(osuu):
            r0, r1 = rr[j] - r_px, rr[j] + r_px + 1
            c0, c1 = cc[j] - r_px, cc[j] + r_px + 1
            if r0 < 0 or c0 < 0 or r1 > H or c1 > W:
                continue
            for li, luokka in enumerate(LUOKAT):
                ulos[i, li] = float(maskit[luokka][r0:r1, c0:c1][kiekko].mean())
            ulos[i, len(LUOKAT)] = float(jm[r0:r1, c0:c1][kiekko].any())
        print(f"  [{k}/{len(tiilet)}] {tile_id}: {len(osuu)}", flush=True)

    np.savez_compressed(ULOS / "maastoluokat.npz", luokat=ulos,
                        nimet=np.array(nimet))
    print(f"\n{'luokka':18s} {'ehdokkaita > 0':>15s} {'osuus':>8s}")
    for li, nimi in enumerate(nimet):
        m = int((ulos[:, li] > 0).sum())
        print(f"{nimi:18s} {m:15,d} {100*m/n:7.2f} %")
    return 0


if __name__ == "__main__":
    sys.exit(main())
