"""Sinilevatilanne satelliittihavainnoista - ja arvio siita milloin siihen voi luottaa.

Levan ymparoima saari ei ole houkutteleva rantautumiskohde, mutta kartta ei
sanonut siita mitaan. Suomen ymparistokeskus tulkitsee satelliittihavainnoista
pintalevaaineiston koko Itamerelle, ja se on avointa dataa.

KAKSI ONGELMAA, JOTKA MOLEMMAT ON PAKKO KERTOA KAYTTAJALLE.

1. TUOREUS. Pilvi peittaa satelliitin nakyman suurimman osan paivista, ja
   pintaleva liikkuu tuulessa tunneissa. Siksi tama moduuli tuottaa kaksi
   asiaa: levatilanteen JA arvion siita kuinka paljon siihen voi luottaa.

2. KATTAVUUS RANNAN LAHELLA. Kumpikaan lahde ei nae varsinaista
   rantavyohyketta: matalassa vedessa pohjan heijastus estaa tulkinnan.
   Mitattuna alle 500 m paassa rannasta katetaan noin 11 % merialueesta,
   yli 5 km paassa 56 %. Kerros vastaa siis kysymykseen "onko levaa
   vesilla joiden yli kuljen", EI kysymykseen "onko tama saari levan
   ymparoima". Sanamuotojen on kestettava tama ero.

AJETAAN ERIKSEEN, EI OSANA BUILDIA. Taysi build kestaa tunteja ja koskee
aineistoa joka muuttuu harvoin; leva muuttuu paivittain. Jos leva olisi osa
buildia, tuoreen tilanteen saisi vain ajamalla koko putken uudelleen.

    python3 -m backend.leva --paivita

Komennossa ei ole ajastinoletuksia eika kayttojarjestelmasidonnaisuuksia:
Railwaylla se on cron-palvelun komento, paikallisesti sen voi ajaa kasin.

---------------------------------------------------------------------------
LUOKKA-ASTEIKKO ON MITATTU, EI OLETETTU.

GeoTIFF-arvo on legendan luokka MIINUS YKSI. Tama piti ratkaista mittaamalla,
koska tiedoston nodata-tagi on 4 ja SYKE:n legendassa luokka 4 on "Varmaa" -
vaara tulkinta olisi varittanyt kartan tasan painvastoin. Todennettu hakemalla
sama rajaus seka GeoTIFFina etta PNG:na ja vertaamalla pikseli pikselilta;
vastaavuus oli 100 % jokaisella luokalla, myos 2:lla ja 3:lla.

    0 -> #2B7EA8  ei levaa
    1 -> #FFFFC9  mahdollista
    2 -> #FFDC28  todennakoista
    3 -> #CD3301  varmaa
    4 -> #FFFFFF  ei dataa (nodata)
---------------------------------------------------------------------------

LAHTEET
    Levahavainnot  Suomen ymparistokeskus (CC BY 4.0)
                     Sentinel-3/OLCI  300 m, paivittain
                     Landsat 8        30 m, noin 16 vrk kierto
    Tuulihavainnot Ilmatieteen laitos (CC BY 4.0)

Molemmat vaativat lahdemaininnan kartalla. Se on lisenssiehto, ei
kohteliaisuus - sama saanto kuin vektoritasoilla.
"""

import argparse
import datetime as dt
import io
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from . import vektoritasot

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "output" / "cache"

SYKE_WMS = "https://geoserver2.ymparisto.fi/geoserver/eo/ows"
FMI_WFS = "https://opendata.fmi.fi/wfs"

# KAKSI LAHDETTA, ja tarkin ensin.
#
# Pelkka Sentinel-3 ei riita. Mitattuna Helsingin edustalla se kattaa 27 %
# merialueesta, ja ALLE KILOMETRIN paassa rannasta vain 10 % - eli juuri
# sen vyohykkeen jossa rantaudutaan se jattaa nakematta. Syy ei ole pilvi
# vaan tuotteen oma vesimaski: matalassa vedessa pohjan heijastus estaa
# tulkinnan. Todennettu silla, etta eri paivien datalliset ruudut ovat
# aina saman joukon OSAJOUKKOJA (Jaccard 1,000) - pilvi vain poistaa
# ruutuja, ei koskaan lisaa. Siksi ikkunan pidentaminen 7:sta 21
# vuorokauteen ei lisannyt kattavuutta lainkaan.
#
# Landsat 8 kattaa samalla alueella 56 % ja alle kilometrin paassa 27 %.
# Se ei kuitenkaan korvaa Sentinel-3:a: kiertoaika on noin 16 vrk, joten
# useimpina paivina siita ei ole havaintoa lainkaan. Yhdessa ne taydentavat
# toisiaan - tarkkuus saaristossa ja tuoreus avomerella.
#
# Molemmilla on TASAN SAMA luokka-asteikko. Todennettu erikseen kummallekin
# hakemalla sama rajaus seka GeoTIFFina etta PNG:na.
LEVA_KERROKSET = (
    ("LC8", "EO_HR_WQ_LC8_ALGAE", 30.0),
    ("OLCI", "EO_MR_OLCI_ALGAE", 300.0),
)

# Ruudukko seuraa TARKINTA lahdetta, ei karkeinta. 60 m on Landsatin 30 m
# rehellinen alinaytteistys; Sentinel-3:n 300 m ruudut vain toistuvat
# useammassa ruudussa, mika ei keksi tarkkuutta vaan esittaa saman
# havainnon tiheammalla ruudukolla. Karkeampi yhteinen ruudukko heittaisi
# Landsatin tarkkuuden pois, mika oli koko syy ottaa se mukaan.
LEVA_GRID_M = 60.0

# Kuinka monelta vuorokaudelta havainnot kootaan. Paivakuva on joko lahes
# taysi tai kokonaan tyhja - mitattuna 14 vuorokaudesta neljana oli
# kayttokelpoinen kuva - joten yhden paivan haku antaisi useimmiten tyhjan
# kartan. Seitseman vuorokautta ylittaa tavallisen pilvijakson.
LEVA_IKKUNA_VRK = 7

# --- LUOTETTAVUUSMALLI ---
#
# Pintaleva nousee nakyviin tyynella ja sekoittuu syvemmalle kun tuuli
# ylittaa noin 5 m/s. Tuuli siis MITATOI HAVAINNON MOLEMPIIN SUUNTIIN: nahty
# leva on voinut sekoittua pois, ja "ei levaa" -havainnon jalkeen tyyni on
# voinut nostaa kukinnan pintaan. Siksi luotettavuus EI riipu havaitusta
# luokasta.
#
# VAKIOT OVAT ARVIOITA, EIVAT JULKAISTU STANDARDI. Sama asema kuin
# PADDLE_WAVE_LIMITS-rajoilla. Mallia ei ole kalibroitu mittausaineistoa
# vastaan - se on perusteltu malli, ei validoitu tulos.
LEVA_TUULI_KYNNYS_MS = 5.0     # alle taman pintaleva sailyy
LEVA_SEKOITUS_TAU = 40.0       # (m/s)*h - sekoitusannoksen vaimennusaika
LEVA_IKA_TAU_VRK = 10.0        # hidas biologinen muutos

# Sentinel-3:n ylilento on keskipaivan tienoilla, joten paivakuvan havainto
# ajoitetaan klo 12 UTC. Tarkempi aika ei ole saatavilla tuotteesta.
HAVAINNON_TUNTI_UTC = 12

# Luokkien nimet SYKE:n omasta legendasta. Ei keksita uusia: kayttaja voi
# verrata muihin palveluihin.
LUOKKA_NIMET = {0: "Ei levaa", 1: "Levaa mahdollisesti",
                2: "Levaa todennakoisesti", 3: "Levaa varmasti"}
LUOKKA_VARIT = {0: "#2B7EA8", 1: "#FFFFC9", 2: "#FFDC28", 3: "#CD3301"}
EI_DATAA = 4          # GeoTIFFin nodata

# MAA JA HAVAINNOTON MERI EROTELLAAN, vaikka kumpaakaan ei piirreta.
#
# Ilman erottelua kattavuus nayttaa katastrofaaliselta ilman syyta: alueen
# ruuduista valtaosa on maata, joka on satelliittituotteessa pysyvasti
# nodataa. Sama virhe tehtiin kertaalleen jo tata aineistoa selvitettaessa
# ("98,7 % ilman dataa" - josta suurin osa oli mannerta).
#
# Erottelu on myos selaimen etu: maaruudut voi ohittaa suoraan ilman
# erillista maamaskia, ja "merta jota ei ole nahty" voi haluttaessa
# nayttaa eri tavalla kuin manner.
MAA_ULOS = 254        # maata - ei koskaan piirreta
EI_DATAA_ULOS = 255   # merta, mutta ei havaintoa ikkunan aikana

# --- KANSALAISHAVAINNOT: SE MITA SATELLIITTI EI NAE ---
#
# Satelliitti sokeutuu juuri rannassa (alle 500 m paassa noin 11 %
# kattavuus), koska matalassa vedessa pohjan heijastus estaa tulkinnan.
# SYKE:n kansalaishavainnot osuvat TASAN sinne: mitattuna Helsingin
# edustalla 27 merihavaintoa, mediaanietaisyys rannasta 60 m, kaikki alle
# 180 m. Ne eivat korvaa satelliittia vaan taydentavat sita toisesta
# paasta.
#
# HARVUUS ON KERROTTAVA REHELLISESTI. Nakemattomasta rannikkovedesta vain
# 23 % on alle 2 km:n paassa havainnosta. Siksi naita EI LEVITETA veden yli
# eika interpoloida: piste piirretaan pisteena. Levittaminen olisi keksittya
# tietoa, ja juuri rannan tuntumassa se olisi vaarallisinta.
#
# ASTEIKKO ON ERI KUIN SATELLIITILLA, ja se on luettu rajapinnan omasta
# maarittelysta (services/<koodi>.xml), ei paatelty:
#
#     1 = Ei sinilevaa            satelliitti: 0 = ei levaa
#     2 = Hieman sinilevaa                     1 = mahdollista
#     3 = Runsaasti sinilevaa                  2 = todennakoista
#     4 = Erittain runsaasti                   3 = varmaa
#
# Satelliitti arvioi TODENNAKOISYYTTA, ihminen MAARAA. Nimet pidetaan siis
# erillaan eika kansalaishavaintoa koskaan kuvata sanalla "todennakoista".
# Variramppi on sama, koska molemmat kulkevat ei-levaa -> paljon-levaa ja
# kaksi eri paletttia samasta ilmiosta olisi huonompi vaihtoehto.
KANSALAISHAVAINNOT_URL = "https://rajapinnat.ymparisto.fi/api/kansalaishavainnot/1.0"
KANSALAISHAVAINTO_KOODI = "algaebloom_service_code_201808151546171"
KANSALAISHAVAINTO_NIMET = {1: "Ei sinilevää", 2: "Hieman sinilevää",
                           3: "Runsaasti sinilevää",
                           4: "Erittäin runsaasti sinilevää"}
# Rajapinnassa on 1 000 havainnon katto per pyynto, eika sivutus toimi:
# `page`-parametri palauttaa joka kerta saman joukon (todennettu sivuille
# 1-3, paallekkaisyys 1000/1000). Kierretaan pilkkomalla aikaan - kahden
# vuorokauden ikkunat, joiden paallekkaisyys on mitattuna 0.
KANSALAISHAVAINTO_IKKUNA_VRK = 2
KANSALAISHAVAINTO_KATTO = 1000


def _pyyda(url, timeout=90):
    with urllib.request.urlopen(url, timeout=timeout) as v:
        return v.read()


# --- LEVAHAVAINNOT ---

def saatavilla_olevat_paivat():
    """{kerros: [paiva, ...]} uusimmasta vanhimpaan, kaikille lahteille.

    Luetaan rajapinnalta eika paatella kalenterista: aineistossa on aukkoja,
    ja olemattoman paivan hakeminen palauttaisi tyhjan kuvan jota ei erota
    pilvisesta paivasta. Landsatilla aukkoja on paljon (16 vrk kierto),
    joten talla saastyy suurin osa turhista hauista."""
    q = {"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"}
    x = _pyyda(f"{SYKE_WMS}?{urllib.parse.urlencode(q)}").decode("utf-8", "replace")
    juuri = ET.fromstring(x)
    ns = {"w": "http://www.opengis.net/wms"}
    halutut = {taso for _n, taso, _m in LEVA_KERROKSET}
    ulos = {}
    for taso in juuri.iter():
        if not taso.tag.endswith("Layer"):
            continue
        nimi = taso.find("w:Name", ns)
        if nimi is None or nimi.text not in halutut:
            continue
        for d in taso.findall("w:Dimension", ns):
            if d.get("name") == "time" and d.text:
                paivat = [a.strip()[:10] for a in d.text.split(",") if a.strip()]
                ulos[nimi.text] = sorted(set(paivat), reverse=True)
    puuttuu = halutut - set(ulos)
    if puuttuu:
        raise RuntimeError(f"Kerroksen aikaulottuvuutta ei loytynyt: {puuttuu}")
    return ulos


def _ruudukko(bbox):
    """(leveys, korkeus) pikseleina LEVA_GRID_M-ruudukolla."""
    w = max(int(round((bbox[2] - bbox[0]) / LEVA_GRID_M)), 1)
    h = max(int(round((bbox[3] - bbox[1]) / LEVA_GRID_M)), 1)
    return w, h


def hae_paiva(bbox, paiva, layer):
    """Yhden vuorokauden luokkakuva (uint8) tai None jos haku epaonnistui.

    GeoTIFF eika PNG: saadaan raa'at luokka-arvot valmiiksi EPSG:3067:ssa,
    jolloin varien purkamista eika projisointia ei tarvita kummassakaan
    paassa. Kerros ilmoittaa vain EPSG:4326:n, mutta palvelin projisoi
    3067:aan pyydettaessa."""
    w, h = _ruudukko(bbox)
    q = {"service": "WMS", "version": "1.1.1", "request": "GetMap",
         "layers": layer, "srs": "EPSG:3067",
         "bbox": ",".join(f"{v:.1f}" for v in bbox),
         "width": w, "height": h, "format": "image/geotiff", "time": paiva}
    try:
        raw = _pyyda(f"{SYKE_WMS}?{urllib.parse.urlencode(q)}")
    except (urllib.error.URLError, OSError) as e:
        print(f"    {paiva}: haku epaonnistui ({type(e).__name__})", flush=True)
        return None
    import rasterio
    with rasterio.io.MemoryFile(raw) as m, m.open() as s:
        a = s.read(1)
    if a.shape != (h, w):
        print(f"    {paiva}: odottamaton muoto {a.shape}, odotettiin {(h, w)}", flush=True)
        return None
    return a


def kokoa_ikkuna(bbox, paivat, on_meri=None):
    """Tuorein havainto per pikseli ja sen IKA VUOROKAUSINA.

    Ika lasketaan itse eika kayteta SYKE:n valmista koostetta
    (EO_ALGAE_OLCI_S2_LC): siita ei saa selville minka ikainen kukin pikseli
    on, ja juuri ika on luotettavuusmallin syote.

    Edistyminen raportoidaan MERIALUEESEEN suhteutettuna. Koko ruudukkoon
    suhteutettu luku olisi harhaanjohtava, koska maa on satelliittituotteessa
    pysyvasti nodataa - se nayttaisi pilvisyydelta vaikka on mannerta.

    TUOREUS RATKAISEE ENSIN, tarkkuus vasta sen jalkeen. Paivat kaydaan
    uusimmasta vanhimpaan, ja saman paivan sisalla tarkin lahde ensin.
    Vuorokauden vanha Sentinel-3-havainto voittaa siis kahden vuorokauden
    ikaisen Landsatin, koska ika on luotettavuuden paatekija.

    paivat: {kerros: [paiva, ...]}. Palauttaa (luokka, ika_vrk, kaytetyt)."""
    w, h = _ruudukko(bbox)
    luokka = np.full((h, w), EI_DATAA, dtype=np.uint8)
    ika = np.zeros((h, w), dtype=np.int16)
    tanaan = dt.date.today()
    kaytetyt = []
    nimittaja = int(on_meri.sum()) if on_meri is not None else w * h
    kaikki_paivat = sorted({p for lista in paivat.values() for p in lista},
                           reverse=True)
    for p in kaikki_paivat:
        puuttuu = luokka == EI_DATAA
        if on_meri is not None:
            puuttuu &= on_meri
        if not puuttuu.any():
            break                      # koko meri jo katettu
        for nimi, taso, _natiivi in LEVA_KERROKSET:
            if p not in paivat.get(taso, ()):
                continue               # talla lahteella ei ole tata paivaa
            a = hae_paiva(bbox, p, taso)
            if a is None:
                continue
            uusi = puuttuu & (a != EI_DATAA)
            n = int(uusi.sum())
            if n:
                luokka[uusi] = a[uusi]
                ika[uusi] = (tanaan - dt.date.fromisoformat(p)).days
                kaytetyt.append({"paiva": p, "lahde": nimi, "ruutuja": n})
                puuttuu &= ~uusi
            print(f"    {p} {nimi:5s}: {n:6d} uutta ruutua "
                  f"({100.0*n/max(nimittaja,1):4.1f} % merialueesta)", flush=True)
    return luokka, ika, kaytetyt


# --- KANSALAISHAVAINNOT ---

def hae_kansalaishavainnot(alku, loppu):
    """Sinilevahavainnot Open311-rajapinnasta koko maasta.

    Haku pilkotaan aikaan KANSALAISHAVAINTO_KATTO:n takia (ks. vakion
    kommentti). Rajaus alueeseen tehdaan vasta taalla, koska rajapinta ei
    tarjoa bbox-suodatinta."""
    from pyproj import Transformer
    t = Transformer.from_crs(4326, 3067, always_xy=True)
    nahdyt = {}
    reuna = alku
    while reuna < loppu:
        pate = min(reuna + dt.timedelta(days=KANSALAISHAVAINTO_IKKUNA_VRK), loppu)
        q = {"service_code": KANSALAISHAVAINTO_KOODI,
             "start_date": reuna.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "end_date": pate.strftime("%Y-%m-%dT%H:%M:%SZ")}
        try:
            osa = json.loads(_pyyda(
                f"{KANSALAISHAVAINNOT_URL}/requests.json?{urllib.parse.urlencode(q)}"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"    kansalaishavainnot {reuna:%Y-%m-%d}: "
                  f"haku epaonnistui ({type(e).__name__})", flush=True)
            reuna = pate
            continue
        if len(osa) >= KANSALAISHAVAINTO_KATTO:
            # Katto tuli vastaan: ikkuna oli liian pitka ja osa havainnoista
            # jai hakematta. Kerrotaan se, ei vaieta.
            print(f"    VAROITUS: {reuna:%Y-%m-%d} palautti {len(osa)} eli katon - "
                  f"lyhenna KANSALAISHAVAINTO_IKKUNA_VRK", flush=True)
        for h in osa:
            tunnus = h.get("service_request_id")
            if tunnus in nahdyt:
                continue
            m = re.search(r"algaebloom_singlevaluelist_\d+:(\d+)",
                          h.get("description") or "")
            if not m:
                continue
            try:
                x, y = t.transform(float(h["long"]), float(h["lat"]))
            except (TypeError, ValueError, KeyError):
                continue
            aika = (h.get("requested_datetime") or "")[:19]
            nahdyt[tunnus] = {"x": round(x, 1), "y": round(y, 1),
                              "arvo": int(m.group(1)), "aika": aika}
        reuna = pate
    return list(nahdyt.values())


def _lisatieto(kuvaus):
    """Havainnon vapaa lisatietoteksti, tai None."""
    m = re.search(r"Ecology_additionalinfo:(.*?)(?:,Ecology_|$)", kuvaus or "", re.S)
    return m.group(1).strip() or None if m else None


# --- TUULIHAVAINNOT ---

def hae_tuulihavainnot(bbox, alku, loppu):
    """FMI:n MENNEET tuulihavainnot tunnin valein.

    Palauttaa (asemat_xy, tunnit, nopeus) jossa asemat_xy on (n, 2)
    EPSG:3067-metreina, tunnit on kasvava lista UTC-aikoja ja nopeus on
    (n_tuntia, n_asemaa) taulukko m/s (NaN = puuttuu)."""
    from pyproj import Transformer
    t3067_4326 = Transformer.from_crs(3067, 4326, always_xy=True)
    lon0, lat0 = t3067_4326.transform(bbox[0], bbox[1])
    lon1, lat1 = t3067_4326.transform(bbox[2], bbox[3])
    # Marginaali: rannikkoruudun ymparilla voi olla asemia jotka parantavat
    # interpolointia mutta jaisivat tarkan rajauksen ulkopuolelle.
    d = 0.5
    q = {"service": "WFS", "version": "2.0.0", "request": "getFeature",
         "storedquery_id": "fmi::observations::weather::multipointcoverage",
         "bbox": f"{lon0-d:.4f},{lat0-d:.4f},{lon1+d:.4f},{lat1+d:.4f}",
         "starttime": alku.strftime("%Y-%m-%dT%H:%M:%SZ"),
         "endtime": loppu.strftime("%Y-%m-%dT%H:%M:%SZ"),
         "parameters": "windspeedms", "timestep": "60"}
    x = _pyyda(f"{FMI_WFS}?{urllib.parse.urlencode(q)}").decode("utf-8", "replace")
    if "ExceptionReport" in x:
        raise RuntimeError("FMI palautti virheen")

    def _sisalto(tagi):
        i = x.find(f"<{tagi}>")
        j = x.find(f"</{tagi}>", i)
        return x[i + len(tagi) + 2:j] if i >= 0 and j > i else ""

    pos = _sisalto("gmlcov:positions").strip().split("\n")
    arvot = _sisalto("gml:doubleOrNilReasonTupleList").strip().split("\n")
    if not pos or not pos[0].strip():
        raise RuntimeError("FMI ei palauttanut havaintoja")

    rivit = []
    for p, a in zip(pos, arvot):
        o = p.split()
        if len(o) < 3:
            continue
        rivit.append((float(o[0]), float(o[1]), int(o[2]),
                      float(a.split()[0]) if a.split()[0] != "NaN" else math.nan))
    asemat = sorted({(r[0], r[1]) for r in rivit})
    tunnit = sorted({r[2] for r in rivit})
    ai = {a: i for i, a in enumerate(asemat)}
    ti = {t: i for i, t in enumerate(tunnit)}
    nopeus = np.full((len(tunnit), len(asemat)), np.nan, dtype=np.float32)
    for lat, lon, ts, v in rivit:
        nopeus[ti[ts], ai[(lat, lon)]] = v

    t4326_3067 = Transformer.from_crs(4326, 3067, always_xy=True)
    xy = np.array([t4326_3067.transform(lon, lat) for lat, lon in asemat],
                  dtype=np.float64)
    return xy, [dt.datetime.fromtimestamp(t, dt.timezone.utc) for t in tunnit], nopeus


def tuulikentta(bbox, asemat_xy, nopeus):
    """Tuulen nopeus ruudukolla joka tunnille, kaanteisella etaisyyden
    neliolla painotettuna - sama menetelma kuin selaimen windAt.

    TUNNETTU VINOUMA, jota ei korjata keksityilla kertoimilla vaan
    dokumentoidaan: havaintoasemat ovat rannikolla ja saarissa, joten
    avomerituuli ALIARVIOITUU. Silloin sekoitusannos jaa liian pieneksi ja
    luotettavuus on avomerella liian OPTIMISTINEN."""
    w, h = _ruudukko(bbox)
    xs = bbox[0] + (np.arange(w) + 0.5) * LEVA_GRID_M
    ys = bbox[3] - (np.arange(h) + 0.5) * LEVA_GRID_M
    gx, gy = np.meshgrid(xs, ys)
    # (h, w, n_asemaa)
    d2 = ((gx[..., None] - asemat_xy[:, 0]) ** 2
          + (gy[..., None] - asemat_xy[:, 1]) ** 2)
    # Asemapisteessa paino on aareton -> annetaan sille tasan aseman arvo.
    d2 = np.maximum(d2, 1.0)
    paino = 1.0 / d2
    kentta = np.empty((len(nopeus), h, w), dtype=np.float32)
    for k, rivi in enumerate(nopeus):
        ok = np.isfinite(rivi)
        if not ok.any():
            kentta[k] = np.nan
            continue
        p = paino[..., ok]
        kentta[k] = (p * rivi[ok]).sum(axis=-1) / p.sum(axis=-1)
    return kentta


def sekoitusannos(kentta, tunnit, havainnon_aika):
    """Kumulatiivinen sekoitusannos havaintohetkesta eteenpain, per IKAVRK.

    annos = summa yli tuntien max(0, U - kynnys) * dt

    Palauttaa taulukon jossa alkio k on annos joka on kertynyt viimeisten
    k vuorokauden aikana. Kynnysta sovelletaan PAIKALLISEEN tuuleen ennen
    integrointia, ei interpoloituun annokseen: max() ei ole lineaarinen,
    joten jarjestyksella on merkitysta."""
    yli = np.clip(kentta - LEVA_TUULI_KYNNYS_MS, 0.0, None)
    yli = np.nan_to_num(yli, nan=0.0)
    # Tunnit ovat kasvavassa jarjestyksessa; kumulatiivinen summa LOPUSTA
    # alkuun kertoo paljonko on kertynyt viimeisten n tunnin aikana.
    lopusta = np.cumsum(yli[::-1], axis=0)
    n_vrk = int(math.ceil(len(tunnit) / 24.0))
    ulos = np.zeros((n_vrk + 1,) + kentta.shape[1:], dtype=np.float32)
    for k in range(1, n_vrk + 1):
        i = min(k * 24, len(lopusta)) - 1
        ulos[k] = lopusta[i]
    return ulos


def luotettavuus(ika_vrk, annos):
    """0..1. Pienenee seka havainnon ian etta sen jalkeisen tuulen mukaan.

    Kayttaytyminen (dokumentoitu tahan, jotta arvot voi tarkistaa):
        1 vrk tyyni                 0,90
        3 vrk tyyni                 0,74
        7 vrk tyyni                 0,50
        3 vrk, joista 12 h 12 m/s   0,09
    """
    return np.exp(-annos / LEVA_SEKOITUS_TAU - ika_vrk / LEVA_IKA_TAU_VRK)


# --- ULOSTULO ---

def meri(bbox):
    """True = merta. Sama lahde kuin muualla putkessa (Maastotietokanta)."""
    from rasterio.transform import from_origin
    from . import vesisto
    w, h = _ruudukko(bbox)
    tr = from_origin(bbox[0], bbox[3], LEVA_GRID_M, LEVA_GRID_M)
    return vesisto.meri_maski(bbox, tr, (h, w))


def _kirjoita_png(polku, luokka, ika, luot, on_meri=None):
    """Datakuva: R = luokka (254 = maa, 255 = ei havaintoa), G = ika vrk,
    B = luotettavuus.

    ALFA ON AINA 255. Canvasin esikerrottu alfa turmelee RGB:n kun A < 255
    (getImageData), joten datakanaville ei kayteta alfaa. Tama on projektissa
    kertaalleen opittu."""
    from PIL import Image
    h, w = luokka.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    r = np.where(luokka == EI_DATAA, EI_DATAA_ULOS, luokka).astype(np.uint8)
    if on_meri is not None:
        r = np.where(on_meri, r, MAA_ULOS).astype(np.uint8)
    rgba[..., 0] = r
    rgba[..., 1] = np.clip(ika, 0, 254).astype(np.uint8)
    rgba[..., 2] = np.clip(np.round(luot * 255.0), 0, 255).astype(np.uint8)
    rgba[..., 3] = 255
    polku.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(polku, "PNG", optimize=True)


def paivita(ulos=None, paivia=LEVA_IKKUNA_VRK):
    """Hakee, kokoaa ja kirjoittaa levatilanteen. Palauttaa metatiedon.

    VIKASIETOISUUS: jos SYKE tai FMI ei vastaa, vanha tiedosto jaa voimaan
    eika kartta kaadu - sama periaate kuin saaennusteella. Siksi kirjoitus
    tehdaan vasta kun kaikki klusterit on saatu haettua."""
    ulos = Path(ulos) if ulos else ROOT / "docs"
    bboxit = vektoritasot.alueen_bboxit()
    print(f"Levatilanne: {len(bboxit)} aluetta, {paivia} vrk ikkuna", flush=True)

    saatavilla = saatavilla_olevat_paivat()
    raja = (dt.date.today() - dt.timedelta(days=paivia)).isoformat()
    paivat = {taso: [p for p in lista if p >= raja]
              for taso, lista in saatavilla.items()}
    for _nimi, taso, natiivi in LEVA_KERROKSET:
        print(f"  {taso}: {len(saatavilla[taso])} paivaa yhteensa, "
              f"{len(paivat[taso])} ikkunassa, tuorein "
              f"{saatavilla[taso][0] if saatavilla[taso] else '-'} "
              f"({natiivi:.0f} m)", flush=True)

    # Kansalaishavainnot haetaan KERRAN koko maasta ja rajataan alueisiin
    # taalla: rajapinnassa ei ole bbox-suodatinta, joten alueittain hakeminen
    # toistaisi saman kyselyn turhaan.
    kh_loppu = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    kh_alku = kh_loppu - dt.timedelta(days=paivia)
    try:
        kansalaishavainnot = hae_kansalaishavainnot(kh_alku, kh_loppu)
        print(f"  kansalaishavaintoja koko maasta: {len(kansalaishavainnot)}",
              flush=True)
    except Exception as e:
        # Vikasietoisuus: satelliittikerros on paatuote, eika sen pida
        # kaatua taydentavan lahteen mukana.
        print(f"  kansalaishavainnot: haku epaonnistui ({type(e).__name__}), "
              f"jatketaan ilman", flush=True)
        kansalaishavainnot = []

    tulokset = []
    for i, bbox in enumerate(bboxit, 1):
        w, h = _ruudukko(bbox)
        on_meri = meri(bbox)
        merta = int(on_meri.sum())
        print(f"  alue {i}/{len(bboxit)}: {w}x{h} ruutua, "
              f"joista merta {merta} ({100.0*merta/(w*h):.0f} %)", flush=True)
        luokka, ika, kaytetyt = kokoa_ikkuna(bbox, paivat, on_meri)
        if not kaytetyt:
            raise RuntimeError(f"alueelle {i} ei saatu yhtaan havaintoa")
        katettu = int(((luokka != EI_DATAA) & on_meri).sum())
        print(f"    kattavuus: {katettu}/{merta} merialueesta "
              f"({100.0*katettu/max(merta,1):.0f} %)", flush=True)

        loppu = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0,
                                                         microsecond=0, tzinfo=None)
        alku = loppu - dt.timedelta(days=paivia)
        asemat_xy, tunnit, nopeus = hae_tuulihavainnot(bbox, alku, loppu)
        print(f"    tuuli: {len(asemat_xy)} asemaa, {len(tunnit)} tuntia", flush=True)
        kentta = tuulikentta(bbox, asemat_xy, nopeus)
        annokset = sekoitusannos(kentta, tunnit, None)
        annos = annokset[np.clip(ika, 0, len(annokset) - 1),
                         np.arange(h)[:, None], np.arange(w)[None, :]]
        luot = luotettavuus(ika.astype(np.float32), annos)
        # Ei dataa -> ei myoskaan luotettavuutta.
        luot = np.where(luokka == EI_DATAA, 0.0, luot)

        _kirjoita_png(ulos / "leva" / f"alue{i}.png", luokka, ika, luot, on_meri)

        # Kansalaishavainnot samalle alueelle, SAMALLA luotettavuusmallilla:
        # nekin vanhenevat ian ja niiden jalkeisen tuulen mukana. Sekoitus-
        # annos luetaan samasta kentasta kuin satelliittipikseleille, joten
        # kaksi eri lahdetta kohtelee tuulta tasan samoin.
        pisteet = []
        for hav in kansalaishavainnot:
            if not (bbox[0] <= hav["x"] < bbox[2] and bbox[1] <= hav["y"] < bbox[3]):
                continue
            try:
                havaittu = dt.datetime.strptime(hav["aika"], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                continue
            ika_vrk = max((kh_loppu - havaittu).total_seconds() / 86400.0, 0.0)
            c = min(int((hav["x"] - bbox[0]) / LEVA_GRID_M), w - 1)
            r = min(int((bbox[3] - hav["y"]) / LEVA_GRID_M), h - 1)
            annos_p = float(annokset[min(int(round(ika_vrk)),
                                         len(annokset) - 1), r, c])
            pisteet.append({
                "x": hav["x"], "y": hav["y"], "arvo": hav["arvo"],
                "ika_vrk": round(ika_vrk, 2),
                "luotettavuus": round(float(luotettavuus(np.float32(ika_vrk),
                                                         np.float32(annos_p))), 3),
            })
        print(f"    kansalaishavaintoja alueella: {len(pisteet)}", flush=True)

        tulokset.append({
            "tiedosto": f"leva/alue{i}.png",
            "bounds_epsg3067": {"minx": bbox[0], "miny": bbox[1],
                                "maxx": bbox[2], "maxy": bbox[3]},
            "leveys": w, "korkeus": h, "grid_m": LEVA_GRID_M,
            "havaintopaivat": kaytetyt,
            "tuuliasemia": len(asemat_xy),
            "meriruutuja": merta,
            "katettu_meriruutua": katettu,
            "kansalaishavainnot": pisteet,
        })

    meta = {
        "ajettu": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "alueet": tulokset,
        "ikkuna_vrk": paivia,
        "luokat": {str(k): {"nimi": v, "vari": LUOKKA_VARIT[k]}
                   for k, v in LUOKKA_NIMET.items()},
        "grid_m": LEVA_GRID_M,
        "kerrokset": [{"nimi": n, "taso": t, "natiivi_m": m}
                      for n, t, m in LEVA_KERROKSET],
        # ERI ASTEIKKO kuin satelliitilla: ihminen arvioi maaraa, satelliitti
        # todennakoisyytta. Nimet luetaan rajapinnan omasta maarittelysta.
        "kansalaishavainto_nimet": {str(k): v
                                    for k, v in KANSALAISHAVAINTO_NIMET.items()},
        "malli": {"tuuli_kynnys_ms": LEVA_TUULI_KYNNYS_MS,
                  "sekoitus_tau": LEVA_SEKOITUS_TAU,
                  "ika_tau_vrk": LEVA_IKA_TAU_VRK,
                  "huom": "Vakiot ovat arvioita, ei julkaistu standardi. "
                          "Mallia ei ole kalibroitu mittausaineistoa vastaan."},
        "lahteet": [
            {"nimi": "Suomen ympäristökeskus (Sentinel-3/OLCI, Landsat 8)",
             "lisenssi": "CC BY 4.0"},
            {"nimi": "Ilmatieteen laitos", "lisenssi": "CC BY 4.0"},
            {"nimi": "Suomen ympäristökeskus, kansalaishavainnot",
             "lisenssi": "CC0"},
        ],
    }
    ulos.mkdir(parents=True, exist_ok=True)
    (ulos / "leva.json").write_text(json.dumps(meta, indent=2))
    print(f"Valmis: {ulos / 'leva.json'}", flush=True)
    return meta


def main():
    p = argparse.ArgumentParser(description="Levatilanteen paivitys")
    p.add_argument("--paivita", action="store_true", help="hae ja kirjoita")
    p.add_argument("--ulos", default=None, help="ulostulohakemisto (oletus docs/)")
    p.add_argument("--paivia", type=int, default=LEVA_IKKUNA_VRK)
    a = p.parse_args()
    if not a.paivita:
        p.error("anna --paivita")
    paivita(a.ulos, a.paivia)


if __name__ == "__main__":
    main()
