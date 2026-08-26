#!/usr/bin/env python3
"""Botin oma vienti: suunta, absoluuttinen pyyhkaisy, esteet.

ERILLINEN DEMO. Lukee demo-oma/:n ja demo-botti/esteet.npz:n, kirjoittaa vain
demo-botti/:iin. demo-oma/ pysyy koskemattomana.

POIKKEAMA SUUNNITELMASTA, ja syy kirjattuna. Suunnitelma sanoi tallettaa
suunnan SININA JA KOSININA. Se perustelu - 359 ja 1 astetta ovat naapureita
mutta lukuina kaukana - patee ETAISYYSLASKENNASSA. Suunta on tassa kuitenkin
VAIN SUODATIN, eika sita koskaan kayteta samankaltaisuuteen. Suodattimelle
"antaako ranta itaan" raaka kulma on tasmallinen ja yksinkertainen, kun taas
sin/cos vaatisi kaanteismuunnoksen joka kerta. Kaarto nollan yli hoidetaan
suodatinlogiikassa (kysely.py: suunta_valissa).
"""
import json
import pathlib
import sys

import numpy as np

JUURI = pathlib.Path(__file__).resolve().parent.parent
LAHDE = JUURI / "demo-oma"
ULOS = JUURI / "demo-botti"
SEKTOREITA = 12
SEKTORIN_ASTE = 360.0 / SEKTOREITA


def fetch_metreina(taso, mini, maksi, tasoja):
    lo, hi = np.log(mini), np.log(maksi)
    return np.exp(lo + (hi - lo) * np.asarray(taso, float) / (tasoja - 1))


def main():
    sys.path.insert(0, str(JUURI))
    from backend import pipeline as P

    m = json.loads((LAHDE / "piirteet.json").read_text())
    d = np.load(LAHDE / "ehdokkaat_raaka.npz", allow_pickle=True)
    xs, ys, kulma = d["x"], d["y"], d["kulma"]
    raaka, nimet = d["piirteet"], [str(x) for x in d["nimet"]]
    esteet = np.load(ULOS / "esteet.npz")["tasot"]

    # --- KIERTO TAKAISIN ---
    # piirteet.py talletti sektorit kierrettyina: roll(absoluuttinen, -siirto).
    # Kaanteinen kierto on sama permutaatio toiseen suuntaan, siis HAVIOTON.
    i0 = nimet.index("pyyhkaisy_0")
    kierretyt = raaka[:, i0:i0 + SEKTOREITA]
    absol = np.empty_like(kierretyt)
    for i in range(len(absol)):
        siirto = int(round(kulma[i] / SEKTORIN_ASTE)) % SEKTOREITA
        absol[i] = np.roll(kierretyt[i], siirto)

    fetch_m = fetch_metreina(absol, P.MIN_FETCH_M, P.MAX_FETCH_M, P.FETCH_LEVELS)
    este_m = esteet * (P.OBSTACLE_MAX_M / (P.OBSTACLE_LEVELS - 1))

    ULOS.mkdir(exist_ok=True)
    # Selaimen tiedostot. Piirteet ja koordinaatit kopioidaan sellaisenaan,
    # jotta demo-omaa ei tarvitse lukea ajonaikana - se on toinen demo.
    (ULOS / "piirteet.bin").write_bytes((LAHDE / "piirteet.bin").read_bytes())
    (ULOS / "koordinaatit.bin").write_bytes((LAHDE / "koordinaatit.bin").read_bytes())
    (ULOS / "kvantiilit.bin").write_bytes((LAHDE / "kvantiilit.bin").read_bytes())
    np.rint(kulma).astype(np.uint16).tofile(ULOS / "suunta.bin")
    # WGS84 VALMIIKSI LASKETTUNA. Tuuliennuste haetaan lat/lon-pisteille,
    # ja muunnos tehdaan tassa pyprojilla eika selaimessa: muuten
    # projektiokaavasta syntyisi NELJAS kopio (tuotannossa on jo
    # wgs84To3067), ja se olisi taas yksi paikka jossa kaksi toteutusta
    # voivat ajautua erilleen.
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:3067", "EPSG:4326", always_xy=True)
    lon, lat = tr.transform(xs, ys)
    np.stack([lat, lon], axis=1).astype(np.float32).tofile(ULOS / "latlon.bin")
    np.rint(np.clip(fetch_m, 0, 65535)).astype(np.uint16).tofile(ULOS / "fetch.bin")
    np.rint(np.clip(este_m * 100, 0, 65535)).astype(np.uint16).tofile(ULOS / "esteet.bin")

    meta = dict(m)
    meta.update({
        "sektoreita": SEKTOREITA,
        "sektorin_aste": SEKTORIN_ASTE,
        # Jaetun sopimuksen vakiot, jotta selain ei kirjoita niita uudelleen.
        "vakiot": {
            "WAVE_COEFF": P.WAVE_COEFF,
            "WIND_SHELTER_MAX": P.WIND_SHELTER_MAX,
            "WIND_SHELTER_K": P.WIND_SHELTER_K,
            "SHELTER_CALM_M": P.SHELTER_CALM_M,
            "SHELTER_ROUGH_M": P.SHELTER_ROUGH_M,
        },
        "este_kerroin": 0.01,      # esteet.bin on senttimetreja
    })
    (ULOS / "piirteet.json").write_text(
        json.dumps(meta, indent=1, ensure_ascii=False))

    print(f"{len(xs):,} ehdokasta")
    print(f"  suunta.bin  {len(xs)*2/1e3:6.1f} kt  (asteita 0-359)")
    print(f"  latlon.bin  {len(xs)*8/1e3:6.1f} kt  (WGS84, pyprojilla)")
    print(f"  fetch.bin   {fetch_m.size*2/1e3:6.1f} kt  (metria, absoluuttiset sektorit)")
    print(f"  esteet.bin  {este_m.size*2/1e3:6.1f} kt  (senttimetria)")
    print(f"\ntodennus: kierto takaisin on haviotton permutaatio")
    # Sektorin 0 (merelle pain) on oltava avoin, ja absoluuttisen sektorin
    # joka vastaa kulmaa oltava sama arvo.
    ok = 0
    for i in range(0, len(absol), 997):
        siirto = int(round(kulma[i] / SEKTORIN_ASTE)) % SEKTOREITA
        ok += abs(absol[i, siirto] - kierretyt[i, 0]) < 1e-9
    n = len(range(0, len(absol), 997))
    print(f"  {ok}/{n} otoksesta: absol[kulmasektori] == kierretty[0]  "
          f"{'OK' if ok == n else 'VIRHE'}")
    print(f"  fetch mediaani merelle pain: "
          f"{np.median([fetch_m[i, int(round(kulma[i]/SEKTORIN_ASTE)) % SEKTOREITA] for i in range(0, len(absol), 97)]):.0f} m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
