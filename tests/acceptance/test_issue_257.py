from pathlib import Path
import sqlite3
from sc_digger.models import Config, Track
from sc_digger.web.app import _scan_url
from sc_digger.web.database import query_digest

def test_query_digest_enriches_engagement_snapshots(tmp_path: Path):
    state_db = tmp_path / "seen.sqlite"
    track_db = tmp_path / "tracks.sqlite"
    with sqlite3.connect(state_db) as sdb:
        sdb.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)")
        sdb.execute("INSERT INTO seen (id, url, title, reported_at) VALUES (123, 'https://soundcloud.com/artist/track', 'Artist - Track', '2026-10-14 12:00:00')")
    with sqlite3.connect(track_db) as tdb:
        tdb.execute(
            "CREATE TABLE track_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, sc_id INTEGER NOT NULL, artist TEXT, title TEXT, plays INTEGER, likes INTEGER, reposts INTEGER, comments INTEGER, recorded_at TEXT)"
        )
        tdb.execute(
            "INSERT INTO track_snapshots (sc_id, artist, title, plays, likes, reposts, comments, recorded_at) VALUES (123, 'Artist', 'Track', 4200, 310, 45, 12, '2026-10-14 12:00:00')"
        )
    result = query_digest(state_db, track_db)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.sc_id == 123
    assert item.plays == 4200
    assert item.likes == 310
    assert item.reposts == 45

def test_scan_url_includes_engagement_stats(monkeypatch, tmp_path: Path):
    class FakeSC:
        def resolve_kind(self, url):
            return "track"
        def track_station(self, url, limit=50):
            t = Track(
                id=777, title="Station Track", url="https://soundcloud.com/test/track",
                artist="Test Artist", artist_url="https://soundcloud.com/test",
                created_at="2026-10-14T12:00:00Z", duration_ms=180000, genre="Schranz",
                tags=["schranz"], description="", bpm=150.0, plays=12000, likes=850,
                reposts=120, comments=40, downloadable=False, has_downloads_left=False,
                purchase_url=None, purchase_title=None,
            )
            return "Test Station", [t]
    monkeypatch.setattr("sc_digger.web.app.SoundCloudClient", FakeSC)
    cfg = Config({"state": {"track_db_path": str(tmp_path / "t.sqlite"), "db_path": str(tmp_path / "s.sqlite")}})
    res = _scan_url(cfg, "https://soundcloud.com/test/track")
    assert res["total"] == 1
    tr = res["tracks"][0]
    assert tr["plays"] == 12000
    assert tr["likes"] == 850
    assert tr["reposts"] == 120
