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

## Miksi tämä ranta sai arvionsa

Klikkaa kartalta rantaa. Paneeli kertoo pistemäärän ja purkaa sen osiin:
**"Sopii sinulle"** ja **"Vie kauemmas ideaalistasi"**.

Selitys on **sama aritmetiikka kuin väri**, ei erillinen tarina. Etäisyys on
`d² = Σ wⱼ·δⱼ²`, joten jokaisen ulottuvuuden osuus luetaan suoraan siitä
samasta summasta jolla kartta väritetään. Erillinen selitysheuristiikka voisi
olla eri mieltä kuin väri — tämä ei voi.

Kaksi lukua ulottuvuutta kohti:

| | |
|---|---|
| **osuus** | `w·δ²` jaettuna kokonaissummalla — paljonko tämä ulottuvuus vie rantaa kauemmas |
| **sopivuus** | kuinka moni ehdokas sopii tässä ulottuvuudessa huonommin |

Molempia tarvitaan. Osuus yksin ei vastaa kysymykseen "mikä tässä on hyvää":
osuus voi olla nolla myös siksi, ettei ulottuvuus erottele mitään.

Sama rivi ei voi olla molemmilla puolilla. Piirre voi hyvin sopia paremmin
kuin 60 % rannoista **ja** olla suurin yksittäinen syy etäisyyteen — molemmat
ovat totta, mutta yhdessä ne lukisivat kuin ohjelma olisi eri mieltä itsensä
kanssa.

Arvot näytetään oikeissa yksiköissä (`kvantiilit.bin`, 101 katkaisukohtaa per
ulottuvuus, 11 kt): "rantakaistaleen leveys 10 m", ei "0,84".

Todennettu Pythonia vasten (`pisteytys.erittely`): identtinen etäisyys
kuudella desimaalilla ja identtiset osuudet ja sopivuudet neljällä.

## Kaksi mittaa rannan laajuudesta

**"rantavyöhykettä ympärillä"** oli aiemmin nimeltään *rantakaistaleen leveys*,
ja nimi oli väärä. Puskurivyöhyke on määritelmän mukaan aina 5–15 m
(`pipeline.compute_shoreline_buffer`), joten sen leveyttä ei voi mitata. Luku
on vyöhykkeen **pinta-ala 25 m säteellä**, ja mitattuna se kertoo kuinka
paljon maata pisteen ympärillä on: korrelaatio kiekon maa-alaan **r = 0,93**.
Arvo 16–22 % = kapea kannas tai pieni luoto, 71–86 % = leveä yhtenäinen ranta.

**"kelvollista rantaa"** on eri asia. Puskurivyöhyke on *arvioitu* vyöhyke,
ei kelvollinen: laaja mutta jyrkkä kallioranta ja laaja loiva hiekkaranta
saavat siitä saman arvon. Tämä mittaa pinta-alan **pistemäärällä
painotettuna** — `pipeline.score_from_components` samalla `NO_SHELTER_MASK`illa
kuin kartan värit — eli montako neliötä ympärillä on maata jolle oikeasti voi
rantautua.

Ne eivät ole toistensa toisintoja: korrelaatio on vain **0,49**, ja saman
laajuuden rannoilla kelvollisen rannan määrä vaihtelee **257–541 m²**
(p10–p90). Mitattu mediaani 261 m², vaihteluväli 14–1 009 m².

Molemmat ovat **suunnallisia** (`ylos`): ylimääräinen tila ei haittaa ketään,
joten ideaalin ylittäminen ei rankaise — ahtaus rankaisee.

## Yksiköt ovat oikeita, eivät pistemääriä

`factors`-kuvan R ja G eivät ole asteita eivätkä metrejä vaan **kyllästyviä
pistemääriä**: jyrkkyys 1,0 alle 5° ja 0 yli 20°, etäisyys 0 alle 20 m ja 1,0
yli 150 m. Näytin ne aluksi prosentteina — "100 %" ei erota 200 metriä
viidestä kilometristä. Nyt ne käännetään takaisin asteiksi ja metreiksi, ja
kyllästysraja merkitään näytöllä: **"yli 150 m"**, ei "150 m".
