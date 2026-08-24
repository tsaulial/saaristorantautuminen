#!/usr/bin/env python3
"""Yhdistaa geometria- ja maanpeitepiirteet ja vie ne selaimelle."""
import json, pathlib, sys
import numpy as np
ULOS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ULOS))
from piirteet import kvantiloi
from pisteytys import LUOKAT

d = np.load(ULOS/"ehdokkaat_raaka.npz", allow_pickle=True)
m = np.load(ULOS/"maastoluokat.npz", allow_pickle=True)
geo = [str(x) for x in d["nimet"]]; maa = [str(x) for x in m["nimet"]]
q = np.hstack([kvantiloi(d["piirteet"]), kvantiloi(m["luokat"])])
nimet = geo + maa
xs, ys = d["x"], d["y"]

# Uint8 riittaa: arvo on kvantiili, joten 1/255 on 0,4 prosenttiyksikkoa -
# hienompi kuin mikaan tulkinta jota siita tehdaan.
(ULOS/"piirteet.bin").write_bytes(
    np.clip(np.rint(q*255), 0, 255).astype(np.uint8).tobytes())
np.stack([np.rint(xs), np.rint(ys)], axis=1).astype(np.int32).tofile(ULOS/"koordinaatit.bin")
(ULOS/"piirteet.json").write_text(json.dumps({
    "n": int(len(xs)), "d": len(nimet), "nimet": nimet,
    "luokat": {n: LUOKAT.get(n, "tavoite") for n in nimet},
    "ryhma_etuliite": "pyyhkaisy_",
    # Piirrekohtainen varianssi kutistustermia varten. EI 1/12: kvantiili-
    # muunnos tuottaa tasajakauman vain jos sidoksia ei ole, ja harvinaisilla
    # maanpeiteluokilla 99 % ehdokkaista on samassa arvossa.
    "varianssit": [float(v) for v in q.var(axis=0)],
    "vali_m": 50.0, "ikkuna_m": 25.0,
    "keskipiste": [386385.0, 6671827.0], "sade_m": 50000.0,
}, indent=1, ensure_ascii=False))
print(f"{len(xs):,} ehdokasta x {len(nimet)} ulottuvuutta")
print(f"  piirteet.bin     {len(xs)*len(nimet)/1e6:.2f} Mt")
print(f"  koordinaatit.bin {len(xs)*8/1e6:.2f} Mt")
