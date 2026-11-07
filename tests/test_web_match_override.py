"""Tests für Matcher-Korrektur per Button im Web-Dashboard (Issue #498)."""
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from sc_digger.db import TrackDB
from sc_digger.web.database import save_match_override
from tests.test_web_tracks_ui import PASSWORD, client_for

ORIGIN = {"origin": "http://testserver"}


def _seed_db(tmp_path: Path) -> Path:
    pfad = tmp_path / "tracks.sqlite"
    with TrackDB(pfad):
        pass
    return pfad


def _seed_digest(tmp_path: Path, sc_id: int = 12345) -> None:
    _seed_db(tmp_path)
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        db.execute(
            "INSERT INTO seen VALUES (?, ?, ?, ?)",
            (sc_id, f"https://soundcloud.com/artist/{sc_id}", "Artist - Super Track", "2026-11-07 10:00:00"),
        )


# ------------------------------------------------------------- save_match_override


def test_save_match_override_types_and_notes(tmp_path: Path):
    pfad = _seed_db(tmp_path)

    # already_in_collection setzen
    assert save_match_override(pfad, 100, "already_in_collection") is True
    with TrackDB(pfad) as db:
        assert db.get_match_override(100) == "already_in_collection"
        row = db.db.execute("SELECT note FROM match_overrides WHERE sc_id = 100").fetchone()
        assert row["note"] == "Dashboard"

    # Überschreiben mit not_duplicate
    assert save_match_override(pfad, 100, "not_duplicate") is True
    with TrackDB(pfad) as db:
        assert db.get_match_override(100) == "not_duplicate"

    # Löschen per clear
    assert save_match_override(pfad, 100, "clear") is True
    with TrackDB(pfad) as db:
        assert db.get_match_override(100) is None

    # Clear auf nicht existierenden Eintrag liefert True
    assert save_match_override(pfad, 9999, "clear") is True


def test_save_match_override_invalid_inputs(tmp_path: Path):
    pfad = _seed_db(tmp_path)

    # sc_id als bool, string, float oder None
    assert save_match_override(pfad, True, "already_in_collection") is False
    assert save_match_override(pfad, False, "already_in_collection") is False
    assert save_match_override(pfad, "100", "already_in_collection") is False  # type: ignore[arg-type]
    assert save_match_override(pfad, 100.5, "already_in_collection") is False  # type: ignore[arg-type]
    assert save_match_override(pfad, None, "already_in_collection") is False  # type: ignore[arg-type]

    # Ungültige Typen
    assert save_match_override(pfad, 100, "ungueltig") is False
    assert save_match_override(pfad, 100, "") is False
    assert save_match_override(pfad, 100, 123) is False  # type: ignore[arg-type]

    with TrackDB(pfad) as db:
        assert db.get_match_override(100) is None


def test_save_match_override_missing_file_or_db_error(tmp_path: Path):
    nicht_vorhanden = tmp_path / "nicht_da.sqlite"
    assert save_match_override(nicht_vorhanden, 100, "not_duplicate") is False
    assert not nicht_vorhanden.exists()

    # Verzeichnis statt Datei
    ordner = tmp_path / "ein_ordner"
    ordner.mkdir()
    assert save_match_override(ordner, 100, "not_duplicate") is False

    # DB-Fehler simulieren
    pfad = _seed_db(tmp_path)
    with patch("sc_digger.web.database.TrackDB", side_effect=sqlite3.OperationalError("disk locked")):
        assert save_match_override(pfad, 100, "not_duplicate") is False


# ------------------------------------------------------------- Route /api/match-override


def test_api_match_override_200_success(tmp_path: Path):
    pfad = _seed_db(tmp_path)
    client = client_for(tmp_path)

    for o_type in ("already_in_collection", "not_duplicate", "clear"):
        r = client.post(
            "/api/match-override",
            json={"sc_id": 55, "override_type": o_type},
            auth=("sc", PASSWORD),
            headers=ORIGIN,
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "sc_id": 55, "override_type": o_type}


def test_api_match_override_400_validation(tmp_path: Path):
    _seed_db(tmp_path)
    client = client_for(tmp_path)

    # Nicht-JSON Body
    r = client.post(
        "/api/match-override",
        content="kein json",
        headers={"Content-Type": "application/json", **ORIGIN},
        auth=("sc", PASSWORD),
    )
    assert r.status_code == 400
    assert r.json()["ok"] is False

    # Payload kein Dict
    r = client.post(
        "/api/match-override",
        json=[{"sc_id": 55, "override_type": "clear"}],
        auth=("sc", PASSWORD),
        headers=ORIGIN,
    )
    assert r.status_code == 400
    assert r.json()["ok"] is False

    # Ungültige Parameter
    invalid_payloads = [
        {"sc_id": True, "override_type": "clear"},
        {"sc_id": False, "override_type": "not_duplicate"},
        {"sc_id": "55", "override_type": "not_duplicate"},
        {"sc_id": 55, "override_type": "invalid"},
        {"sc_id": 55},
        {"override_type": "clear"},
        {},
    ]
    for p in invalid_payloads:
        res = client.post(
            "/api/match-override",
            json=p,
            auth=("sc", PASSWORD),
            headers=ORIGIN,
        )
        assert res.status_code == 400
        assert res.json()["ok"] is False


def test_api_match_override_401_auth(tmp_path: Path):
    _seed_db(tmp_path)
    client = client_for(tmp_path)
    body = {"sc_id": 55, "override_type": "clear"}

    # Ohne Auth
    r = client.post("/api/match-override", json=body, headers=ORIGIN)
    assert r.status_code == 401

    # Falsches Passwort
    r = client.post("/api/match-override", json=body, auth=("sc", "falsch"), headers=ORIGIN)
    assert r.status_code == 401


def test_api_match_override_403_origin(tmp_path: Path):
    _seed_db(tmp_path)
    client = client_for(tmp_path)
    body = {"sc_id": 55, "override_type": "clear"}

    # Fehlende Origin
    r = client.post("/api/match-override", json=body, auth=("sc", PASSWORD))
    assert r.status_code == 403
    assert r.json() == {"ok": False, "error": "Ungültige Herkunft"}

    # Fremde Origin
    r = client.post(
        "/api/match-override",
        json=body,
        auth=("sc", PASSWORD),
        headers={"origin": "https://evil-site.com"},
    )
    assert r.status_code == 403
    assert r.json() == {"ok": False, "error": "Ungültige Herkunft"}


def test_api_match_override_500_db_error(tmp_path: Path):
    # Datei tracks.sqlite existiert nicht
    client = client_for(tmp_path)
    r = client.post(
        "/api/match-override",
        json={"sc_id": 55, "override_type": "not_duplicate"},
        auth=("sc", PASSWORD),
        headers=ORIGIN,
    )
    assert r.status_code == 500
    assert r.json() == {"ok": False, "sc_id": 55, "override_type": "not_duplicate"}


# ------------------------------------------------------------- Digest Template Rendering


def test_digest_page_renders_match_override_buttons(tmp_path: Path):
    _seed_digest(tmp_path, sc_id=98765)
    client = client_for(tmp_path)

    r = client.get("/digest", auth=("sc", PASSWORD))
    assert r.status_code == 200

    # match-fix Container mit sc_id
    assert '<div class="match-fix" data-sc-id="98765"' in r.text

    # Drei Buttons vorhanden
    assert 'data-override="already_in_collection"' in r.text
    assert "Schon in Sammlung" in r.text
    assert 'data-override="not_duplicate"' in r.text
    assert "Kein Duplikat" in r.text
    assert 'data-override="clear"' in r.text
    assert "Zurücknehmen" in r.text

    # JavaScript-Handler vorhanden
    assert "sendMatchOverride" in r.text
