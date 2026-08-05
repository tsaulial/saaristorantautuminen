"""Maanmittauslaitoksen tiedostopalvelun yhteinen perusta.

Tassa on VAIN tunnistautuminen ja asynkronisen lataustyon ajaminen. Kaikki
aineistokohtainen (mita ladataan, mihin ja missa muodossa) on kutsujissa:
backend/lidar.py (laserkeilaus) ja backend/mml_lataus.py (korkeusmalli,
peruskartta, maastotietokanta).

API-AVAIN ON SALAISUUS, ja sen kasittelyn on oltava yhdessa paikassa - kaksi
toteutusta tarkoittaisi kaksi tilaisuutta vuotaa se. Saannot:

  - luetaan tiedostosta ~/.mml-api-key, ei ymparistomuuttujasta eika koodista
  - valitetaan HTTP Basic authina, EI URL-parametrina: URL:t paatyvat
    palvelinlokeihin ja komentohistoriaan
  - ei koskaan tulosteta, lokiteta eika sisallyteta virheilmoitukseen
"""

import base64
import json
import time
import urllib.request
from pathlib import Path

OGC_BASE = "https://avoin-paikkatieto.maanmittauslaitos.fi/tiedostopalvelu/ogcproc/v1"
API_KEY_PATH = Path.home() / ".mml-api-key"


def api_key():
    """API-avain tiedostosta. Ei koskaan tulosteta eika palauteta lokiin."""
    if not API_KEY_PATH.exists():
        raise RuntimeError(
            f"API-avainta ei loydy: {API_KEY_PATH}. Luo se OmaTilissa ja "
            f"tallenna:  printf '%s' 'AVAIN' > {API_KEY_PATH} && chmod 600 {API_KEY_PATH}"
        )
    return API_KEY_PATH.read_text().strip()


def request(url, key, data=None, timeout=120):
    req = urllib.request.Request(url, data=data)
    # Basic auth: avain kayttajatunnuksena, salasana tyhja (MML:n ohje).
    # Ei URL-parametrina, koska URL:t paatyvat palvelinlokeihin.
    token = base64.b64encode(f"{key}:".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# Yksi kysely saa sisaltaa enintaan 100 karttalehtea (rajapinnan oma raja).
MAX_SHEETS_PER_JOB = 100


def run_job(process_id, inputs, key=None, poll_s=5, max_polls=240):
    """Ajaa lataustyon ja palauttaa tulosalkiot.

    Rajapinta on asynkroninen: POST luo tyon, jonka tilaa kysellaan kunnes se
    on valmis.

    KAKSI ANSAA jotka maksoivat aikaa ja ovat siksi tassa kirjattuna:
      - POST-rungossa on oltava "id"-kentta. Ilman sita vastaus on HTTP 400
        ILMAN vastausrunkoa, eli mikaan ei kerro mika oli vialla.
      - tuloslistassa on lehtien lisaksi yksi yhteenvetoalkio ILMAN
        path-kenttaa. Suora indeksointi kaatuu siihen, joten .get()."""
    key = key or api_key()
    body = json.dumps({"id": process_id, "inputs": inputs}).encode()
    job = json.loads(request(f"{OGC_BASE}/processes/{process_id}/execution", key, data=body))
    job_id = job["jobID"]

    status = None
    for _ in range(max_polls):
        st = json.loads(request(f"{OGC_BASE}/jobs/{job_id}", key))
        status = st.get("status")
        if status in ("successful", "failed", "dismissed"):
            break
        time.sleep(poll_s)
    if status != "successful":
        raise RuntimeError(f"Lataustyo epaonnistui: {status} {st.get('message', '')}")

    res = json.loads(request(f"{OGC_BASE}/jobs/{job_id}/results", key))
    return [item for item in res.get("results", []) if item.get("path")]


def download_to(item, dest_dir, key, timeout=900):
    """Tulosalkion tiedosto levylle. Palauttaa polun."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / item["path"].rsplit("/", 1)[-1]
    path.write_bytes(request(item["path"], key, timeout=timeout))
    return path
