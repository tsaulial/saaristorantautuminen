"""Meri ja rantaviiva Maastotietokannan vektoriaineistosta.

KORVAA PERUSKARTAN VARILUOKITUKSEN. Meri tunnistettiin aiemmin peruskartan
rasterista varin perusteella (raster_filters.detect_water_fill_mask) ja
rantaviiva omalla varillaan (detect_shoreline_mask). Peruskartta on kuitenkin
IHMISELLE PIIRRETTY KUVA, ei mittausaineisto, ja se johti kahteen vikaan jotka
molemmat loytyivat vasta Helsingin aineistosta:

  1. Vesistojen NIMET on painettu tasan samalla sinisella kuin rantaviiva,
     joten sanat "Purolahti" ja "Backviken" tulkittiin rantaviivaksi keskella
     lahtea. Kokeilin viitta erottelijaa (maan laheisyys, sama avattuna,
     viivan paksuus, valkoinen reunus, etaisyys vesialueen rajasta) - kaikki
     joko sailyttivat tekstin tai poistivat yli neljanneksen aidosta
     rantaviivasta.

  2. Meri jouduttiin erottamaan jarvista PINTA-ALAN perusteella (50 ha), koska
     rasterissa ne ovat samanvarisia. Se vaati lisaksi morfologisen sulkemisen
     siltapenkereille ja erillissaannon tiilirajoille.

Maastotietokannassa meri ja jarvi ovat ERI TASOJA, joten kumpaakaan kikkaa ei
tarvita: meri on meri ja jarvi on jarvi, eika tekstia ole olemassa.

Todennettu: Tiiliruukinlahti Helsingissa - joka putosi rasteripolussa pois
9,9 ha "sisavetena" - sisaltyy meri-polygoniin sellaisenaan.

Lahde: Maanmittauslaitos, Maastotietokanta, teema "hydrografia" (CC BY 4.0).
"""

from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
GPKG = ROOT / "vesistot-mml" / "hydrografia.gpkg"

# MERI JA VESI OVAT ERI ASIOITA, ja ero on pidettava nakyvissa.
#
# Aluksi tama moduuli tunsi vain meren. Kun aineistoon lisattiin sisavesia
# (Paijanteen kansallispuisto ja vesireitti Padasjoelle), pelkka jarvi-tason
# lisaaminen meri_maskiin olisi ollut hiljainen virhe: leva.py kysyy
# merimaskia levahavaintoja varten, ja ne ovat aidosti merikohtaisia.
# Levakerros olisi varittanyt Paijanteen.
#
# Siksi tasoja on kaksi:
#   MERI_TASOT  - vain meri. Levahavainnot ja muu merikohtainen.
#   VESI_TASOT  - meri ja jarvi. Rantautuminen, pyyhkaisymatkat, aallokko.
#
# Jarvi-taso on ollut aineistossa koko ajan; se vain jatettiin lukematta
# (ks. mml_lataus.yhdista_hydrografia). MTK:n hydrografia sisaltaa lisaksi
# tasot virtavesikapea, vesikivi, matalikko ja vesikivikko - niita EI oteta
# mukaan, jotta sisavesi kasitellaan tasan samoin kuin meri eika synny
# kahta eri laatuvaatimusta.
MERI_TASOT = ("meri",)
VESI_TASOT = ("meri", "jarvi")

# Yhteensopivuus: moduulin ulkopuolella viitattiin aiemmin tahan nimeen.
MERI_LAYER = "meri"

_saatavat_tasot = None
_saatavat_leima = None


def _tasot_tiedostossa():
    """GPKG:ssa oikeasti olevat tasot.

    VALIMUISTITETTU MUOKKAUSAJAN MUKAAN, ei pysyvasti. Tata kysytaan kerran
    per tiili per maski ja tiilia on tuhansia, joten joka kerta lukeminen
    olisi turhaa - mutta pysyva valimuisti olisi tasan se vikaluokka jota
    tama projekti on toistuvasti kohdannut: jokin paatetaan kerran eika
    merkita vanhentuneeksi. Jos sama prosessi lisaa jarvi-tason ja
    rasteroi sen jalkeen, taso loytyy."""
    global _saatavat_tasot, _saatavat_leima
    if not GPKG.exists():
        return set()
    leima = GPKG.stat().st_mtime_ns
    if _saatavat_tasot is None or leima != _saatavat_leima:
        import pyogrio
        _saatavat_tasot = {t for t in pyogrio.list_layers(GPKG)[:, 0]}
        _saatavat_leima = leima
    return _saatavat_tasot

# Rantaviiva on meri-polygonin REUNA. all_touched, koska viiva on aareton
# ohut: ilman sita rasterointi pudottaisi osan pikseleista ja rantaviivaan
# tulisi reikia.
_RASTER_KWARGS = {"dtype": "uint8"}


def saatavilla():
    return GPKG.exists()


def _polygonit(bbox, tasot=VESI_TASOT):
    """Annettujen tasojen polygonit yhtena kehyksena.

    Puuttuva taso EI ole virhe: rannikon hydrografia.gpkg on ladattu ennen
    kuin jarvi-tasoa luettiin, joten siina on vain meri. Sisamaan palasissa
    taas meri-taso on tyhja."""
    import geopandas as gpd
    import pandas as pd
    saatavat = _tasot_tiedostossa()
    osat = []
    for taso in tasot:
        if taso not in saatavat:
            continue
        g = gpd.read_file(GPKG, layer=taso, bbox=tuple(bbox))
        if len(g):
            osat.append(g.geometry)
    if not osat:
        return gpd.GeoSeries([], crs=None)
    return osat[0] if len(osat) == 1 else pd.concat(osat, ignore_index=True)


def _rasteroi(geometriat, transform, shape, reunat=False):
    from rasterio.features import rasterize
    muodot = [g.boundary if reunat else g for g in geometriat if g is not None]
    muodot = [g for g in muodot if g is not None and not g.is_empty]
    if not muodot:
        return np.zeros(shape, dtype=bool)
    kwargs = dict(_RASTER_KWARGS)
    if reunat:
        kwargs["all_touched"] = True
    return rasterize(((g, 1) for g in muodot), out_shape=shape,
                     transform=transform, **kwargs).astype(bool)


def vesi_maski(bbox, transform, shape):
    """True = vetta: meri TAI jarvi (ks. VESI_TASOT).

    Tama on se maski jota rantautuminen kayttaa. Meri erikseen: meri_maski."""
    return _rasteroi(_polygonit(bbox, VESI_TASOT), transform, shape)


def meri_maski(bbox, transform, shape):
    """True = merta, EI jarvia. Merikohtaisille kerroksille (ks. leva.py)."""
    return _rasteroi(_polygonit(bbox, MERI_TASOT), transform, shape)


def rantaviiva_maski(bbox, transform, shape):
    """True = rantaviiva eli vesipolygonin reuna (meri ja jarvi).

    Sisaltaa myos saarten reunat, koska polygonin reikien reunat kuuluvat
    geometrian rajaan - juuri niita rantautumisessa etsitaan."""
    return _rasteroi(_polygonit(bbox, VESI_TASOT), transform, shape, reunat=True)
