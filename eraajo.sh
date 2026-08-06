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
#   ./eraajo.sh --bbox MINX MINY MAXX MAXY      # uusi alue
#   ./eraajo.sh                                 # vain rakenna nykyisesta
#
# ESIMERKKI (Helsingin edusta)
#   ./eraajo.sh --bbox 370000 6658000 402000 6682000
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

BBOX=""
if [ "${1:-}" = "--bbox" ]; then
    shift
    BBOX="$1 $2 $3 $4"
    shift 4
fi

kello() { date '+%H:%M:%S'; }
vaihe() { echo; echo "=== $(kello)  $* ==="; }

ALKU=$(date +%s)

# Tarkistetaan edellytykset ETUKATEEN: tuntien ajon kaatuminen puuttuvaan
# avaimeen tai kirjastoon on turhauttavaa.
vaihe "0/4  Edellytysten tarkistus"
python3 -u - <<'PY'
import sys
puuttuu = []
for m in ("numpy", "scipy", "cv2", "rasterio", "geopandas", "pyproj", "PIL",
          "laspy", "lazrs", "pandas", "pyogrio"):
    try:
        __import__(m)
    except ImportError:
        puuttuu.append(m)
print(f"  python {sys.version.split()[0]}")
if puuttuu:
    print(f"  PUUTTUU: {', '.join(puuttuu)}  ->  pip install -r requirements.txt")
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

if [ -n "$BBOX" ]; then
    vaihe "1/4  Lahtoaineiston lataus: $BBOX"
    # Korkeusmalli, peruskartta, hydrografia (meri) ja rakennukset.
    # Jo levylla olevat lehdet ohitetaan.
    python3 -u -m backend.mml_lataus --bbox $BBOX

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
    vaihe "1-3/4  Ohitetaan lataus (--bbox ei annettu)"
fi

vaihe "4/4  Staattinen build"
python3 -u build_static.py

KESTO=$(( $(date +%s) - ALKU ))
vaihe "VALMIS  ${KESTO} s = $((KESTO/3600)) h $(((KESTO%3600)/60)) min"
du -sh docs/ 2>/dev/null || true
echo "Tulos on docs/-hakemistossa. Siirto takaisin esim:"
echo "  rsync -avz --delete docs/ KAYTTAJA@TAMA-KONE:polku/docs/"
