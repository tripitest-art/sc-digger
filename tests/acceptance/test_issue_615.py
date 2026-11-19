"""Akzeptanztests: Lebenslauf eines Tracks unter /track/sc/<id>."""
import re
import sqlite3
from pathlib import Path

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.output import State
from sc_digger.web.app import create_app
from sc_digger.web.track_life import STEP_LABELS, collect_track_life
from tests.test_web_tracks_ui import TestClient

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-leben"
AUTH = ("sc", PASSWORD)


def _cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def _seed(tmp_path):
    State(tmp_path / "seen.sqlite").db.close()
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute("INSERT INTO seen (id, url, title, reported_at) VALUES "
                   "(11, 'https://soundcloud.com/a/eins', 'Artist A - Eins', '2026-11-01 07:30:00')")
        db.execute("INSERT INTO seen (id, url, title, reported_at) VALUES "
                   "(12, 'https://soundcloud.com/b/zwei', 'Artist B - Zwei', '2026-11-02 07:30:00')")
        db.execute("INSERT INTO rejected (id, url, title, reason, rejected_at) VALUES "
                   "(12, 'https://soundcloud.com/b/zwei', 'Artist B - Zwei', 'fake_transcode', '2026-11-03 08:00:00')")
    inbox = tmp_path / "inbox"
    with TrackDB(tmp_path / "tracks.sqlite") as tdb:
        tdb.upsert_track(TrackRecord(path=str(inbox / "[11] Artist A - Eins.wav"), mtime=1.0, size=1,
                                     sc_id=11, status="inbox", artist="Artist A", title="Eins",
                                     bpm=150.0, key_camelot="8A", adopted_at="2026-11-05 09:00:00"))
    with sqlite3.connect(tmp_path / "tracks.sqlite") as db:
        db.execute("INSERT INTO sc_feedback (sc_id, url, value, updated_at) VALUES "
                   "(11, 'u', 'like', '2026-11-01 20:00:00')")
        db.execute("INSERT INTO gate_items (sc_id, title, artist, gate_url, first_seen) VALUES "
                   "(11, 'Eins', 'Artist A', 'https://hypeddit.com/a/eins', '2026-11-01 07:30:00')")
        db.execute("INSERT INTO gate_items (sc_id, title, artist, gate_url, first_seen) VALUES "
                   "(13, 'Drei', 'Artist C', 'javascript:alert(1)', '2026-11-04 07:30:00')")


def test_reihenfolge_der_stationen():
    assert list(STEP_LABELS) == [
        "found", "rated", "gate", "store", "rejected", "inbox", "analyzed", "archive", "adopted",
    ]
    assert STEP_LABELS["rejected"] == "Quarantäne"


def test_lebenslauf_eines_uebernommenen_tracks(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(tmp_path)
    life = collect_track_life(cfg, 11)
    assert (life.artist, life.title, life.url) == ("Artist A", "Eins", "https://soundcloud.com/a/eins")
    assert [s.key for s in life.steps] == ["found", "rated", "gate", "inbox", "analyzed", "adopted"]
    schritte = {s.key: s for s in life.steps}
    assert schritte["found"].at == "2026-11-01 07:30:00"
    assert schritte["rated"].detail == "gefällt"
    assert schritte["gate"].detail == "https://hypeddit.com/a/eins"
    assert schritte["analyzed"].detail == "150 BPM · 8A"
    assert schritte["adopted"].at == "2026-11-05 09:00:00"
    assert life.current == "adopted"


def test_lebenslauf_eines_track_in_quarantaene(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(tmp_path)
    life = collect_track_life(cfg, 12)
    assert [s.key for s in life.steps] == ["found", "rejected"]
    assert life.steps[-1].detail == "fake_transcode"
    assert life.current == "rejected"


def test_unbekannt_und_ohne_datenbanken(tmp_path):
    cfg = _cfg(tmp_path)
    assert collect_track_life(cfg, 11) is None
    assert not (tmp_path / "seen.sqlite").exists()
    assert not (tmp_path / "tracks.sqlite").exists()
    _seed(tmp_path)
    assert collect_track_life(cfg, 999) is None
    assert collect_track_life(cfg, "abc") is None


def test_seite_zeigt_den_lebenslauf(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(tmp_path)
    client = TestClient(create_app(cfg, password=PASSWORD))
    antwort = client.get("/track/sc/11", auth=AUTH)
    assert antwort.status_code == 200
    html = antwort.text
    assert re.findall(r'<li data-step="([a-z]+)"', html) == ["found", "rated", "gate", "inbox", "analyzed", "adopted"]
    assert re.search(r'<li data-step="adopted" class="is-current" aria-current="step">', html)
    assert '<a href="https://hypeddit.com/a/eins" target="_blank" rel="noopener">' in html
    assert "Artist A – Eins" in html


def test_keine_gefaehrlichen_links(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(tmp_path)
    html = TestClient(create_app(cfg, password=PASSWORD)).get("/track/sc/13", auth=AUTH).text
    assert 'href="javascript:' not in html
    assert "javascript:alert(1)" in html


def test_unbekannte_id_und_anmeldung(tmp_path):
    cfg = _cfg(tmp_path)
    _seed(tmp_path)
    client = TestClient(create_app(cfg, password=PASSWORD))
    antwort = client.get("/track/sc/999", auth=AUTH)
    assert antwort.status_code == 404
    assert 'href="/digest"' in antwort.text
    assert client.get("/track/sc/11").status_code == 401
