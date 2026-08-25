# Oma ranta — demo (clusterplan.md, vaiheet 1–2)

Henkilökohtainen rantautumiskelpoisuus: käyttäjä merkitsee ≥3 suosikkia, ja
rannan laatu on **etäisyys niiden muodostamaan ideaaliin**.

**Ei kosketa tuotantoon.** Lukee vain `docs/cache/`-kuvia ja kirjoittaa tänne.

## Ajaminen

```bash
cd <repo>
python3 -m http.server 8771 --bind 127.0.0.1
# http://127.0.0.1:8771/demo-oma/index.html
```

## Tuottaminen alusta

```bash
python3 demo-oma/piirteet.py        # 18 geometriapiirrettä docs/cache-kuvista
python3 demo-oma/maastopiirteet.py  # 9 maanpeiteluokkaa maasto-mml/maasto.gpkg:sta
python3 demo-oma/vaylapiirteet.py   # etäisyys kauppamerenkulun väylään docs/vaylat.json:ista
python3 demo-oma/vie.py             # yhdistää, kvantiloi, kirjoittaa selaimelle
python3 demo-oma/poisjattokoe.py    # mittari: toimiiko mitta
```

Maastoaineisto haetaan MML:n maastotietokannasta teemalla `maasto`
(`backend/mml.run_job("maastotietokanta_bbox", …)`), bbox
`336385 6621827 436385 6721827` = 10 000 km², alle rajapinnan 17 000 km² katon.

## Sopimus

`pisteytys.py` ja `index.html`:n JavaScript laskevat **saman asian**. Jos
muutat toista, muuta molemmat. Todennettu: identtiset etäisyydet kuudella
desimaalilla ja identtinen järjestys samalla suosikkijoukolla.

## Mittarit

| | 18d geometria | 27d + maanpeite |
|---|---:|---:|
| hiekkaranta top 10 % | 31,0 % | **100,0 %** |
| kivikko | 30,2 % | 96,2 % |
| satama | 40,0 % | 99,5 % |
| hoidettu ranta | 50,7 % | 96,8 % |
| **satunnainen (nolla)** | 13,2 % | 11,5 % |

18d on riippumaton koe: hiekka ei ole piirteissä. 27d näyttää tuotteen
käyttäytymisen, ja on hiekan osalta osin kehäpäätelmä — mutta nollavertailu
pysyi paikallaan, joten kyse on aidosta signaalista.

PCA: 95 % selitysaste vaatii 9 pääkomponenttia (kynnys oli 5) — piirteitä ei
ole liikaa.

## Etäisyys rahtiväylään

28. ulottuvuus. Vain **VL1–VL2** eli kauppamerenkulun väylät — ero veneilyyn
on melojalle turvallisuusasia, ja `vektoritasot.py` säilyttää sen jo
ominaisuustietona. Testialueella 34 väylää, 497 km. Ehdokkaiden mediaani­etäisyys
2 780 m; 6,9 % on alle 500 m päässä.

**Suunnallinen, ei tavoitteellinen.** Rahtiväylä on vaara eikä mieltymys:
ideaalin ylittäminen ei saa rangaista. Jos suosikkisi ovat 3 km päässä, 5 km
päässä oleva ranta on yhtä hyvä — ei "liian kaukana".

Seuraus on **epäsymmetria**, ja se on tarkoitettu:

| suosikit | suurin vaikutus | parhaan 5 %:n mediaanietäisyys |
|---|---|---:|
| 6–8,5 km väylästä | `etaisyys_rahtivaylaan` 1,00 | 5 684 m |
| 60–280 m väylästä | ei kärkikolmikossa | 2 012 m |
| *koko aineisto* | | *2 780 m* |

Mitta osaa siis ilmaista "haluan kauas rahtiväylistä" mutta ei "haluan
lähelle". Lisäys ei muuttanut yhtäkään aiempaa tulosta desimaaliakaan.
