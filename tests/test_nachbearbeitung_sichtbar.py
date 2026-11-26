"""Eigene Tests zu Issue #761: fehlgeschlagene Nachbearbeitungsschritte werden sichtbar.

Deckt ab, was die Akzeptanztests nicht prüfen: die Eingangs-Zeile von run_intake, den Aufruf
von record_nachbearbeitung in _discover (genau einmal, im Probelauf nie) und WAV→FLAC.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from sc_digger import main as m
from sc_digger.models import Config
import sc_digger.output as out
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[1]
CFG = Config.load(ROOT / "config.yaml")
OK = {"ok": True, "clipped": False, "ext": "wav", "bitrate_kbps": 1411, "reason": "echte Qualität"}


# ---------------------------------------------------------------- run_intake
def _intake_env(tmp_path: Path, monkeypatch) -> tuple[Config, Path]:
    inbox = tmp_path / "inbox"
    cfg = Config(dict(CFG.raw))
    cfg.raw["state"] = {**CFG["state"], "db_path": str(tmp_path / "state.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                           "inbox_dir": str(inbox), "intake_dir": str(inbox / "_eingang"),
                           "intake_min_age_s": 120}
    cfg.raw["fingerprint"] = {"check_downloads": False}
    cfg.raw["rekordbox"] = {"xml_enabled": False}
    monkeypatch.setattr(out, "check_file", lambda p, cfg: dict(OK))
    monkeypatch.setattr(m, "analyze_track", lambda p: {"bpm": 152.0, "key_camelot": "6A",
                                                        "key_name": "Gm"})
    monkeypatch.setattr(m, "write_tags", lambda p, **kw: True)
    intake = inbox / "_eingang"
    intake.mkdir(parents=True)
    f = intake / "Artist - Track.wav"
    f.write_bytes(b"x")
    alt = time.time() - 3600
    os.utime(f, (alt, alt))
    return cfg, intake


def test_intake_zeile_ohne_problem_unveraendert(tmp_path, monkeypatch):
    cfg, _ = _intake_env(tmp_path, monkeypatch)
    lines = m.run_intake(cfg)
    assert lines[1] == "✅ Artist – Track · 152 BPM · 6A"


def test_intake_zeile_nennt_fehlgeschlagene_schritte(tmp_path, monkeypatch):
    cfg, _ = _intake_env(tmp_path, monkeypatch)

    def boom(*a, **k):
        raise RuntimeError("/geheimer/pfad kaputt")

    monkeypatch.setattr(m, "write_tags", boom)
    monkeypatch.setattr(m, "find_first_kick", boom)
    lines = m.run_intake(cfg)
    assert lines[1] == ("✅ Artist – Track · 152 BPM · 6A · ⚠️ Nachbearbeitung: "
                        "Tagging, Strukturanalyse")
    assert "geheimer" not in lines[1]  # nur Schrittnamen, nie Ausnahmetexte


# ---------------------------------------------------------------- _discover
def _stub_discover(tmp_path: Path, monkeypatch, fresh: list) -> tuple[Config, list]:
    cfg = Config({**CFG.raw, "state": {**CFG["state"],
                                       "db_path": str(tmp_path / "seen.sqlite"),
                                       "track_db_path": str(tmp_path / "tracks.sqlite")}})
    reihenfolge: list = []
    discovery = m.Discovery(tracks=[], reference_ids=set(), total_sources=1, succeeded=1,
                            failed=[], aborted=None, first_error=None, exploration_used=[])
    monkeypatch.setattr(m, "SoundCloudClient", lambda: object())
    monkeypatch.setattr(m, "collect_sources", lambda sc, s: discovery)
    monkeypatch.setattr(m, "retry_downloads", lambda sc, cfg: [])
    monkeypatch.setattr(m, "run_intake", lambda cfg: [])
    monkeypatch.setattr(m, "snapshot_tracks", lambda cfg, tracks: None)

    def process(tracks, cfg, dry_run):
        reihenfolge.append("process")
        return fresh, []

    monkeypatch.setattr(m, "process", process)
    monkeypatch.setattr(m, "record_nachbearbeitung",
                        lambda cfg, tracks, *, no_telegram:
                        reihenfolge.append(("nachbearbeitung", tracks, no_telegram)))
    monkeypatch.setattr(m, "deliver", lambda *a, **k: reihenfolge.append("deliver"))
    monkeypatch.setattr(m, "record_exploration_stats", lambda *a, **k: None)
    monkeypatch.setattr(m, "write_rekordbox_xml", lambda cfg: None)
    monkeypatch.setattr(m, "run_rekordbox_import", lambda cfg, no_telegram=False: None)
    monkeypatch.setattr(m, "is_sunday", lambda now=None: False)
    return cfg, reihenfolge


def test_discover_ruft_record_nachbearbeitung_genau_einmal_nach_process(tmp_path, monkeypatch):
    t = mk(701)
    t.quality_report = dict(OK)
    t.problems = ["Tagging"]
    cfg, reihenfolge = _stub_discover(tmp_path, monkeypatch, [t])
    m._discover(cfg, dry_run=False, no_telegram=True)
    aufrufe = [r for r in reihenfolge if isinstance(r, tuple)]
    assert aufrufe == [("nachbearbeitung", [t], True)]
    assert reihenfolge.index(aufrufe[0]) == reihenfolge.index("process") + 1


def test_discover_probelauf_ruft_record_nachbearbeitung_nie(tmp_path, monkeypatch):
    cfg, reihenfolge = _stub_discover(tmp_path, monkeypatch, [mk(702)])
    m._discover(cfg, dry_run=True, no_telegram=True)
    assert "process" in reihenfolge
    assert not [r for r in reihenfolge if isinstance(r, tuple)]


# ---------------------------------------------------------------- WAV→FLAC
def _finish_env(tmp_path: Path, monkeypatch) -> tuple[Config, Path, Path]:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    audio = inbox / "a.wav"
    audio.write_bytes(b"x")
    cfg = Config({
        "organize": {"enabled": False, "write_tags": False, "detect_bpm": False,
                     "detect_key": False, "wav_to_flac": True},
        "search": {"bpm_min": 140.0, "bpm_max": 170.0},
        "state": {"db_path": str(tmp_path / "seen.sqlite")},
    })
    monkeypatch.setattr(m, "finalize_quality", lambda t, p, inbox, cfg: p)
    return cfg, inbox, audio


def test_wav_to_flac_fehlschlag_wird_gemerkt_und_geloggt(tmp_path, monkeypatch, caplog):
    cfg, inbox, audio = _finish_env(tmp_path, monkeypatch)
    monkeypatch.setattr(m, "convert_wav_to_flac", lambda p: None)
    t = mk(703, title="Wellen")
    with caplog.at_level(logging.WARNING):
        assert m.finish_file(t, audio, inbox, cfg) == audio
    assert t.problems == ["WAV→FLAC"]
    assert "WAV→FLAC fehlgeschlagen, WAV bleibt" in t.notes  # Notiz unverändert
    assert "Nachbearbeitung WAV→FLAC fehlgeschlagen für Wellen" in caplog.text


def test_wav_to_flac_erfolg_ohne_problem(tmp_path, monkeypatch):
    cfg, inbox, audio = _finish_env(tmp_path, monkeypatch)
    flac = audio.with_suffix(".flac")
    monkeypatch.setattr(m, "convert_wav_to_flac", lambda p: flac)
    t = mk(704)
    assert m.finish_file(t, audio, inbox, cfg) == flac
    assert t.problems == []


# ---------------------------------------------------------------- Helfer
def test_nachbearbeitung_fehler_merkt_schritt_nur_einmal():
    t = mk(705)
    m._nachbearbeitung_fehler(t, "Tagging", RuntimeError("a"))
    m._nachbearbeitung_fehler(t, "Organize", RuntimeError("b"))
    m._nachbearbeitung_fehler(t, "Tagging", RuntimeError("c"))
    assert t.problems == ["Tagging", "Organize"]
    assert t.notes == []  # Notizen bleiben Sache der Aufrufer


def test_digest_hinweis_ist_html_escaped():
    t = mk(706)
    t.problems = ["<b>Tagging</b>"]
    text = out._fmt_track(t)
    assert "&lt;b&gt;Tagging&lt;/b&gt;" in text
    assert "<b>Tagging</b>" not in text
