"""Akzeptanztests: Zeiten im Dashboard in Ortszeit (web.timezone)."""
import sqlite3
from pathlib import Path

from sc_digger.models import Config
from sc_digger.output import State
from sc_digger.schema import FIELDS, used_by
from sc_digger.web.app import create_app
from sc_digger.web.timefmt import DEFAULT_TIMEZONE, to_local
from tests.test_web_tracks_ui import TestClient

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-zeit"
AUTH = ("sc", PASSWORD)


def test_umrechnung_winter_und_sommer():
    assert DEFAULT_TIMEZONE == "Europe/Berlin"
    assert to_local("2026-11-19 07:30:00", "Europe/Berlin") == "2026-11-19 08:30"
    assert to_local("2026-07-01 07:30:00", "Europe/Berlin") == "2026-07-01 09:30"
    assert to_local("2026-11-19T23:30:00Z", "Europe/Berlin") == "2026-11-20 00:30"
    assert to_local("2026-11-19 07:30:00", "UTC") == "2026-11-19 07:30"


def test_randfaelle():
    assert to_local(None) == "—"
    assert to_local("") == "—"
    assert to_local("gestern") == "gestern"
    assert to_local("2026-11-19 07:30:00", "Mars/Olympus") == "2026-11-19 07:30 UTC"
    assert to_local("2026-11-19 07:30:00", "Europe/Berlin", fmt="%H:%M") == "08:30"


def test_einstellung_steht_in_config_und_schema():
    cfg = Config.load(ROOT / "config.yaml")
    assert cfg.raw["web"]["timezone"] == "Europe/Berlin"
    feld = [f for f in FIELDS if f.path == "web.timezone"]
    assert len(feld) == 1 and feld[0].kind == "str" and feld[0].editable
    assert used_by("web.timezone") == ("Web-Dashboard",)


def _client(tmp_path, zone=None):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    if zone is not None:
        cfg.raw["web"] = {"timezone": zone}
    State(tmp_path / "seen.sqlite").db.close()
    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute("INSERT INTO seen (id, url, title, reported_at) VALUES "
                   "(1, 'https://soundcloud.com/a/b', 'A - B', '2026-11-19 07:30:00')")
        db.execute("INSERT INTO rejected (id, url, title, reason, rejected_at) VALUES "
                   "(2, 'https://soundcloud.com/c/d', 'C - D', 'fake_transcode', '2026-11-19 06:05:00')")
    return TestClient(create_app(cfg, password=PASSWORD))


def test_digest_und_quarantaene_zeigen_ortszeit(tmp_path):
    client = _client(tmp_path)
    digest = client.get("/digest", auth=AUTH).text
    assert "2026-11-19 08:30" in digest
    assert "2026-11-19 07:30" not in digest
    quarantaene = client.get("/rejected", auth=AUTH).text
    assert "2026-11-19 07:05" in quarantaene


def test_zone_kommt_aus_der_konfiguration(tmp_path):
    client = _client(tmp_path, zone="UTC")
    assert "2026-11-19 07:30" in client.get("/digest", auth=AUTH).text
