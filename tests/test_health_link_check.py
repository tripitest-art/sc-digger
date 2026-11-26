"""Eigene Tests zu Issue #755: On-Demand-Checks in runs und im Health-Alarm."""
from __future__ import annotations

import logging
import sqlite3

import pytest

from sc_digger import main as m
from sc_digger.health import record_link_check
from sc_digger.soundcloud import SoundCloudError
from sc_digger.web.status import ALERT_LABELS
from tests.acceptance.test_issue_755 import _BrokenSC, _cfg, _check_runs, _LinkSC


def _errors(tmp_path):
    with sqlite3.connect(tmp_path / "state.sqlite") as db:
        return [r[0] for r in db.execute("SELECT error FROM runs WHERE mode='check' ORDER BY id")]


def test_run_link_erfolg_schreibt_lauf_mit_neuen_tracks(tmp_path):
    source, fresh, dupes = m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=_LinkSC())
    assert source == "Playlist: Liste"
    assert _check_runs(tmp_path) == [("check", 1, fresh)]
    assert _errors(tmp_path) == [None]


def test_run_link_fehler_erreicht_aufrufer_unveraendert(tmp_path):
    class _Sc(_BrokenSC):
        exc = SoundCloudError("genau diese")

        def resolve_kind(self, url):
            raise self.exc

    with pytest.raises(SoundCloudError) as info:
        m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=_Sc())
    assert info.value is _Sc.exc
    assert _errors(tmp_path) == ["SoundCloudError: genau diese"]


def test_run_link_unbekannter_linktyp_wird_protokolliert(tmp_path):
    class _Unknown:
        def resolve_kind(self, url):
            return "user"

    with pytest.raises(SoundCloudError):
        m.run_link(_cfg(tmp_path), "u", no_telegram=True, sc=_Unknown())
    assert _check_runs(tmp_path) == [("check", 0, 0)]


def test_run_link_probelauf_mit_fehler_protokolliert_nicht(tmp_path):
    with pytest.raises(SoundCloudError):
        m.run_link(_cfg(tmp_path), "u", dry_run=True, no_telegram=True, sc=_BrokenSC())
    assert _check_runs(tmp_path) == []


def test_dritter_fehlschlag_meldet_alarm_ueber_notify_health(tmp_path, monkeypatch):
    sent: list[str | None] = []
    monkeypatch.setattr(m, "_notify_health", lambda cfg, text, no_tg: sent.append(text))
    cfg = _cfg(tmp_path)
    for _ in range(3):
        with pytest.raises(SoundCloudError):
            m.run_link(cfg, "u", no_telegram=True, sc=_BrokenSC())
    assert sent[:2] == [None, None]
    assert sent[2] and "check" in sent[2]
    m.run_link(cfg, "u", no_telegram=True, sc=_LinkSC())
    assert sent[3] and "läuft wieder" in sent[3]


def test_record_link_check_loggt_quelle(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="sc_digger.health"):
        record_link_check(tmp_path / "s.db", source="Playlist: X", fresh=4, dupes=2)
    assert "On-Demand-Check: Playlist: X (4 neu, 2 vorhanden)" in caplog.text


def test_record_link_check_ohne_fehler_ist_ok(tmp_path):
    db = tmp_path / "s.db"
    record_link_check(db, source="X", fresh=0, dupes=5)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT mode, ok, found, error FROM runs").fetchall() == [
            ("check", 1, 0, None)]


def test_alarm_label_fuer_check():
    assert ALERT_LABELS["check"] == "On-Demand-Check"
