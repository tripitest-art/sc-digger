"""Eigene Tests zum Web-Werkzeug Issue #231: Mix-Finder und On-Demand-URL-Scanner.

Ergänzen den Akzeptanztest um die Web-Schicht:
* ``GET /mix`` rendert Formular und Treffer, meldet ungültige Keys und fehlende BPM,
  und legt als reine Leseroute keine Datei an bzw. verändert keine.
* ``POST /api/check-url`` prüft Anmeldung, Herkunft und Payload, wandelt Playlist- und
  Track-Links über einen Fake-Client in JSON um und schreibt nichts.

Kein Netz: ``SoundCloudClient`` wird im Modul ``sc_digger.web.app`` durch einen Fake ersetzt.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import sc_digger.web.app as app_module
from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config, Track
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-231"
# _same_origin vergleicht den netloc von Origin/Referer mit dem Host-Header.
ORIGIN = {"origin": "http://testserver"}


def make_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def client_for(tmp_path: Path) -> TestClient:
    return TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))


def make_track(
    track_id: int,
    *,
    title: str = "Track",
    artist: str = "Svetec",
    description: str = "",
    tags: list[str] | None = None,
    downloadable: bool = False,
    has_downloads_left: bool = True,
    purchase_url: str | None = None,
) -> Track:
    return Track(
        id=track_id,
        title=title,
        url=f"https://soundcloud.com/svetec/{track_id}",
        artist=artist,
        artist_url=f"https://soundcloud.com/{artist.lower()}",
        created_at="2026-10-12T08:00:00Z",
        duration_ms=300_000,
        genre="Schranz",
        tags=tags or [],
        description=description,
        bpm=None,
        plays=100,
        likes=10,
        reposts=1,
        comments=2,
        downloadable=downloadable,
        has_downloads_left=has_downloads_left,
        purchase_url=purchase_url,
        purchase_title=None,
    )


def fake_sc(monkeypatch, *, kind="playlist", playlist=("Playlist", []), station=("Station", [])):
    """Ersetzt SoundCloudClient im Web-Modul; kein Netz, feste Antworten je ``kind``."""

    class _Fake:
        def __init__(self, request_delay: float = 0.6):
            pass

        def resolve_kind(self, url: str):
            return kind

        def playlist_tracks(self, url: str, limit: int | None = None):
            return playlist

        def track_station(self, url: str, limit: int = 50):
            return station

    monkeypatch.setattr(app_module, "SoundCloudClient", _Fake)


def seed_track_db(tmp_path: Path) -> Path:
    """Track-DB mit passenden und unpassenden Keys sowie einem Inbox-Track."""
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path="/music/Schranz/155/[101] Svetec - Acid Kick.wav", mtime=1.0, size=1000,
            artist="Svetec", title="Acid Kick", bpm=155.0, key_camelot="8A",
            quality_status=QualityStatus.OK, status=TrackStatus.ARCHIVE,
        ))
        db.upsert_track(TrackRecord(
            path="/music/Schranz/156/[102] Svetec - Zweiter Kick.wav", mtime=1.0, size=1000,
            artist="Svetec", title="Zweiter Kick", bpm=156.0, key_camelot="9A",
            quality_status=QualityStatus.OK, status=TrackStatus.ARCHIVE,
        ))
        db.upsert_track(TrackRecord(
            path="/music/Schranz/155/[103] Kobosil - Weit Weg.wav", mtime=1.0, size=1000,
            artist="Kobosil", title="Weit Weg", bpm=155.0, key_camelot="1A",
            quality_status=QualityStatus.OK, status=TrackStatus.ARCHIVE,
        ))
        db.upsert_track(TrackRecord(
            path="/music/inbox/[104] Viper XXL - Frisch.wav", mtime=1.0, size=1000,
            artist="Viper XXL", title="Frisch", bpm=155.0, key_camelot="8A",
            quality_status=QualityStatus.OK, status=TrackStatus.INBOX,
        ))
    return db_path


def seed_seen(tmp_path: Path, ids: list[int]) -> Path:
    db_path = tmp_path / "seen.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        for track_id in ids:
            db.execute(
                "INSERT INTO seen VALUES (?, ?, ?, ?)",
                (track_id, f"https://soundcloud.com/svetec/{track_id}", "Alt", "2026-10-01 08:00:00"),
            )
    return db_path


def get_mix(tmp_path: Path, query: str = ""):
    return client_for(tmp_path).get(f"/mix{query}", auth=("sc", PASSWORD))


def post_check(tmp_path: Path, payload, *, headers=None):
    return client_for(tmp_path).post(
        "/api/check-url", json=payload, auth=("sc", PASSWORD), headers=headers or ORIGIN
    )


# ------------------------------------------------------------------ GET /mix
def test_mix_requires_auth(tmp_path):
    r = client_for(tmp_path).get("/mix")
    assert r.status_code == 401
    assert "basic" in r.headers["www-authenticate"].lower()


def test_mix_without_key_shows_form_and_hint(tmp_path):
    r = get_mix(tmp_path)
    assert r.status_code == 200
    assert 'action="/mix"' in r.text
    for name in ("key", "bpm", "tol"):
        assert f'name="{name}"' in r.text
    assert "Camelot-Key" in r.text
    assert "nicht gefunden" not in r.text


def test_mix_navigation_tab_is_active(tmp_path):
    r = get_mix(tmp_path)
    assert 'href="/mix" class="nav-tab active"' in r.text


def test_mix_finds_compatible_tracks_only(tmp_path):
    seed_track_db(tmp_path)
    r = get_mix(tmp_path, "?key=8A&bpm=155&tol=2")
    assert r.status_code == 200
    assert "3 Treffer" in r.text
    assert "Acid Kick" in r.text        # 8A, exakt
    assert "Zweiter Kick" in r.text     # 9A, Nachbar im Rad
    assert "Frisch" in r.text           # 8A in der Inbox zählt wie beim Telegram-Befehl /mix
    assert "Weit Weg" not in r.text     # 1A, nicht kompatibel


def test_mix_respects_bpm_tolerance(tmp_path):
    """Ohne Toleranz-Angabe gilt der Standard 3 BPM; außerhalb bleibt der Track draußen."""
    seed_track_db(tmp_path)
    narrow = get_mix(tmp_path, "?key=8A&bpm=150&tol=1")
    assert "Zweiter Kick" not in narrow.text   # 156 liegt 6 BPM entfernt
    assert "Acid Kick" not in narrow.text      # 155 liegt 5 BPM entfernt
    assert "Keine passenden Tracks" in narrow.text


def test_mix_invalid_key_reports_error(tmp_path):
    seed_track_db(tmp_path)
    r = get_mix(tmp_path, "?key=13A&bpm=155")
    assert r.status_code == 200
    assert "Ungültiger Camelot-Key" in r.text


def test_mix_missing_bpm_reports_error(tmp_path):
    seed_track_db(tmp_path)
    r = get_mix(tmp_path, "?key=8A")
    assert r.status_code == 200
    assert "Ziel-BPM" in r.text


def test_mix_missing_track_db_reports_error_and_creates_nothing(tmp_path):
    """Reine Leseroute: eine fehlende Track-DB wird gemeldet, aber nicht angelegt."""
    r = get_mix(tmp_path, "?key=8A&bpm=155")
    assert r.status_code == 200
    assert "Track-Datenbank nicht lesbar." in r.text
    assert not (tmp_path / "tracks.sqlite").exists()


def test_mix_does_not_modify_track_db(tmp_path):
    db_path = seed_track_db(tmp_path)
    before = db_path.read_bytes()
    get_mix(tmp_path, "?key=8A&bpm=155&tol=2")
    assert db_path.read_bytes() == before


# ------------------------------------------------------------- POST /api/check-url
def test_check_url_requires_auth(tmp_path):
    r = client_for(tmp_path).post(
        "/api/check-url", json={"url": "https://soundcloud.com/x"}, headers=ORIGIN
    )
    assert r.status_code == 401


def test_check_url_rejects_missing_origin(tmp_path):
    r = client_for(tmp_path).post(
        "/api/check-url", json={"url": "https://soundcloud.com/x"}, auth=("sc", PASSWORD)
    )
    assert r.status_code == 403


def test_check_url_rejects_foreign_origin(tmp_path):
    r = client_for(tmp_path).post(
        "/api/check-url", json={"url": "https://soundcloud.com/x"},
        auth=("sc", PASSWORD), headers={"origin": "https://boese.example"},
    )
    assert r.status_code == 403


def test_check_url_rejects_missing_url(tmp_path):
    r = post_check(tmp_path, {"url": "  "})
    assert r.status_code == 400
    assert r.json()["ok"] is False


def test_check_url_rejects_non_dict_payload(tmp_path):
    r = post_check(tmp_path, ["https://soundcloud.com/x"])
    assert r.status_code == 400


def test_check_url_rejects_malformed_json(tmp_path):
    r = client_for(tmp_path).post(
        "/api/check-url", content=b"{kein json",
        auth=("sc", PASSWORD),
        headers={**ORIGIN, "content-type": "application/json"},
    )
    assert r.status_code == 400


def test_check_url_playlist_returns_tracks(tmp_path, monkeypatch):
    seed_track_db(tmp_path)
    seed_seen(tmp_path, [555])
    native = make_track(101, title="Acid Kick", description="155 bpm", downloadable=True)
    stream = make_track(999, title="Nur Stream")
    seen = make_track(555, title="Schon Gesehen")
    fake_sc(monkeypatch, kind="playlist", playlist=("Kick Playlist", [native, stream, seen]))

    r = post_check(tmp_path, {"url": "https://soundcloud.com/svetec/sets/kick"})

    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["kind"] == "playlist"
    assert data["source"] == "Playlist: Kick Playlist"
    assert data["total"] == 3
    by_id = {t["id"]: t for t in data["tracks"]}
    assert by_id[101]["bpm"] == 155.0           # aus "155 bpm" im Text geschätzt
    assert by_id[101]["download_kind"] == "native"
    assert by_id[101]["status"] == "in Sammlung"
    assert by_id[999]["download_kind"] == "none"
    assert by_id[999]["status"] == "neu"
    assert by_id[555]["status"] == "gesehen"


def test_check_url_track_returns_station(tmp_path, monkeypatch):
    station_track = make_track(201, title="Station Treffer")
    fake_sc(monkeypatch, kind="track", station=("Svetec – Acid Kick", [station_track]))

    r = post_check(tmp_path, {"url": "https://soundcloud.com/svetec/acid-kick"})

    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["kind"] == "track"
    assert data["source"] == "Station zu: Svetec – Acid Kick"
    assert data["tracks"][0]["title"] == "Station Treffer"


def test_check_url_unknown_kind_is_unprocessable(tmp_path, monkeypatch):
    fake_sc(monkeypatch, kind="user")
    r = post_check(tmp_path, {"url": "https://soundcloud.com/svetec"})
    assert r.status_code == 422
    assert r.json()["ok"] is False


def test_check_url_writes_nothing(tmp_path, monkeypatch):
    """Reine Leseroute: der Link landet nicht in der seen-DB, sie wird nicht einmal angelegt."""
    fake_sc(monkeypatch, kind="playlist", playlist=("P", [make_track(1)]))
    r = post_check(tmp_path, {"url": "https://soundcloud.com/svetec/sets/x"})
    assert r.status_code == 200
    assert not (tmp_path / "seen.sqlite").exists()
    assert not (tmp_path / "tracks.sqlite").exists()


def test_check_url_dedupes_repeated_ids(tmp_path, monkeypatch):
    fake_sc(monkeypatch, kind="playlist", playlist=(
        "P", [make_track(7, title="Einmal"), make_track(7, title="Einmal")],
    ))
    r = post_check(tmp_path, {"url": "https://soundcloud.com/svetec/sets/x"})
    assert r.status_code == 200
    assert r.json()["total"] == 1
