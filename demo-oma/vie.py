#!/usr/bin/env python3
"""Yhdistaa piirteet ja vie ne selaimelle."""
import json
import pathlib
import sys

import numpy as np

ULOS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ULOS))
from piirteet import kvantiloi            # noqa: E402
from pisteytys import LUOKAT              # noqa: E402

# Pyyhkaisytasot ovat logaritmisia (pipeline: MIN_FETCH_M..MAX_FETCH_M).
MIN_FETCH_M, MAX_FETCH_M, FETCH_LEVELS = 50.0, 15000.0, 16

# ESITYSTIEDOT. Selitys puhuu oikeista yksikoista eika kvantiilista:
# "puskurin leveys 12,3 m" kertoo jotain, "0,84" ei.
#   otsikko - kayttajalle nayetettava nimi
#   yksikko - "m", "%", "taso" tai "" (pelkka jarjestysluku)
#   kerroin - raaka-arvo kerrotaan talla ennen naytto
#   nakyy   - false: tekninen suure jota ei kannata selittaa
ESITYS = {
    "jyrkkyys":               ("loivuus",                "%",   100 / 255, True),
    "etaisyys_rakennuksiin":  ("etäisyys rakennuksiin",  "%",   100 / 255, True),
    "kallio":                 ("kallio kartasta",        "%",   100.0,     True),
    "suo":                    ("suo kartasta",           "%",   100.0,     True),
    "tasapelinpurku":         ("tasapelinpurku",         "",    1.0,       False),
    "puskurin_leveys":        ("rantakaistaleen leveys", "m",   1.0,       True),
    "hietikko":               ("hiekkaranta",            "%",   100.0,     True),
    "kivikko":                ("kivikko",                "%",   100.0,     True),
    "kallio_vektori":         ("kallioalue",             "%",   100.0,     True),
    "suo_vektori":            ("suo",                    "%",   100.0,     True),
    "avoin_maa":              ("avoin maa",              "%",   100.0,     True),
    "hoidettu":               ("puisto tai virkistys",   "%",   100.0,     True),
    "satama":                 ("satama-alue",            "%",   100.0,     True),
    "rakennettu":             ("rakennettu alue",        "%",   100.0,     True),
    "jyrkanne_lahella":       ("jyrkänne lähellä",       "%",   100.0,     True),
    "etaisyys_rahtivaylaan":  ("etäisyys rahtiväylään",  "m",   1.0,       True),
}
RYHMA_OTSIKKO = "avoimuus merelle"


def fetch_metreina(taso):
    """Pyyhkaisytaso 0-15 -> metria (sama logaritminen asteikko kuin
    pipeline.py:ssa). Selityksessa taso 6 ei kerro mitaan, 900 m kertoo."""
    lo, hi = np.log(MIN_FETCH_M), np.log(MAX_FETCH_M)
    return np.exp(lo + (hi - lo) * np.asarray(taso, float) / (FETCH_LEVELS - 1))


def main():
    d = np.load(ULOS / "ehdokkaat_raaka.npz", allow_pickle=True)
    m = np.load(ULOS / "maastoluokat.npz", allow_pickle=True)
    v = np.load(ULOS / "vaylaetaisyys.npz", allow_pickle=True)

    geo = [str(x) for x in d["nimet"]]
    maa = [str(x) for x in m["nimet"]]
    vay = [str(x) for x in v["nimet"]]
    nimet = geo + maa + vay
    raaka = np.hstack([d["piirteet"], m["luokat"], v["etaisyys"].reshape(-1, 1)])
    q = np.hstack([kvantiloi(d["piirteet"]), kvantiloi(m["luokat"]),
                   kvantiloi(v["etaisyys"].reshape(-1, 1))])
    xs, ys = d["x"], d["y"]

    # KVANTIILI TAKAISIN RAAKA-ARVOKSI. 101 katkaisukohtaa per ulottuvuus
    # riittaa: selitys nayttaa yhden desimaalin, ei tutkimustulosta.
    taulu = np.stack([np.percentile(raaka[:, j], np.arange(101))
                      for j in range(raaka.shape[1])])
    for j, nimi in enumerate(nimet):
        if nimi.startswith("pyyhkaisy_"):
            taulu[j] = fetch_metreina(taulu[j])

    # Uint8 riittaa: arvo on kvantiili, joten 1/255 on 0,4 prosenttiyksikkoa.
    (ULOS / "piirteet.bin").write_bytes(
        np.clip(np.rint(q * 255), 0, 255).astype(np.uint8).tobytes())
    np.stack([np.rint(xs), np.rint(ys)], axis=1).astype(np.int32).tofile(
        ULOS / "koordinaatit.bin")
    taulu.astype(np.float32).tofile(ULOS / "kvantiilit.bin")

    esitys = {}
    for n in nimet:
        if n.startswith("pyyhkaisy_"):
            esitys[n] = {"otsikko": RYHMA_OTSIKKO, "yksikko": "m",
                         "kerroin": 1.0, "nakyy": True}
        else:
            o, y, k, nak = ESITYS.get(n, (n, "", 1.0, True))
            esitys[n] = {"otsikko": o, "yksikko": y, "kerroin": k, "nakyy": nak}

    (ULOS / "piirteet.json").write_text(json.dumps({
        "n": int(len(xs)), "d": len(nimet), "nimet": nimet,
        "luokat": {n: LUOKAT.get(n, "tavoite") for n in nimet},
        "esitys": esitys,
        "ryhma_etuliite": "pyyhkaisy_",
        "ryhma_otsikko": RYHMA_OTSIKKO,
        # Piirrekohtainen varianssi kutistustermia varten. EI 1/12:
        # kvantiilimuunnos tuottaa tasajakauman vain jos sidoksia ei ole.
        "varianssit": [float(x) for x in q.var(axis=0)],
        "vali_m": 50.0, "ikkuna_m": 25.0,
        "keskipiste": [386385.0, 6671827.0], "sade_m": 50000.0,
    }, indent=1, ensure_ascii=False))

    print(f"{len(xs):,} ehdokasta x {len(nimet)} ulottuvuutta")
    print(f"  piirteet.bin     {len(xs)*len(nimet)/1e6:.2f} Mt")
    print(f"  koordinaatit.bin {len(xs)*8/1e6:.2f} Mt")
    print(f"  kvantiilit.bin   {taulu.size*4/1e3:.1f} kt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
