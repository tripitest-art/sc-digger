"""Akzeptanztests: Fehlschläge in finish_file sind im Log, im Digest und im Health-Lauf sichtbar."""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

import sc_digger.main as m
from sc_digger.models import Config
from sc_digger.output import _fmt_track
from sc_digger.web.status import ALERT_LABELS
from tests.test_modes import mk


def _cfg(tmp_path: Path, **organize) -> Config:
    return Config({
        "organize": {"enabled": True, "write_tags": True, **organize},
        "search": {"bpm_min": 140.0, "bpm_max": 170.0},
        "state": {"db_path": str(tmp_path / "seen.sqlite"),
                  "track_db_path": str(tmp_path / "tracks.sqlite")},
        "health": {"alert_after_bad_runs": 2},
    })


def _datei(tmp_path: Path) -> tuple[Path, Path]:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    audio = inbox / "a.wav"
    audio.write_bytes(b"x")
    return inbox, audio


def _patch(monkeypatch, **fehler):
    """Alle Schritte laufen glatt; wer in `fehler` steht, wirft."""
    monkeypatch.setattr(m, "finalize_quality", lambda t, p, inbox, cfg: p)
    monkeypatch.setattr(m, "analyze_track", fehler.get("analyse") or (
        lambda p: {"bpm": 150.0, "key_camelot": "8A", "key_name": "Am"}))
    monkeypatch.setattr(m, "write_tags", fehler.get("tagging") or (lambda p, **k: None))
    monkeypatch.setattr(m, "find_first_kick", fehler.get("struktur") or (lambda p: None))
    monkeypatch.setattr(m, "analyze_structure", lambda p: [])
    monkeypatch.setattr(m, "organize", fehler.get("organize") or (lambda p, inbox, **k: p))


def _boom(msg):
    def f(*a, **k):
        raise RuntimeError(msg)
    return f


def test_erfolg_hinterlaesst_keine_probleme(tmp_path, monkeypatch):
    inbox, audio = _datei(tmp_path)
    _patch(monkeypatch)
    t = mk(1)
    m.finish_file(t, audio, inbox, _cfg(tmp_path))
    assert t.problems == []


def test_alle_schritte_fehlgeschlagen_werden_in_reihenfolge_gemerkt(tmp_path, monkeypatch):
    inbox, audio = _datei(tmp_path)
    _patch(monkeypatch, analyse=_boom("a"), tagging=_boom("t"), struktur=_boom("s"),
           organize=_boom("o"))
    t = mk(2)
    assert m.finish_file(t, audio, inbox, _cfg(tmp_path)) == audio
    assert t.problems == ["Audio-Analyse", "Tagging", "Strukturanalyse", "Organize"]
    assert any("Tagging fehlgeschlagen" in n for n in t.notes)  # Notizen bleiben wie bisher


def test_fehlschlag_steht_im_log(tmp_path, monkeypatch, caplog):
    inbox, audio = _datei(tmp_path)
    _patch(monkeypatch, tagging=_boom("Header kaputt"))
    t = mk(3, title="Mein Track")
    with caplog.at_level(logging.WARNING):
        m.finish_file(t, audio, inbox, _cfg(tmp_path))
    assert "Nachbearbeitung Tagging fehlgeschlagen für Mein Track" in caplog.text
    assert "RuntimeError: Header kaputt" in caplog.text


def test_track_db_fehler_wird_gemerkt(tmp_path, monkeypatch):
    inbox, audio = _datei(tmp_path)
    _patch(monkeypatch)
    sperre = tmp_path / "datei"
    sperre.write_text("kein Ordner")
    cfg = _cfg(tmp_path)
    cfg.raw["state"]["track_db_path"] = str(sperre / "tracks.sqlite")
    t = mk(4)
    m.finish_file(t, audio, inbox, cfg)
    assert t.problems == ["Track-DB"]


def test_digest_zeigt_hinweiszeile_nur_bei_problemen():
    sauber = mk(5)
    kaputt = mk(6)
    kaputt.problems = ["Tagging", "Organize"]
    assert "Nachbearbeitung" not in _fmt_track(sauber)
    assert "⚠️ Nachbearbeitung fehlgeschlagen: Tagging, Organize" in _fmt_track(kaputt)


def _lauf(tmp_path: Path) -> list[tuple]:
    db = tmp_path / "seen.sqlite"
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        return con.execute(
            "SELECT ok, found, error FROM runs WHERE mode='nachbearbeitung' ORDER BY id").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _geprueft(i, *probleme):
    t = mk(i)
    t.quality_report = {"ok": True}
    t.problems = list(probleme)
    return t


def test_lauf_ohne_probleme_ist_ok(tmp_path):
    m.record_nachbearbeitung(_cfg(tmp_path), [_geprueft(1), _geprueft(2)], no_telegram=True)
    assert _lauf(tmp_path) == [(1, 0, None)]


def test_lauf_mit_problemen_nennt_schritte_und_zahlen(tmp_path):
    tracks = [_geprueft(1, "Tagging", "Organize"), _geprueft(2, "Tagging"), _geprueft(3)]
    m.record_nachbearbeitung(_cfg(tmp_path), tracks, no_telegram=True)
    (ok, found, error), = _lauf(tmp_path)
    assert (ok, found) == (0, 2)
    assert error == "2 von 3 Tracks: Organize 1, Tagging 2"


def test_ohne_geladene_tracks_kein_lauf(tmp_path):
    ungeladen = mk(1)  # quality_report ist None: nur verlinkt, nie geprüft
    m.record_nachbearbeitung(_cfg(tmp_path), [ungeladen], no_telegram=True)
    m.record_nachbearbeitung(_cfg(tmp_path), [], no_telegram=True)
    assert _lauf(tmp_path) == []


def test_zweiter_schlechter_lauf_schickt_telegram_alarm(tmp_path, monkeypatch):
    gesendet = []
    monkeypatch.setattr(m, "send_telegram", lambda cfg, msgs, *a, **k: gesendet.append(msgs))
    cfg = _cfg(tmp_path)
    m.record_nachbearbeitung(cfg, [_geprueft(1, "Tagging")], no_telegram=False)
    assert gesendet == []
    m.record_nachbearbeitung(cfg, [_geprueft(2, "Tagging")], no_telegram=False)
    assert len(gesendet) == 1
    text = "\n".join(gesendet[0])
    assert "nachbearbeitung" in text and "Tagging 1" in text


def test_ohne_state_db_wird_nichts_geschrieben_und_nichts_geworfen():
    m.record_nachbearbeitung(Config({}), [_geprueft(1, "Tagging")], no_telegram=True)


def test_statusseite_kennt_den_modus():
    assert ALERT_LABELS["nachbearbeitung"] == "Nachbearbeitung"
    assert m.NACHBEARBEITUNG_MODE == "nachbearbeitung"
