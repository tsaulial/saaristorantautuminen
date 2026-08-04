# Rantautumispaikkojen analysointisovellus

## Mikä tämä on?

Verkkosovellus, joka näyttää kartalla **mihin kohtiin Saaristomeren rannikkoa kannattaa rantautua** veneellä tai kajakilla — ja mihin ei.

Sovellus katsoo jokaista rantaviivan lähellä olevaa kohtaa ja pisteyttää sen väreillä:

- 🟢 **vihreä** = hyvä rantautumispaikka
- 🟡 **keltainen** = ihan ok
- 🔴 **punainen** = huono

Kokeile sovellusta: **https://tsaulial.github.io/saaristorantautuminen/**

## Miten pisteytys toimii?

Pisteytys perustuu neljään asiaan:

1. **Onko ranta loiva vai jyrkkä** — tärkein tekijä
2. **Kuinka lähellä on rakennuksia** (ei haluta rantautua jonkun pihaan)
3. **Onko kohta paljasta kalliota** — hyvä, koska tukeva
4. **Onko kohta suota** — huono, jalka uppoaa

Pisteytys koskee vain **merenrantaa** — saarten sisällä olevat purot ja lammet jätetään huomiotta, koska niille ei kuitenkaan pääse rantautumaan.

Koko laskenta on tehty niin, ettei sovellus tarvitse jatkuvasti pyörivää palvelinta — kaikki on laskettu valmiiksi etukäteen, ja lopputulos on pelkkiä kuvia ja tietoa, jotka mikä tahansa nettisivu voi näyttää. Siksi se pyörii ilmaiseksi GitHub Pagesissa.

## Mitä kartalla voi säätää?

Kartan oikean alakulman valikosta voi valita **mikä neljästä näkymästä** on esillä:

- **Rantautumispisteytys** — koko rannikko väritettynä vihreästä punaiseen
- **Parhaat rantautumispaikat** — vain kerma päältä, omalla kirkkaan magentalla värillä
- **Kärkipaikat** — tiukempi seula, violetilla
- **Melottavuus** — värittää meren eikä rantaa: millaista vesi on matkalla perille

Samasta valikosta pääsee **asetussivulle**, jossa on kaikki muu säädettävä.

### Mitä "Kärkipaikat" tarkoittaa?

"Parhaat rantautumispaikat" katsoo jokaista kohtaa erikseen. Siitä seuraa kaksi ongelmaa: ranta voi näyttää huippuhyvältä aivan vesirajassa vaikka muutaman metrin päässä olisi pystysuora kallio, eikä kartalta erota mahtuuko paikkaan vene vai onko kyse metrin levyisestä kolosta.

**Kärkipaikat vaatii, että koko rantakaistale on hyvä** — ei vain vesiraja vaan koko 30 metrin syvyys sisämaahan päin — **ja että hyvää rantaa on yhtäjaksoisesti vähintään noin 20 metriä.** Käytännössä siis kohtia, joissa pääsee rantaan, pääsee ylös ja mahtuu olemaan.

Yksittäinen mittausvirhe ei kaada paikkaa: arvio perustuu kaistaleen huonoimpaan kymmenykseen, ei sen huonoimpaan yksittäiseen pisteeseen.

Kärkipaikkoja on yhtä paljon kuin parhaita rantautumispaikkojakin (oletuksena 7 % rantaviivasta), mutta ne ovat **eri kohtia**: vain 43 % vanhoista säilyy. Ennen kaikkea ne ovat isompia ja yhtenäisempiä — erillisten kohtien määrä putoaa 7581:stä 2330:een ja tyypillinen koko yli nelinkertaistuu.

### Mitkä tekijät otetaan huomioon?

Asetussivulla voi valita rastittamalla, **mitkä tekijät vaikuttavat pisteytykseen**:

- ☑️ **Maaston jyrkkyys**
- ☑️ **Suo**
- ☑️ **Kallio**
- ☑️ **Rakennukset**
- ☐ **Tuulensuoja** — huomioi mistä suunnasta tuulee ja kuinka avoin ranta on sille

Neljä ensimmäistä ovat oletuksena mukana. Tuulensuoja on oletuksena pois päältä, koska se muuttaa kartan sen mukaan mikä tuuli sattuu olemaan — se on hyödyllinen juuri tietylle retkelle, mutta huono yleiskuvaan. Jos jonkin poistaa, se häviää laskennasta kokonaan ja loppujen keskinäinen painoarvo suhteutetaan uudelleen sataan prosenttiin — sivu näyttää kunkin tekijän osuuden heti valintojen vierellä. Näin voi katsoa esimerkiksi pelkkää maaston jyrkkyyttä, tai jättää rakennukset huomiotta jos ei välitä siitä että lähistöllä on mökkejä. Vähintään yksi tekijä on oltava valittuna.

Muutokset tallentuvat heti ja ovat voimassa kun palaa kartalle.

### Miten rantaviiva jakautuu?

Asetussivun alalaidassa on kuvaaja, joka näyttää **koko rantaviivan jaettuna rantautumiskelpoisuuden mukaan** — kuinka monta kilometriä on helppoa, kuinka moni kohtalaista ja kuinka moni vaikeaa. Kuvaaja päivittyy heti kun tekijävalintoja muuttaa, joten siitä näkee suoraan miten paljon esimerkiksi rakennusten huomiotta jättäminen muuttaa tilannetta.

Oletusasetuksilla kuva on tämä: **noin 500 km helppoa (72 %), 186 km kohtalaista (27 %) ja vain 12 km vaikeaa (2 %)**. Saaristo on siis suurimmaksi osaksi hyvää rantautumismaastoa.

Kuvaajassa on lisäksi violetti käyrä, joka näyttää saman rantaviivan **kärkipaikkojen mittapuulla** — eli kun jokainen kohta arvioidaan koko rantakaistaleensa heikoimman osan mukaan. Ero on iso ja kertoo paljon: helpon rannan määrä putoaa 502 kilometristä **191 kilometriin**. Kaksi kolmasosaa siitä rannasta, joka näyttää vesirajassa helpolta, ei siis ole helppoa enää muutamaa metriä kauempana. Jos katsoo pelkkää maaston jyrkkyyttä, vaikean osuus nousee 93 kilometriin — jyrkkiä kalliorantoja on selvästi enemmän kuin mitä kokonaispisteytys antaa ymmärtää, koska muut tekijät nostavat niiden pisteitä.

Rantaviivaa on kaikkiaan noin **700 km** (haarukka 600–900 km). Luku on arvio: rantaviiva on luettu peruskartan kuvasta, ei valmiista viivageometriasta, joten sen pituus riippuu siitä millä tarkkuudella mitataan — sama ilmiö kuin klassisessa kysymyksessä "kuinka pitkä on Britannian rannikko". Jakauman muoto ja prosenttiosuudet ovat sen sijaan tarkkoja.

### Esitystapa

Asetussivulla on lisäksi kaksi liukusäädintä, jotka eivät vaikuta pisteytykseen vaan pelkkään ulkoasuun:

- **Rantaviivan korostuksen paksuus** (1–20 pikseliä, oletus 10) — kuinka leveänä rantakaista piirretään.
- **Parhaat rannat** (1–10 %, oletus 7 %) — kuinka tiukka "parhaat rantautumispaikat" -näkymä on. 1 % näyttää vain aivan huippukohdat, 10 % selvästi laajemman joukon. Vertailu on aina **koko alueen** laajuinen, ei saarikohtainen — ja se ottaa huomioon juuri ne tekijät jotka on valittu.

### Kokeellinen vertailu

Valikossa on toistaiseksi myös **"Toteutus (vertailua varten)"** -valinta (*Nykyinen* / *Uusi*). Se ei muuta pisteytystä eikä lopputulosta, vain tavan jolla kartta piirretään: uudessa tavassa väritys ja rajaukset lasketaan vasta selaimessa yhdestä esilasketusta kuvasta, kun nykyisessä jokainen säätimen asento on oma valmiiksi laskettu kuvansa. Uusi tapa on hieman karkeampi (3,5 metrin ruutu), mutta vie murto-osan levytilasta — sillä on merkitystä, jos kartoitettava alue joskus laajenee koko Suomenlahdelle ja länsirannikolle. Valinta on kehityksen aikainen vertailu eikä jää lopulliseen sovellukseen.

## Entä tuuli ja aallokko?

Sama ranta on eri ranta eri tuulella. Kartta osaa siksi ottaa sään huomioon kahdella tavalla.

**Melottavuusnäkymä värittää meren** sen mukaan, kuinka vaativaa siellä on meloa. Arvio perustuu kahteen asiaan, jotka pidetään erillään koska ne vaikuttavat eri tavoin: **aallokko** määrää veneen liikkeen ja syntyy tasaisesta tuulesta, **puuskat** taas määräävät käsiteltävyyden eli sen pysyykö kurssissa. Vaikeus on aina huonomman mukaan — kumpi tahansa yksin riittää tekemään matkasta vaativan.

**Tuulensuojan voi ottaa mukaan rantojen pisteytykseen.** Silloin suojaisa poukama nousee ja tuulen puolella oleva avoin ranta laskee. Kärkipaikat näyttää tällöin vain ne kohdat, jotka kelpaisivat myös tyynellä — kova tuuli siis vähentää niitä.

### Mistä sää tulee?

Ennuste haetaan **Ilmatieteen laitokselta** eikä sitä tarvitse arvata. Tuulta ei kuitenkaan haeta yhdestä pisteestä: sovelluksen on määrä kattaa aikanaan koko rannikko, ja niin laajalla alueella yksi luku olisi pahasti väärä. Koekyselyssä saman hetken tuuli oli Merenkurkussa 11,6 m/s ja Perämerellä 2,2 m/s — yli viisinkertainen ero. Tuuli haetaan siksi **useasta pisteestä** ja välissä olevat kohdat lasketaan niiden perusteella.

Tuulen voi myös **säätää käsin**. Se on hyödyllinen "entä jos" -työkalu ja samalla varakeino: jos ennustepalvelu ei vastaa, kartta toimii silti.

Aikajänne on noin **50 tuntia**. Pidemmälle ei mennä, koska sovellus on tarkoitettu lähipäivien retken suunnitteluun eivätkä kaukaisemmat ennusteet ole riittävän varmoja.

### Tietopallot

**Kartan klikkaus lisää siihen kohtaan pallon**, joka kertoo juuri sen paikan olosuhteet: tuulen nopeuden, puuskat, suunnan ja aallonkorkeuden. Palloja voi olla monta, jolloin kahta reittivaihtoehtoa voi verrata rinnakkain. Pallon klikkaus poistaa sen.

Pallossa on kolme visuaalista vihjettä, jotka näkee ennen kuin numeroita ehtii lukea:

- **Reunaviiva aaltoilee** sitä voimakkaammin mitä korkeampi aallokko on — tyynellä pallo on ympyrä
- **Kehällä kiertää pieni pallo** kuin kellon osoitin ja näyttää mistä tuuli tulee; sen koko ja väri kertovat puuskan voimakkuuden
- **Varoitus** ilmestyy, jos olosuhteet heikkenevät oleellisesti seuraavan kolmen tunnin aikana

Varoitus on niistä tärkein. Vaarallinen tilanne ei ole se, että keli on kova — sen näkee kartalta — vaan se, että lähtee helppoon keliin ja jää kiinni muutokseen.

Pallo hakee lisäksi **Norjan ilmatieteen laitoksen ennusteen** omalta kohdaltaan ja näyttää sen **vain jos lähteet ovat eri mieltä**. Kun ne ovat samaa mieltä, toinen luku ei kerro mitään. Erimielisyys sen sijaan kertoo juuri sen, mitä on hyödyllistä tietää: että lukuun ei kannata luottaa liikaa.

## Käytetyt avoimet aineistot

Kolme Maanmittauslaitoksen (MML) avointa aineistoa:

1. **Korkeusmalli (2 m)** — MML:n avoin korkeusmalliaineisto, kertoo maaston muodot ja jyrkkyyden.
2. **Maastotietokanta, rakennukset** (`rakennukset.gpkg`) — MML:n rekisteri rakennuksista ja rakennelmista, käytetty etäisyyksien laskemiseen.
3. **Peruskartta** (rasterikuvat) — MML:n tavallinen karttapohja, josta luettiin värien perusteella kalliot, rannat ja suot.
4. **Sää- ja aallokkoennuste** — Ilmatieteen laitoksen avoin data: tuuli, puuskat ja aallonkorkeus.
5. **Vertailuennuste** — MET Norway, toinen mielipide tietopalloissa; näytetään vain jos se poikkeaa.

Kolme ensimmäistä ovat osa MML:n avoimen tietoaineiston kokonaisuutta. Myös sääaineistot ovat avointa dataa eivätkä vaadi tunnuksia — juuri siksi sivu toimii ilman omaa palvelinta.

### Mitä rakennusaineistosta otettiin mukaan?

Maastotietokannan rakennusaineisto sisälsi yli 20 erilaista kohdeluokkaa. Mukaan pisteytykseen otettiin vain kaksi:

- ✅ **Varsinaiset rakennukset** (asuinrakennukset, mökit ym.)
- ✅ **Muut rakennelmat** (esim. laiturit, aitat, saunat)

Nämä kertovat parhaiten, missä ihmiset todella asuvat tai oleskelevat — ja niitä lähelle ei haluta rantautua.

Pois jätettiin loput noin 20 kohdeluokkaa, kuten **aidat, mastot, muistomerkit, näkötornit, savupiiput, tuulivoimalat ja aallonmurtajat.** Ne eivät kerro asutuksen läheisyydestä samalla tavalla, joten niitä ei laskettu mukaan etäisyyteen.

## Matkan varrella ratkottuja pulmia

- **Kartta ja korkeusmalli eivät olleet samassa "ruudukossa".** Ne oli jaettu palasiin eri tavalla, joten palasten yhdistäminen piti tehdä sijainnin, ei tiedostonimien, perusteella.
- **Kartan väreistä piti opettaa tietokone tunnistamaan oikeat kohteet** (kallio, ranta, suo). Kartalla oli useita hyvin samannäköisiä sävyjä vierekkäin, joten oikeat värirajat piti hakea kokeilemalla ja mittaamalla, jotta esimerkiksi suo ei sekoittunut johonkin muuhun keltaiseen merkintään.
- **Kartan palaset eivät aluksi täsmänneet saumattomasti yhteen**, vaan väliin jäi näkyviä rakoja. Syy löytyi: suomalaiset kartat käyttävät koordinaatistoa, joka on hieman "vinossa" todelliseen pohjoiseen nähden. Jokainen kartanpala piti erikseen suoristaa oikeaan muotoonsa ennen yhdistämistä.
- **Paras rantautumiskaista oli niin kapea, ettei se erottunut kartalla.** Se jouduttiin piirtämään näkyvyyden vuoksi paksumpana kuin se todellisuudessa on — ja paksuudesta tehtiin lopulta käyttäjän säädettävä, koska sopiva leveys riippuu siitä kuinka kaukaa karttaa katsoo.
- **Sisämaan purot ja lammet näyttivät merenrannalta.** Rantaviiva tunnistettiin kartan sinisestä väristä, joten mukaan tarttui myös saarten sisällä olevia puroja ja lampia, joille ei veneellä pääse. Ne rajattiin pois.
- **"Parhaat rannat" -säädin ei aluksi tehnyt mitään.** Asetuksilla 1 % ja 7 % näkyi täsmälleen sama alue. Syy: niin moni rannankohta sai tasan täydet pisteet, ettei niitä voinut asettaa paremmuusjärjestykseen lainkaan. Ratkaisu oli purkaa tasapelit tarkemmilla mittausarvoilla — loivempi ranta ja kauempana rakennuksista oleva kohta voittaa.
- **Kartta latautui hitaasti.** Koko rannikko piirrettiin aina tarkimmalla mahdollisella tarkkuudella, vaikka katselisi koko aluetta kerralla. Nyt kartta on laskettu valmiiksi neljällä eri tarkkuudella ja lataa vain sen, mikä ruudulla juuri nyt näkyy.
- **Yleisempi karttaruudutus kokeiltiin ja hylättiin.** Kartta yritettiin pilkkoa samalla tavalla kuin tavalliset verkkokartat tekevät, mutta se osoittautui käytännössä epävakaaksi: kartta pätki ja ruutuja jäi satunnaisesti lataamatta. Vanha, yksinkertaisempi tapa palautettiin.
- **Julkaisu piti muuttaa "itsestään pyöriväksi".** Sen sijaan että sovellus laskisi asioita joka kerta uudestaan, kaikki laskettiin kertaalleen valmiiksi kuviksi — näin sivu voi pyöriä ilmaiseksi ilman jatkuvasti käynnissä olevaa konetta.
- **GitHub Pages ei aluksi suostunut näyttämään sivua**, koska julkaisukansion nimi ei kelvannut sen säännöille — pelkkä uudelleennimeäminen korjasi asian.
