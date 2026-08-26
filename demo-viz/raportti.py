#!/usr/bin/env python3
"""Kokoaa raportin: kuvat base64:na ja luvut suoraan aineistosta.

LUVUT LUETAAN, EI KIRJOITETA KASIN. Muuten ne vanhenevat hiljaa
seuraavassa muutoksessa - sama saanto kuin tarina.html:n taulukoissa.
"""
import base64, json, pathlib, sys
import numpy as np

JUURI = pathlib.Path(__file__).resolve().parent.parent
LAHDE, VIZ = JUURI / "demo-oma", JUURI / "demo-viz"

def b64(nimi):
    d = (VIZ / "kuvat" / nimi).read_bytes()
    return f"data:image/png;base64,{base64.b64encode(d).decode()}"

m = json.loads((LAHDE / "piirteet.json").read_text())
q = (np.fromfile(LAHDE / "piirteet.bin", dtype=np.uint8)
     .reshape(m["n"], m["d"]).astype(np.float64) / 255.0)
kl = np.load(VIZ / "klusterit.npz")
C = np.corrcoef(q, rowvar=False)
otsikot = [m["esitys"][n]["otsikko"] for n in m["nimet"]]
X = q - q.mean(0); _, s, _ = np.linalg.svd(X, full_matrices=False)
kum = np.cumsum(s**2 / (s**2).sum())
LUVUT = {
    "n": f"{m['n']:,}".replace(",", " "), "d": m["d"],
    "arvoja": f"{m['n']*m['d']:,}".replace(",", " "),
    "k95": int(np.searchsorted(kum, 0.95) + 1),
    "k": int(kl["k"]), "siluetti": f"{float(kl['siluetti']):.3f}".replace(".", ","),
    "varmin": f"{q.var(axis=0).min():.4f}".replace(".", ","),
    "varmin_nimi": otsikot[int(np.argmin(q.var(axis=0)))],
    "kallio_r": f"{C[m['nimet'].index('kallio'), m['nimet'].index('kallio_vektori')]:.3f}".replace(".", ","),
}
print(json.dumps(LUVUT, ensure_ascii=False, indent=1))
runko = (VIZ / "raportti_runko.html").read_text(encoding="utf-8")
for k, v in LUVUT.items():
    runko = runko.replace("{{" + k + "}}", str(v))
for i, nimi in enumerate(sorted(p.name for p in (VIZ/"kuvat").glob("*.png")), 1):
    runko = runko.replace("{{kuva" + str(i) + "}}", b64(nimi))
(VIZ / "raportti.html").write_text(runko, encoding="utf-8")
koko = (VIZ / "raportti.html").stat().st_size
print(f"\nraportti.html {koko/1e6:.2f} Mt  ({'OK' if koko < 16e6 else 'YLI 16 Mt'})")
