from pathlib import Path
import sqlite3
from sc_digger.web.database import DigestItem, DigestSearchResult, query_digest

def test_query_digest_fuzzy_matches_inbox_dir(tmp_path: Path):
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"
    inbox_dir = tmp_path / "inbox"
    sub = inbox_dir / "155-160" / "8A"
    sub.mkdir(parents=True)
    # Datei ohne SoundCloud-ID im Namen
    (sub / "True Damage - Know My Name (Voidstorm Hardtechno Edit).mp3").write_bytes(b"dummy")

    with sqlite3.connect(state_db) as db:
        db.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        db.execute(
            "INSERT INTO seen VALUES (501, 'https://soundcloud.com/dj/track', 'True Damage - Know My Name (Voidstorm Hardtechno Edit)', '2026-10-14 05:30:00')"
        )

    res = query_digest(state_db, track_db, inbox_dir=inbox_dir)
    assert res.total == 1
    item = res.items[0]
    assert item.sc_id == 501
    assert item.in_inbox is True
    assert item.in_collection is False

def test_query_digest_fuzzy_matches_tracks_db(tmp_path: Path):
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"

    with sqlite3.connect(state_db) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        db.execute("INSERT INTO seen VALUES (502, 'url2', 'Svetec - Acid Storm (Original Mix)', '2026-10-14 06:00:00')")

    with sqlite3.connect(track_db) as db:
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY, path TEXT, artist TEXT, title TEXT, status TEXT)")
        # In TrackDB ohne ID im Pfad eingetragen
        db.execute("INSERT INTO tracks VALUES (1, '/music/inbox/150/8A/Svetec - Acid Storm.wav', 'Svetec', 'Acid Storm', 'inbox')")

    res = query_digest(state_db, track_db)
    assert res.total == 1
    item = res.items[0]
    assert item.sc_id == 502
    assert item.in_inbox is True

def test_finish_file_persists_inbox_track_to_db(tmp_path: Path, monkeypatch):
    from sc_digger import main as m
    from sc_digger.models import Config, Track
    from sc_digger.db import TrackDB, TrackStatus
    cfg = Config.load("config.yaml")
    track_db = tmp_path / "tracks.sqlite"
    cfg.raw["state"] = {"track_db_path": str(track_db)}
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    audio = inbox / "test.wav"
    audio.write_bytes(b"RIFF dummy")

    t = Track(
        id=777,
        title="Know My Name (Voidstorm Hardtechno Edit)",
        url="https://soundcloud.com/voidstorm/track",
        artist="True Damage",
        artist_url="",
        created_at="",
        duration_ms=180000,
        genre="Hardtechno",
        tags=[],
        description="",
        bpm=155.0,
        plays=0,
        likes=0,
        reposts=0,
        comments=0,
        downloadable=True,
        has_downloads_left=True,
        purchase_url=None,
        purchase_title=None,
    )
    t.quality_report = {"ok": True, "clipped": False, "bitrate_kbps": 320.0, "ext": "wav"}
    monkeypatch.setattr(m, "finalize_quality", lambda trk, p, inb, c: p)
    monkeypatch.setattr(m, "analyze_track", lambda p: {"bpm": 155.0, "key_camelot": "8A", "key_name": "Am"})
    monkeypatch.setattr(m, "write_tags", lambda *a, **k: None)
    monkeypatch.setattr(m, "organize", lambda p, inb, **k: p)

    res = m.finish_file(t, audio, inbox, cfg)
    assert res == audio

    with TrackDB(track_db) as db:
        rec = db.get_track_by_path(audio)
        assert rec is not None
        assert rec.status == TrackStatus.INBOX.value
        assert rec.artist == "True Damage"
        assert rec.title == "Know My Name (Voidstorm Hardtechno Edit)"
