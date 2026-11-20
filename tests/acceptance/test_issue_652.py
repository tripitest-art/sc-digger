"""Akzeptanztests: Vorhören auf der Track-Detailseite (Rest von #590, Issue #<N>)."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as web_app
from sc_digger.web.routes import track_detail
from sc_digger.web.track_life import LifeStep, TrackLife

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-tv-4711"
AUTH = ("sc", PASSWORD)


def _life() -> TrackLife:
    return TrackLife(
        sc_id=4711, artist="Artist", title="Titel", url="https://soundcloud.com/a/b",
        steps=(LifeStep("found", "Entdeckt", None, None),),
    )


def _client(tmp_path, monkeypatch, *, preview: bool):
    monkeypatch.setattr(track_detail, "collect_track_life",
                        lambda _cfg, sc_id: _life() if sc_id == 4711 else None)
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw["web"] = {"stream_preview": preview}
    return TestClient(web_app.create_app(cfg, password=PASSWORD))


def test_player_steht_bei_eingeschaltetem_schalter(tmp_path, monkeypatch):
    html = _client(tmp_path, monkeypatch, preview=True).get("/track/sc/4711", auth=AUTH).text
    assert 'id="track-audio"' in html
    assert 'src="/api/preview/sc/4711"' in html
    assert 'preload="none"' in html
    assert "track-audio" in html


def test_ohne_schalter_kein_player(tmp_path, monkeypatch):
    html = _client(tmp_path, monkeypatch, preview=False).get("/track/sc/4711", auth=AUTH).text
    assert 'id="track-audio"' not in html
    assert "/api/preview/sc/" not in html


def test_unbekannte_id_ohne_player(tmp_path, monkeypatch):
    antwort = _client(tmp_path, monkeypatch, preview=True).get("/track/sc/9999", auth=AUTH)
    assert antwort.status_code == 404
    assert 'id="track-audio"' not in antwort.text
