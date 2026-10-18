"""Sammlungs-Kandidaten im Curator-Mining (Issue #333)."""
from pathlib import Path

import pytest

from sc_digger import bot, schema
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config


def _add(db: TrackDB, path: str, artist: str | None, status: str = "archive",
         adopted_at: str | None = None) -> None:
    db.upsert_track(TrackRecord(
        path=path, mtime=1.0, size=10, status=status, adopted_at=adopted_at, artist=artist,
    ))


def _cfg(db_path: Path, followed: list[str] | None = None,
         reference: list[str] | None = None, **cm: object) -> Config:
    return Config({
        "state": {"track_db_path": str(db_path)},
        "search": {"followed_users": followed or [], "reference_accounts": reference or []},
        "curator_mining": {"min_appearances": 2, "max_likers_per_track": 50, **cm},
    })


class FakeSC:
    def __init__(self, likers: dict[int, list[dict]] | None = None) -> None:
        self.likers = likers or {}

    def get_likers(self, sc_id, max_results=50):
        return self.likers.get(sc_id, [])

    def get_reposters(self, sc_id, max_results=50):
        return []


# ================================================================= DB-Abfrage

def test_collection_counts_archive_and_adopted_only(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        _add(db, "a1.wav", "Svetec")
        _add(db, "a2.wav", "Svetec")
        # Inbox zählt nur mit Übernahme-Zeitstempel
        _add(db, "a3.wav", "Svetec", status="inbox", adopted_at="2026-10-18 12:00:00")
        _add(db, "a4.wav", "Svetec", status="inbox")
        # Abgelehnt ohne Übernahme zählt nicht zur Sammlung
        _add(db, "a5.wav", "Svetec", status="rejected")

        assert db.get_top_collection_artists(min_tracks=3) == [("Svetec", 3)]


def test_collection_is_case_insensitive_and_keeps_common_spelling(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        _add(db, "b1.wav", "O.B.I.")
        _add(db, "b2.wav", "o.b.i.")
        _add(db, "b3.wav", "O.B.I.")

        assert db.get_top_collection_artists(min_tracks=3) == [("O.B.I.", 3)]


def test_collection_ignores_blank_artist_names(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        for i in range(3):
            _add(db, f"blank{i}.wav", "")
            _add(db, f"none{i}.wav", None)
            _add(db, f"spaces{i}.wav", "   ")

        assert db.get_top_collection_artists(min_tracks=1) == []


def test_collection_sorts_by_count_then_name_and_limits(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        for i in range(4):
            _add(db, f"b{i}.wav", "Boss")
        for i in range(3):
            _add(db, f"a{i}.wav", "Alpha")
            _add(db, f"c{i}.wav", "Charlie")

        assert db.get_top_collection_artists(min_tracks=3) == [
            ("Boss", 4), ("Alpha", 3), ("Charlie", 3),
        ]
        assert db.get_top_collection_artists(min_tracks=3, limit=2) == [
            ("Boss", 4), ("Alpha", 3),
        ]


def test_min_collection_tracks_field_in_schema():
    field = schema.get_field("curator_mining.min_collection_tracks")
    assert field is not None
    assert field.kind == "int"
    assert field.min == 1


# ================================================================= Bot-Ausgabe

def test_collection_candidates_appear_without_liked_tracks(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        for i in range(3):
            _add(db, f"s{i}.wav", "Svetec")

    text = bot.run_curator_mining(FakeSC(), _cfg(tmp_path / "t.sqlite"))

    assert "📚 Sammlungs-Favoriten (häufige Artists deiner Sammlung)" in text
    assert "• Svetec (soundcloud.com/svetec) – 3 Tracks" in text
    assert text.candidates == [("svetec", "Svetec", 3)]


def test_already_followed_artist_is_filtered(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        for i in range(3):
            _add(db, f"v{i}.wav", "Viper XXL")
            _add(db, f"r{i}.wav", "Ref Artist")

    cfg = _cfg(
        tmp_path / "t.sqlite",
        followed=["https://soundcloud.com/Viper-XXL"],
        reference=["Ref Artist"],
    )
    text = bot.run_curator_mining(FakeSC(), cfg)

    assert "Viper XXL" not in text
    assert "Ref Artist" not in text


def test_same_artist_not_listed_in_both_sections(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        for i in range(3):
            _add(db, f"s{i}.wav", "Svetec")
        db.set_sc_feedback(1, "like")

    sc = FakeSC({1: [{"id": 7, "permalink": "svetec", "username": "Svetec"}] * 2})
    text = bot.run_curator_mining(sc, _cfg(tmp_path / "t.sqlite"))

    assert "Curator-Vorschläge (aus gelikten Tracks)" in text
    assert "Sammlungs-Favoriten" not in text
    assert text.candidates == [("svetec", "Svetec", 2)]


def test_collection_threshold_ignores_rare_artist(tmp_path: Path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        _add(db, "one1.wav", "Svetec")
        _add(db, "one2.wav", "Svetec")

    text = bot.run_curator_mining(FakeSC(), _cfg(tmp_path / "t.sqlite", min_collection_tracks=3))

    assert "Svetec" not in text
    assert text == "Keine neuen Curator-Vorschläge."
