"""Akzeptanztests: playlist, similar, intake und übersprungene Läufe landen in runs."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

import sc_digger.main as m
from sc_digger.models import Config
from sc_digger.runlock import run_lock
from sc_digger.web.status import ALERT_LABELS


def _runs(db: Path, mode: str) -> list[tuple]:
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT ok, found, error FROM runs WHERE mode=? ORDER BY id", (mode,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _cli(monkeypatch, tmp_path: Path, *args: str) -> None:
    cfg = Config({"state": {"db_path": str(tmp_path / "seen.sqlite"),
                            "track_db_path": str(tmp_path / "tracks.sqlite")},
                  "health": {"alert_after_bad_runs": 2}})
    monkeypatch.setattr(m.Config, "load", classmethod(lambda cls, path=None: cfg))
    monkeypatch.setattr(sys, "argv", ["sc_digger.main", *args, "--no-telegram"])
    m.cli()


def test_playlist_lauf_wird_mit_trefferzahl_protokolliert(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "run_playlist", lambda cfg, url, likes, dry_run, no_telegram: 5)
    _cli(monkeypatch, tmp_path, "playlist", "https://soundcloud.com/x/sets/y")
    assert _runs(tmp_path / "seen.sqlite", "playlist") == [(1, 5, None)]


def test_similar_lauf_wird_protokolliert(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "run_similar", lambda cfg, url, use_station, apply_filter, limit, dry_run,
                        no_telegram: 0)
    _cli(monkeypatch, tmp_path, "similar", "https://soundcloud.com/x/y")
    assert _runs(tmp_path / "seen.sqlite", "similar") == [(1, 0, None)]


def test_intake_lauf_zaehlt_gemeldete_zeilen(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "run_intake", lambda cfg, dry_run=False: ["✅ A – B", "❌ c.mp3: zu leise"])
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg, *a, **k: None)
    _cli(monkeypatch, tmp_path, "intake")
    assert _runs(tmp_path / "seen.sqlite", "intake") == [(1, 2, None)]


def test_fehler_im_modus_wird_protokolliert_und_beendet_mit_exit_1(tmp_path, monkeypatch):
    def kaputt(cfg, url, likes, dry_run, no_telegram):
        raise RuntimeError("SoundCloud nicht erreichbar")

    monkeypatch.setattr(m, "run_playlist", kaputt)
    with pytest.raises(SystemExit) as exc:
        _cli(monkeypatch, tmp_path, "playlist", "https://soundcloud.com/x/sets/y")
    assert exc.value.code == 1
    (ok, found, error), = _runs(tmp_path / "seen.sqlite", "playlist")
    assert (ok, found) == (0, 0)
    assert "RuntimeError" in error and "SoundCloud nicht erreichbar" in error


def test_probelauf_wird_nicht_protokolliert(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "run_playlist", lambda cfg, url, likes, dry_run, no_telegram: 3)
    _cli(monkeypatch, tmp_path, "playlist", "https://soundcloud.com/x/sets/y", "--dry-run")
    assert _runs(tmp_path / "seen.sqlite", "playlist") == []


def test_statusseite_kennt_die_neuen_modi():
    for mode in ("playlist", "similar", "intake"):
        assert ALERT_LABELS.get(mode)


def _lock_cfg(tmp_path: Path, schwelle: int) -> Config:
    return Config({"state": {"db_path": str(tmp_path / "seen.sqlite"), "lock_wait_s": 0},
                   "health": {"alert_after_bad_runs": schwelle}})


@pytest.mark.parametrize("modus", ["scout", "audit", "taste", "kaufliste", "intake"])
def test_uebersprungener_lauf_steht_in_runs(tmp_path, modus):
    aufrufe = []
    with run_lock(tmp_path, "discover"):
        assert m.run_locked(_lock_cfg(tmp_path, 5), modus, lambda: aufrufe.append(1),
                            no_telegram=True) is False
    assert aufrufe == []
    (ok, found, error), = _runs(tmp_path / "seen.sqlite", modus)
    assert (ok, found) == (0, 0)
    assert "Lauf-Sperre belegt: discover" in error


def test_uebersprungener_lauf_loest_nach_schwelle_telegram_alarm_aus(tmp_path, monkeypatch):
    gesendet = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msgs, *a, **k: gesendet.append(msgs))
    with run_lock(tmp_path, "discover"):
        m.run_locked(_lock_cfg(tmp_path, 2), "scout", lambda: None, no_telegram=False)
        assert gesendet == []  # erst der zweite Fehlschlag in Folge alarmiert
        m.run_locked(_lock_cfg(tmp_path, 2), "scout", lambda: None, no_telegram=False)
    assert len(gesendet) == 1
    assert "scout" in "\n".join(gesendet[0])


def test_gelaufener_modus_schreibt_keinen_skip_eintrag(tmp_path):
    aufrufe = []
    assert m.run_locked(_lock_cfg(tmp_path, 2), "scout", lambda: aufrufe.append(1), no_telegram=True) is True
    assert aufrufe == [1]
    assert _runs(tmp_path / "seen.sqlite", "scout") == []
