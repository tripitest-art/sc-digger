"""Eigene Tests zu Issue #758: playlist, similar, intake und übersprungene Läufe in runs."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from sc_digger import main as m
from sc_digger.models import Config
from sc_digger.runlock import run_lock
from tests.test_modes import CFG, FakeSC, raw_track


def _runs(db: Path, mode: str) -> list[tuple]:
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT ok, found, error FROM runs WHERE mode=? ORDER BY id",
                           (mode,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _pipeline_cfg(tmp_path: Path) -> Config:
    cfg = Config(dict(CFG.raw))
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll")}
    (tmp_path / "coll").mkdir()
    return cfg


# ---------------- Rückgabewerte der Modi ----------------
def test_run_playlist_liefert_zahl_der_fehlenden_tracks(tmp_path, monkeypatch):
    cfg = _pipeline_cfg(tmp_path)
    # Track 1 liegt schon in der Sammlung, 2 und 3 fehlen
    (tmp_path / "coll" / "Artist1 - Track 1.wav").write_bytes(b"x")
    url = "https://soundcloud.com/u/sets/p"
    routes = {url: {"kind": "playlist", "title": "Liste",
                    "tracks": [raw_track(1), raw_track(2), raw_track(3)]}}
    monkeypatch.setattr(m, "SoundCloudClient", lambda: FakeSC(routes))
    assert m.run_playlist(cfg, url, likes=False, dry_run=True, no_telegram=True) == 2


def test_run_playlist_liefert_null_wenn_alles_vorhanden(tmp_path, monkeypatch):
    cfg = _pipeline_cfg(tmp_path)
    (tmp_path / "coll" / "Artist1 - Track 1.wav").write_bytes(b"x")
    url = "https://soundcloud.com/u/sets/p"
    routes = {url: {"kind": "playlist", "title": "Liste", "tracks": [raw_track(1)]}}
    monkeypatch.setattr(m, "SoundCloudClient", lambda: FakeSC(routes))
    result = m.run_playlist(cfg, url, likes=False, dry_run=True, no_telegram=True)
    assert result == 0 and isinstance(result, int)


def test_run_similar_liefert_zahl_der_neuen_tracks(tmp_path, monkeypatch):
    cfg = _pipeline_cfg(tmp_path)
    seed_url = "https://soundcloud.com/a/seed"
    routes = {
        seed_url: raw_track(99),
        "/tracks/99/related": {"collection": [raw_track(1), raw_track(99), raw_track(2),
                                              raw_track(3)]},
    }
    monkeypatch.setattr(m, "SoundCloudClient", lambda: FakeSC(routes))
    # Seed fällt heraus, drei Empfehlungen bleiben, keine davon in der Sammlung
    assert m.run_similar(cfg, seed_url, use_station=False, apply_filter=False, limit=50,
                         dry_run=True, no_telegram=True) == 3


# ---------------- run_intake_cli ----------------
def test_run_intake_cli_meldet_null_bei_abgeschaltetem_eingang(monkeypatch):
    geschrieben = []
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg, *a, **k: geschrieben.append(1))
    cfg = Config({"download": {"intake_dir": ""}})
    assert m.run_intake_cli(cfg, dry_run=False, no_telegram=True) == 0
    assert geschrieben == [1]  # XML wie bisher auch ohne Eingang neu schreiben


def test_run_intake_cli_meldet_null_bei_leerem_eingang(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg, *a, **k: None)
    gesendet = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, text, *a, **k: gesendet.append(text))
    intake = tmp_path / "_eingang"
    intake.mkdir()
    cfg = Config({"download": {"intake_dir": str(intake), "inbox_dir": str(tmp_path / "inbox"),
                               "intake_min_age_s": 0}})
    assert m.run_intake_cli(cfg, dry_run=False, no_telegram=False) == 0
    assert gesendet == []


def test_run_intake_cli_sendet_zeilen_per_telegram(monkeypatch, capsys):
    monkeypatch.setattr(m, "run_intake", lambda cfg, dry_run=False: ["📥 Eingang", "✅ A – B"])
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg, *a, **k: None)
    gesendet = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, text, *a, **k: gesendet.append(text))
    assert m.run_intake_cli(Config({}), dry_run=False, no_telegram=False) == 2
    assert gesendet == ["📥 Eingang\n✅ A – B"]
    assert capsys.readouterr().out == ""


def test_run_intake_cli_probelauf_druckt_und_schreibt_kein_xml(monkeypatch, capsys):
    monkeypatch.setattr(m, "run_intake", lambda cfg, dry_run=False: ["✅ A – B"])
    geschrieben, gesendet = [], []
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg, *a, **k: geschrieben.append(1))
    monkeypatch.setattr(m, "send_telegram", lambda cfg, text, *a, **k: gesendet.append(text))
    assert m.run_intake_cli(Config({}), dry_run=True, no_telegram=False) == 1
    assert "✅ A – B" in capsys.readouterr().out
    assert geschrieben == [] and gesendet == []


# ---------------- cli(): Protokoll in runs ----------------
def _cli(monkeypatch, tmp_path: Path, *args: str) -> None:
    cfg = Config({"state": {"db_path": str(tmp_path / "seen.sqlite"),
                            "track_db_path": str(tmp_path / "tracks.sqlite")},
                  "health": {"alert_after_bad_runs": 2}})
    monkeypatch.setattr(m.Config, "load", classmethod(lambda cls, path=None: cfg))
    monkeypatch.setattr(sys, "argv", ["sc_digger.main", *args, "--no-telegram"])
    m.cli()


def test_intake_probelauf_wird_nicht_protokolliert(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "run_intake", lambda cfg, dry_run=False: ["✅ A – B"])
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg, *a, **k: None)
    _cli(monkeypatch, tmp_path, "intake", "--dry-run")
    assert _runs(tmp_path / "seen.sqlite", "intake") == []


def test_similar_fehler_wird_protokolliert(tmp_path, monkeypatch):
    def kaputt(*a, **k):
        raise ValueError("Seed nicht auflösbar")

    monkeypatch.setattr(m, "run_similar", kaputt)
    try:
        _cli(monkeypatch, tmp_path, "similar", "https://soundcloud.com/x/y")
    except SystemExit as e:
        assert e.code == 1
    else:
        raise AssertionError("cli() hätte mit Exit 1 enden müssen")
    (ok, found, error), = _runs(tmp_path / "seen.sqlite", "similar")
    assert (ok, found) == (0, 0)
    assert "ValueError: Seed nicht auflösbar" in error


def test_similar_argumente_werden_durchgereicht(tmp_path, monkeypatch):
    aufrufe = []
    monkeypatch.setattr(m, "run_similar", lambda *a: aufrufe.append(a[1:]) or 4)
    _cli(monkeypatch, tmp_path, "similar", "https://soundcloud.com/x/y", "--radio")
    assert aufrufe and aufrufe[0][0] == "https://soundcloud.com/x/y"
    assert aufrufe[0][1] is True  # use_station
    assert _runs(tmp_path / "seen.sqlite", "similar") == [(1, 4, None)]


# ---------------- run_locked: übersprungene Läufe ----------------
def _lock_cfg(tmp_path: Path) -> Config:
    return Config({"state": {"db_path": str(tmp_path / "seen.sqlite"), "lock_wait_s": 0},
                   "health": {"alert_after_bad_runs": 2}})


def test_uebersprungener_discover_bleibt_beim_eigenen_zweig(tmp_path):
    with run_lock(tmp_path, "scout"):
        assert m.run_locked(_lock_cfg(tmp_path), "discover", lambda: None, no_telegram=True) is False
    (ok, found, error), = _runs(tmp_path / "seen.sqlite", "discover")
    assert (ok, found) == (0, 0)
    assert "Lauf-Sperre belegt: scout" in error


def test_uebersprungener_modus_schreibt_nur_eigenen_eintrag(tmp_path):
    with run_lock(tmp_path, "discover"):
        assert m.run_locked(_lock_cfg(tmp_path), "playlist", lambda: None, no_telegram=True) is False
    assert len(_runs(tmp_path / "seen.sqlite", "playlist")) == 1
    assert _runs(tmp_path / "seen.sqlite", "discover") == []
