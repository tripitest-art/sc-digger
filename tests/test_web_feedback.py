"""Eigene Tests für das interaktive Feedback im Web (Issue #229).

Deckt den einzigen schreibenden Pfad der Web-Oberfläche ab: ``save_track_feedback`` legt die
Bewertung in ``sc_feedback`` der Track-DB ab (Upsert je SoundCloud-ID, wirft nie), und
``POST /api/feedback`` nimmt sie als JSON entgegen – mit Grundprüfung, Origin-Prüfung (CSRF,
wie beim Konfigeditor) und JSON-Status. Dazu: die Seiten /digest und /tracks liefern die
Bewertungsknöpfe aus, aber nur wenn die SoundCloud-ID im Dateinamen steckt.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import save_track_feedback

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-229"
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


def feedback_table(path: Path) -> None:
    """Minimale Track-DB: nur die Tabelle, in die save_track_feedback schreibt."""
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE sc_feedback "
            "(sc_id INTEGER PRIMARY KEY, url TEXT, value TEXT, updated_at TEXT)"
        )


def seed_track_db(tmp_path: Path, *, sc_id: int | None) -> Path:
    """Legt eine echte Track-DB an; sc_id None -> Dateiname ohne ``[<id>]``-Präfix."""
    db_path = tmp_path / "tracks.sqlite"
    name = f"[{sc_id}] Svetec - Acid Kick.wav" if sc_id is not None else "Svetec - Acid Kick.wav"
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path=f"/music/inbox/{name}", mtime=1.0, size=1000,
            artist="Svetec", title="Acid Kick", format="wav", bitrate_kbps=1411.0,
            cutoff_hz=21000, quality_status=QualityStatus.OK, bpm=150.0,
            key_camelot="8A", status=TrackStatus.INBOX,
            source_url="https://soundcloud.com/svetec/acid-kick",
        ))
    return db_path


def seed_digest_dbs(tmp_path: Path, *, sc_id: int = 101, feedback: str | None = None) -> None:
    """State-DB mit einem gesehenen Track und Track-DB mit passender Bewertung."""
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        db.execute(
            "INSERT INTO seen VALUES (?, ?, ?, ?)",
            (sc_id, f"https://soundcloud.com/svetec/{sc_id}",
             "Svetec - Acid Kick", "2026-10-12 08:00:00"),
        )
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute(
            "CREATE TABLE sc_feedback "
            "(sc_id INTEGER PRIMARY KEY, url TEXT, value TEXT, updated_at TEXT)"
        )
        if feedback is not None:
            db.execute(
                "INSERT INTO sc_feedback (sc_id, value, updated_at) VALUES (?, ?, ?)",
                (sc_id, feedback, "2026-10-12 09:00:00"),
            )


# ----------------------------------------------------------- save_track_feedback
def test_save_track_feedback_writes_value(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    feedback_table(db_path)

    assert save_track_feedback(db_path, 12345, "like") is True

    with sqlite3.connect(db_path) as db:
        row = db.execute("SELECT value, updated_at FROM sc_feedback WHERE sc_id=12345").fetchone()
    assert row is not None
    assert row[0] == "like"
    assert row[1]                                    # CURRENT_TIMESTAMP gesetzt


def test_save_track_feedback_upsert_keeps_one_row(tmp_path):
    """Letzte Wahl gewinnt: wie im Bot überschreibt eine neue Bewertung die alte."""
    db_path = tmp_path / "tracks.sqlite"
    feedback_table(db_path)

    assert save_track_feedback(db_path, 7, "like") is True
    assert save_track_feedback(db_path, 7, "dislike") is True

    with sqlite3.connect(db_path) as db:
        rows = db.execute("SELECT value FROM sc_feedback WHERE sc_id=7").fetchall()
    assert rows == [("dislike",)]


def test_save_track_feedback_invalid_value_changes_nothing(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    feedback_table(db_path)

    assert save_track_feedback(db_path, 1, "vielleicht") is False

    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM sc_feedback").fetchone()[0] == 0


def test_save_track_feedback_missing_file_creates_none(tmp_path):
    """Modus rw: ein unbekannter Pfad wird nicht als leere Datenbank angelegt."""
    db_path = tmp_path / "gibts-nicht.sqlite"

    assert save_track_feedback(db_path, 1, "later") is False
    assert not db_path.exists()


def test_save_track_feedback_missing_table_returns_false(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE tracks (id INTEGER PRIMARY KEY)")

    assert save_track_feedback(db_path, 1, "like") is False


# ------------------------------------------------------------- POST /api/feedback
def test_api_feedback_requires_auth(tmp_path):
    client = client_for(tmp_path)
    r = client.post("/api/feedback", json={"sc_id": 1, "value": "like"}, headers=ORIGIN)
    assert r.status_code == 401


def test_api_feedback_rejects_missing_origin(tmp_path):
    """Schreibende Route: ohne Origin/Referer kein Schreibzugriff (CSRF-Schutz)."""
    seed_track_db(tmp_path, sc_id=101)
    client = client_for(tmp_path)
    r = client.post("/api/feedback", json={"sc_id": 101, "value": "like"},
                    auth=("sc", PASSWORD))
    assert r.status_code == 403


def test_api_feedback_rejects_foreign_origin(tmp_path):
    seed_track_db(tmp_path, sc_id=101)
    client = client_for(tmp_path)
    r = client.post("/api/feedback", json={"sc_id": 101, "value": "like"},
                    auth=("sc", PASSWORD), headers={"origin": "https://boese.example"})
    assert r.status_code == 403


def test_api_feedback_saves_valid_value(tmp_path):
    db_path = seed_track_db(tmp_path, sc_id=101)
    client = client_for(tmp_path)

    r = client.post("/api/feedback", json={"sc_id": 101, "value": "like"},
                    auth=("sc", PASSWORD), headers=ORIGIN)

    assert r.status_code == 200
    assert r.json() == {"ok": True, "sc_id": 101, "value": "like"}
    with sqlite3.connect(db_path) as db:
        row = db.execute("SELECT value FROM sc_feedback WHERE sc_id=101").fetchone()
    assert row == ("like",)


def test_api_feedback_rejects_invalid_value(tmp_path):
    db_path = seed_track_db(tmp_path, sc_id=101)
    client = client_for(tmp_path)

    r = client.post("/api/feedback", json={"sc_id": 101, "value": "kaputt"},
                    auth=("sc", PASSWORD), headers=ORIGIN)

    assert r.status_code == 400
    with sqlite3.connect(db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM sc_feedback").fetchone()[0] == 0


def test_api_feedback_rejects_non_numeric_sc_id(tmp_path):
    seed_track_db(tmp_path, sc_id=101)
    client = client_for(tmp_path)

    r = client.post("/api/feedback", json={"sc_id": "keine-zahl", "value": "like"},
                    auth=("sc", PASSWORD), headers=ORIGIN)

    assert r.status_code == 400


def test_api_feedback_rejects_non_dict_payload(tmp_path):
    seed_track_db(tmp_path, sc_id=101)
    client = client_for(tmp_path)

    r = client.post("/api/feedback", json=["like"],
                    auth=("sc", PASSWORD), headers=ORIGIN)

    assert r.status_code == 400


def test_api_feedback_rejects_malformed_json(tmp_path):
    seed_track_db(tmp_path, sc_id=101)
    client = client_for(tmp_path)

    r = client.post("/api/feedback", content=b"{kein json",
                    auth=("sc", PASSWORD),
                    headers={**ORIGIN, "content-type": "application/json"})

    assert r.status_code == 400


def test_api_feedback_missing_track_db_returns_error(tmp_path):
    """Ohne Track-DB kann nicht gespeichert werden: 500 statt stiller Erfolg."""
    client = client_for(tmp_path)                      # keine tracks.sqlite angelegt

    r = client.post("/api/feedback", json={"sc_id": 101, "value": "like"},
                    auth=("sc", PASSWORD), headers=ORIGIN)

    assert r.status_code == 500
    assert r.json()["ok"] is False
    assert not (tmp_path / "tracks.sqlite").exists()


# ------------------------------------------------------------------ Knöpfe in HTML
def test_digest_page_renders_feedback_buttons(tmp_path):
    seed_digest_dbs(tmp_path, sc_id=101, feedback="like")
    client = client_for(tmp_path)

    r = client.get("/digest", auth=("sc", PASSWORD))

    assert r.status_code == 200
    assert 'data-feedback-cell data-sc-id="101"' in r.text
    for value in ("like", "dislike", "later"):
        assert f'data-value="{value}"' in r.text
    # Vorhandene Like-Bewertung ist als aktiv markiert
    assert 'data-value="like" title="Gefällt mir"' in r.text
    assert "feedback-btn active" in r.text
    assert "sendFeedback" in r.text


def test_digest_page_offers_kaufliste_quick_filter(tmp_path):
    seed_digest_dbs(tmp_path, sc_id=101)
    client = client_for(tmp_path)

    r = client.get("/digest", auth=("sc", PASSWORD))

    assert 'href="/digest?download_kind=store"' in r.text


def test_tracks_page_renders_feedback_buttons(tmp_path):
    seed_track_db(tmp_path, sc_id=101)
    client = client_for(tmp_path)

    r = client.get("/tracks", auth=("sc", PASSWORD))

    assert r.status_code == 200
    assert "<th>Feedback</th>" in r.text
    assert 'data-feedback-cell data-sc-id="101"' in r.text
    assert 'data-value="dislike"' in r.text
    assert "sendFeedback" in r.text


def test_tracks_page_without_sc_id_shows_placeholder(tmp_path):
    """Dateiname ohne ``[<sc_id>]`` kann nicht bewertet werden: nur ein Strich."""
    seed_track_db(tmp_path, sc_id=None)
    client = client_for(tmp_path)

    r = client.get("/tracks", auth=("sc", PASSWORD))

    assert r.status_code == 200
    assert "data-feedback-cell data-sc-id" not in r.text
    assert "<th>Feedback</th>" in r.text


def test_tracks_page_feedback_column_changes_nothing_in_db(tmp_path):
    """Rendern bleibt lesend: die Track-DB darf sich durch GET /tracks nicht ändern."""
    db_path = seed_track_db(tmp_path, sc_id=101)
    before = db_path.read_bytes()
    client = client_for(tmp_path)

    client.get("/tracks", auth=("sc", PASSWORD))

    assert db_path.read_bytes() == before


def test_api_feedback_payload_is_json_serializable(tmp_path):
    """Der Erfolgspfad liefert reines JSON (kein Template, keine Seite)."""
    seed_track_db(tmp_path, sc_id=101)
    client = client_for(tmp_path)

    r = client.post("/api/feedback", json={"sc_id": 101, "value": "later"},
                    auth=("sc", PASSWORD), headers=ORIGIN)

    assert r.headers["content-type"].startswith("application/json")
    assert json.loads(r.text) == {"ok": True, "sc_id": 101, "value": "later"}
