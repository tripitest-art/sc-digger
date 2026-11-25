"""Eigene Tests für die Helfer des festen Digest-Formats (Issue 739)."""

from sc_digger import output as out
from sc_digger.models import DownloadKind
from tests.test_modes import mk


def test_de_plays():
    assert out._de_plays(0) == "0"
    assert out._de_plays(9999) == "9999"
    assert out._de_plays(10000) == "10,0k"
    assert out._de_plays(48211) == "48,2k"


def test_de_percent():
    assert out._de_percent(0.0) == "0,0 %"
    assert out._de_percent(0.0392) == "3,9 %"


def test_digest_head_mit_gemischten_gruppen():
    tracks = [mk(i) for i in range(1, 8)]
    tracks[0].download_kind = DownloadKind.NATIVE
    tracks[1].download_kind = DownloadKind.NATIVE
    tracks[2].download_kind = DownloadKind.HYPEDDIT
    tracks[3].download_kind = DownloadKind.DROPLOUD
    tracks[4].download_kind = DownloadKind.STORE
    tracks[5].download_kind = DownloadKind.MEGA
    tracks[6].download_kind = DownloadKind.NONE
    assert out.digest_head(tracks) == (
        "<b>sc-digger – 7 neue Treffer</b>\n"
        "2 ✅ · 2 🚪 · 2 🛒 · 1 🎧"
    )


def test_track_ohne_optionale_felder():
    t = mk(1)
    text = out._fmt_track(t)
    zeilen = text.splitlines()
    assert zeilen[0] == '<a href="https://soundcloud.com/a/t1">Artist1 – Track 1</a>'
    assert zeilen[1] == "    ▶ 1000 · ♥ 5,0 %"
    assert len(zeilen) == 2
