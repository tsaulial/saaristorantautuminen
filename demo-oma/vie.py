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

# PISTEMAARAT KAANNETAAN TAKAISIN OIKEIKSI YKSIKOIKSI.
#
# factors-kuvan R ja G eivat ole asteita eivatka metreja vaan KYLLASTYVIA
# PISTEMAARIA (score_engine): jyrkkyys on 1,0 alle 5 asteen ja 0 yli 20:n,
# etaisyys 0 alle 20 metrin ja 1,0 yli 150:n. Naytin ne aluksi prosentteina
# nimilla "loivuus" ja "etaisyys rakennuksiin", mika lupasi enemman kuin luku
# antaa: 200 m ja 5 km rakennuksista nayttavat molemmat "100 %".
#
# Nyt ne kaannetaan takaisin asteiksi ja metreiksi, ja KYLLASTYSRAJA
# merkitaan naytolla - "yli 150 m" on rehellinen, "100 %" ei.
SLOPE_FLAT_DEG, SLOPE_REJECT_DEG = 5.0, 20.0
DIST_REJECT_M, DIST_IDEAL_M = 20.0, 150.0


def jyrkkyys_asteina(pisteet):
    """slope_score 0-255 -> kaltevuus asteina."""
    s = np.clip(np.asarray(pisteet, float) / 255.0, 0.0, 1.0)
    return SLOPE_REJECT_DEG - s * (SLOPE_REJECT_DEG - SLOPE_FLAT_DEG)


def etaisyys_metreina(pisteet):
    """dist_score 0-255 -> etaisyys metreina (kyllastyy 150 m:iin)."""
    s = np.clip(np.asarray(pisteet, float) / 255.0, 0.0, 1.0)
    return DIST_REJECT_M + s * (DIST_IDEAL_M - DIST_REJECT_M)

# ESITYSTIEDOT. Selitys puhuu oikeista yksikoista eika kvantiilista:
# "puskurin leveys 12,3 m" kertoo jotain, "0,84" ei.
#   otsikko - kayttajalle nayetettava nimi
#   yksikko - "m", "%", "taso" tai "" (pelkka jarjestysluku)
#   kerroin - raaka-arvo kerrotaan talla ennen naytto
#   nakyy   - false: tekninen suure jota ei kannata selittaa
ESITYS = {
    "jyrkkyys":               ("kaltevuus",              "°",   1.0,       True),
    "etaisyys_rakennuksiin":  ("etäisyys rakennuksiin",  "m+",  1.0,       True),
    "kallio":                 ("kallio kartasta",        "%",   100.0,     True),
    "suo":                    ("suo kartasta",           "%",   100.0,     True),
    "tasapelinpurku":         ("tasapelinpurku",         "",    1.0,       False),
    # EI "rantakaistaleen leveys". Puskurivyohyke on maaritelman mukaan aina
    # 5-15 m (pipeline.compute_shoreline_buffer), joten sen leveytta ei voi
    # mitata. Luku on vyohykkeen PINTA-ALA 25 m sateella jaettuna 50:lla, ja
    # mitattuna se kertoo kuinka paljon maata pisteen ymparilla on:
    # korrelaatio kiekon maa-alaan on r = 0,93. Arvo 0,6 vastaa 16-22 %
    # maata (kapea kannas tai pieni luoto), arvo 18 vastaa 71-86 %.
    "puskurin_leveys":        ("rantavyöhykettä ympärillä", "%", 100 / 39.27, True),
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
    # Pinta-ala PISTEMAARALLA PAINOTETTUNA: paljonko ymparilla on maata jolle
    # oikeasti voi rantautua. Eri asia kuin "rantavyohyketta ymparilla", joka
    # laskee myos jyrkan kallion mukaan - saman laajuuden rannoilla tama
    # vaihtelee mitattuna 257-541 m2 (p10-p90).
    "kelvollista_rantaa":     ("kelvollista rantaa",     "m2",  1.0,       True),
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
    ka = np.load(ULOS / "kelpoala.npz", allow_pickle=True)
    yl = np.load(ULOS / "yleinen.npz", allow_pickle=True)

    geo = [str(x) for x in d["nimet"]]
    maa = [str(x) for x in m["nimet"]]
    vay = [str(x) for x in v["nimet"]]
    kel = [str(x) for x in ka["nimet"]]
    nimet = geo + maa + vay + kel
    # TASAPELINPURKU POIS PIIRREVEKTORISTA.
    #
    # Se on TUOTANNOSSA valttamaton: varsinainen pistemaara kyllastyy tasan
    # arvoon 1,0, jolloin ilman sita "parhaat 1 %" ja "parhaat 7 %" ovat
    # sama alue (pipeline.py:278). Piirteena se ei ansaitse paikkaansa -
    # mitattuna korrelaatio jyrkkyyspisteisiin 0,63 ja etaisyyspisteisiin
    # 0,74, ja sen poistaminen ei muuttanut poisjattokoetta desimaaliakaan.
    # clusterplan varoittaa tasta: korreloivat piirteet heikentavat hakua.
    #
    # backend/pipeline.py:hyn EI kosketa - poisto koskee vain tata vektoria.
    POIS = {"tasapelinpurku"}
    pidä = [i for i, n in enumerate(nimet) if n not in POIS]
    raaka = np.hstack([d["piirteet"], m["luokat"],
                       v["etaisyys"].reshape(-1, 1), ka["ala"].reshape(-1, 1)])
    raaka = raaka[:, pidä]
    nimet = [nimet[i] for i in pidä]
    q = kvantiloi(raaka)
    xs, ys = d["x"], d["y"]

    # KVANTIILI TAKAISIN RAAKA-ARVOKSI. 101 katkaisukohtaa per ulottuvuus
    # riittaa: selitys nayttaa yhden desimaalin, ei tutkimustulosta.
    taulu = np.stack([np.percentile(raaka[:, j], np.arange(101))
                      for j in range(raaka.shape[1])])
    for j, nimi in enumerate(nimet):
        if nimi.startswith("pyyhkaisy_"):
            taulu[j] = fetch_metreina(taulu[j])
        elif nimi == "jyrkkyys":
            taulu[j] = jyrkkyys_asteina(taulu[j])
        elif nimi == "etaisyys_rakennuksiin":
            taulu[j] = etaisyys_metreina(taulu[j])

    # Uint8 riittaa: arvo on kvantiili, joten 1/255 on 0,4 prosenttiyksikkoa.
    (ULOS / "piirteet.bin").write_bytes(
        np.clip(np.rint(q * 255), 0, 255).astype(np.uint8).tobytes())
    np.stack([np.rint(xs), np.rint(ys)], axis=1).astype(np.int32).tofile(
        ULOS / "koordinaatit.bin")
    taulu.astype(np.float32).tofile(ULOS / "kvantiilit.bin")

    # YLEISEN MALLIN PISTEMAARA JA SEN TERMIT. Omassa tiedostossaan, koska
    # se on mallin ULOSTULO eika syote - piirrevektoriin lisattyna oma malli
    # olisi osittain kopio yleisesta.
    osat_u8 = np.clip(np.rint(yl["osat"] * 255), 0, 255).astype(np.uint8)
    # PISTEMAARA JOHDETAAN SAMOISTA PYORISTETYISTA TERMEISTA, ei erikseen.
    #
    # Erikseen pyoristettyna kolmen termin summa ja pistemaara erosivat
    # toisistaan enintaan 1,5/255 - mitattuna 0,004. Se ei nayta miltaan,
    # mutta selityspaneeli vaittaa termien selittavan luvun, ja silloin
    # niiden on summauduttava siihen. Pieni aaneton ristiriita on tassa
    # projektissa maksanut aikaa useammin kuin suuri kaatuminen.
    pisteet_u8 = np.clip(osat_u8[:, :3].astype(np.int32).sum(axis=1),
                         0, 255).astype(np.uint8)
    (ULOS / "yleinen.bin").write_bytes(pisteet_u8.tobytes())
    (ULOS / "yleinen_osat.bin").write_bytes(osat_u8.tobytes())

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
        # Yleisen mallin selitys: termien nimet ja kayttajalle nayetettavat
        # otsikot. Kolme ensimmaista summautuvat pistemaaraan, nelias on
        # suon kertovan rangaistuksen viema osuus.
        "yleinen_osat": [str(x) for x in yl["osien_nimet"]],
        "yleinen_otsikot": {
            "jyrkkyys": "loivuus", "etaisyys": "etäisyys rakennuksiin",
            "kallio": "kallio", "suo_menetys": "suo",
        },
        "vali_m": 50.0, "ikkuna_m": 25.0,
        "keskipiste": [386385.0, 6671827.0], "sade_m": 50000.0,
    }, indent=1, ensure_ascii=False))

    print(f"{len(xs):,} ehdokasta x {len(nimet)} ulottuvuutta")
    print(f"  piirteet.bin     {len(xs)*len(nimet)/1e6:.2f} Mt")
    print(f"  koordinaatit.bin {len(xs)*8/1e6:.2f} Mt")
    print(f"  kvantiilit.bin   {taulu.size*4/1e3:.1f} kt")
    print(f"  yleinen.bin      {len(xs)/1e3:.1f} kt")
    print(f"  yleinen_osat.bin {len(xs)*4/1e3:.1f} kt")
    print(f"  (tasapelinpurku pudotettu vektorista)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
