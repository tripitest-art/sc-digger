"""Eigene Tests für getaggte Datei bevorzugen bei Dubletten (Issue #198)."""
from datetime import datetime
from pathlib import Path

from sc_digger.rekordbox import InboxTrack, dedupe_by_soundcloud_id


def mk(rel: str, key: str = "", bpm: float | None = None) -> InboxTrack:
    return InboxTrack(path=Path("/music/inbox") / rel, title="T", artist="A", genre="", bpm=bpm,
                      key=key, comment="", duration_s=90.0, size=1, added=datetime(2026, 9, 29))


def test_gleichstand_bei_allen_kriterien_kleinster_pfad_gewinnt():
    # Beide getaggt, beide Original-Stem, gleiche Tiefe -> kleinster Pfad gewinnt.
    tracks = [mk("b/[42] A - X.wav", key="6A"), mk("a/[42] A - X.wav", key="6A")]
    out = dedupe_by_soundcloud_id(tracks)
    assert [t.path.as_posix() for t in out] == ["/music/inbox/a/[42] A - X.wav"]


def test_getaggt_schlaegt_ungetaggt_kleinster_pfad_bei_gleichstand():
    # Getaggt, aber größerer Pfad, schlägt ungetaggt mit kleinerem Pfad.
    tracks = [mk("a/[43] A - Y.wav"), mk("z/[43] A - Y_1.wav", bpm=150.0)]
    out = dedupe_by_soundcloud_id(tracks)
    assert [t.path.as_posix() for t in out] == ["/music/inbox/z/[43] A - Y_1.wav"]
