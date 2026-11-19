"""Akzeptanztests: Aktionen auf der Track-Detailseite (Teil 2 von #590, Issue #<N>)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as web_app
from sc_digger.web.routes import track_detail
from sc_digger.web.track_life import LifeStep, TrackLife

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-ta-4711"
AUTH = ("sc", PASSWORD)
LABELS = {"found": "Entdeckt", "rated": "Bewertet", "rejected": "Quarantäne", "inbox": "In der Inbox"}


def _life(*, track_id=None, steps=("found", "rated")):
    return TrackLife(
        sc_id=4711, artist="Artist", title="Titel", url="https://soundcloud.com/a/b",
        steps=tuple(LifeStep(k, LABELS.get(k, k), None, None) for k in steps),
        track_id=track_id,
    )


def _client(tmp_path, monkeypatch, life):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    monkeypatch.setattr(track_detail, "collect_track_life",
                        lambda _cfg, sc_id: life if sc_id == 4711 else None)
    return TestClient(web_app.create_app(cfg, password=PASSWORD))


def test_bewertungsknoepfe_zeigen_auf_das_feedback(tmp_path, monkeypatch):
    html = _client(tmp_path, monkeypatch, _life()).get("/track/sc/4711", auth=AUTH).text
    for kennung in ('id="track-rate-like"', 'id="track-rate-dislike"', 'id="track-rate-later"'):
        assert kennung in html
    for wert in ("like", "dislike", "later"):
        assert f'data-feedback="{wert}"' in html
    assert "/api/feedback" in html
    assert 'id="track-action-status"' in html


def test_matcher_knoepfe_zeigen_auf_match_override(tmp_path, monkeypatch):
    html = _client(tmp_path, monkeypatch, _life()).get("/track/sc/4711", auth=AUTH).text
    for art in ("already_in_collection", "not_duplicate", "clear"):
        assert f'data-match="{art}"' in html
    assert "/api/match-override" in html


def test_retry_nur_in_der_quarantaene_mit_track_id(tmp_path, monkeypatch):
    ohne = _client(tmp_path, monkeypatch, _life(track_id=None, steps=("found", "rejected")))
    assert 'id="track-retry"' not in ohne.get("/track/sc/4711", auth=AUTH).text

    ohne_quarantaene = _client(tmp_path, monkeypatch, _life(track_id=77, steps=("found", "inbox")))
    assert 'id="track-retry"' not in ohne_quarantaene.get("/track/sc/4711", auth=AUTH).text

    mit = _client(tmp_path, monkeypatch, _life(track_id=77, steps=("found", "rejected")))
    html = mit.get("/track/sc/4711", auth=AUTH).text
    assert 'id="track-retry"' in html
    assert 'data-track-id="77"' in html
    assert "/api/retry" in html


def test_upload_verweist_auf_den_eingang(tmp_path, monkeypatch):
    html = _client(tmp_path, monkeypatch, _life()).get("/track/sc/4711", auth=AUTH).text
    assert 'href="/intake"' in html


def test_unbekannte_id_hat_keine_aktionen(tmp_path, monkeypatch):
    # Nicht auf den Endpunkt prüfen: base.html enthält /api/feedback seit #576 für die
    # Tastatur-Bewertung auf jeder Seite. Entscheidend ist, dass die Aktionsknöpfe fehlen.
    client = _client(tmp_path, monkeypatch, _life())
    antwort = client.get("/track/sc/9999", auth=AUTH)
    assert antwort.status_code == 404
    assert 'id="track-actions"' not in antwort.text
    assert 'id="track-rate-like"' not in antwort.text
