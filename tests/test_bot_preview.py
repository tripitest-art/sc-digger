"""Eigene Tests für den /preview-Bot-Befehl und die Track-Suche (Issue #142)."""
from pathlib import Path

from sc_digger import bot
from sc_digger.db import TrackDB, TrackRecord


def _add(db: TrackDB, tmp_path: Path, artist: str, title: str, name: str | None = None):
    p = tmp_path / (name or f"{artist} - {title}.mp3")
    p.write_bytes(b"fake-audio")
    return db.upsert_track(
        TrackRecord(path=str(p), mtime=1.0, size=10, artist=artist, title=title)
    )


def test_search_tracks_underscore_is_literal(tmp_path):
    """Anders als das LIKE-Wildcard _ darf ein Unterstrich im Suchtext nur den
    Unterstrich selbst treffen, nicht ein beliebiges Zeichen."""
    with TrackDB(tmp_path / "t.sqlite") as db:
        axb = _add(db, tmp_path, "Artist", "axb")
        a_b = _add(db, tmp_path, "Artist", "a_b")
        found = db.search_tracks("a_b")
    ids = [r.id for r in found]
    assert ids == [a_b.id]
    assert axb.id not in ids


def test_search_tracks_percent_is_literal(tmp_path):
    """Ein Prozentzeichen im Suchtext ist wörtlich gemeint: "100%" darf nicht
    "100x" treffen (ohne Escape würde % als Wildcard wirken)."""
    with TrackDB(tmp_path / "t.sqlite") as db:
        _add(db, tmp_path, "Artist", "100x")
        pct = _add(db, tmp_path, "Artist", "100%")
        found = db.search_tracks("100%")
    assert [r.id for r in found] == [pct.id]


def test_search_tracks_respects_limit(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        for i in range(8):
            _add(db, tmp_path, "Svetec", f"Track {i:02d}")
        assert len(db.search_tracks("svetec", limit=3)) == 3
        assert len(db.search_tracks("svetec")) == 6  # Default


def test_search_tracks_requires_every_word(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        _add(db, tmp_path, "Svetec", "Raw")
        assert len(db.search_tracks("svetec raw")) == 1
        assert db.search_tracks("svetec gabber") == []
        assert db.search_tracks("") == []


def test_search_tracks_multiple_distinct_ids(tmp_path):
    """Mehrere Treffer müssen eigene IDs behalten, damit /preview sie auflisten kann."""
    with TrackDB(tmp_path / "t.sqlite") as db:
        a = _add(db, tmp_path, "Svetec", "Raw")
        b = _add(db, tmp_path, "Svetec", "Brutal")
        found = db.search_tracks("svetec")
    assert a.id != b.id
    assert {r.id for r in found} == {a.id, b.id}


def test_search_tracks_db_error_returns_empty(tmp_path):
    db = TrackDB(tmp_path / "t.sqlite")
    db.close()
    assert db.search_tracks("svetec") == []


def test_preview_in_help_text():
    assert "/preview" in bot.HELP_TEXT
    assert "Sprachnachricht" in bot.HELP_TEXT
