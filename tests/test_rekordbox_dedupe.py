"""Eigene Tests für Dubletten-Erkennung per SoundCloud-ID (Issue #194)."""
from datetime import datetime
from pathlib import Path

from sc_digger.rekordbox import (
    InboxTrack,
    dedupe_by_soundcloud_id,
    soundcloud_id_from_name,
)


def mk(rel: str) -> InboxTrack:
    return InboxTrack(path=Path("/music/inbox") / rel, title="T", artist="A", genre="", bpm=160.0,
                      key="6A", comment="", duration_s=90.0, size=1, added=datetime(2026, 9, 29))


def test_id_mit_leerzeichen_und_unicode():
    assert soundcloud_id_from_name("[12345] Nächtliche Grüße – Mix.wav") == "12345"
    assert soundcloud_id_from_name("[12345] Nächtliche Grüße – Mix_3.wav") == "12345"


def test_mehrere_verschiedene_ids_gleichzeitig():
    tracks = [
        mk("[11] A - X.wav"),
        mk("[22] B - Y.wav"),
        mk("[11] A - X_1.wav"),
        mk("[22] B - Y_1.wav"),
        mk("ohne.wav"),
    ]
    out = dedupe_by_soundcloud_id(tracks)
    assert [t.path.name for t in out] == ["[11] A - X.wav", "[22] B - Y.wav", "ohne.wav"]


def test_id_nicht_am_anfang_wird_ignoriert():
    assert soundcloud_id_from_name("Mix [123] Ende.wav") is None
    assert soundcloud_id_from_name("prefix[123].wav") is None


def test_original_gewinnt_auch_wenn_kleinster_pfad_kopie_ist():
    tracks = [mk("a/[42] A - X_1.wav"), mk("b/[42] A - X.wav")]
    out = dedupe_by_soundcloud_id(tracks)
    assert [t.path.as_posix() for t in out] == ["/music/inbox/b/[42] A - X.wav"]
