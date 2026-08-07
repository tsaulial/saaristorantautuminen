#!/usr/bin/env bash
#
# ERAAJO: koko putki alusta loppuun yhdella komennolla, toisella koneella.
#
# Ajaa jarjestyksessa: lahtoaineiston lataus MML:sta -> laserkeilaus ->
# valimuistin mitatointi -> staattinen build. Kaikki vaiheet ovat
# TOISTETTAVIA: jo ladattu aineisto ohitetaan ja valmiit valimuistit
# sailyvat, joten keskeytyneen ajon voi kaynnistaa uudelleen samalla
# komennolla ilman etta tyo menee hukkaan.
#
# KAYTTO
#   ./eraajo.sh --bbox MINX MINY MAXX MAXY      # yksi suorakaide
#   ./eraajo.sh --rannikko [--leveys 20000]     # koko Suomen rannikko
#   ./eraajo.sh --rannikko --osa 3/8            # vain kolmas kahdeksasosa
#   ./eraajo.sh                                 # vain rakenna nykyisesta
#
# ESIMERKKI (Helsingin edusta)
#   ./eraajo.sh --bbox 370000 6658000 402000 6682000
#
# KOKO RANNIKKO on KAYTAVA eika suorakaide: rannikon ympari piirretty
# suorakaide on 230 782 km2 ja vaatisi 6 496 karttalehtea (147 Gt), kun
# 20 km kaytava vaatii 867 lehtea (19,7 Gt). Suorakaide ei myoskaan kelpaisi
# vektorihakuun, joka ottaa enintaan 17 334 km2 kerralla.
#   ./eraajo.sh --rannikko
#
# EDELLYTYKSET KONEELLA
#   - Python 3.11+ ja requirements.txt asennettuna
#   - MML:n API-avain tiedostossa ~/.mml-api-key (chmod 600)
#     Avain on SALAISUUS: kopioi se itse, ala laheta sita chatissa
#     tai versionhallinnassa.
#   - Levytilaa: lahtoaineisto n. 0,5 Gt / 1000 km2, docs/ n. 4,9 Mt/tiili
#
# ETENEMINEN NAKYY SHELLISSA. Jokainen vaihe tulostaa kellonajan, ja pitkat
# vaiheet raportoivat edistymista rivi kerrallaan:
#   - lataus:      lehtiera kerrallaan
#   - laserkeilaus: tiili kerrallaan, kesto sekunteina
#   - build:       tiili kerrallaan + arvio jaljella olevasta ajasta
#   - pyyhkaisymatkat: ilmansuunta kerrallaan (48 kpl) + aika-arvio
#   - kynnysarvot: tiili kerrallaan, neljassa vaiheessa vektoritasojen
#                  jalkeen (ks. pipeline.tiilet_edistymisella)
# Jos mikaan naista ei etene minuutteihin, ajo on aidosti jumissa.
#
# Kaikki python-kutsut kaytetaan -u -lipulla, jotta tuloste ei jaa
# puskuriin kun se ohjataan tiedostoon. Ilman sita loki nayttaisi tyhjalta
# tunteja.
#
# Ajo kestaa tunteja. Kaynnista irti paatteesta:
#   nohup ./eraajo.sh --bbox ... > eraajo.log 2>&1 &
#   tail -f eraajo.log

set -euo pipefail
cd "$(dirname "$0")"

# --bbox suorakaide, tai --rannikko koko Suomen rannikko kaytavana.
ALUE=""
if [ "${1:-}" = "--bbox" ]; then
    shift
    ALUE="--bbox $1 $2 $3 $4"
    shift 4
elif [ "${1:-}" = "--rannikko" ]; then
    shift
    ALUE="--rannikko"
    while [ "${1:-}" = "--leveys" ] || [ "${1:-}" = "--osa" ]; do
        ALUE="$ALUE $1 $2"; shift 2
    done
fi

kello() { date '+%H:%M:%S'; }
vaihe() { echo; echo "=== $(kello)  $* ==="; }

ALKU=$(date +%s)

# Tarkistetaan edellytykset ETUKATEEN: tuntien ajon kaatuminen puuttuvaan
# avaimeen tai kirjastoon on turhauttavaa.
vaihe "0/4  Edellytysten tarkistus"

# VIRTUAALIYMPARISTO AKTIVOIDAAN ITSE jos sellainen on projektin vieressa
# eika mikaan ole jo aktiivinen. Ilman tata unohtunut aktivointi nayttaa
# puuttuvilta kirjastoilta, ja "pip install -r requirements.txt" -neuvo
# johtaa Ubuntulla umpikujaan: PEP 668 estaa asennuksen jarjestelman
# Pythoniin, ja --break-system-packages rikkoisi apt-paketteja. Paketit
# ovat jo olemassa - vain polku puuttuu.
if [ -z "${VIRTUAL_ENV:-}" ]; then
    for v in .venv venv ../.venv; do
        if [ -f "$v/bin/activate" ]; then
            # shellcheck disable=SC1090
            . "$v/bin/activate"
            echo "  aktivoitiin virtuaaliymparisto: $v"
            break
        fi
    done
fi

python3 -u - <<'PY'
import sys
puuttuu = []
for m in ("numpy", "scipy", "cv2", "rasterio", "geopandas", "pyproj", "PIL",
          "laspy", "lazrs", "pandas", "pyogrio"):
    try:
        __import__(m)
    except ImportError:
        puuttuu.append(m)
print(f"  python {sys.version.split()[0]}  ({sys.prefix})")
if puuttuu:
    print(f"  PUUTTUU: {', '.join(puuttuu)}")
    if sys.prefix == sys.base_prefix:
        print("  Virtuaaliymparisto EI ole aktiivinen eika sellaista loytynyt.")
        print("  Luo ja asenna:")
        print("      python3 -m venv .venv && . .venv/bin/activate")
        print("      pip install -r requirements.txt")
        print("  ALA kayta 'pip install --break-system-packages' - se asentaa")
        print("  jarjestelman Pythoniin ja voi rikkoa apt-paketteja.")
    else:
        print("  Virtuaaliymparisto on aktiivinen mutta paketteja puuttuu:")
        print("      pip install -r requirements.txt")
    raise SystemExit(1)
print("  kirjastot ok")
PY
if [ ! -f "$HOME/.mml-api-key" ]; then
    echo "  VIRHE: ~/.mml-api-key puuttuu."
    echo "  Luo avain MML:n OmaTilissa ja tallenna:"
    echo "      printf '%s' 'AVAIN' > ~/.mml-api-key && chmod 600 ~/.mml-api-key"
    exit 1
fi
echo "  API-avain loytyy"
echo "  levytilaa vapaana: $(df -h . | tail -1 | awk '{print $4}')"

if [ -n "$ALUE" ]; then
    vaihe "1/4  Lahtoaineiston lataus: $ALUE"
    # Korkeusmalli, peruskartta, hydrografia (meri) ja rakennukset.
    # Jo levylla olevat lehdet ohitetaan.
    python3 -u -m backend.mml_lataus $ALUE

    vaihe "2/4  Laserkeilaus puuttuville tiilille"
    # Raaka LAZ on kymmenia gigatavuja, joten se ladataan lehti kerrallaan,
    # hilautetaan 10 m rasteriksi ja POISTETAAN heti.
    python3 -u - <<'PY'
import time
from backend import lidar, tiles
puuttuu = [t for t in tiles.get_registry() if not lidar.have_lidar(t)]
print(f"  {len(puuttuu)} tiilta ilman laserkeilausta", flush=True)
t_alku = time.time()
for i, tid in enumerate(puuttuu, 1):
    t0 = time.time()
    try:
        lidar.get_or_compute_tile_surface(tid)
        tila = f"ok {time.time()-t0:.0f} s"
    except Exception as e:
        # Yksi epaonnistunut lehti ei saa kaataa koko ajoa - puuttuvalle
        # tiilelle jaa kasvillisuusarvaus, mika on huonompi mutta toimiva.
        tila = f"VIRHE: {type(e).__name__}: {e}"
    kulunut = time.time() - t_alku
    arvio = kulunut / i * (len(puuttuu) - i) / 60
    print(f"  [{i}/{len(puuttuu)}] {tid} {tila}  (n. {arvio:.0f} min jaljella)", flush=True)
PY

    vaihe "3/4  Valimuistin mitatointi"
    # Tiilien lisaaminen muuttaa merimosaiikin muodon, jolloin siihen sidotut
    # valimuistit vanhenevat HILJAA. Per-tiili raw ja lidar sailyvat.
    python3 -u -m backend.mml_lataus --mitatoi
else
    vaihe "1-3/4  Ohitetaan lataus (--bbox/--rannikko ei annettu)"
fi

vaihe "4/4  Staattinen build"
python3 -u build_static.py

KESTO=$(( $(date +%s) - ALKU ))
vaihe "VALMIS  ${KESTO} s = $((KESTO/3600)) h $(((KESTO%3600)/60)) min"
du -sh docs/ 2>/dev/null || true
echo "Tulos on docs/-hakemistossa."
echo
# VEDA, ALA TYONNA. Tyontaminen vaatii sshd:n VASTAANOTTAVASSA paassa, ja
# macOS:n Remote Login on oletuksena pois - "connection refused" porttiin 22.
# Vetaminen kayttaa TAMAN koneen sshd:ta, joka on jo olemassa (siksi tama
# skripti ylipaataan voidaan ajaa taalla).
# Portti luetaan oikeasti kuuntelevasta sshd:sta: se ei valttamatta ole 22
# (talla koneella se on 2222), ja vaara portti vihjeessa on sama umpikuja
# kuin vaara suunta.
SSH_PORTTI=$(ss -tlnH 2>/dev/null | awk '/sshd|:22 |:2222 /{split($4,a,":"); print a[length(a)]; exit}')
echo "Siirto: VEDA vastaanottavasta koneesta, ala tyonna taalta:"
echo "  rsync -avz -e 'ssh -p ${SSH_PORTTI:-22}' \\"
echo "    $(whoami)@$(hostname):$(pwd)/docs/  KOHDEPOLKU/docs/"
echo
# --delete PUUTTUU TAHALLAAN. Kynnysarvot ovat GLOBAALEJA (lasketaan koko
# tiilirekisterin yli), joten kahden eri ajon docs/-hakemistoja EI VOI
# yhdistaa: kuvat olisivat eri aineistosta kuin kynnys jolla ne varitetaan.
# Joko korvaa kohde kokonaan tai aja alueet yhtena ajona.
echo "HUOM: ala yhdista kahden eri ajon docs/-hakemistoja. Kynnysarvot"
echo "      lasketaan koko tiilirekisterin yli, joten sekoitus varittaa"
echo "      kartan vaarin. Korvaa kohde kokonaan tai aja alueet yhdessa."
