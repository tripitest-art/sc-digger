"""Akzeptanztests: On-Demand-Link-Checks schreiben einen Lauf und melden Fehler in den Health-Alarm."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sc_digger import main as m
from sc_digger.health import record_link_check
from sc_digger.models import Config
from sc_digger.soundcloud import SoundCloudError
from tests.test_modes import mk

CFG_PATH = Path(__file__).resolve().parents[2] / "config.yaml"
if not CFG_PATH.is_file():  # issuecheck legt den Block außerhalb des Repos ab; pytest läuft dort im Repo
    CFG_PATH = Path.cwd() / "config.yaml"
CFG = Config.load(CFG_PATH)


def _cfg(tmp_path):
    cfg = Config(dict(CFG.raw))
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    cfg.raw["state"] = {"db_path": str(tmp_path / "state.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["search"] = {**CFG["search"], "reference_accounts": [], "followed_users": []}
    return cfg


def _check_runs(tmp_path):
    path = tmp_path / "state.sqlite"
    if not path.exists():  # Probelauf: keine Lauf-DB, also keine Zeile
        return []
    with sqlite3.connect(path) as db:
        try:
            return db.execute(
                "SELECT mode, ok, found FROM runs WHERE mode='check' ORDER BY id"
            ).fetchall()
        except sqlite3.OperationalError:
            return []


class _LinkSC:
    """Nur die Methoden, die run_link für einen Playlist-Link aufruft."""

    def resolve_kind(self, url):
        return "playlist"

    def playlist_tracks(self, url, limit=None):
        return "Liste", [mk(11), mk(12)]


class _BrokenSC:
    def resolve_kind(self, url):
        raise SoundCloudError("Abfrage fehlgeschlagen")


def test_erfolg_schreibt_genau_einen_lauf(tmp_path):
    assert record_link_check(tmp_path / "s.db", source="Playlist: X", fresh=4, dupes=2) is None
    with sqlite3.connect(tmp_path / "s.db") as db:
        assert db.execute("SELECT mode, ok, found, error FROM runs").fetchall() == [
            ("check", 1, 4, None)
        ]


def test_check_ohne_neue_tracks_ist_kein_fehler(tmp_path):
    db = tmp_path / "s.db"
    assert record_link_check(db, source="X", fresh=0, dupes=7) is None
    assert record_link_check(db, source="X", fresh=0, dupes=7) is None
    assert record_link_check(db, source="X", fresh=0, dupes=7) is None


def test_dritter_fehler_in_folge_alarmiert(tmp_path):
    db = tmp_path / "s.db"
    assert record_link_check(db, source="X", fresh=0, dupes=0, error="A") is None
    assert record_link_check(db, source="X", fresh=0, dupes=0, error="B") is None
    text = record_link_check(db, source="X", fresh=0, dupes=0, error="C")
    assert text and "check" in text


def test_erfolg_nach_alarm_entwarnt(tmp_path):
    db = tmp_path / "s.db"
    for _ in range(3):
        record_link_check(db, source="X", fresh=0, dupes=0, error="kaputt")
    text = record_link_check(db, source="X", fresh=3, dupes=0)
    assert text and "check" in text


def test_protokollfehler_wirft_nicht(tmp_path):
    assert record_link_check(tmp_path, source="X", fresh=1, dupes=0) is None


def test_run_link_fehler_wird_protokolliert(tmp_path):
    with pytest.raises(SoundCloudError):
        m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=_BrokenSC())
    assert _check_runs(tmp_path) == [("check", 0, 0)]


def test_run_link_probelauf_protokolliert_nicht(tmp_path):
    m.run_link(_cfg(tmp_path), "u", dry_run=True, no_telegram=True, sc=_LinkSC())
    assert _check_runs(tmp_path) == []
