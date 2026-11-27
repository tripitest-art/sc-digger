"""Eigene Tests zu Issue #769: run_link schreibt das Ergebnis wie der tägliche Lauf über
record_outcomes (abgelehnte Downloads in die Quarantäne, der Rest nach seen)."""
from __future__ import annotations

import itertools
import sqlite3
from pathlib import Path

import pytest

from sc_digger import main as m
from sc_digger.models import Config
from sc_digger.output import State
from tests.test_modes import mk

CFG = Config.load(Path(__file__).resolve().parents[1] / "config.yaml")


def _cfg(tmp_path):
    cfg = Config(dict(CFG.raw))
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(tmp_path / "inbox")}
    cfg.raw["state"] = {"db_path": str(tmp_path / "state.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["search"] = {**CFG["search"], "reference_accounts": [], "followed_users": []}
    return cfg


class _PlaylistSC:
    def __init__(self, ids):
        self.ids = list(ids)

    def resolve_kind(self, url):
        return "playlist"

    def playlist_tracks(self, url, limit=None):
        return "Liste", [mk(i) for i in self.ids]


class _StationSC:
    """Track-Link: run_link holt die Station statt einer Playlist."""

    def resolve_kind(self, url):
        return "track"

    def track_station(self, url, limit=None):
        return "Seed", [mk(21), mk(22)]


def _fake_process(monkeypatch, *, download_fail=(), quality_fail=(), note_fail=(), dupes=()):
    """process() ersetzen: legt die Ergebnisse je ID fest, ohne Netz und ohne Dateien."""
    def fake(tracks, cfg, *, dry_run, skip_duplicates=True):
        fresh, dup = [], []
        for t in tracks:
            if t.id in download_fail:
                t.download_error = f"Download nicht möglich ({t.id})"
            if t.id in quality_fail:
                t.quality_report = {"ok": False, "reason": f"Fake ({t.id})", "cutoff_hz": 15500.0}
            if t.id in note_fail:
                t.notes.append("Qualitätsprüfung fehlgeschlagen: ffprobe kaputt")
            if t.id in dupes:
                t.duplicate_of = f"/music/Schranz/{t.id}.wav"
                dup.append(t)
            else:
                fresh.append(t)
        return fresh, dup

    monkeypatch.setattr(m, "process", fake)
    monkeypatch.setattr(m, "deliver", lambda *a, **k: None)


def _ids(tmp_path, tabelle):
    with sqlite3.connect(tmp_path / "state.sqlite") as db:
        return sorted(r[0] for r in db.execute(f"SELECT id FROM {tabelle}"))


def test_frueher_abgelehnter_track_landet_nach_sauberem_check_in_seen(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    _fake_process(monkeypatch, quality_fail={12})
    m.run_link(cfg, "u", no_telegram=True, sc=_PlaylistSC([11, 12]))
    assert _ids(tmp_path, "rejected") == [12]
    assert _ids(tmp_path, "seen") == [11]

    # Zweiter Check: Track 12 besteht jetzt die Prüfung (z. B. nach /retry oder neuem Upload).
    _fake_process(monkeypatch)
    m.run_link(cfg, "u", no_telegram=True, sc=_PlaylistSC([11, 12]))
    assert _ids(tmp_path, "seen") == [11, 12]


@pytest.mark.parametrize("order", list(itertools.permutations([31, 32, 33, 34])))
def test_reihenfolge_von_quarantaene_und_seen_aendert_die_zahlen_nicht(tmp_path, monkeypatch, order):
    # 31 Download-Fehler, 32 Qualitäts-Fehler, 33 Duplikat, 34 sauber – in jeder Reihenfolge.
    _fake_process(monkeypatch, download_fail={31}, quality_fail={32}, dupes={33})
    quelle, neu, vorhanden = m.run_link(_cfg(tmp_path), "u", no_telegram=True,
                                        sc=_PlaylistSC(order))
    assert quelle == "Playlist: Liste"
    assert (neu, vorhanden) == (3, 1)
    assert _ids(tmp_path, "rejected") == [31, 32]
    assert _ids(tmp_path, "seen") == [33, 34]


def test_duplikate_der_sammlung_werden_weiter_als_gesehen_markiert(tmp_path, monkeypatch):
    _fake_process(monkeypatch, dupes={11, 12})
    _, neu, vorhanden = m.run_link(_cfg(tmp_path), "u", no_telegram=True,
                                   sc=_PlaylistSC([11, 12, 13]))
    assert (neu, vorhanden) == (1, 2)
    assert _ids(tmp_path, "seen") == [11, 12, 13]
    assert _ids(tmp_path, "rejected") == []


def test_gescheiterte_nachbearbeitung_als_notiz_geht_in_die_quarantaene(tmp_path, monkeypatch):
    _fake_process(monkeypatch, note_fail={13})
    m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=_PlaylistSC([11, 13]))
    with sqlite3.connect(tmp_path / "state.sqlite") as db:
        rows = dict(db.execute("SELECT id, reason FROM rejected"))
    assert rows == {13: "Qualitätsprüfung fehlgeschlagen: ffprobe kaputt"}
    assert _ids(tmp_path, "seen") == [11]


def test_track_link_mit_station_legt_ablehnungen_ebenso_in_die_quarantaene(tmp_path, monkeypatch):
    _fake_process(monkeypatch, download_fail={22})
    quelle, neu, vorhanden = m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=_StationSC())
    assert quelle == "Station zu: Seed"
    assert (neu, vorhanden) == (2, 0)
    assert _ids(tmp_path, "rejected") == [22]
    assert _ids(tmp_path, "seen") == [21]


def test_abgelehnter_track_wird_im_naechsten_lauf_nicht_als_gesehen_gefiltert(tmp_path, monkeypatch):
    _fake_process(monkeypatch, download_fail={11})
    cfg = _cfg(tmp_path)
    m.run_link(cfg, "u", no_telegram=True, sc=_PlaylistSC([11, 12]))
    with State(cfg["state"]["db_path"]) as state:
        # discover filtert über is_seen; der abgelehnte Track muss dort frei bleiben.
        assert not state.is_seen(11)
        assert state.is_seen(12)


def test_check_wird_weiter_als_lauf_protokolliert(tmp_path, monkeypatch):
    _fake_process(monkeypatch, quality_fail={11})
    m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=_PlaylistSC([11, 12]))
    with sqlite3.connect(tmp_path / "state.sqlite") as db:
        runs = db.execute("SELECT mode, ok FROM runs").fetchall()
    assert runs == [("check", 1)]
