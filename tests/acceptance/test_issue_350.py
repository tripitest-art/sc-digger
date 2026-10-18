from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.audio import parse_range_header, resolve_playable_path

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-audio"
AUTH = ("", PASSWORD)


def _setup(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    outside = tmp_path / "fremd"
    outside.mkdir()
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw.setdefault("download", {})
    cfg.raw["download"]["inbox_dir"] = str(inbox)
    cfg.raw["download"]["collection_dir"] = str(tmp_path / "sammlung")
    return cfg, inbox, outside


def _add(tmp_path: Path, path: Path) -> int:
    from sc_digger.db import TrackRecord

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(path=str(path), mtime=1.0, size=path.stat().st_size))
        return db.get_track_by_path(path).id


def test_resolve_playable_path_inside_and_outside(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    ok = inbox / "a.mp3"
    ok.write_bytes(b"x" * 10)
    assert resolve_playable_path(str(ok), [inbox]) == ok.resolve()
    assert resolve_playable_path(str(inbox / ".." / "a.mp3"), [inbox]) is None
    txt = inbox / "a.txt"
    txt.write_text("x")
    assert resolve_playable_path(str(txt), [inbox]) is None
    assert resolve_playable_path(str(inbox / "fehlt.mp3"), [inbox]) is None


def test_parse_range_header():
    assert parse_range_header(None, 100) is None
    assert parse_range_header("bytes=0-9", 100) == (0, 9)
    assert parse_range_header("bytes=90-", 100) == (90, 99)
    assert parse_range_header("bytes=-10", 100) == (90, 99)
    assert parse_range_header("bytes=50-500", 100) == (50, 99)
    for bad in ("bytes=100-", "bytes=9-1", "bytes=0-1,5-6", "items=0-1", "bytes=a-b"):
        try:
            parse_range_header(bad, 100)
        except ValueError:
            continue
        raise AssertionError(f"ValueError erwartet für {bad!r}")


def test_audio_route_full_and_range(tmp_path: Path):
    cfg, inbox, _ = _setup(tmp_path)
    f = inbox / "t.mp3"
    f.write_bytes(bytes(range(100)))
    tid = _add(tmp_path, f)
    client = TestClient(create_app(cfg, password=PASSWORD))
    r = client.get(f"/api/audio/{tid}", auth=AUTH)
    assert r.status_code == 200
    assert r.content == bytes(range(100))
    assert r.headers["accept-ranges"] == "bytes"
    r = client.get(f"/api/audio/{tid}", auth=AUTH, headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.content == bytes(range(10, 20))
    assert r.headers["content-range"] == "bytes 10-19/100"
    r = client.get(f"/api/audio/{tid}", auth=AUTH, headers={"Range": "bytes=500-"})
    assert r.status_code == 416


def test_audio_route_security(tmp_path: Path):
    cfg, inbox, outside = _setup(tmp_path)
    secret = outside / "geheim.mp3"
    secret.write_bytes(b"geheim")
    tid = _add(tmp_path, secret)
    client = TestClient(create_app(cfg, password=PASSWORD))
    assert client.get(f"/api/audio/{tid}", auth=AUTH).status_code == 404
    assert client.get("/api/audio/99999", auth=AUTH).status_code == 404
    f = inbox / "t.mp3"
    f.write_bytes(b"abc")
    ok_id = _add(tmp_path, f)
    assert client.get(f"/api/audio/{ok_id}").status_code == 401
