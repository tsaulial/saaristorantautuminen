# Persoonapohjainen käyttöliittymätestaus — ruututiilidemo

## Menetelmä ja sen rajat

Tämä on **heuristinen arviointi persoonien läpi**, ei käyttäjätestaus.
Havainnot on johdettu mittaamalla oikeaa demoa (tekstikoot, kosketuskohteet,
kontrastit, siirtomäärät, DOM-rakenne) ja arvioimalla ne kunkin persoonan
tilanteessa.

**Se ei korvaa oikeita käyttäjiä.** Menetelmä löytää rakenteelliset ja
saavutettavuusongelmat luotettavasti, mutta ei kerro mitä ihmiset oikeasti
yrittävät tehdä tai mihin he takertuvat.

---

## Persoonat

| | Kuka | Laite ja tilanne | Mitä tarvitsee |
|---|---|---|---|
| **P1** | Pirjo, 34, melojaretkeilijä | Läppäri suunnitteluun, puhelin vesillä. Märät kädet, auringonpaiste | Mihin voi nousta maihin, missä olen |
| **P2** | Markku, 62, veneilijä | iPad ohjaamossa, ikänäkö, ei lukulaseja | Iso teksti, isot napit, ei jargonia |
| **P3** | Aino, 71, mökkiläinen | Läppäri, vähän tekninen | Pääseekö vieras rantaan — selkokielellä |
| **P4** | Tuomas, 45, asiantuntija | Iso näyttö | Luvut ja menetelmä näkyviin |
| **P5** | Sanna, 28, partiolaisjohtaja | Jaettu läppäri | Vertailu ja linkin jakaminen |
| **P6** | Janne, 39, retkeilijä | Puhelin, heikko kenttä, akku vähissä | Nopea lataus, palaute siitä että toimii |

---

## Lähtötila mitattuna

| | |
|---|---|
| Legenda | **ei ole** |
| Mediakyselyt (mobiili) | **0 kpl** |
| Pienin teksti | 12 px |
| Kosketuskohteet | zoom 30×30, linkit 51×14 |
| Siirto yhdelle näkymälle | **16,5 Mt**, suurin kuva 4,73 Mt |
| Muisti | 290 Mt |
| Kontrasti | 13,7:1 ja 5,38:1 (kunnossa) |

---

## Kierros 1 — estävät viat

| Havainto | Persoonat | Korjaus |
|---|---|---|
| Värejä ei selitetä mitenkään | P1 P2 P3 P5 P6 | Legenda + selkokielinen kuvaus |
| Ei mobiililayoutia | P1 P6 P2 | Paneeli alalaitaan alle 820 px |
| Kehittäjän mittarit hallitsevat | P3 P2 P1 | Omaan suljettuun lohkoonsa |
| Kosketuskohteet 30×30 ja 51×14 | P2 P1 | 44×44 kosketuslaitteilla |
| Pienin teksti 12 px | P2 | Perusteksti 15 px, 16 px kosketuksella |

---

## Kierros 2 — palaute kierroksen 1 jälkeen

Kierros 1 paljasti oman vikansa heti: legendan neljästä palikasta **kaksi
ylintä olivat vain 24° päässä toisistaan sävyltään** ja näyttivät molemmat
vihreiltä. Palikat lupasivat luokkia joita jatkuvassa asteikossa ei ole.

| Havainto | Persoonat | Korjaus |
|---|---|---|
| Legenda ei vastaa karttaa | P1 P3 | Jatkuva liukuväri, päät nimettyinä |
| Pisteen arvoa ei näe | P4 P3 | Napautus → pistemäärä ja osatekijät |
| 16,5 Mt ilman latauspalautetta | P6 | Latausmerkki |
| Näkymää ei voi jakaa | P5 | Näkymä URL-tunnisteeseen |
| iPad pystyssä 768 px | P2 | Mobiiliraja 620 → 820 px |

---

## Kierros 3 — palaute kierroksen 2 jälkeen

Kierros 2 paljasti vakavamman vian kuin mikään aiempi: **osapistemäärien
nimet valehtelivat suunnan.** Popupissa luki "jyrkkyys 100 %", mikä
tarkoittaa loivinta mahdollista rantaa — täsmälleen päinvastoin kuin se
lukee. Aino olisi valinnut rannan sen perusteella väärin.

| Havainto | Persoonat | Korjaus |
|---|---|---|
| "jyrkkyys 100 %" luetaan päinvastoin | P3 P4 | Sanallinen arvio: "Ranta: loiva", ja "suuri on hyvä" |
| Ei paikannusta | P1 P6 | Paikannuspainike, sama todennettu projektio kuin tuotannossa |
| Popupin teksti ja sulkunappi pieniä | P2 | 15 px, sulkunappi 36×36 |
| Kahta paikkaa ei voi vertailla | P5 | Useampi popup pysyy auki |

---

## Avoimeksi jäänyt

**Popupit menevät päällekkäin.** Juuri se ominaisuus jonka lisäsin vertailua
varten (useampi popup auki) on käyttökelvoton kun pisteet ovat lähekkäin —
ne peittävät toisensa ja paneelin. Oikea ratkaisu on luultavasti lista
kiinnitetyistä pisteistä paneelissa, ei popupeja kartalla. Tämä löytyi
kuvakaappauksesta kierroksen 3 jälkeen eikä ehtinyt korjattavaksi.

**Mobiililayoutia ei nähty käytössä.** Säännöt ovat paikallaan ja oikein
ehdollistettuina (tarkistettu CSSOM:sta), mutta selainikkuna ei suostunut
pienenemään testiympäristössä, joten en nähnyt niitä omin silmin.

**16,5 Mt näkymää kohti on yhä paljon** (P6). Ruututiilet eivät korjaa sitä:
lähdekuvat ovat yhä 3000² per 6 km tiili. Se ratkeaa vasta jos lähdeaineisto
pilkotaan pyramidiksi rakennusvaiheessa.

**Melottavuus ja kärkipaikat puuttuvat demosta.** Vain rantautumispisteytys
on toteutettu; P1 ja P5 tarvitsisivat molemmat.
