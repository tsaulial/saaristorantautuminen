"""Backend-paketti.

TAMA TIEDOSTO ON OLEMASSA VAIN YHDEN VIRHEILMOITUKSEN TAKIA.

Ilman sita aktivoimaton virtuaaliymparisto nakyy nain:

    File ".../backend/rannikko.py", line 18, in <module>
        from pyproj import Transformer
    ModuleNotFoundError: No module named 'pyproj'

Se ei kerro mitaan siita mika on vialla eika mita pitaisi tehda - ja se
osui samaan kayttajaan KOLME KERTAA perakkain, koska eraajo.sh aktivoi
ymparistön itse mutta suora `python3 -m backend.jotain` ei. Kolmas kerta
keskeytti kahden gigatavun latauksen ennen kuin se ehti alkaa.

Tarkistus on halpa: importlib.util.find_spec ei tuo moduulia vaan etsii
vain sen sijainnin.
"""

import importlib.util
import os
import sys

# Kirjastot jotka JOKAINEN backend-moduuli tarvitsee suoraan tai valillisesti.
# Ei koko requirements.txt:aa - naiden puuttuminen tarkoittaa kaytannossa
# aina vaaraa Pythonia, ei puuttuvaa yksittaista pakettia.
_PAKOLLISET = ("numpy", "pyproj", "rasterio", "cv2")


def _tarkista_ymparisto():
    puuttuu = []
    for nimi in _PAKOLLISET:
        try:
            if importlib.util.find_spec(nimi) is None:
                puuttuu.append(nimi)
        except (ImportError, ValueError):
            puuttuu.append(nimi)
    if not puuttuu:
        return

    venv = ""
    if sys.prefix == sys.base_prefix:
        # Etsitaan projektin oma ymparisto, jotta ohje on tasmallinen eika
        # yleinen "asenna riippuvuudet".
        juuri = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for v in (".venv", "venv"):
            if os.path.exists(os.path.join(juuri, v, "bin", "activate")):
                venv = (f"\n\nVirtuaaliymparisto ei ole aktiivinen. Aja ensin:"
                        f"\n    source {v}/bin/activate"
                        f"\ntai kayta sen omaa Pythonia suoraan:"
                        f"\n    {v}/bin/python3 -m backend.<moduuli>")
                break
        else:
            venv = ("\n\nVirtuaaliymparistoa ei loytynyt. Luo ja asenna:"
                    "\n    python3 -m venv .venv && . .venv/bin/activate"
                    "\n    pip install -r requirements.txt")
    else:
        venv = ("\n\nVirtuaaliymparisto on aktiivinen mutta paketteja puuttuu:"
                "\n    pip install -r requirements.txt")

    raise ImportError(
        f"Puuttuvat kirjastot: {', '.join(puuttuu)}"
        f"\nKaytossa: {sys.executable}" + venv
    )


_tarkista_ymparisto()
