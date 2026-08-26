#!/usr/bin/env python3
"""Ubuntun paatepiste: kysymys -> validoitu kysely.

TAMA PALVELIN EI KOSKAAN PALAUTA FAKTOJA RANNOISTA. Se palauttaa vain
kyselyn, joka suoritetaan selaimessa paikallista aineistoa vasten. Nain
hallusinaatio ei rakenteellisesti VOI tuottaa vaaraa vaitetta rannasta.

Kolme porttia:
  1. Kehote antaa mallille VAIN sanaston ja kaavan
  2. Vastaus validoidaan kysely.validoi():lla ennen palautusta
  3. Selain validoi uudelleen (index.html: validoi)

Kaynnistys Ubuntulla:
    ANTHROPIC_API_KEY=... .venv/bin/python3 demo-botti/palvelin.py

Ilman avainta palvelin kaynnistyy silti ja palauttaa 503, jolloin selain
siirtyy varapolulle - demo ei ole koskaan kuollut.
"""
import json
import os
import pathlib
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

JUURI = pathlib.Path(__file__).resolve().parent.parent
ULOS = JUURI / "demo-botti"
PORTTI = int(os.environ.get("BOTTI_PORTTI", "8772"))
MALLI = os.environ.get("BOTTI_MALLI", "claude-sonnet-5")

sys.path.insert(0, str(ULOS))
import kysely as K  # noqa: E402


def kehote(meta, sanasto):
    """Kehote antaa mallille VAIN sanaston ja kaavan - ei vapautta keksia."""
    ominaisuudet = "\n".join(
        f"  {s}: {d['ulottuvuus']} {d['op']} {d['arvo']}"
        for s, d in sanasto["ominaisuudet"].items())
    suunnat = "\n".join(f"  {s}: {d['valissa']}" for s, d in sanasto["suunnat"].items())
    return f"""Kaannat suomenkielisen kysymyksen rannoista JSON-kyselyksi.

PALAUTA VAIN JSON. Ala koskaan kirjoita vaitteita rannoista, ala arvioi
turvallisuutta, ala keksi ominaisuuksia.

Sallitut ulottuvuudet (VAIN nama):
{", ".join(meta["nimet"])}

Sanasto (sana -> suodatin):
{ominaisuudet}

Suunnat (mihin ilmansuuntaan ranta antaa, [alku, loppu] asteina):
{suunnat}

Dynaamiset suureet: aallonkorkeus (metria), suojaisuus (0-1).

Kaava:
{{"suodattimet": [{{"ulottuvuus": "...", "op": "<|>|<=|>=", "arvo": luku}}],
  "dynaaminen": [{{"suure": "aallonkorkeus", "op": "<", "arvo": 0.15}}],
  "aika": {{"tunti": 24}},
  "tulkinta": "lyhyt kuvaus suomeksi"}}

Suunta annetaan muodossa {{"ulottuvuus": "suunta", "valissa": [45, 135]}}.

Jos kysymys koskee turvallisuutta ("onko turvallista", "kannattaako"),
palauta {{"kieltaydy": "turvallisuus"}}.
Jos kysymys koskee jotain mita sanastossa ei ole (ruovikko, syvyys,
laituri), palauta {{"tuntematon": "<se sana>"}}.
"""


class Kasittelija(BaseHTTPRequestHandler):
    def _json(self, koodi, data):
        runko = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(koodi)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("access-control-allow-origin", "*")
        self.send_header("content-length", str(len(runko)))
        self.end_headers()
        self.wfile.write(runko)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("access-control-allow-origin", "*")
        self.send_header("access-control-allow-headers", "content-type")
        self.end_headers()

    def do_POST(self):
        if self.path.rstrip("/") not in ("/kysely", "/api/kysely", ""):
            return self._json(404, {"virhe": f"tuntematon reitti {self.path}"})
        pituus = int(self.headers.get("content-length", 0))
        try:
            pyynto = json.loads(self.rfile.read(pituus) or b"{}")
        except ValueError:
            return self._json(400, {"virhe": "viallinen JSON"})
        if pyynto.get("koe"):
            return self._json(200, {"tila": "ok", "malli": MALLI})

        kysymys = (pyynto.get("kysymys") or "").strip()
        if not kysymys:
            return self._json(400, {"virhe": "kysymys puuttuu"})
        avain = os.environ.get("ANTHROPIC_API_KEY")
        if not avain:
            # 503 EIKA KAATUMINEN: selain siirtyy varapolulle ja kertoo sen.
            return self._json(503, {"virhe": "ei API-avainta"})

        try:
            import anthropic
            vastaus = anthropic.Anthropic(api_key=avain).messages.create(
                model=MALLI, max_tokens=600,
                system=kehote(self.meta, self.sanasto),
                messages=[{"role": "user", "content": kysymys}])
            teksti = "".join(o.text for o in vastaus.content if o.type == "text")
            alku, loppu = teksti.find("{"), teksti.rfind("}")
            k = json.loads(teksti[alku:loppu + 1])
        except Exception as e:
            return self._json(502, {"virhe": f"{type(e).__name__}: {e}"})

        if "kieltaydy" in k or "tuntematon" in k:
            return self._json(200, k)
        # PORTTI 2: validointi ennen palautusta. Selain validoi uudelleen.
        ok, virheet = K.validoi(k, self.meta)
        if not ok:
            return self._json(200, {"eiYmmarretty": True, "virheet": virheet})
        return self._json(200, k)

    def log_message(self, *a):
        pass


def main():
    meta, sanasto = K.lataa()
    Kasittelija.meta, Kasittelija.sanasto = meta, sanasto
    print(f"rantabotti kuuntelee: http://127.0.0.1:{PORTTI}/kysely  (malli {MALLI})")
    print("  selain kayttaa tata oletuksena; muualle: ?api=<osoite>")
    print(f"  API-avain: {'on' if os.environ.get('ANTHROPIC_API_KEY') else 'PUUTTUU (503, selain kayttaa varapolkua)'}")
    ThreadingHTTPServer(("127.0.0.1", PORTTI), Kasittelija).serve_forever()


if __name__ == "__main__":
    sys.exit(main())
