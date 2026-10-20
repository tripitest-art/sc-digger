"""Eigene Tests für den Quarantäne-Manager im Web-Dashboard (Issue #341).

Deckt die rein lesende Abfrage ``query_rejected_tracks`` (Sortierung, Suche, Limit/Offset,
fehlende DB/Tabelle), die Freigabe ``retry_quarantined_track`` sowie die Routen
``/rejected``, ``GET /api/rejected`` und ``POST /api/retry`` ab – inklusive Origin-Schutz,
ungültiger IDs und HTML-Escaping.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config, Track
from sc_digger.output import State
from sc_digger.web.app import create_app
from sc_digger.web.database import query_rejected_tracks, retry_quarantined_track

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-341"
AUTH = ("sc", PASSWORD)


def make_cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def client_for(tmp_path: Path) -> TestClient:
    return TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))


def make_track(track_id: int, title: str) -> Track:
    return Track(
        id=track_id, title=title, url=f"https://soundcloud.com/test/{track_id}",
        artist="Test Artist", artist_url="", created_at="", duration_ms=180000,
        genre="Schranz", tags=[], description="", bpm=150.0, plays=0, likes=0,
        reposts=0, comments=0, downloadable=True, has_downloads_left=True,
        purchase_url=None, purchase_title=None,
    )


def seed_rejected(state_db: Path, entries: list[tuple[int, str, str, float | None]]) -> None:
    """Legt Quarantäne-Einträge mit klar getrennten Zeitstempeln an (neueste zuerst)."""
    with State(state_db) as state:
        for index, (track_id, title, reason, cutoff) in enumerate(entries, 1):
            state.mark_rejected(
                make_track(track_id, title), reason=reason, cutoff_hz=cutoff
            )
            # Eindeutige, aufsteigende Zeitstempel, damit die Sortierung prüfbar ist.
            state.db.execute(
                "UPDATE rejected SET rejected_at = ? WHERE id = ?",
                (f"2026-10-{index:02d} 08:00:00", track_id),
            )
        state.db.commit()


def test_query_rejected_tracks_missing_db_returns_empty(tmp_path: Path):
    """Fehlende Datei wirft nicht und wird nicht angelegt (rein lesend)."""
    db_path = tmp_path / "fehlt.sqlite"
    assert query_rejected_tracks(db_path) == {"total": 0, "items": []}
    assert not db_path.exists()


def test_query_rejected_tracks_without_table(tmp_path: Path):
    """Alte State-DB ohne rejected-Tabelle: leeres Ergebnis statt Exception."""
    db_path = tmp_path / "alt.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT)")

    assert query_rejected_tracks(db_path) == {"total": 0, "items": []}
    # Die Abfrage legt die Tabelle nicht an.
    with sqlite3.connect(db_path) as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
    assert "rejected" not in names


def test_query_rejected_tracks_leere_quarantaene(tmp_path: Path):
    db_path = tmp_path / "seen.sqlite"
    with State(db_path):
        pass
    result = query_rejected_tracks(db_path)
    assert result["total"] == 0
    assert result["items"] == []


def test_query_rejected_tracks_sortiert_und_liefert_felder(tmp_path: Path):
    db_path = tmp_path / "seen.sqlite"
    seed_rejected(db_path, [
        (701, "Alt", "Spektrum endet bei 14 kHz", 14000.0),
        (702, "Neu", "Clipping True Peak +4.0", 21000.0),
    ])

    result = query_rejected_tracks(db_path)
    assert result["total"] == 2
    assert [item["id"] for item in result["items"]] == [702, 701]
    neuester = result["items"][0]
    assert neuester["url"] == "https://soundcloud.com/test/702"
    assert neuester["reason"] == "Clipping True Peak +4.0"
    assert neuester["cutoff_hz"] == 21000.0
    assert neuester["rejected_at"] == "2026-10-02 08:00:00"
    # Ohne "Artist - Titel" im gespeicherten Text bleibt artist leer.
    assert neuester["artist"] is None


def test_query_rejected_tracks_leitet_uploader_aus_titel_ab(tmp_path: Path):
    """:State speichert nur den Track-Titel; ein "Artist - Titel" wird aufgeteilt."""
    db_path = tmp_path / "seen.sqlite"
    seed_rejected(db_path, [(703, "Svetec - Raw Tool", "Fake Transcode", 13500.0)])

    item = query_rejected_tracks(db_path)["items"][0]
    assert item["artist"] == "Svetec"
    assert item["title"] == "Svetec - Raw Tool"


def test_query_rejected_tracks_suche_und_escaping(tmp_path: Path):
    db_path = tmp_path / "seen.sqlite"
    seed_rejected(db_path, [
        (711, "Brickwall Master", "Clipping", 21000.0),
        (712, "Fake Transcode", "Spektrum endet bei 14 kHz", 14000.0),
    ])

    assert [i["id"] for i in query_rejected_tracks(db_path, search="Brickwall")["items"]] == [711]
    # Grund und URL sind ebenfalls durchsuchbar.
    assert [i["id"] for i in query_rejected_tracks(db_path, search="Clipping")["items"]] == [711]
    assert [i["id"] for i in query_rejected_tracks(db_path, search="test/712")["items"]] == [712]
    # Mehrere Wörter müssen alle passen (UND).
    assert query_rejected_tracks(db_path, search="Brickwall Spektrum")["total"] == 0
    # LIKE-Sonderzeichen werden escaped und nicht als Platzhalter ausgewertet.
    assert query_rejected_tracks(db_path, search="%")["total"] == 0


def test_query_rejected_tracks_pagination_limit_offset(tmp_path: Path):
    db_path = tmp_path / "seen.sqlite"
    seed_rejected(db_path, [
        (721, "Track 1", "", None),
        (722, "Track 2", "", None),
        (723, "Track 3", "", None),
    ])

    first = query_rejected_tracks(db_path, limit=2, offset=0)
    assert first["total"] == 3
    assert [item["id"] for item in first["items"]] == [723, 722]

    second = query_rejected_tracks(db_path, limit=2, offset=2)
    assert second["total"] == 3
    assert [item["id"] for item in second["items"]] == [721]

    beyond = query_rejected_tracks(db_path, limit=2, offset=10)
    assert beyond["total"] == 3
    assert beyond["items"] == []

    # limit=0 liefert absichtlich keine Zeile, total bleibt die Gesamtzahl.
    none_page = query_rejected_tracks(db_path, limit=0)
    assert none_page["total"] == 3
    assert none_page["items"] == []


def test_query_rejected_tracks_limit_gedeckelt(tmp_path: Path):
    """Ein riesiges limit wird auf MAX_LIMIT begrenzt, nicht als Fehler behandelt."""
    db_path = tmp_path / "seen.sqlite"
    seed_rejected(db_path, [(731, "Nur einer", "", 13000.0)])
    assert query_rejected_tracks(db_path, limit=10**6)["total"] == 1


def test_retry_quarantined_track_entfernt_auch_seen(tmp_path: Path):
    db_path = tmp_path / "seen.sqlite"
    track = make_track(741, "Doppelt gemeldet")
    with State(db_path) as state:
        state.mark_rejected(track, reason="Fake", cutoff_hz=13000.0)
        state.mark_one(track)
        assert state.is_seen(741) is True

    assert retry_quarantined_track(db_path, 741) is True
    assert query_rejected_tracks(db_path)["total"] == 0
    with State(db_path) as state:
        # Vorsorglich auch aus seen, damit der nächste Lauf ihn erneut prüft.
        assert state.is_seen(741) is False


def test_retry_quarantined_track_unbekannt_und_ungueltig(tmp_path: Path):
    db_path = tmp_path / "seen.sqlite"
    with State(db_path):
        pass
    assert retry_quarantined_track(db_path, 999) is False
    # Ungültige IDs sind kein Fehler, sondern nur kein Treffer.
    assert retry_quarantined_track(db_path, "abc") is False  # type: ignore[arg-type]
    assert retry_quarantined_track(db_path, None) is False  # type: ignore[arg-type]


def test_retry_quarantined_track_missing_db(tmp_path: Path):
    """Fehlende State-DB wird nicht angelegt."""
    db_path = tmp_path / "fehlt.sqlite"
    assert retry_quarantined_track(db_path, 1) is False
    assert not db_path.exists()


def test_rejected_seite_leer(tmp_path: Path):
    client = client_for(tmp_path)
    response = client.get("/rejected", auth=AUTH)
    assert response.status_code == 200
    assert "Keine abgewiesenen Tracks für diese Filtereinstellungen." in response.text
    # Ohne Treffer keine Aktionsknöpfe und keine Paginierung.
    assert 'data-track-id="' not in response.text
    assert 'onclick="retryTrack(this)"' not in response.text
    assert "Nächste &rarr;" not in response.text


def test_rejected_seite_zeigt_reason_und_frequenz(tmp_path: Path):
    seed_rejected(tmp_path / "seen.sqlite", [
        (801, "Quarantined Sound", "Fake Spektrum", 13500.0),
    ])

    response = client_for(tmp_path).get("/rejected", auth=AUTH)
    assert response.status_code == 200
    assert "Quarantined Sound" in response.text
    assert "Fake Spektrum" in response.text
    assert "13.5 kHz" in response.text
    assert "13500" in response.text
    assert "https://soundcloud.com/test/801" in response.text
    assert "🔄 Wiederholen / Freigeben" in response.text
    assert 'data-track-id="801"' in response.text


def test_rejected_seite_paginierung_und_suche(tmp_path: Path):
    seed_rejected(tmp_path / "seen.sqlite", [
        (811, "Track 1", "Fake", 13000.0),
        (812, "Track 2", "Clipping", 21000.0),
        (813, "Track 3", "Fake", 13000.0),
    ])
    client = client_for(tmp_path)

    response = client.get("/rejected?limit=2", auth=AUTH)
    assert response.status_code == 200
    assert "Track 3" in response.text
    assert "Track 2" in response.text
    assert "Track 1" not in response.text
    assert "Seite 1 von 2" in response.text
    assert 'href="/rejected?limit=2&amp;offset=2"' in response.text

    second = client.get("/rejected?limit=2&offset=2", auth=AUTH)
    assert second.status_code == 200
    assert "Track 1" in second.text
    assert "Track 3" not in second.text
    assert "Seite 2 von 2" in second.text

    filtered = client.get("/rejected?q=Clipping", auth=AUTH)
    assert filtered.status_code == 200
    assert "Track 2" in filtered.text
    assert "Track 1" not in filtered.text


def test_rejected_seite_escaped_html(tmp_path: Path):
    """Titel und Grund stammen aus fremden Quellen und dürfen kein Markup einschleusen."""
    db_path = tmp_path / "seen.sqlite"
    with State(db_path) as state:
        state.mark_rejected(
            make_track(821, "<script>alert('x')</script>"),
            reason="<img src=x onerror=alert(1)>",
            cutoff_hz=13000.0,
        )

    response = client_for(tmp_path).get("/rejected", auth=AUTH)
    assert response.status_code == 200
    assert "<script>alert('x')</script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "<img src=x onerror" not in response.text
    assert "&lt;img src=x onerror" in response.text


def test_rejected_seite_verlinkt_nur_http_schemata(tmp_path: Path):
    """URLs aus fremden Feeds dürfen kein aktives Schema wie javascript: werden."""
    db_path = tmp_path / "seen.sqlite"
    with State(db_path) as state:
        state.db.execute(
            "INSERT INTO rejected (id, url, title, reason, cutoff_hz) "
            "VALUES (831, 'javascript:alert(1)', 'Böser Link', 'Test', 13000.0)"
        )
        state.db.commit()

    response = client_for(tmp_path).get("/rejected", auth=AUTH)
    assert response.status_code == 200
    assert "Böser Link" in response.text
    assert "javascript:alert(1)" not in response.text


def test_api_rejected_auth_suche_und_paginierung(tmp_path: Path):
    seed_rejected(tmp_path / "seen.sqlite", [
        (841, "Track 1", "Fake", 13000.0),
        (842, "Brickwall", "Clipping", 21000.0),
    ])
    client = client_for(tmp_path)

    assert client.get("/api/rejected").status_code == 401

    data = client.get("/api/rejected?q=Brickwall", auth=AUTH).json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == 842

    page = client.get(
        "/api/rejected", params={"limit": 1, "offset": 1}, auth=AUTH
    ).json()
    assert page["total"] == 2
    assert [item["id"] for item in page["items"]] == [841]


def test_api_retry_freigabe_und_unknown(tmp_path: Path):
    seed_rejected(tmp_path / "seen.sqlite", [(851, "Track", "Fake", 13000.0)])
    client = client_for(tmp_path)
    origin = {"origin": "http://testserver"}

    ok = client.post("/api/retry", json={"track_id": 851}, auth=AUTH, headers=origin)
    assert ok.status_code == 200
    assert ok.json() == {"ok": True}
    assert query_rejected_tracks(tmp_path / "seen.sqlite")["total"] == 0

    again = client.post("/api/retry", json={"track_id": 851}, auth=AUTH, headers=origin)
    assert again.status_code == 200
    assert again.json() == {"ok": False, "error": "Track nicht in Quarantäne"}


def test_api_retry_braucht_anmeldung_und_origin(tmp_path: Path):
    seed_rejected(tmp_path / "seen.sqlite", [(861, "Track", "Fake", 13000.0)])
    client = client_for(tmp_path)
    payload = {"track_id": 861}

    # Ohne Basic-Anmeldung kein Schreibzugriff.
    assert client.post("/api/retry", json=payload).status_code == 401

    # Ohne Origin/Referer (z. B. fremdes Formular) abgelehnt, Eintrag bleibt.
    assert client.post("/api/retry", json=payload, auth=AUTH).status_code == 403
    # Fremde Origin ebenfalls abgelehnt.
    fremd = client.post(
        "/api/retry", json=payload, auth=AUTH,
        headers={"origin": "http://evil.example"},
    )
    assert fremd.status_code == 403
    # Gleicher Host, aber anderer Port, ist eine andere Origin.
    anderer_port = client.post(
        "/api/retry", json=payload, auth=AUTH,
        headers={"origin": "http://testserver:8080"},
    )
    assert anderer_port.status_code == 403

    assert query_rejected_tracks(tmp_path / "seen.sqlite")["total"] == 1


def test_api_retry_valideriert_track_id(tmp_path: Path):
    seed_rejected(tmp_path / "seen.sqlite", [(871, "Track", "Fake", 13000.0)])
    client = client_for(tmp_path)
    origin = {"origin": "http://testserver"}

    assert client.post(
        "/api/retry", json={"track_id": "abc"}, auth=AUTH, headers=origin
    ).status_code == 400
    assert client.post(
        "/api/retry", json={"track_id": True}, auth=AUTH, headers=origin
    ).status_code == 400
    assert client.post(
        "/api/retry", json={}, auth=AUTH, headers=origin
    ).status_code == 400
    assert client.post(
        "/api/retry", content=b"kein json", auth=AUTH,
        headers={**origin, "content-type": "application/json"},
    ).status_code == 400
    # Falsches Schema (Liste) statt Objekt.
    assert client.post(
        "/api/retry", json=[871], auth=AUTH, headers=origin
    ).status_code == 400

    # Nichts davon hat den Eintrag entfernt.
    assert query_rejected_tracks(tmp_path / "seen.sqlite")["total"] == 1


def test_tracks_seite_verlinkt_quarantaene(tmp_path: Path):
    response = client_for(tmp_path).get("/tracks", auth=AUTH)
    assert response.status_code == 200
    assert "Quarantäne-Liste anzeigen (/rejected)" in response.text
    assert 'href="/rejected"' in response.text
