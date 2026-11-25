"""Feste Digest-Form: deutsche Zahlen, Kopf mit Verteilung und Track ohne optionale Felder."""
from sc_digger.models import DownloadKind
from sc_digger.output import _de_percent, _de_plays, _fmt_track, digest_head
from tests.test_modes import mk


def test_de_plays_kurz_ab_zehntausend():
    assert _de_plays(0) == "0"
    assert _de_plays(9999) == "9999"
    assert _de_plays(10000) == "10,0k"
    assert _de_plays(48211) == "48,2k"


def test_de_percent_mit_komma():
    assert _de_percent(0.0) == "0,0 %"
    assert _de_percent(0.0392) == "3,9 %"


def test_digest_head_zaehlt_gemischte_gruppen_in_fester_reihenfolge():
    tracks = [mk(1), mk(2), mk(3), mk(4)]
    tracks[0].download_kind = DownloadKind.NATIVE
    tracks[1].download_kind = DownloadKind.STORE
    tracks[2].download_kind = DownloadKind.NATIVE
    tracks[3].download_kind = DownloadKind.NONE
    assert digest_head(tracks, "T") == "<b>T</b>\n2 ✅ · 1 🛒 · 1 🎧"


def test_fmt_track_ohne_optionale_felder_nur_reichweite():
    assert _fmt_track(mk(1)) == (
        '<a href="https://soundcloud.com/a/t1">Artist1 – Track 1</a>\n'
        "    ▶ 1000 · ♥ 5,0 %"
    )
