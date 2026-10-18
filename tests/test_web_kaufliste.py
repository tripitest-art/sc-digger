"""Eigene Tests für die interaktive Kaufliste im Web-Dashboard (Issue #338).

Deckt die rein lesende Abfrage ``query_store_items`` (Sortierung, Suche, Feedback-Filter,
Limit/Offset, fehlende DB) sowie die Routen ``/kaufliste`` und ``GET /api/kaufliste`` ab.
Die Bewertung selbst nutzt den bestehenden Pfad ``POST /api/feedback`` (Issue #229).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.database import query_store_items

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-338"


def make_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def client_for(tmp_path: Path) -> TestClient:
    return TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))


def seed_items(db_path: Path, count: int = 3) -> None:
    """Legt Store-Items mit klar getrennten ``last_seen``-Werten an (neueste zuerst)."""
    with TrackDB(db_path) as db:
        for index in range(1, count + 1):
            db.upsert_store_item(
                sc_id=100 + index,
                title=f"Track {index}",
                artist=f"Artist {index}",
                purchase_url=f"https://label{index}.bandcamp.com/track/{index}",
                purchase_title="Bandcamp",
            )
            db.db.execute(
                "UPDATE store_items SET last_seen = ? WHERE sc_id = ?",
                (f"2026-10-{index:02d} 08:00:00", 100 + index),
            )
        db.db.commit()


def test_query_store_items_missing_db_returns_empty(tmp_path: Path):
    """Fehlende Datei wirft nicht und wird nicht angelegt (rein lesend)."""
    db_path = tmp_path / "fehlt.sqlite"
    result = query_store_items(db_path)
    assert result.total == 0
    assert result.items == []
    assert not db_path.exists()


def test_query_store_items_leere_kaufliste(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path):
        pass
    result = query_store_items(db_path)
    assert result.total == 0
    assert result.items == []


def test_query_store_items_sortiert_neueste_zuerst(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    seed_items(db_path, count=3)
    result = query_store_items(db_path)
    assert result.total == 3
    assert [item["sc_id"] for item in result.items] == [103, 102, 101]
    assert result.items[0]["purchase_title"] == "Bandcamp"


def test_query_store_items_feedback_filter(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    seed_items(db_path, count=3)
    with TrackDB(db_path) as db:
        db.set_sc_feedback(101, "like")
        db.set_sc_feedback(102, "later")

    likes = query_store_items(db_path, feedback="like")
    assert likes.total == 1
    assert likes.items[0]["sc_id"] == 101
    assert likes.items[0]["feedback"] == "like"

    later = query_store_items(db_path, feedback="later")
    assert [item["sc_id"] for item in later.items] == [102]

    unrated = query_store_items(db_path, feedback="none")
    assert [item["sc_id"] for item in unrated.items] == [103]

    # Unbekannter Wert ist kein Fehler, sondern einfach kein Treffer.
    assert query_store_items(db_path, feedback="unbekannt").total == 3


def test_query_store_items_pagination_limit_offset(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    seed_items(db_path, count=4)

    first = query_store_items(db_path, limit=2, offset=0)
    assert first.total == 4
    assert [item["sc_id"] for item in first.items] == [104, 103]

    second = query_store_items(db_path, limit=2, offset=2)
    assert second.total == 4
    assert [item["sc_id"] for item in second.items] == [102, 101]

    beyond = query_store_items(db_path, limit=2, offset=10)
    assert beyond.total == 4
    assert beyond.items == []

    # limit=0 liefert absichtlich keine Zeile, total bleibt die Gesamtzahl.
    none_page = query_store_items(db_path, limit=0)
    assert none_page.total == 4
    assert none_page.items == []


def test_query_store_items_suche_als_wortsuche(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_store_item(
            sc_id=201, title="Raw Tool", artist="Viper XXL",
            purchase_url="https://label.bandcamp.com/track/raw-tool",
        )
        db.upsert_store_item(
            sc_id=202, title="Acid Drop", artist="Svetec",
            purchase_url="https://beatport.com/track/acid-drop/123",
        )

    assert [it["sc_id"] for it in query_store_items(db_path, search="svetec").items] == [202]
    assert [it["sc_id"] for it in query_store_items(db_path, search="raw tool").items] == [201]
    # Mehrere Wörter müssen alle passen (UND), wie in der Digest-Suche.
    assert query_store_items(db_path, search="svetec raw").total == 0
    # LIKE-Sonderzeichen werden escaped und nicht als Platzhalter ausgewertet.
    assert query_store_items(db_path, search="%").total == 0


def test_query_store_items_ohne_feedback_tabelle(tmp_path: Path):
    """Alte Track-DB ohne sc_feedback: Items kommen mit feedback=None, Filter wirken."""
    db_path = tmp_path / "alt.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE store_items (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "sc_id INTEGER UNIQUE NOT NULL, title TEXT NOT NULL, artist TEXT NOT NULL, "
            "purchase_url TEXT NOT NULL, purchase_title TEXT, "
            "first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        db.execute(
            "INSERT INTO store_items (sc_id, title, artist, purchase_url) "
            "VALUES (301, 'Alt', 'Bestand', 'https://label.bandcamp.com/track/alt')"
        )

    result = query_store_items(db_path)
    assert result.total == 1
    assert result.items[0]["feedback"] is None
    assert query_store_items(db_path, feedback="none").total == 1
    assert query_store_items(db_path, feedback="like").total == 0


def test_kaufliste_seite_leer(tmp_path: Path):
    client = client_for(tmp_path)
    response = client.get("/kaufliste", auth=("sc", PASSWORD))
    assert response.status_code == 200
    assert "Keine Store-Tracks für diese Filtereinstellungen gefunden." in response.text
    # Ohne Treffer keine Bewertungsknöpfe und keine Paginierung.
    assert 'class="feedback-group"' not in response.text
    assert "Nächste &rarr;" not in response.text


def test_kaufliste_seite_paginierung_und_feedback(tmp_path: Path):
    seed_items(tmp_path / "tracks.sqlite", count=3)
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.set_sc_feedback(103, "like")

    client = client_for(tmp_path)
    response = client.get("/kaufliste?limit=2", auth=("sc", PASSWORD))
    assert response.status_code == 200
    assert "Track 3" in response.text
    assert "Track 2" in response.text
    assert "Track 1" not in response.text
    assert "Seite 1 von 2" in response.text
    assert 'href="/kaufliste?limit=2&amp;offset=2"' in response.text
    assert 'data-sc-id="103"' in response.text
    assert 'data-value="like" title="Gefällt mir"' in response.text
    assert "sendFeedback" in response.text

    second = client.get("/kaufliste?limit=2&offset=2", auth=("sc", PASSWORD))
    assert second.status_code == 200
    assert "Track 1" in second.text
    assert "Track 3" not in second.text
    assert "Seite 2 von 2" in second.text


def test_kaufliste_seite_filtert_feedback(tmp_path: Path):
    seed_items(tmp_path / "tracks.sqlite", count=2)
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.set_sc_feedback(102, "dislike")

    client = client_for(tmp_path)
    response = client.get("/kaufliste?feedback=none", auth=("sc", PASSWORD))
    assert response.status_code == 200
    assert "Track 1" in response.text
    assert "Track 2" not in response.text


def test_kaufliste_escaped_html(tmp_path: Path):
    """Artist/Titel aus fremden Feeds dürfen kein Markup in die Seite schmuggeln."""
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_store_item(
            sc_id=401,
            title="<script>alert('x')</script>",
            artist="<img src=x onerror=alert(1)>",
            purchase_url="https://evil.example/track?a=1&b=2",
        )

    client = client_for(tmp_path)
    response = client.get("/kaufliste", auth=("sc", PASSWORD))
    assert response.status_code == 200
    assert "<script>alert('x')</script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "<img src=x onerror" not in response.text
    assert "&lt;img src=x onerror" in response.text
    # URL-Parameter bleiben escapt, der Link bleibt aber nutzbar.
    assert "https://evil.example/track?a=1&amp;b=2" in response.text


def test_kaufliste_verlinkt_nur_http_schemata(tmp_path: Path):
    """Kauf-URLs aus fremden Feeds dürfen kein aktives Schema wie javascript: werden."""
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_store_item(
            sc_id=402,
            title="Böser Link",
            artist="Evil",
            purchase_url="javascript:alert(1)",
        )

    client = client_for(tmp_path)
    response = client.get("/kaufliste", auth=("sc", PASSWORD))
    assert response.status_code == 200
    assert "Böser Link" in response.text
    assert "javascript:alert(1)" not in response.text


def test_api_kaufliste_filter_und_auth(tmp_path: Path):
    seed_items(tmp_path / "tracks.sqlite", count=2)
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.set_sc_feedback(101, "like")

    client = client_for(tmp_path)
    assert client.get("/api/kaufliste").status_code == 401

    data = client.get("/api/kaufliste?feedback=like", auth=("sc", PASSWORD)).json()
    assert data["total"] == 1
    assert data["items"][0]["sc_id"] == 101
    assert data["items"][0]["feedback"] == "like"

    page = client.get(
        "/api/kaufliste", params={"limit": 1, "offset": 1}, auth=("sc", PASSWORD)
    ).json()
    assert page["total"] == 2
    assert [item["sc_id"] for item in page["items"]] == [101]
