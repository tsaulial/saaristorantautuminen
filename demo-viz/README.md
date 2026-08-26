# Rantojen piirreavaruus — visualisointi ja klusterointi

**Erillinen demo.** Lukee `demo-oma/`:n valmiit binäärit, kirjoittaa vain
tänne. Ei muuta tuotantoa (`frontend/`, `docs/`, `backend/`) eikä oma
malli -demoa (`demo-oma/`).

## Ajaminen

```bash
python3 demo-viz/klusterit.py   # MiniBatchKMeans k=6..12, siluetti, profiilit
python3 demo-viz/kuvat.py       # viisi kuvaa demo-viz/kuvat/
python3 demo-viz/raportti.py    # kokoaa raportti.html (kuvat base64:na)
```

`raportti.py` lukee luvut aineistosta ja korvaa ne runkoon
(`raportti_runko.html`). **Lukuja ei kirjoiteta käsin** — muuten ne
vanhenevat hiljaa seuraavassa muutoksessa.

## Tulokset

| tarkistus | tulos | |
|---|---:|---|
| yksikään kuvio ei seuraa tiilirajoja | 28/28 | läpi |
| pääkomponentteja 95 %:iin > 5 | 13 | läpi |
| siluettipisteet > 0,25 | **0,165** | **ei läpi** |
| klusterit maantieteellisesti mielekkäitä | — | läpi |
| ulottuvuuksia joilla \|r\| > 0,9 | 2 paria | **korjattava** |

### Klusterointi ei läpäissyt mittaria

Paras k = 7, siluetti 0,165 (kynnys 0,25). Kokeiltu myös ilman harvinaisia
luokkia (0,160) ja 9 pääkomponentissa (0,196).

Kahteen komponenttiin projisoituna siluetti on 0,355, mutta **sitä ei
lasketa läpimenoksi**: siluetti kasvaa mekaanisesti kun ulottuvuuksia
vähennetään, joten se mittaisi projektiota eikä aineistoa.

Tulkinta: **saariston rannat ovat jatkumo, eivät tyyppejä.** Klusterit ovat
taipumuksia — kartalla ne erottuvat — mutta eivät luokkia. Lähtökohtana
omalle mallille ne kelpaavat, nimettyinä rantatyyppeinä kartalle eivät.

### Kaksi löydöstä joita luvut eivät kertoneet

1. **Kallio on mitattu kahdesti.** `kallio` (kartan väreistä) ja
   `kallio_vektori` (maastotietokannasta) korreloivat **r = 0,941**.
   Pikselitasolla ne olivat vain Jaccard 0,58 yksimielisiä, mutta 25 m
   ikkunaan keskiarvoistettuna ero katoaa. Toinen kuuluisi pudottaa.
2. **PCA vaatii nyt 13 komponenttia, ei 9.** Aiempi luku koski
   18-ulottuvuuksista vektoria. Lisätty maanpeite oli siis pääosin uutta
   tietoa eikä vanhan uudelleenmuotoilua.

## Raportti

Julkaistu Artifactina: `raportti.html`, 2,23 Mt (kuvat base64:na).
