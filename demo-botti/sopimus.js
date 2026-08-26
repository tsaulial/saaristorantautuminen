// JAETUN SOPIMUKSEN KOLMAS KOPIO.
//
// Sama laskenta on backend/pipeline.py:ssa (shelter_score_from_fetch) ja
// frontend/index.html:ssa (shelterScoreFromFetch). Nyt myos tassa.
//
// KOLME KOPIOTA ON VELKA, EI RATKAISU. Sen ainoa suoja on
// demo-botti/testaa_sopimus.py, joka ajaa taman tiedoston nodessa ja vertaa
// tulosta Pythoniin koko ruudukolla. JOS SE TESTI EI OLE VIHREA, BOTTI EI
// SAA NAYTTAA YHTAAN AALLONKORKEUTTA.
//
// Vakiot EI kirjoiteta tahan vaan luetaan piirteet.json:ista, jonka vie.py
// kopioi suoraan pipeline.py:sta. Nain yksi kolmesta eroamisen tavasta -
// eri vakiot - on suljettu pois rakenteellisesti.

function shelteredWind(windSpeed, fetchM, obstacleH, V) {
  // Etaisyydeksi pyyhkaisymatkan PUOLIVALI: aallokko kasvaa koko matkan
  // varrella eika vain rannassa.
  if (!(obstacleH > 0)) return windSpeed;
  const factor = 1 - V.WIND_SHELTER_MAX
    * Math.exp(-(fetchM / 2) / (V.WIND_SHELTER_K * obstacleH));
  return windSpeed * factor;
}

// Merkitseva aallonkorkeus metreina. Tama on se luku jonka kayttaja nakee.
function aallonkorkeus(fetchM, windSpeed, obstacleH, V) {
  return V.WAVE_COEFF * shelteredWind(windSpeed, fetchM, obstacleH, V)
         * Math.sqrt(fetchM);
}

function shelterScoreFromFetch(fetchM, windSpeed, obstacleH, V) {
  const hs = aallonkorkeus(fetchM, windSpeed, obstacleH, V);
  const s = (V.SHELTER_ROUGH_M - hs) / (V.SHELTER_ROUGH_M - V.SHELTER_CALM_M);
  return s < 0 ? 0 : s > 1 ? 1 : s;
}

// TUULEN SUUNTA INTERPOLOIDAAN KAHDEN SEKTORIN VALILLA, ei kvantisoida
// takaisin sektoriksi. Tuotanto tekee niin, ja jos botti pyoristaisi, sama
// ranta saisi kaksi eri aallonkorkeutta riippuen siita kumpaa sovellusta
// katsoo.
//
// Tuuli tulee suunnasta `bearing`; aallokko syntyy siita suunnasta, joten
// pyyhkaisymatka luetaan samasta suunnasta.
function sektoriPaino(bearing, sektoreita) {
  const aste = 360 / sektoreita;
  const pos = (((bearing % 360) + 360) % 360) / aste;
  const lo = Math.floor(pos) % sektoreita;
  const hi = (lo + 1) % sektoreita;
  return { lo, hi, t: pos - Math.floor(pos) };
}

function suureSuunnasta(taulu, i, bearing, sektoreita) {
  const { lo, hi, t } = sektoriPaino(bearing, sektoreita);
  const a = taulu[i * sektoreita + lo], b = taulu[i * sektoreita + hi];
  return a * (1 - t) + b * t;
}

if (typeof module !== "undefined") {
  module.exports = { shelteredWind, aallonkorkeus, shelterScoreFromFetch,
                     sektoriPaino, suureSuunnasta };
}
