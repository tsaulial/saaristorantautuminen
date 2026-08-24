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
