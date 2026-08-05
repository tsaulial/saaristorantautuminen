"""TM35FIN-karttalehtijako: nimi <-> koordinaatit.

MML:n latauspalvelu haluaa LEHTINIMIA (esim. L3114D), mutta me tiedamme
alueen KOORDINAATTEINA. Tama moduuli kaantaa suunnan kumpaankin.

SAANTO ON JOHDETTU AINEISTOSTA, EI DOKUMENTAATIOSTA, ja se toistaa kaikki 20
olemassa olevaa lehteamme metrin tarkkuudella (11 DEM-tiiltä + 9 karttalehtea,
0 virhetta). Sen voi todentaa milloin tahansa:  python3 -m backend.karttalehti

Jako:

  taso 1   192 x 96 km   kirjain (pohjoisvyohyke) + numero (itavyohyke),
                         esim. "L3"
  numerot  puolittavat molemmat suunnat: 1 = lounas, 2 = luode,
                         3 = kaakko, 4 = koillinen. Kolme numeroa ->
                         24 x 12 km lehti, esim. "L3114"
  L / R    puolittaa idassa -> 12 x 12 km, esim. "L3114R"  (KARTTALEHTI)
  A - H    jakaa 24 x 12 km lehden 6 x 6 km ruutuihin: sarake vasemmalta
                         oikealle, kussakin etela ennen pohjoista
                         (A=lo, B=lp, C, D, ...), esim. "L3114D"  (DEM-TIILI)

HUOM: DEM ja kartta kayttavat ERI JAKOA (6x6 km vs 12x12 km), eivatka niiden
nimet vastaa toisiaan. Siksi tiles.build_registry yhdistaa ne koordinaateilla
eika nimilla - sama syy patee tassa: ala paattele kartan nimea DEM:n nimesta.

Helsingin edusta osuu KAHDEN ykköstason lehden rajalle (K42.. ja L41..):
raja kulkee y = 6 666 000 eli Helsingin lapi. Juuri tallaisen takia lehdet
lasketaan eika arvata.
"""

from pathlib import Path

# Pohjoisvyohykkeiden kirjaimet etelasta pohjoiseen.
LETTERS = "KLMNPQRSTUVWX"

# Vyohykkeen K2 vasen alakulma EPSG:3067-metreina ja tason 1 lehden koko.
ORIGIN_X, ORIGIN_Y = -76000.0, 6570000.0
LEVEL1_W, LEVEL1_H = 192000.0, 96000.0

QUADRANT_LETTERS = "ABCDEFGH"


def sheet_bounds(nimi):
    """Lehtinimi -> (minx, miny, maxx, maxy) EPSG:3067."""
    kirjain, loput = nimi[0].upper(), nimi[1:].upper()
    if kirjain not in LETTERS:
        raise ValueError(f"Tuntematon vyohykekirjain {kirjain!r} nimessa {nimi!r}")
    rivi = LETTERS.index(kirjain)
    sarake = int(loput[0]) - 2

    x = ORIGIN_X + sarake * LEVEL1_W
    y = ORIGIN_Y + rivi * LEVEL1_H
    w, h = LEVEL1_W, LEVEL1_H

    for merkki in loput[1:]:
        if merkki in "1234":
            n = int(merkki) - 1
            w, h = w / 2, h / 2
            x += (n // 2) * w        # 1,2 = lansi   3,4 = ita
            y += (n % 2) * h         # 1,3 = etela   2,4 = pohjoinen
        elif merkki in "LR":
            w = w / 2
            if merkki == "R":
                x += w
        elif merkki in QUADRANT_LETTERS:
            n = QUADRANT_LETTERS.index(merkki)
            w, h = w / 4, h / 2
            x += (n // 2) * w
            y += (n % 2) * h
        else:
            raise ValueError(f"Tuntematon merkki {merkki!r} nimessa {nimi!r}")
    return (x, y, x + w, y + h)


def sheet_name(x, y, taso="dem"):
    """Koordinaatti -> lehtinimi. taso: "dem" (6x6 km) tai "kartta" (12x12 km)."""
    sarake = int((x - ORIGIN_X) // LEVEL1_W)
    rivi = int((y - ORIGIN_Y) // LEVEL1_H)
    if not 0 <= rivi < len(LETTERS):
        raise ValueError(f"Piste ({x:.0f}, {y:.0f}) on lehtijaon ulkopuolella")
    nimi = LETTERS[rivi] + str(sarake + 2)

    bx = ORIGIN_X + sarake * LEVEL1_W
    by = ORIGIN_Y + rivi * LEVEL1_H
    w, h = LEVEL1_W, LEVEL1_H
    for _ in range(3):                      # 1:100k -> 1:50k -> 1:25k
        w, h = w / 2, h / 2
        i = 0 if x < bx + w else 1
        j = 0 if y < by + h else 1
        nimi += str(1 + i * 2 + j)
        bx += i * w
        by += j * h

    if taso == "kartta":
        return nimi + ("L" if x < bx + w / 2 else "R")
    if taso != "dem":
        raise ValueError(f"Tuntematon taso {taso!r}")
    sar = min(int((x - bx) // (w / 4)), 3)
    riv = min(int((y - by) // (h / 2)), 1)
    return nimi + QUADRANT_LETTERS[sar * 2 + riv]


# Ruutukoot metreina tasoittain - kaytetaan alueen kayntiin lapi.
STEP = {"dem": 6000.0, "kartta": 12000.0}


def sheets_for_bbox(bbox, taso="dem"):
    """Kaikki lehdet jotka LEIKKAAVAT laatikkoa (minx, miny, maxx, maxy)."""
    x0, y0, x1, y1 = bbox
    askel = STEP[taso] / 2      # puolikas askel: ei voi hypata lehden yli
    ulos = set()
    x = x0
    while x < x1:
        y = y0
        while y < y1:
            ulos.add(sheet_name(x, y, taso))
            y += askel
        ulos.add(sheet_name(x, y1 - 1e-6, taso))
        x += askel
    y = y0
    while y < y1:
        ulos.add(sheet_name(x1 - 1e-6, y, taso))
        y += askel
    ulos.add(sheet_name(x1 - 1e-6, y1 - 1e-6, taso))
    return sorted(ulos)


def sheets_for_corridor(pisteet, leveys_m, taso="dem"):
    """Lehdet jotka leikkaavat murtoviivan ympari piirrettya kaytavaa.

    pisteet: [(x, y), ...] EPSG:3067. Kaytetaan laajennettaessa aluetta
    rannikkoreittia pitkin, jolloin suorakaide hukkaisi valtaosan lehdista
    avomereen ja sisamaahan."""
    from shapely.geometry import LineString, box

    kaytava = LineString(pisteet).buffer(leveys_m / 2.0)
    x0, y0, x1, y1 = kaytava.bounds
    askel = STEP[taso]
    ulos = []
    x = (x0 // askel) * askel
    while x < x1:
        y = (y0 // askel) * askel
        while y < y1:
            if kaytava.intersects(box(x, y, x + askel, y + askel)):
                ulos.append(sheet_name(x + 1.0, y + 1.0, taso))
            y += askel
        x += askel
    return sorted(set(ulos))


def _todenna():
    """Toistaa olemassa olevien tiedostojen lehtinimet niiden omista rajoista.

    Tama on moduulin ainoa totuudenlahde: saanto on johdettu aineistosta,
    joten aineisto myos todentaa sen."""
    import rasterio

    juuri = Path(__file__).resolve().parent.parent
    virheet = 0
    tarkistettu = 0
    for kansio, kuvio in ((juuri / "korkeusmalli-mml", "*.tif"),
                          (juuri / "karttakuva-mll", "*.png")):
        for p in sorted(kansio.glob(kuvio)):
            with rasterio.open(p) as ds:
                b = (ds.bounds.left, ds.bounds.bottom, ds.bounds.right, ds.bounds.top)
            laskettu = sheet_bounds(p.stem)
            ok = all(abs(a - c) < 1.0 for a, c in zip(laskettu, b))
            tarkistettu += 1
            if not ok:
                virheet += 1
                print(f"  ERI {p.stem}: laskettu {laskettu} todellinen {b}")
            # Nimi takaisin keskipisteesta - kumpikin suunta on testattava.
            taso = "dem" if kuvio == "*.tif" else "kartta"
            nimi = sheet_name((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, taso)
            if nimi != p.stem:
                virheet += 1
                print(f"  ERI {p.stem}: keskipisteesta saatiin {nimi}")
    print(f"karttalehti: {tarkistettu} lehtea tarkistettu, {virheet} virhetta")
    return virheet


if __name__ == "__main__":
    raise SystemExit(1 if _todenna() else 0)
