#!/usr/bin/env python3
"""Vaihe 2: henkilokohtainen rantautumiskelpoisuus (clusterplan.md).

Sama laskenta on toteutettava selaimessa TASMALLEEN samoin. Tama tiedosto
on maarittely; demo-oma/index.html toistaa sen JavaScriptilla. Projektissa
on jo yksi tallainen sopimus (score_from_components / scoreFromComponents),
ja sen opetus on etta MOLEMMAT MUUTETAAN AINA YHDESSA.
"""
import numpy as np

# Piirteiden luokat (clusterplan.md, kohta 2). Luokittelu on
# SUUNNITTELUVALINTA EIKA TOTUUS, joten se on tassa taulukkona eika
# upotettuna kaavaan - se on tarkoitettu testattavaksi ja muutettavaksi.
#
#   "ylos"  = suunnallinen, enemman parempi. Ideaalin YLITTAMINEN ei rankaise.
#   "alas"  = suunnallinen, vahemman parempi.
#   "tavoite" = tavoitteellinen. Haluat juuri tietynlaisen, et aaripaata.
LUOKAT = {
    "jyrkkyys": "ylos",                 # loivuuspisteet: 1,0 = tasainen
    "suo": "alas",                      # kukaan ei etsi enemman suota
    "etaisyys_rakennuksiin": "tavoite",  # toinen haluaa eramaata, toinen palveluja
    "kallio": "tavoite",                # sileä kallio on laituri tai este
    "puskurin_leveys": "tavoite",
    "tasapelinpurku": "tavoite",
}
RYHMA_ETULIITE = "pyyhkaisy_"           # 12 sektoria kasitellaan YHTENA ryhmana

# KUTISTUSTERMI ON PIIRREKOHTAINEN, EI 1/12.
#
# clusterplan.md sanoo: "sigma^2 koko aineiston varianssi (kvantiili-
# normalisoinnin jalkeen ~ 1/12 kaikilla)". Se pitaa paikkansa vain
# JATKUVILLE piirteille. Kvantiilimuunnos tuottaa tasajakauman vain jos
# sidoksia ei ole; harvinaisilla maanpeiteluokilla 99 % ehdokkaista on
# samassa arvossa, jolloin muunnoksen jalkeinen varianssi on murto-osa
# 1/12:sta - mitattuna suo_vektorilla 0,0002 eli 400 kertaa pienempi.
#
# Seuraus oli nakyva: jokainen harvinainen luokka sai suurimman painon,
# koska suosikit olivat siita triviaalisti yhta mielta (kaikki nollia).
# Painolista naytti "suo, kivikko, suo_vektori" vaikka suosikit olivat
# hiekkarantoja.
#
# Nyt sigma^2 luetaan aineistosta piirteittain (piirteet.json: variassit).
KUTISTUS_KERROIN = 3.0                  # lambda = KUTISTUS_KERROIN / n
OLETUSVARIANSSI = 1.0 / 12.0            # jatkuvalle piirteelle oikea arvo


def jaottelu(nimet):
    """(yksittaisten indeksit, ryhman indeksit)."""
    ryhma = [i for i, n in enumerate(nimet) if n.startswith(RYHMA_ETULIITE)]
    yksin = [i for i, n in enumerate(nimet) if not n.startswith(RYHMA_ETULIITE)]
    return yksin, ryhma


def ideaali(q, suosikit):
    """Suosikkien MEDIAANI ulottuvuuksittain, ei keskiarvo.

    Yksi poikkeava suosikki ei silloin siirra ideaalia. Kolmella suosikilla
    keskiarvo liikkuisi kolmasosan yhden virhevalinnan verran."""
    return np.median(q[suosikit], axis=0)


def aineiston_varianssit(q):
    """Piirrekohtainen varianssi, kutistustermin pohjaksi."""
    return q.var(axis=0)


def painot(q, suosikit, nimet, varianssit=None):
    """Painot suosikkien yksimielisyydesta.

    YDINAJATUS: ulottuvuudet joista suosikit ovat YHTA MIELTA ovat ne jotka
    merkitsevat. Jos kaikki suosikkisi ovat suojaisia mutta puolet kalliota
    ja puolet hiekkaa, suojaisuus painaa ja pinta ei.

    KUTISTUS ON VALTTAMATON. Kolmesta esimerkista laskettu varianssi on
    kohinaa; ilman lambdaa yksi sattumalta yhtenevä ulottuvuus saisi
    valtavan painon ja mitta romahtaisi sen varaan."""
    n = len(suosikit)
    lam = KUTISTUS_KERROIN / max(n, 1)
    s2 = q[suosikit].var(axis=0)
    sig2 = aineiston_varianssit(q) if varianssit is None else np.asarray(varianssit)
    yksin, ryhma = jaottelu(nimet)
    w = np.zeros(len(nimet))
    for i in yksin:
        w[i] = 1.0 / (s2[i] + lam * sig2[i])
    if ryhma:
        # RYHMAPAINOTUS ERIKSEEN: 12 sektoria hukuttaisivat 6 muuta piirretta
        # pelkalla lukumaarallaan. Ryhma saa yhden piirteen painon.
        w_r = 1.0 / (s2[ryhma].mean() + lam * sig2[ryhma].mean())
        for i in ryhma:
            w[i] = w_r / len(ryhma)
    return w


def vaikutus(q, m, w, nimet):
    """Piirteen TODELLINEN vaikutus jarjestykseen, ei pelkka paino.

    Suuri paino ei tarkoita suurta vaikutusta. Jos kaikki ehdokkaat ovat
    piirteessa samanlaisia kuin ideaali, poikkeama on aina nolla eika paino
    erottele mitaan - se vain nayttaa suurelta. Vaikutus on paino kertaa se
    hajonta jonka piirre TOSIASIASSA tuottaa etaisyyteen.

    Tama on se luku joka kayttajalle nayetaan: "mika sinulle merkitsee"
    tarkoittaa mika erottelee sinun rantasi muista, ei mika sattuu olemaan
    vakio koko aineistossa."""
    d = poikkeama(q, m, nimet)
    return w * (d * d).mean(axis=0)


def poikkeama(q, m, nimet):
    """delta ulottuvuuksittain, luokan mukaan."""
    d = np.abs(q - m)
    for i, nimi in enumerate(nimet):
        luokka = LUOKAT.get(nimi, "tavoite")
        if luokka == "ylos":
            d[..., i] = np.maximum(0.0, m[i] - q[..., i])
        elif luokka == "alas":
            d[..., i] = np.maximum(0.0, q[..., i] - m[i])
    return d


def etaisyys(q, suosikit, nimet, varianssit=None):
    """Painotettu etaisyys ideaaliin jokaiselle ehdokkaalle."""
    m = ideaali(q, suosikit)
    w = painot(q, suosikit, nimet, varianssit)
    d = poikkeama(q, m, nimet)
    return np.sqrt((d * d * w).sum(axis=1)), m, w


def pistemaara(q, suosikit, nimet, varianssit=None):
    """1 - persentiili(etaisyys). Tulos 0-1, suurempi on parempi.

    Istuu sellaisenaan nykyiseen "parhaat X %" -kayttoliittymaan: kynnys
    lasketaan vain kyselykohtaisesti eika buildissa."""
    d, m, w = etaisyys(q, suosikit, nimet, varianssit)
    jarjestys = np.argsort(d, kind="stable")
    sija = np.empty(len(d))
    sija[jarjestys] = np.arange(len(d))
    return 1.0 - sija / max(len(d) - 1, 1), d, m, w
