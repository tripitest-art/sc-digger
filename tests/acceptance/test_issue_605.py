"""Akzeptanztests: Ein gemeinsamer Player im Track-Explorer statt <audio> je Zeile."""
import re
from pathlib import Path

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.app import create_app
from tests.test_web_tracks_ui import TestClient

ROOT = Path(__file__).resolve().parents[2]
TRACKS = (ROOT / "sc_digger" / "web" / "templates" / "tracks.html").read_text(encoding="utf-8")
PASSWORD = "geheim-player"
AUTH = ("sc", PASSWORD)


def _client(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    datei = inbox / "da.mp3"
    datei.write_bytes(b"ID3")
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw.setdefault("download", {})["inbox_dir"] = str(inbox)
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        da = db.upsert_track(TrackRecord(path=str(datei), mtime=1.0, size=3, artist="A", title="Da",
                                         quality_status=QualityStatus.OK, status=TrackStatus.INBOX))
        fehlt = db.upsert_track(TrackRecord(path=str(inbox / "fehlt.mp3"), mtime=1.0, size=3,
                                            artist="B", title="Fehlt", quality_status=QualityStatus.OK,
                                            status=TrackStatus.INBOX))
    return TestClient(create_app(cfg, password=PASSWORD)), da, fehlt


def test_genau_ein_player_ausserhalb_der_tabelle(tmp_path):
    client, _, _ = _client(tmp_path)
    html = client.get("/tracks", auth=AUTH).text
    assert len(re.findall(r"<audio\b", html)) == 1
    assert re.search(r'<audio id="deck-audio"[^>]*>', html)
    leiste = html.index('id="dj-deck-bar"')
    assert html.index('id="deck-audio"') > leiste
    teil = client.get("/tracks", auth=AUTH, headers={"X-Sc-Digger-Partial": "1"}).text
    assert "<audio" not in teil


def test_zeile_hat_abspielknopf_nur_bei_vorhandener_datei(tmp_path):
    client, da, fehlt = _client(tmp_path)
    html = client.get("/tracks", auth=AUTH).text
    assert f'class="play-btn btn-page btn-xs" data-play-src="/api/audio/{da}"' in html
    assert f"/api/audio/{fehlt}" not in html
    assert html.count('class="play-btn') == 1
    assert f'data-track-id="{da}"' in html
    assert f'data-track-id="{fehlt}"' in html


def test_kein_zeilen_player_mehr_im_template():
    assert "audio-preview" not in TRACKS
    assert "activeAudio" not in TRACKS


def test_gemeinsamer_player_im_skript():
    assert "const deckAudio = document.getElementById('deck-audio');" in TRACKS
    assert re.search(r"^\s*let activeTrackId = null;", TRACKS, re.M)
    for name in ("playRow", "bindDeckPlayers", "initDeckPlayer", "deckTogglePlay", "deckStepTrack",
                 "rateActiveTrack", "undoLastFeedback"):
        assert re.search(rf"function {name}\(", TRACKS), name
    start = TRACKS.index("function initDeckPlayer(")
    teil = TRACKS[start:TRACKS.index("\n  }\n", start)]
    assert "document.addEventListener('click'" in teil
    assert ".play-btn" in teil
