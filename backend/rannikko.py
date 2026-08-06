"""Suomen rannikon linjaus ja sen pilkkominen ladattaviksi paloiksi.

MIKSI KAYTAVA EIKA SUORAKAIDE. Rannikko ulottuu itarajalta Torniojoelle,
ja sen ympari piirretty suorakaide on 346 x 667 km eli 230 782 km2. Siita
kaytava on alle kymmenesosa: mitattuna suorakaide vaatisi 6 496 karttalehtea
ja 147 Gt latausta, kun 20 km kaytava vaatii 867 lehtea ja 19,7 Gt.

Suorakaide ei myoskaan KELPAA: maastotietokannan rajapinnassa on
17 334 km2 katto per haku, jonka suorakaide ylittaa 14-kertaisesti.
Rakennus- ja hydrografiahaut kaatuisivat heti.

Linjaus on karkea eika seuraa rantaviivaa metrilleen - sen ei tarvitsekaan,
koska kaytavan leveys (oletuksena 20 km) kattaa vaihtelun. Pisteet on
poimittu rannikkokaupungeista, joten linja kulkee saariston lapi eika
ulkomerella.
"""

from pyproj import Transformer

# (lon, lat) WGS84, idasta pohjoiseen.
RANNIKKO_WGS84 = [
    ("Virolahti (itaraja)", 27.75, 60.55),
    ("Kotka", 26.95, 60.47),
    ("Loviisa", 26.22, 60.45),
    ("Porvoo", 25.70, 60.30),
    ("Helsinki", 24.95, 60.15),
    ("Porkkala", 24.40, 59.98),
    ("Inkoo", 23.99, 60.02),
    ("Tammisaari", 23.45, 59.90),
    ("Hanko", 22.97, 59.83),
    ("Kemionsaari", 22.75, 60.05),
    ("Parainen", 22.30, 60.15),
    ("Nauvo", 21.90, 60.19),
    ("Uusikaupunki", 21.40, 60.80),
    ("Rauma", 21.50, 61.13),
    ("Pori", 21.48, 61.55),
    ("Kristiinankaupunki", 21.37, 62.27),
    ("Vaasa", 21.60, 63.10),
    ("Pietarsaari", 22.70, 63.68),
    ("Kokkola", 23.13, 63.85),
    ("Raahe", 24.48, 64.68),
    ("Oulu", 25.47, 65.01),
    ("Kemi", 24.56, 65.73),
    ("Tornio", 24.14, 65.85),
]

# Ahvenanmaa on oma haaransa: se ei ole mannerrannikon linjalla, mutta
# kuuluu tavoitealueeseen. Ajetaan tarvittaessa erikseen omana bboxinaan.
AHVENANMAA_BBOX = (100000.0, 6650000.0, 175000.0, 6730000.0)

KAYTAVA_LEVEYS_M = 20000.0


def rannikkolinja():
    """Rannikon linjaus EPSG:3067-metreina."""
    t = Transformer.from_crs(4326, 3067, always_xy=True)
    return [t.transform(lon, lat) for _nimi, lon, lat in RANNIKKO_WGS84]


def kaytava(leveys_m=KAYTAVA_LEVEYS_M):
    """Kaytava polygonina."""
    from shapely.geometry import LineString
    return LineString(rannikkolinja()).buffer(leveys_m / 2.0)


def lehdet(leveys_m=KAYTAVA_LEVEYS_M):
    """(dem_lehdet, karttalehdet) koko rannikolle."""
    from . import karttalehti
    p = rannikkolinja()
    return (karttalehti.sheets_for_corridor(p, leveys_m, "dem"),
            karttalehti.sheets_for_corridor(p, leveys_m, "kartta"))


# Kuinka pitkiin jaksoihin rannikko pilkotaan vektorihakuja varten.
#
# Maastotietokannan rajapinta ottaa enintaan 17 334 km2 kerralla, joten haku
# on pakko pilkkoa. Palat seuraavat LINJAA eivatka ruudukkoa, koska kaytava
# kulkee vinosti: mitattuna 30 km jaksot hakevat 1,7-kertaisen alan kaytavaan
# nahden, kun 20 km ruudukko hakee 2,2-kertaisen ja tarvitsee kolme kertaa
# enemman pyyntoja.
#
# Suurin pala jaa noin 1 700 km2:iin eli kymmenesosaan katosta - marginaali
# on tahallinen, koska palan ala riippuu siita miten mutkainen jakso on.
VEKTORI_JAKSO_M = 30000.0


def vektoripalat(leveys_m=KAYTAVA_LEVEYS_M, jakso_m=VEKTORI_JAKSO_M):
    """Kaytavan peittavat bbox-palat vektorihakuja varten (rakennukset,
    hydrografia). Palat seuraavat rannikkolinjaa - ks. VEKTORI_JAKSO_M."""
    from shapely.geometry import LineString, box

    ls = LineString(rannikkolinja())
    n = max(int(ls.length / jakso_m) + 1, 1)
    palat = []
    for i in range(n):
        alku, loppu = i * ls.length / n, (i + 1) * ls.length / n
        # Jakso poimitaan naytepisteina, jotta mutka ei jaa suoran sisaan.
        pisteet = [ls.interpolate(alku + k * (loppu - alku) / 8) for k in range(9)]
        seg = LineString([(p.x, p.y) for p in pisteet]).buffer(leveys_m / 2.0)
        palat.append(tuple(round(v, 1) for v in box(*seg.bounds).bounds))
    return palat
