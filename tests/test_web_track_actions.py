"""Tests für Aktionen auf der Track-Detailseite: Gate-Link, Upload-Feld, Rückfallweg (Issue #708)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web import app as web_app
from sc_digger.web.routes import track_detail
from sc_digger.web.track_life import LifeStep, TrackLife

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-web-track-actions"
AUTH = ("sc", PASSWORD)
SC = 4711
GATE_URL = "https://hypeddit.com/track/123"


def _client_with_life(tmp_path, monkeypatch, life):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    monkeypatch.setattr(
        track_detail, "collect_track_life", lambda _cfg, sc_id: life if sc_id == SC else None
    )
    return TestClient(web_app.create_app(cfg, password=PASSWORD))


def _client_with_db(tmp_path, gate_url=GATE_URL):
    track_db = tmp_path / "track.sqlite"
    seen_db = tmp_path / "seen.sqlite"
    with TrackDB(track_db) as db:
        db.upsert_track(
            TrackRecord(
                path="/inbox/alpha.mp3",
                mtime=1.0,
                size=1000,
                artist="Alpha",
                title="Erster Track",
                bpm=155.0,
                key_camelot="8A",
                status="inbox",
                sc_id=SC,
            )
        )
        if gate_url:
            db.upsert_gate_item(SC, "Erster Track", "Alpha", gate_url)
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(seen_db),
        "track_db_path": str(track_db),
        "health_db_path": str(seen_db),
    }
    return TestClient(web_app.create_app(cfg, password=PASSWORD))


def test_gate_link_mit_attributen(tmp_path):
    client = _client_with_db(tmp_path, gate_url=GATE_URL)
    html = client.get(f"/track/sc/{SC}", auth=AUTH).text
    assert 'id="track-gate-open"' in html
    assert f'href="{GATE_URL}"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener"' in html
    assert "Gate öffnen ↗" in html


def test_ohne_gate_kein_gate_link(tmp_path):
    client = _client_with_db(tmp_path, gate_url=None)
    html = client.get(f"/track/sc/{SC}", auth=AUTH).text
    assert 'id="track-gate-open"' not in html
    assert "Gate öffnen" not in html


def test_upload_feld_und_knoepfe_vorhanden(tmp_path):
    client = _client_with_db(tmp_path)
    html = client.get(f"/track/sc/{SC}", auth=AUTH).text
    assert 'id="track-upload"' in html
    assert 'class="track-upload"' in html
    assert f'data-sc-id="{SC}"' in html
    assert 'accept="audio/*"' in html
    assert 'id="track-upload-send"' in html
    assert "Datei ablegen" in html
    assert "/api/upload-intake" in html


def test_rueckfall_link_intake_nicht_doppelt(tmp_path):
    client = _client_with_db(tmp_path)
    html = client.get(f"/track/sc/{SC}", auth=AUTH).text
    # Rückfall-Link zum Intake ist vorhanden, erscheint aber nicht doppelt
    assert 'href="/intake"' in html
    assert html.count('href="/intake"') == 1


def test_bestehende_aktionen_unveraendert(tmp_path, monkeypatch):
    life = TrackLife(
        sc_id=SC,
        artist="Alpha",
        title="Track 4711",
        url="https://soundcloud.com/alpha/track",
        steps=(
            LifeStep("found", "Entdeckt", None, None),
            LifeStep("rejected", "Quarantäne", None, "Audioqualität zu gering"),
        ),
        track_id=88,
    )
    client = _client_with_life(tmp_path, monkeypatch, life)
    html = client.get(f"/track/sc/{SC}", auth=AUTH).text

    # Bewertung
    assert 'id="track-rate-like"' in html
    assert 'id="track-rate-dislike"' in html
    assert 'id="track-rate-later"' in html
    assert "/api/feedback" in html

    # Matcher-Korrektur
    assert 'id="track-match-collection"' in html
    assert 'id="track-match-duplicate"' in html
    assert 'id="track-match-clear"' in html
    assert "/api/match-override" in html

    # Retry
    assert 'id="track-retry"' in html
    assert 'data-track-id="88"' in html
    assert "/api/retry" in html

    # Status
    assert 'id="track-action-status"' in html
