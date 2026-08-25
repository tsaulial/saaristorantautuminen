#!/usr/bin/env python3
"""Yleisen mallin pistemaara ehdokkaille.

Yleinen malli vastaa kysymykseen "onko tama hyva ranta" kiintein painoin.
Oma malli vastaa kysymykseen "onko tama MINUN rantani". Molempia tarvitaan:
yleinen on ainoa mahdollinen vastaus uudelle kayttajalle, se on
selitettavissa, ja se on riippumaton koetinkivi jota vasten oman mallin voi
arvioida (clusterplan.md).

Pistemaara lasketaan pipeline.score_from_components:lla eli TASAN samalla
funktiolla kuin tuotannon kartan varit - ei uudella kaavalla. Maskina
NO_SHELTER_MASK: suojaisuus on dynaaminen (tuuli) eika kuulu staattiseen
arvioon.

ERO kelpoala.py:hyn: siella sama pistemaara SUMMATAAN pinta-alaksi
("kuinka paljon kelvollista rantaa"), tassa se KESKIARVOISTETAAN
("kuinka hyvaa se on"). Kaksi eri kysymysta samasta luvusta.

TULOS EI MENE PIIRREVEKTORIIN. Se on mallin ulostulo, ei syote; vektoriin
lisattyna oma malli olisi osittain kopio yleisesta.
"""
import pathlib
import sys

import numpy as np

JUURI = pathlib.Path(__file__).resolve().parent.parent
ULOS = JUURI / "demo-oma"
PX_M = 2.0
IKKUNA_SADE_M = 25.0


def main():
    sys.path.insert(0, str(JUURI))
    sys.path.insert(0, str(ULOS))
    from backend import pipeline
    from piirteet import testialueen_tiilet, tiilen_kanavat

    maski = pipeline.NO_SHELTER_MASK
    print(f"tekijamaski {maski} (jyrkkyys | etaisyys | kallio | suo)")

    from backend import score_engine
    d = np.load(ULOS / "ehdokkaat_raaka.npz", allow_pickle=True)
    xs, ys = d["x"], d["y"]
    ulos = np.full(len(xs), np.nan, dtype=np.float32)
    # Nelja termia joilla pistemaara selitetaan. Kolme ensimmaista
    # SUMMAUTUVAT TASAN pistemaaraan; nelias on suon vieman osuuden suuruus.
    OSAT = ("jyrkkyys", "etaisyys", "kallio", "suo_menetys")
    osat = np.full((len(xs), len(OSAT)), np.nan, dtype=np.float32)

    r_px = int(round(IKKUNA_SADE_M / PX_M))
    yy, xx = np.mgrid[-r_px:r_px + 1, -r_px:r_px + 1]
    kiekko = (yy * yy + xx * xx) <= r_px * r_px

    tiilet = testialueen_tiilet()
    for k, (tile_id, b) in enumerate(tiilet, 1):
        osuu = np.where((xs >= b[0]) & (xs < b[2]) & (ys >= b[1]) & (ys < b[3]))[0]
        if not len(osuu):
            continue
        kan = tiilen_kanavat(tile_id)
        if kan is None:
            continue
        jy = kan["jyrkkyys"].astype(np.float64)
        et = kan["etaisyys"].astype(np.float64)
        kal = (kan["bitit"] & 1) > 0
        suo = (kan["bitit"] & 2) > 0
        pisteet = pipeline.score_from_components(jy, et, kal, suo, maski)

        # TERMIEN OSUUDET, samasta rakenteesta kuin score_from_components:
        #   score = (w_j*jyrkkyys + w_e*etaisyys + w_k*kallio) / W
        #   suo:    score * SWAMP_PENALTY_FACTOR
        # Kertova rangaistus jaetaan kaikkiin termeihin, jolloin ne
        # summautuvat TASAN pistemaaraan - selitys ei voi olla eri mielta
        # kuin luku jota se selittaa.
        Wsum = (score_engine.SLOPE_WEIGHT + score_engine.DIST_WEIGHT
                + pipeline.ROCK_WEIGHT)
        rangaistus = np.where(suo, pipeline.SWAMP_PENALTY_FACTOR, 1.0)
        t_jy = score_engine.SLOPE_WEIGHT * (jy / 255.0) / Wsum * rangaistus
        t_et = score_engine.DIST_WEIGHT * (et / 255.0) / Wsum * rangaistus
        t_kal = pipeline.ROCK_WEIGHT * np.where(
            kal, pipeline.ROCK_SCORE_YES, pipeline.ROCK_SCORE_NO) / Wsum * rangaistus
        # Suon vieman osuuden suuruus: paljonko pistemaara olisi ilman sita.
        t_suo = (t_jy + t_et + t_kal) / np.maximum(rangaistus, 1e-9) - (t_jy + t_et + t_kal)
        H, W = kan["puskuri"].shape
        cc = ((xs[osuu] - b[0]) / PX_M).astype(np.int32)
        rr = ((b[3] - ys[osuu]) / PX_M).astype(np.int32)
        for j, i in enumerate(osuu):
            r0, r1 = rr[j] - r_px, rr[j] + r_px + 1
            c0, c1 = cc[j] - r_px, cc[j] + r_px + 1
            if r0 < 0 or c0 < 0 or r1 > H or c1 > W:
                continue
            # KESKIARVO VAIN PUSKURIPIKSELEISTA. Ilman maskia mukaan tulisi
            # vesi ja sisamaa, joiden pistemaara ei tarkoita mitaan - ne
            # laimentaisivat tuloksen kohti nollaa sita enemman mita
            # kapeampi ranta on.
            m = kan["puskuri"][r0:r1, c0:c1] & kiekko
            if not m.any():
                continue
            ulos[i] = float(pisteet[r0:r1, c0:c1][m].mean())
            for oi, taulu in enumerate((t_jy, t_et, t_kal, t_suo)):
                osat[i, oi] = float(taulu[r0:r1, c0:c1][m].mean())
        print(f"  [{k}/{len(tiilet)}] {tile_id}: {len(osuu)}", flush=True)

    ulos = np.nan_to_num(ulos, nan=0.0)
    osat = np.nan_to_num(osat, nan=0.0)
    np.savez_compressed(ULOS / "yleinen.npz", pisteet=ulos, osat=osat,
                        osien_nimet=np.array(OSAT))
    print(f"\n{len(ulos):,} ehdokasta, yleinen pistemaara:")
    for p in (0, 10, 25, 50, 75, 90, 100):
        print(f"  p{p:<3d} {np.percentile(ulos, p):.3f}")
    # Todennus: kolmen termin summan ON oltava pistemaara.
    summa = osat[:, :3].sum(axis=1)
    poikkeama = np.abs(summa - ulos).max()
    print(f"\ntermien summa vs pistemaara, suurin poikkeama: {poikkeama:.2e}")
    print("  " + ("OK" if poikkeama < 1e-5 else "VIRHE: selitys ei summaudu pistemaaraan"))
    print(f"suon menetys: {100*(osat[:,3] > 0.001).mean():.1f} % ehdokkaista, "
          f"suurin {osat[:,3].max():.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
