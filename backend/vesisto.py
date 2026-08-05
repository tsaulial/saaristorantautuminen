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

# Vain meri. Jarvi, virtavesi ja muut tasot ovat samassa tiedostossa mutta
# eivat kuulu merelliseen rantautumiseen - ja juuri niiden erottaminen oli
# rasteripolun ongelma.
MERI_LAYER = "meri"

# Rantaviiva on meri-polygonin REUNA. all_touched, koska viiva on aareton
# ohut: ilman sita rasterointi pudottaisi osan pikseleista ja rantaviivaan
# tulisi reikia.
_RASTER_KWARGS = {"dtype": "uint8"}


def saatavilla():
    return GPKG.exists()


def _polygonit(bbox):
    import geopandas as gpd
    return gpd.read_file(GPKG, layer=MERI_LAYER, bbox=tuple(bbox))


def meri_maski(bbox, transform, shape):
    """True = merta. Rasteroitu meri-polygoneista annetulle ruudukolle."""
    from rasterio.features import rasterize
    g = _polygonit(bbox)
    if not len(g):
        return np.zeros(shape, dtype=bool)
    return rasterize(((geom, 1) for geom in g.geometry if geom is not None),
                     out_shape=shape, transform=transform,
                     **_RASTER_KWARGS).astype(bool)


def rantaviiva_maski(bbox, transform, shape):
    """True = rantaviiva eli meri-polygonin reuna.

    Sisaltaa myos saarten reunat, koska polygonin reikien reunat kuuluvat
    geometrian rajaan - juuri niita rantautumisessa etsitaan."""
    from rasterio.features import rasterize
    g = _polygonit(bbox)
    if not len(g):
        return np.zeros(shape, dtype=bool)
    reunat = [geom.boundary for geom in g.geometry if geom is not None]
    if not reunat:
        return np.zeros(shape, dtype=bool)
    return rasterize(((r, 1) for r in reunat), out_shape=shape,
                     transform=transform, all_touched=True,
                     **_RASTER_KWARGS).astype(bool)
