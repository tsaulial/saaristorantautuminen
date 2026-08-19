"""Vektorikarttatasot melojalle ja pienveneilijalle.

Kolme lahdetta, jotka vastaavat kolmeen kysymykseen joihin pisteytyskartta ei
vastaa lainkaan:

    vaylat        - mita pitaa VAISTAA (rahtilaivojen kulkuvaylat)
    suojelualueet - minne ei ehka saa MENNA
    palvelut      - mita on TARJOLLA (kaupat, saunat, laavut, veneluiskat)

KAIKKI HAETAAN BUILD-VAIHEESSA, ei selaimessa. Sovelluksessa ei ole
palvelinta, ja offline-kaytto on keskeinen vaatimus - ajonaikainen haku
rikkoisi molemmat.

KOORDINAATISTO: Vaylavirasto ja SYKE palauttavat GeoJSONin suoraan
EPSG:3067:ssa eli samassa koordinaatistossa jota koko projekti kayttaa. Niita
ei projisoida kummassakaan paassa. OSM palauttaa WGS84:aa, joka muunnetaan
TASSA - selaimeen ei viedä projektiokirjastoa.

---------------------------------------------------------------------------
MAIHINNOUSUKIELTOA EI OLE OLEMASSA PAIKKATIETONA. Tama on moduulin tarkein
rajoite, ja se on kirjoitettu tahan koska sen unohtaminen johtaisi
vaarallisen vaaraan kayttoliittymatekstiin.

SYKE:n aineisto antaa alueen rajauksen, nimen, tyypin ja saadoksen - mutta
EI kenttaa joka kertoisi saako rantautua. Varsinaiset maihinnousukiellot ovat
Metsahallituksen jarjestyssaannoissa, jotka julkaistaan PDF-julkaisuina ja
Luontoon.fi:ssa. Metsahallituksen avoin biotooppiaineisto rajaa merialueet
nimenomaisesti ulos - eli tasan taman sovelluksen toimialueen.

Siksi: taso ja tietopallo saavat kertoa MISSA kayttaja on ("Saaristomeren
kansallispuisto") ja kehottaa tarkistamaan jarjestyssaannon. Ne eivat saa
koskaan sanoa etta rantautuminen on sallittua tai kiellettya. Vaara
"sallittu" olisi pahempi kuin ei tietoa lainkaan.
---------------------------------------------------------------------------

KATTAVUUS: valtakunnalliset aineistot eivat kata Ahvenanmaata, joka hoitaa
omansa itse. Mitattuna samankokoisilla alueilla Ahvenanmaa vs Turun saaristo:
Natura-lintualueet 0 vs 5, valtion suojelualueet 0 vs 16, yksityiset 3 vs 202,
OSM-palvelukohteet 17 vs 390. Vaylat sen sijaan kattavat Ahvenanmaan (omistaja
on maakuntahallitus, mutta jakelu kulkee saman rajapinnan kautta).
Tyhja taso koealueella on siis OIKEA tulos eika todista toteutusta toimivaksi.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import tiles

CACHE_DIR = Path(__file__).resolve().parent.parent / "output" / "cache"

VAYLA_WFS = "https://avoinapi.vaylapilvi.fi/vaylatiedot/ows"

# HUOM: SYKE:n YLEINEN /geoserver/ows vastaa "Service WFS is disabled".
# Toimiva polku on TYOTILAKOHTAINEN. Tama maksoi yhden turhan kierroksen,
# joten se on kirjattu tahan eika arvattavaksi.
SYKE_WFS = "https://paikkatiedot.ymparisto.fi/geoserver/inspire_ps/wfs"

OVERPASS = "https://overpass-api.de/api/interpreter"

# Bboxia levitetaan, jotta tiilien reunan yli menevat kohteet tulevat mukaan
# kokonaisina eivatka katkea kesken. 2 km riittaa: sita pidempia yksittaisia
# vaylaosuuksia ei aineistossa kayteta, ja polygonit ovat saaria.
BBOX_MARGIN_M = 2000.0

# Koordinaatit pyoristetaan METRIIN. Lahtoaineisto on kartoitettu
# metriluokassa ja koko kartta piirretaan 10 m ruudukolla, joten desimaaleilla
# ei ole kayttoa - mutta ne kaksinkertaistaisivat JSONin koon.
COORD_DECIMALS = 0

# Polygonien yksinkertaistus (Douglas-Peucker, metreja).
#
# KAKSI ERI TOLERANSSIA, koska kohteiden koko on eri luokkaa:
#
#   Suojelualueet ovat kilometrien mittaisia ja seuraavat rantaviivaa, joten
#   ne ovat erittain pisteikkaita. 20 m ei nay 10 m ruudukolla piirrettavassa
#   kartassa mutta pudottaa pistemaaran murto-osaan.
#
#   Vaylaalueet ovat KAPEITA KAYTAVIA, tyypillisesti muutaman kymmenen metrin
#   levyisia. Sama 20 m siirtaisi reunaa merkittavan osan kaytavan leveydesta
#   - ja juuri reuna on se mita melojan pitaa vaistaa. Niille riittaa 5 m,
#   joka on alle piirtoruudukon.
SIMPLIFY_ALUE_M = 20.0
SIMPLIFY_VAYLA_M = 5.0

# --- VAYLAT ---
#
# vaylaluokkakoodi 1-6. VL1 ja VL2 ovat KAUPPAMERENKULUN vaylia eli niita
# joilla liikkuu satojen metrien rahtialuksia; loput ovat veneilya ja
# hyotyliikennetta. Ero on melojalle turvallisuusasia eika luokitteludetalji,
# joten se sailytetaan ominaisuustietona selaimeen asti.
VAYLA_LAYERS = {
    "vaylat": "vesivaylatiedot:vaylat_uusi",
    "vaylaalueet": "vesivaylatiedot:vaylaalueet_uusi",
    "nopeusrajoitukset": "vesivaylatiedot:vesivaylien_nopeusrajoitusalueet",
    "rajoitusalueet": "vesivaylatiedot:vesivaylien_muut_rajoitusalueet",
}

# Kentat jotka viedaan selaimeen. Rajaus on tarkoituksellinen: WFS palauttaa
# kymmenia kenttia (diaarinumerot, irrotuspaivat, ylläpitajatiedot), joista
# yksikaan ei auta melojaa mutta jotka moninkertaistaisivat tiedoston koon.
VAYLA_FIELDS = ("nimifi", "vaylaluokkakoodi", "vaylaluokkafi", "valaistusfi")
VAYLAALUE_FIELDS = ("tyyppi", "mitoitussyvays", "haraussyvyys", "liikennointisuunta")
RAJOITUS_FIELDS = ("rajoitustyyppi", "suuruus", "rajoitusarvo", "nimi", "kuvaus")

# --- SUOJELUALUEET ---
SUOJELU_LAYERS = {
    "lintualue": "inspire_ps:PS.ProtectedSitesSpecialProtectionArea",
    "luontoalue": "inspire_ps:PS.ProtectedSitesSpecialAreaOfConservation",
    "valtio": "inspire_ps:PS.ProtectedSitesValtionOmistamaLuonnonsuojelualue",
    "yksityinen": "inspire_ps:PS.ProtectedSitesYksityistenMaillaOlevaLuonnonsuojelualue",
}
SUOJELU_FIELDS = ("nimi", "tyyppinimi", "paatnimi")

# --- PALVELUT (OpenStreetMap) ---
#
# Avain-arvo -parit joilla haetaan. Ryhmanimi menee selaimeen ikonin
# valintaa varten; OSM:n oma arvo sailytetaan jotta virhetulkinta olisi
# jaljitettavissa lahteeseen.
PALVELU_TAGS = [
    ("kauppa", 'nwr["shop"~"^(convenience|supermarket|general|kiosk)$"]'),
    ("sauna", 'nwr["leisure"="sauna"]'),
    ("sauna", 'nwr["amenity"="sauna"]'),
    ("majoitus", 'nwr["tourism"~"^(hotel|guest_house|hostel|chalet)$"]'),
    ("leirinta", 'nwr["tourism"~"^(camp_site|caravan_site)$"]'),
    ("katos", 'nwr["amenity"="shelter"]'),
    ("nuotio", 'nwr["amenity"="bbq"]'),
    ("nuotio", 'nwr["leisure"="firepit"]'),
    ("vesi", 'nwr["amenity"="drinking_water"]'),
    ("kaymala", 'nwr["amenity"="toilets"]'),
    ("luiska", 'nwr["leisure"="slipway"]'),
    ("luiska", 'nwr["waterway"="slipway"]'),
    ("satama", 'nwr["leisure"="marina"]'),
    ("polttoaine", 'nwr["amenity"="fuel"]'),
]


def alueen_bbox(marginaali=BBOX_MARGIN_M):
    """Tiilirekisterin kattama alue YHTENA laatikkona.

    Sailytetty apufunktioksi; hakuihin kaytetaan alueen_bboxit(), ks. alla."""
    registry = tiles.get_registry()
    if not registry:
        raise RuntimeError("Tiilirekisteri on tyhja")
    xs, ys = [], []
    for t in registry.values():
        xs += [t.bounds[0], t.bounds[2]]
        ys += [t.bounds[1], t.bounds[3]]
    return (min(xs) - marginaali, min(ys) - marginaali,
            max(xs) + marginaali, max(ys) + marginaali)


def alueen_bboxit(marginaali=BBOX_MARGIN_M):
    """Laatikko per YHTENAINEN tiiliklusteri.

    Miksi ei yhta laatikkoa: demossa on kaksi erillista aluetta (Ahvenanmaa ja
    Helsingin edusta), joiden vali on yli 200 km. Yksi laatikko kattaisi myos
    valin ja hakisi koko Saaristomeren vaylat ja suojelualueet - satoja
    kohteita joita ei nay millaan kartalla. Yhtenaisella alueella klustereita
    on yksi, jolloin kaytos on tasan sama kuin ennen.

    Klusterointi on tiles.tiilirykelmat(): tiilet ovat 6x6 km ruudukossa, ja
    kaksi tiilta kuuluvat samaan klusteriin jos ne koskettavat toisiaan
    reunasta tai kulmasta. Sama jako tarvitaan pyyhkaisymatkojen
    laskenta-alueisiin (pipeline._laskenta_alueet), joten se on yhdessa
    paikassa - kaksi eri toteutusta ajautuisi ajan mittaan erilleen."""
    registry = tiles.get_registry()
    if not registry:
        raise RuntimeError("Tiilirekisteri on tyhja")

    klusterit = []
    for ryhma in tiles.tiilirykelmat(registry):
        rajat = [registry[t].bounds for t in ryhma]
        klusterit.append((
            min(b[0] for b in rajat) - marginaali,
            min(b[1] for b in rajat) - marginaali,
            max(b[2] for b in rajat) + marginaali,
            max(b[3] for b in rajat) + marginaali,
        ))
    return sorted(klusterit)


# Kuinka monta kertaa ulkoista palvelua yritetaan, ja odotukset yritysten
# valissa. Overpass (OSM) on jaettu ilmaispalvelu ja palauttaa 504:n
# kuormituspiikeissa rutiininomaisesti - nain kavi kesken tunnin ajon.
# Odotukset kasvavat, koska katko kestaa tavallisesti minuutteja, ei sekunteja.
_YRITYKSET = 3
_ODOTUKSET = (5, 20, 60)


def _hae(url, timeout=180, data=None):
    """HTTP-haku, joka kestaa ohimenevan katkon.

    UUDELLEENYRITYS VAIN OHIMENEVISTA VIRHEISTA: 5xx, verkkovirhe ja
    aikakatkaisu. 4xx on meidan kyselyssamme eika parane odottamalla, joten
    se nousee heti - muuten viallinen kysely maksaisi 85 sekuntia hiljaista
    odotusta ennen kuin virhe nakyy."""
    import time as _time

    req = urllib.request.Request(url, data=data)
    req.add_header("User-Agent", "saaristorantautuminen/1.0 (avoin data, build-vaihe)")
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for yritys in range(1, _YRITYKSET + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code < 500 or yritys == _YRITYKSET:
                raise
            syy = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if yritys == _YRITYKSET:
                raise
            syy = type(e).__name__
        odota = _ODOTUKSET[min(yritys - 1, len(_ODOTUKSET) - 1)]
        print(f"    {url.split('/')[2]}: {syy}, yritys {yritys}/{_YRITYKSET}, "
              f"odotetaan {odota} s", flush=True)
        _time.sleep(odota)


def _wfs_geojson(base_url, type_name, bbox):
    """WFS 2.0 -haku GeoJSONina EPSG:3067:ssa.

    Palauttaa (features, numberMatched). numberMatched otetaan talteen, jotta
    HILJAINEN KATKAISU paljastuu: jos palvelin rajaa vastauksen omaan
    oletusmaaraansa, saatujen ja luvattujen ero kertoo sen heti. Ilman tata
    puuttuva puolikas taso nayttaisi taysin normaalilta."""
    params = {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": type_name,
        "srsName": "urn:ogc:def:crs:EPSG::3067",
        "bbox": "%.0f,%.0f,%.0f,%.0f,urn:ogc:def:crs:EPSG::3067" % bbox,
        "outputFormat": "application/json",
        "count": "20000",
    }
    raw = _hae(f"{base_url}?{urllib.parse.urlencode(params)}")
    d = json.loads(raw)
    return d.get("features", []), d.get("numberMatched")


def _wfs_hits(base_url, type_name, bbox):
    """Kohdemaara ilman geometriaa - todentamista varten."""
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeNames": type_name, "resultType": "hits",
        "bbox": "%.0f,%.0f,%.0f,%.0f,urn:ogc:def:crs:EPSG::3067" % bbox,
    }
    raw = _hae(f"{base_url}?{urllib.parse.urlencode(params)}").decode("utf-8", "replace")
    import re
    m = re.search(r'numberMatched="(\d+)"', raw)
    return int(m.group(1)) if m else None


def _pyorista(geom, kertoimet=None):
    """Koordinaatit metrin tarkkuuteen, rakenne ennallaan."""
    def r(c):
        if isinstance(c[0], (int, float)):
            return [round(c[0], COORD_DECIMALS), round(c[1], COORD_DECIMALS)]
        return [r(x) for x in c]
    return {"type": geom["type"], "coordinates": r(geom["coordinates"])}


def _yksinkerta(geom, toleranssi):
    """Douglas-Peucker shapelyllä. Sailyttaa topologian (preserve_topology),
    jottei rannikkoa seuraavasta polygonista tule itseaan leikkaavaa."""
    from shapely.geometry import mapping, shape
    g = shape(geom).simplify(toleranssi, preserve_topology=True)
    if g.is_empty:
        return None
    return mapping(g)


def _kentat(props, nimet):
    """Vain halutut kentat, tyhjat pois."""
    out = {}
    for n in nimet:
        v = props.get(n)
        if v not in (None, "", []):
            out[n] = v
    return out


def _ominaisuudet(features, kentat, toleranssi=None):
    """WFS-piirteet kevennetyksi GeoJSON-listaksi.

    toleranssi=None jattaa geometrian ennalleen (viivat)."""
    out = []
    for f in features:
        g = f.get("geometry")
        if not g:
            continue
        if toleranssi is not None:
            g = _yksinkerta(g, toleranssi)
            if g is None:
                continue
        out.append({"g": _pyorista(g), "p": _kentat(f.get("properties", {}), kentat)})
    return out


def _hae_klustereittain(base_url, type_name):
    """Sama WFS-taso jokaiselta tiiliklusterilta, kohteet deduplikoiden.

    Deduplikointi WFS:n omalla id:lla: klusterien laatikot voivat menna
    paallekkain (marginaali), ja sama vayla voi ulottua kahdelle klusterille.
    Palauttaa (piirteet, luvattu_yhteensa)."""
    nahdyt = {}
    luvattu_yht = 0
    for bbox in alueen_bboxit():
        piirteet, luvattu = _wfs_geojson(base_url, type_name, bbox)
        if luvattu is not None and len(piirteet) < luvattu:
            raise RuntimeError(
                f"{type_name}: saatiin {len(piirteet)} mutta luvattu {luvattu} - "
                "vastaus katkaistiin, nosta count-parametria")
        luvattu_yht += luvattu or len(piirteet)
        for i, f in enumerate(piirteet):
            nahdyt.setdefault(f.get("id") or f"{bbox}:{i}", f)
    return list(nahdyt.values()), luvattu_yht


# --- JULKINEN RAJAPINTA ---------------------------------------------------


def _valimuisti(nimi, rakenna, force=False):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    polku = CACHE_DIR / f"_{nimi}.json"
    if not force and polku.exists():
        return json.loads(polku.read_text())
    data = rakenna()
    polku.write_text(json.dumps(data))
    return data


def get_or_compute_vaylat(force=False):
    """Vesivaylat, vaylaalueet ja rajoitusalueet (Vaylavirasto, CC BY 4.0)."""
    def rakenna():
        ulos = {"lahde": "Väylävirasto", "lisenssi": "CC BY 4.0", "tasot": {}}
        kentat = {
            "vaylat": VAYLA_FIELDS,
            "vaylaalueet": VAYLAALUE_FIELDS,
            "nopeusrajoitukset": RAJOITUS_FIELDS,
            "rajoitusalueet": RAJOITUS_FIELDS,
        }
        for avain, taso in VAYLA_LAYERS.items():
            piirteet, luvattu = _hae_klustereittain(VAYLA_WFS, taso)
            # Vaylalinjat sailyvat sellaisenaan; alueet yksinkertaistetaan
            # kapeille kaytaville sopivalla toleranssilla.
            tol = None if avain == "vaylat" else SIMPLIFY_VAYLA_M
            ulos["tasot"][avain] = _ominaisuudet(piirteet, kentat[avain], toleranssi=tol)
            print(f"    {avain:20} {len(piirteet):5} kpl (haettu {luvattu})")
        return ulos
    return _valimuisti("vaylat", rakenna, force)


def get_or_compute_suojelualueet(force=False):
    """Suojelualueiden RAJAUKSET (SYKE, CC BY 4.0).

    Ei sisalla eika voi sisaltaa tietoa siita saako alueella rantautua -
    ks. moduulin alun huomautus."""
    def rakenna():
        ulos = {"lahde": "Suomen ympäristökeskus (SYKE)", "lisenssi": "CC BY 4.0",
                "tasot": {}}
        for avain, taso in SUOJELU_LAYERS.items():
            piirteet, luvattu = _hae_klustereittain(SYKE_WFS, taso)
            ulos["tasot"][avain] = _ominaisuudet(piirteet, SUOJELU_FIELDS,
                                                 toleranssi=SIMPLIFY_ALUE_M)
            print(f"    {avain:20} {len(piirteet):5} kpl (haettu {luvattu})")
        return ulos
    return _valimuisti("suojelualueet", rakenna, force)


def get_or_compute_palvelut(force=False):
    """Retkeilyn ja veneilyn palvelut (OpenStreetMap, ODbL).

    ODbL EDELLYTTAA nimeamista - se ei ole kohteliaisuus vaan lisenssiehto."""
    def rakenna():
        from pyproj import Transformer
        to_wgs = Transformer.from_crs("EPSG:3067", "EPSG:4326", always_xy=True)
        to_3067 = Transformer.from_crs("EPSG:4326", "EPSG:3067", always_xy=True)

        # OSM-tagi -> ryhmanimi. Rakennetaan suodattimista, jottei sama
        # kuvaus olisi kahdessa paikassa.
        ryhma_avaimet = {}
        for ryhma, suodatin in PALVELU_TAGS:
            avain = suodatin.split('["', 1)[1].split('"', 1)[0]
            ryhma_avaimet.setdefault(avain, []).append(ryhma)

        # Deduplikointi OSM:n tyypilla ja id:lla: klusterien laatikot voivat
        # menna paallekkain.
        kohteet = {}
        for x0, y0, x1, y1 in alueen_bboxit():
            lon0, lat0 = to_wgs.transform(x0, y0)
            lon1, lat1 = to_wgs.transform(x1, y1)
            alue = f"({lat0:.4f},{lon0:.4f},{lat1:.4f},{lon1:.4f})"
            rivit = "".join(f"  {suodatin}{alue};\n" for _, suodatin in PALVELU_TAGS)
            kysely = f"[out:json][timeout:180];\n(\n{rivit});\nout tags center;"
            raw = _hae(OVERPASS, data=urllib.parse.urlencode({"data": kysely}).encode())
            elementit = json.loads(raw).get("elements", [])

            for e in elementit:
                t = e.get("tags", {})
                keskus = e if e.get("type") == "node" else e.get("center")
                if not keskus or "lat" not in keskus:
                    continue
                ryhma = None
                for avain, ryhmat in ryhma_avaimet.items():
                    if avain in t:
                        ryhma = ryhmat[0] if len(ryhmat) == 1 else _ryhma_arvosta(avain, t[avain])
                        break
                if ryhma is None:
                    continue
                x, y = to_3067.transform(keskus["lon"], keskus["lat"])
                kohteet[(e.get("type"), e.get("id"))] = {
                    "x": round(x), "y": round(y), "r": ryhma,
                    "n": t.get("name", ""),
                }
        lista = list(kohteet.values())
        harvenna_palvelut(lista)
        print(f"    palvelut             {len(lista):5} kpl")
        return {"lahde": "OpenStreetMap", "lisenssi": "ODbL",
                "kohteet": lista}
    return _valimuisti("palvelut", rakenna, force)


# --- PALVELUIDEN HARVENNUS ZOOMIN MUKAAN ---
#
# 5 049 pistetta peittaa kartan uloimmilla zoomeilla taysin - mitattuna
# maastoa ei nay lainkaan, vain pistemassa. Rannikolla niita on noin 25 000.
#
# ONGELMA EI OLE NOPEUS. Se mitattiin ennen korjausta: Leaflet piirtaa vain
# nakyman sisalla olevat markerit, joten 25 245 pistetta pannaa 59 fps eli
# yhta nopeasti kuin 5 049. Suunniteltu geometrian yksinkertaistus olisi
# ollut turhaa tyota - vaylilla se sailoi 1,05x ja suojelualueilla 1,00x,
# koska lahdeaineisto on jo valmiiksi karkeaa.
#
# Kyse on siis PELKASTA LUETTAVUUDESTA, ja se ratkaistaan kevyimmalla
# mahdollisella tavalla: jokainen piste saa tason jolla se ILMESTYY, ja
# selain suodattaa yhdella lukuvertailulla. Ei klusterointia ajonaikana,
# koska se maksaisi joka piirrolla.
#
# MERKITTAVYYSJARJESTYS on veneilijan nakokulma, ei OSM:n. Uloimmalla
# zoomilla kiinnostaa MINNE VOI MENNA (satama, luiska, kauppa, polttoaine),
# ei mita perilla on (katos, nuotio, kaymala) - jalkimmaisilla on merkitysta
# vasta kun kohde on jo valittu.
PALVELU_MERKITTAVYYS = [
    "satama", "luiska", "kauppa", "polttoaine", "majoitus",
    "leirinta", "vesi", "sauna", "katos", "nuotio", "kaymala",
]

# Ruudun koko tasoittain: uloimmalla yksi piste per 5 km, keskitasolla
# per 1,5 km, tarkalla kaikki. Luvut on valittu niin etta pisteita on
# ruudulla kymmenia eika tuhansia.
PALVELU_RUUDUT = [5000.0, 1500.0]


def harvenna_palvelut(kohteet):
    """Antaa jokaiselle pisteelle tason jolla se ilmestyy (kentta "z").

    z = 0 nakyy aina, 1 keskitasolta, 2 vasta tarkalla. Muokkaa listaa
    paikallaan."""
    arvo = {r: i for i, r in enumerate(PALVELU_MERKITTAVYYS)}
    # Merkittavin ensin, jotta ruudun valinta osuu siihen.
    jarjestys = sorted(kohteet, key=lambda k: arvo.get(k["r"], len(arvo)))
    for k in kohteet:
        k["z"] = len(PALVELU_RUUDUT)
    for taso, ruutu in enumerate(PALVELU_RUUDUT):
        varattu = set()
        for k in jarjestys:
            if k["z"] < taso:
                continue                      # jo nakyvissa karkeammalla
            solu = (int(k["x"] // ruutu), int(k["y"] // ruutu))
            if solu in varattu:
                continue
            varattu.add(solu)
            k["z"] = taso
    return kohteet


def _ryhma_arvosta(avain, arvo):
    """Sama OSM-avain voi kuulua eri ryhmiin (esim. leisure=sauna vs
    leisure=slipway vs leisure=firepit)."""
    for ryhma, suodatin in PALVELU_TAGS:
        if f'["{avain}"' in suodatin and (f'="{arvo}"' in suodatin or arvo in suodatin):
            return ryhma
    return None
