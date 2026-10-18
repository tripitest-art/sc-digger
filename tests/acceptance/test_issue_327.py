"""Akzeptanztests: Strukturanalyse und Cue-Tags in finish_file()."""
from pathlib import Path
import subprocess
import pytest

from sc_digger import cues
from sc_digger import main as m
from sc_digger.db import TrackDB
from sc_digger.models import Config, Track
from sc_digger.structure import Segment

ROOT = Path(__file__).resolve().parents[2]


def _make_wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "anoisesrc=d=1:c=pink:r=44100:a=0.2",
            "-ac", "1", str(path),
        ],
        check=True,
    )
    return path


def _make_track() -> Track:
    t = Track(
        id=901,
        title="Industrial Tool",
        url="[https://soundcloud.com/test/industrial-tool](https://soundcloud.com/test/industrial-tool)",
        artist="Test Artist",
        artist_url="",
        created_at="",
        duration_ms=180000,
        genre="Schranz",
        tags=[],
        description="",
        bpm=150.0,
        plays=0,
        likes=0,
        reposts=0,
        comments=0,
        downloadable=True,
        has_downloads_left=True,
        purchase_url=None,
        purchase_title=None,
    )
    t.quality_report = {"ok": True, "clipped": False, "bitrate_kbps": 1411.0, "ext": "wav"}
    return t


def test_finish_file_schreibt_cue_tags_und_breakdown_ratio(tmp_path: Path, monkeypatch):
    cfg = Config.load(ROOT / "config.yaml")
    track_db = tmp_path / "tracks.sqlite"
    inbox = tmp_path / "inbox"
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(track_db)}
    cfg.raw["download"]["inbox_dir"] = str(inbox)
    cfg.raw["organize"]["enabled"] = False
    cfg.raw["rekordbox"]["cues_phrase_align"] = False

    audio = _make_wav(inbox / "track.wav")
    t = _make_track()

    segs = [
        Segment("intro", 0.0, 6.4),
        Segment("main", 6.4, 25.6),
        Segment("breakdown", 25.6, 38.4),
        Segment("main", 38.4, 57.6),
        Segment("outro", 57.6, 64.0),
    ]

    monkeypatch.setattr(m, "finalize_quality", lambda trk, p, inb, c: p)
    monkeypatch.setattr(m, "analyze_track", lambda p: {"bpm": 150.0, "key_camelot": "8A", "key_name": "Am"})
    monkeypatch.setattr(m, "find_first_kick", lambda p: 6.4)
    monkeypatch.setattr(m, "analyze_structure", lambda p: segs)

    out_path = m.finish_file(t, audio, inbox, cfg)
    assert out_path == audio

    assert cues.read_first_kick_tag(out_path) == pytest.approx(6.4, abs=0.001)
    assert cues.read_drop_tag(out_path) == pytest.approx(38.4, abs=0.001)
    assert cues.read_breakdown_tag(out_path) == pytest.approx(25.6, abs=0.001)
    assert t.breakdown_ratio == pytest.approx(0.25, abs=0.001)

    with TrackDB(track_db) as db:
        rec = db.get_track_by_path(out_path)
        assert rec is not None
        assert rec.breakdown_ratio == pytest.approx(0.25, abs=0.001)


def test_finish_file_quantisiert_drop_und_breakdown_bei_phrase_align(tmp_path: Path, monkeypatch):
    cfg = Config.load(ROOT / "config.yaml")
    inbox = tmp_path / "inbox"
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["download"]["inbox_dir"] = str(inbox)
    cfg.raw["organize"]["enabled"] = False
    cfg.raw["rekordbox"]["cues_phrase_align"] = True

    audio = _make_wav(inbox / "align.wav")
    t = _make_track()

    # 150 BPM -> 1 Beat = 0.4 s, 16 Beats = 6.4 s.
    # first_kick = 0.0 -> Phrasengrenzen bei 12.8 s (32 Beats) und 25.6 s (64 Beats).
    segs = [
        Segment("main", 0.0, 12.5),
        Segment("breakdown", 12.5, 25.3),
        Segment("main", 25.3, 51.2),
    ]

    monkeypatch.setattr(m, "finalize_quality", lambda trk, p, inb, c: p)
    monkeypatch.setattr(m, "analyze_track", lambda p: {"bpm": 150.0, "key_camelot": "8A", "key_name": "Am"})
    monkeypatch.setattr(m, "find_first_kick", lambda p: 0.0)
    monkeypatch.setattr(m, "analyze_structure", lambda p: segs)

    out_path = m.finish_file(t, audio, inbox, cfg)
    assert out_path == audio
    assert cues.read_first_kick_tag(out_path) == pytest.approx(0.0, abs=0.001)
    assert cues.read_breakdown_tag(out_path) == pytest.approx(12.8, abs=0.001)
    assert cues.read_drop_tag(out_path) == pytest.approx(25.6, abs=0.001)


def test_finish_file_strukturfehler_bricht_lauf_nicht_ab(tmp_path: Path, monkeypatch):
    cfg = Config.load(ROOT / "config.yaml")
    inbox = tmp_path / "inbox"
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["download"]["inbox_dir"] = str(inbox)
    cfg.raw["organize"]["enabled"] = False

    audio = _make_wav(inbox / "err.wav")
    t = _make_track()

    monkeypatch.setattr(m, "finalize_quality", lambda trk, p, inb, c: p)
    monkeypatch.setattr(m, "analyze_track", lambda p: {"bpm": 150.0, "key_camelot": "8A", "key_name": "Am"})

    def _boom(_path):
        raise RuntimeError("Kaputter Header")

    monkeypatch.setattr(m, "find_first_kick", _boom)

    out_path = m.finish_file(t, audio, inbox, cfg)
    assert out_path == audio
    assert any("Strukturanalyse fehlgeschlagen" in n for n in t.notes)
