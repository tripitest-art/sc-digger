"""Tests für den Verfügbarkeits-Wächter im Web-Dashboard (Issue #494).

Prüft:
- query_watched_tracks (Sortierung, Snapshot- und Store-Fallback, Fehler- und Leertoleranz)
- Route GET /api/watch (Authentifizierung, JSON-Struktur mit total, available_count, items)
- Route GET /kaufliste (Abschnitt data-watch-section, Anzeige verfügbarer und wartender Tracks)
"""
from pathlib import Path

from starlette.testclient import TestClient

from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import query_watched_tracks

PASSWORD = "watch-test-secret"
AUTH = ("sc-digger", PASSWORD)


def _make_cfg(tmp_path: Path) -> tuple[Config, Path]:
    track_db = tmp_path / "tracks.sqlite"
    state_db = tmp_path / "state.sqlite"
    cfg = Config(raw={
        "state": {
            "track_db_path": str(track_db),
            "db_path": str(state_db),
            "health_db_path": str(state_db),
        },
        "download": {"target_dir": str(tmp_path / "inbox")},
        "collection": {"local_dirs": [str(tmp_path / "archive")]},
    })
    return cfg, track_db


def test_query_watched_tracks_missing_file_or_table(tmp_path: Path):
    """Fehlende DB-Datei oder fehlende availability_watch-Tabelle liefert leere Liste."""
    missing = tmp_path / "nonexistent.sqlite"
    assert query_watched_tracks(missing) == []
    assert not missing.exists()

    # DB existiert, aber availability_watch nicht
    empty_db = tmp_path / "empty.sqlite"
    import sqlite3
    with sqlite3.connect(empty_db) as conn:
        conn.execute("CREATE TABLE other_table (id INTEGER PRIMARY KEY)")
    assert query_watched_tracks(empty_db) == []


def test_query_watched_tracks_sorting_order(tmp_path: Path):
    """Sortierung:
    1. Verfügbare Tracks zuerst (available_at IS NOT NULL)
    2. innerhalb verfügbarer nach available_at DESC
    3. danach nicht verfügbare nach last_checked DESC, sc_id ASC.
    """
    _, track_db = _make_cfg(tmp_path)
    with TrackDB(track_db) as db:
        # Nicht verfügbar: last_checked älter
        db.db.execute(
            "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
            "VALUES (10, '2026-11-01 10:00:00', '2026-11-03 10:00:00', 'none', NULL)"
        )
        # Nicht verfügbar: last_checked jünger, sc_id 30
        db.db.execute(
            "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
            "VALUES (30, '2026-11-01 10:00:00', '2026-11-05 10:00:00', 'none', NULL)"
        )
        # Nicht verfügbar: last_checked gleich wie sc_id 30, aber kleinere sc_id 20
        db.db.execute(
            "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
            "VALUES (20, '2026-11-01 10:00:00', '2026-11-05 10:00:00', 'none', NULL)"
        )
        # Verfügbar: available_at älter
        db.db.execute(
            "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
            "VALUES (40, '2026-11-01 10:00:00', '2026-11-04 12:00:00', 'native', '2026-11-04 12:00:00')"
        )
        # Verfügbar: available_at neuer
        db.db.execute(
            "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
            "VALUES (50, '2026-11-01 10:00:00', '2026-11-06 18:00:00', 'hypeddit', '2026-11-06 18:00:00')"
        )
        db.db.commit()

    items = query_watched_tracks(track_db)
    sc_ids = [item["sc_id"] for item in items]
    # Erwartete Reihenfolge:
    # Verfügbare: 50 (neueres available_at), 40 (älteres available_at)
    # Nicht verfügbare: 20 (gleiches last_checked wie 30, kleinere sc_id), 30, 10 (ältestes last_checked)
    assert sc_ids == [50, 40, 20, 30, 10]
    assert items[0]["is_available"] is True
    assert items[1]["is_available"] is True
    assert items[2]["is_available"] is False
    assert items[3]["is_available"] is False
    assert items[4]["is_available"] is False


def test_query_watched_tracks_metadata_fallbacks(tmp_path: Path):
    """Metadaten-Auflösung:
    - Jüngster Snapshot (MAX(id)) liefert artist/title
    - Fehlt Snapshot, wird store_items herangezogen
    - Fehlt beides, artist/title sind None.
    """
    _, track_db = _make_cfg(tmp_path)
    with TrackDB(track_db) as db:
        # sc_id 100: zwei Snapshots -> der mit höherer ID zählt
        db.db.execute(
            "INSERT INTO track_snapshots (id, sc_id, artist, title) "
            "VALUES (1, 100, 'Old Artist', 'Old Title')"
        )
        db.db.execute(
            "INSERT INTO track_snapshots (id, sc_id, artist, title) "
            "VALUES (2, 100, 'New Artist', 'New Title')"
        )
        db.db.execute(
            "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
            "VALUES (100, '2026-11-01 10:00:00', '2026-11-06 10:00:00', 'gate', '2026-11-06 10:00:00')"
        )

        # sc_id 200: kein Snapshot, aber store_items vorhanden
        db.upsert_store_item(
            sc_id=200,
            artist="Store Artist",
            title="Store Title",
            purchase_url="https://bandcamp.com/store/200",
            purchase_title="Bandcamp",
        )
        db.db.execute(
            "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
            "VALUES (200, '2026-11-01 10:00:00', '2026-11-05 10:00:00', 'none', NULL)"
        )

        # sc_id 300: weder Snapshot noch Store-Item
        db.db.execute(
            "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
            "VALUES (300, '2026-11-01 10:00:00', '2026-11-04 10:00:00', 'none', NULL)"
        )
        db.db.commit()

    items = query_watched_tracks(track_db)
    by_id = {item["sc_id"]: item for item in items}

    assert by_id[100]["artist"] == "New Artist"
    assert by_id[100]["title"] == "New Title"

    assert by_id[200]["artist"] == "Store Artist"
    assert by_id[200]["title"] == "Store Title"

    assert by_id[300]["artist"] is None
    assert by_id[300]["title"] is None


def test_api_watch_auth_and_schema(tmp_path: Path):
    """GET /api/watch verlangt Authentifizierung und liefert korrekte Zähler und Struktur."""
    cfg, track_db = _make_cfg(tmp_path)
    with TrackDB(track_db) as db:
        db.db.execute(
            "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
            "VALUES (501, '2026-11-01 10:00:00', '2026-11-06 12:00:00', 'native', '2026-11-06 12:00:00')"
        )
        db.db.execute(
            "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
            "VALUES (502, '2026-11-01 10:00:00', '2026-11-06 12:00:00', 'none', NULL)"
        )
        db.db.commit()

    client = TestClient(create_app(cfg, password=PASSWORD))

    # 401 ohne Auth
    assert client.get("/api/watch").status_code == 401

    # 200 mit Auth
    res = client.get("/api/watch", auth=AUTH)
    assert res.status_code == 200
    payload = res.json()
    assert payload["total"] == 2
    assert payload["available_count"] == 1
    assert len(payload["items"]) == 2
    assert payload["items"][0]["sc_id"] == 501
    assert payload["items"][0]["is_available"] is True
    assert payload["items"][1]["sc_id"] == 502
    assert payload["items"][1]["is_available"] is False


def test_kaufliste_watch_section_rendering(tmp_path: Path):
    """Die Seite /kaufliste rendert den Abschnitt data-watch-section."""
    cfg, track_db = _make_cfg(tmp_path)
    with TrackDB(track_db) as db:
        db.db.execute(
            "INSERT INTO track_snapshots (id, sc_id, artist, title) "
            "VALUES (1, 601, 'Watch Artist', 'Watch Title')"
        )
        db.db.execute(
            "INSERT INTO availability_watch (sc_id, first_checked, last_checked, last_kind, available_at) "
            "VALUES (601, '2026-11-01 10:00:00', '2026-11-06 15:00:00', 'gate', '2026-11-06 15:00:00')"
        )
        db.db.commit()

    client = TestClient(create_app(cfg, password=PASSWORD))
    res = client.get("/kaufliste", auth=AUTH)
    assert res.status_code == 200
    assert "data-watch-section" in res.text
    assert "Watch Artist" in res.text
    assert "Watch Title" in res.text
    assert "Jetzt verfügbar" in res.text
    assert "Verfügbarkeits-Wächter" in res.text


def test_kaufliste_watch_section_empty(tmp_path: Path):
    """Wenn keine Tracks überwacht werden, zeigt der Abschnitt einen sauberen Hinweis."""
    cfg, _ = _make_cfg(tmp_path)
    client = TestClient(create_app(cfg, password=PASSWORD))
    res = client.get("/kaufliste", auth=AUTH)
    assert res.status_code == 200
    assert "data-watch-section" in res.text
    assert "Keine Tracks im Verfügbarkeits-Wächter." in res.text
