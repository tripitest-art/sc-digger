"""Unit-Tests für sc_digger.intake und run_intake in main.py.

Deckt ab:
- Tags aus einer echten Datei gelesen (synthetisches Audio per ffmpeg)
- Duplikat per Fingerprint → Zeile „schon in der Sammlung" (nicht im Scope, da kein
  Fuzzy-Match in intake – nur der Fingerprint in finalize_quality, der hier gemonkeypatcht ist)
- CLI-Modus intake im Dry-Run
- Korrekte Track-Erstellung aus Dateinamen mit und ohne Artist
- Verschiedene Audio-Endungen
"""
import os
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from sc_digger.intake import AUDIO_EXTS, find_ready_files, track_from_file
from sc_digger import main as m
from sc_digger.models import Config
import sc_digger.output as out

ROOT = Path(__file__).resolve().parents[1]
CFG = Config.load(ROOT / "config.yaml")
OLD = time.time() - 3600
OK = {"ok": True, "clipped": False, "ext": "wav", "bitrate_kbps": 1411, "reason": "echte Qualität"}


def put(path: Path, age: float = OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    os.utime(path, (age, age))
    return path


def _make_wav(path: Path, seconds: int = 2, sr: int = 44100) -> Path:
    """Erzeugt eine echte WAV-Datei per ffmpeg (synthetisches Audio)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n = seconds * sr
    audio = (np.random.default_rng(42).standard_normal(n) * 0.1).astype(np.float32)
    raw = path.with_suffix(".f32")
    raw.write_bytes(audio.tobytes())
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1",
         "-i", str(raw), str(path)],
        check=True,
    )
    raw.unlink()
    os.utime(path, (OLD, OLD))
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    cfg = Config(dict(CFG.raw))
    cfg.raw["search"] = {**CFG["search"], "tags": ["schranz"], "followed_users": [],
                         "reference_accounts": []}
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
    tags = []
    monkeypatch.setattr(m, "write_tags", lambda p, **kw: tags.append(kw) or True)
    return cfg, inbox, inbox / "_eingang", tags


# ------------ intake.py unit tests ------------

class TestFindReadyFiles:
    def test_returns_sorted_by_relative_path(self, tmp_path):
        d = tmp_path / "intake"
        put(d / "z.wav")
        put(d / "a.mp3")
        put(d / "m.flac")
        result = find_ready_files(d, 0)
        names = [p.name for p in result]
        assert names == ["a.mp3", "m.flac", "z.wav"]

    def test_includes_all_file_types(self, tmp_path):
        """find_ready_files gibt auch Nicht-Audio-Dateien zurück – die Filterung
        nach Endung passiert in run_intake, nicht in find_ready_files."""
        d = tmp_path / "intake"
        put(d / "file.txt")
        put(d / "track.wav")
        result = find_ready_files(d, 0)
        assert len(result) == 2

    def test_skips_hidden_files(self, tmp_path):
        d = tmp_path / "intake"
        put(d / ".hidden.wav")
        put(d / "visible.wav")
        result = find_ready_files(d, 0)
        assert len(result) == 1
        assert result[0].name == "visible.wav"

    def test_nonexistent_dir_returns_empty(self, tmp_path):
        assert find_ready_files(tmp_path / "nope", 0) == []


class TestTrackFromFile:
    def test_parses_artist_dash_title(self, tmp_path):
        p = put(tmp_path / "Some Artist - Cool Track (Edit).wav")
        t = track_from_file(p)
        assert t.artist == "Some Artist"
        assert t.title == "Cool Track (Edit)"

    def test_no_dash_uses_stem(self, tmp_path):
        p = put(tmp_path / "only_a_filename.mp3")
        t = track_from_file(p)
        assert t.artist == ""
        assert t.title == "only_a_filename"

    def test_multiple_dashes_splits_on_first(self, tmp_path):
        p = put(tmp_path / "A - B - C.wav")
        t = track_from_file(p)
        assert t.artist == "A"
        assert t.title == "B - C"

    def test_track_has_zero_defaults(self, tmp_path):
        p = put(tmp_path / "X.wav")
        t = track_from_file(p)
        assert t.id == 0
        assert t.plays == 0
        assert t.url == ""
        assert t.bpm is None

    def test_reads_mutagen_tags_with_arbitrary_filename(self, tmp_path):
        """Tags aus einer echten Datei gelesen, wenn Dateiname kein Artist - Titel ist."""
        p = _make_wav(tmp_path / "track_v2_final_master.wav")
        from mutagen.wave import WAVE
        from mutagen.id3 import TIT2, TPE1
        w = WAVE(p)
        w.add_tags()
        w.tags.add(TIT2(encoding=3, text=["My Track"]))
        w.tags.add(TPE1(encoding=3, text=["My Artist"]))
        w.save()
        t = track_from_file(p)
        assert t.artist == "My Artist"
        assert t.title == "My Track"

    def test_reads_easy_tags_from_mp3(self, tmp_path):
        """Easy-Tags aus MP3 mit beliebigem Dateinamen."""
        p = tmp_path / "random_file_001.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "1", str(p)],
            check=True,
        )
        from mutagen import File
        f = File(p, easy=True)
        f["artist"] = ["Producer Z"]
        f["title"] = ["Epic Anthem"]
        f.save()
        t = track_from_file(p)
        assert t.artist == "Producer Z"
        assert t.title == "Epic Anthem"


class TestAudioExts:
    def test_contains_common_formats(self):
        for ext in (".wav", ".mp3", ".flac", ".aiff", ".aif", ".m4a"):
            assert ext in AUDIO_EXTS


# ------------ run_intake integration tests ------------

class TestRunIntake:
    def test_tags_written_with_real_wav(self, tmp_path, monkeypatch):
        """Synthetisches Audio per ffmpeg → Dateiname-basierte Tags werden geschrieben."""
        inbox = tmp_path / "inbox"
        intake = inbox / "_eingang"
        cfg = Config(dict(CFG.raw))
        cfg.raw["search"] = {**CFG["search"], "tags": ["schranz"], "followed_users": [],
                             "reference_accounts": []}
        cfg.raw["state"] = {**CFG["state"], "db_path": str(tmp_path / "state.sqlite"),
                            "track_db_path": str(tmp_path / "tracks.sqlite")}
        cfg.raw["download"] = {**CFG["download"], "collection_dir": str(tmp_path / "coll"),
                               "inbox_dir": str(inbox), "intake_dir": str(intake),
                               "intake_min_age_s": 0}
        cfg.raw["fingerprint"] = {"check_downloads": False}
        cfg.raw["rekordbox"] = {"xml_enabled": False}

        monkeypatch.setattr(out, "check_file", lambda p, cfg: dict(OK))
        monkeypatch.setattr(m, "analyze_track", lambda p: {"bpm": 155.0, "key_camelot": "5A",
                                                            "key_name": "Fm"})
        written = []
        monkeypatch.setattr(m, "write_tags", lambda p, **kw: written.append((p, kw)) or True)

        wav = _make_wav(intake / "Test Artist - Test Track.wav")
        lines = m.run_intake(cfg)
        assert len(lines) == 2
        assert "1 Datei verarbeitet" in lines[0]
        assert "Test Artist" in lines[1]
        assert written
        _, kw = written[0]
        assert kw["artist"] == "Test Artist"
        assert kw["title"] == "Test Track"
        assert kw["bpm"] == 155.0

    def test_duplicate_via_fingerprint_rejected(self, env, monkeypatch):
        """Duplikat per Fingerprint → Zeile ‚schon in der Sammlung‘."""
        cfg, inbox, intake, _ = env
        cfg.raw["fingerprint"]["check_downloads"] = True
        monkeypatch.setattr(out, "compute_fingerprint", lambda p: "fake_fp")
        from unittest.mock import MagicMock
        rec = MagicMock()
        rec.path = "/coll/Known - Track.wav"
        monkeypatch.setattr(out, "find_same_recording", lambda fp, db: rec)
        put(intake / "Known - Track.wav")
        lines = m.run_intake(cfg)
        assert (inbox / "_rejected" / "duplicate" / "Known - Track.wav").exists()
        assert any("♻️" in l and "Known - Track.wav: schon in der Sammlung" in l for l in lines)

    def test_clipped_file_goes_to_rejected_clipped(self, env, monkeypatch):
        """Datei mit clipped: True landet in _rejected/clipped/."""
        cfg, inbox, intake, _ = env
        monkeypatch.setattr(out, "check_file", lambda p, cfg: {
            **OK, "ok": True, "clipped": True,
            "loudness_range_lu": 1.8, "true_peak_dbfs": 3.5,
        })
        put(intake / "Brickwall - Master.wav")
        lines = m.run_intake(cfg)
        assert (inbox / "_rejected" / "clipped" / "Brickwall - Master.wav").exists()
        assert any("❌" in l and "Brickwall - Master.wav" in l for l in lines)

    def test_process_calls_finish_file(self, tmp_path, monkeypatch):
        """process() delegiert die Nachbearbeitung nach dem Download an finish_file()."""
        inbox = tmp_path / "inbox"
        cfg = Config(dict(CFG.raw))
        cfg.raw["download"] = {**CFG["download"], "inbox_dir": str(inbox)}
        from sc_digger.models import Track
        t = Track(
            id=123, title="Test", url="", artist="Artist", artist_url="",
            created_at="", duration_ms=0, genre="", tags=[], description="",
            bpm=None, plays=0, likes=0, reposts=0, comments=0,
            downloadable=True, has_downloads_left=True, purchase_url=None, purchase_title=None,
        )
        t.download_kind = out.DownloadKind.NATIVE
        called = []
        monkeypatch.setattr(m, "download_native", lambda trk, inb, tok: put(inbox / "dl.wav"))
        monkeypatch.setattr(m, "finish_file", lambda trk, p, inb, c: called.append((trk, p)) or p)
        monkeypatch.setenv("SOUNDCLOUD_AUTH_TOKEN", "dummy")
        m.process([t], cfg, dry_run=False, skip_duplicates=False)
        assert len(called) == 1
        assert called[0][0].id == 123

    def test_dry_run_lists_files_without_moving(self, env):
        cfg, inbox, intake, _ = env
        f = put(intake / "Artist - Track.wav")
        lines = m.run_intake(cfg, dry_run=True)
        assert len(lines) == 2
        assert "Dry-Run" in lines[0]
        assert "Artist – Track" in lines[1]
        assert f.exists()
        assert not list(inbox.glob("*/*/Artist - Track.wav"))

    def test_empty_intake_returns_empty(self, env):
        cfg, inbox, intake, _ = env
        intake.mkdir(parents=True, exist_ok=True)
        assert m.run_intake(cfg) == []

    def test_multiple_files_processed_in_order(self, env):
        cfg, inbox, intake, tags = env
        put(intake / "Bravo - Second.wav")
        put(intake / "Alpha - First.wav")
        lines = m.run_intake(cfg)
        assert len(lines) == 3
        assert "2 Dateien verarbeitet" in lines[0]
        # Alphabetisch sortiert
        assert "Alpha – First" in lines[1]
        assert "Bravo – Second" in lines[2]

    def test_mixed_audio_and_non_audio(self, env):
        cfg, inbox, intake, _ = env
        put(intake / "Track.wav")
        put(intake / "notes.txt")
        lines = m.run_intake(cfg)
        assert len(lines) == 3
        assert "2 Dateien verarbeitet" in lines[0]
        # notes.txt ist kein Audio → _unbekannt
        txt_line = [l for l in lines if "notes.txt" in l]
        assert txt_line
        assert "_unbekannt" in txt_line[0]

    def test_cli_intake_dry_run(self, env, monkeypatch, capsys):
        """CLI-Modus 'intake --dry-run' gibt Vorschau aus, schreibt keine XML."""
        cfg, inbox, intake, _ = env
        put(intake / "Test - Track.wav")
        import sys
        monkeypatch.setattr(Config, "load", lambda p: cfg)
        xml_called = []
        monkeypatch.setattr(m, "write_rekordbox_xml", lambda c: xml_called.append(c))
        monkeypatch.setattr(sys, "argv", ["sc-digger", "intake", "--dry-run"])
        m.cli()
        out = capsys.readouterr().out
        assert "Eingang" in out and "Test - Track.wav" in out
        assert not xml_called
        assert (intake / "Test - Track.wav").exists()

    def test_cli_intake_runs_and_writes_xml(self, env, monkeypatch, capsys):
        """CLI-Modus 'intake' verarbeitet Dateien und schreibt die Rekordbox-XML."""
        cfg, inbox, intake, _ = env
        put(intake / "Test - Track.wav")
        import sys
        monkeypatch.setattr(Config, "load", lambda p: cfg)
        xml_called = []
        monkeypatch.setattr(m, "write_rekordbox_xml", lambda c: xml_called.append(c))
        monkeypatch.setattr(sys, "argv", ["sc-digger", "intake", "--no-telegram"])
        m.cli()
        assert xml_called
        assert not (intake / "Test - Track.wav").exists()
