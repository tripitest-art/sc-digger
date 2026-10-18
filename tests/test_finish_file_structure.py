"""Unit-Tests für Strukturanalyse und Cue-Tags in finish_file() (Issue #327)."""
from pathlib import Path
import subprocess
import pytest

from sc_digger import cues
from sc_digger import main as m
from sc_digger.db import TrackDB
from sc_digger.models import Config, Track
from sc_digger.structure import Segment

ROOT = Path(__file__).resolve().parents[1]


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
        id=999,
        title="Schranz Banger",
        url="https://soundcloud.com/test/schranz-banger",
        artist="Test Producer",
        artist_url="",
        created_at="",
        duration_ms=240000,
        genre="Schranz",
        tags=[],
        description="",
        bpm=152.0,
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


def test_track_ohne_breakdown_schreibt_nur_first_kick(tmp_path: Path, monkeypatch):
    """Track ohne Breakdown: nur SCDIGGER_FIRST_KICK geschrieben, kein Drop- oder Breakdown-Tag."""
    cfg = Config.load(ROOT / "config.yaml")
    track_db = tmp_path / "tracks.sqlite"
    inbox = tmp_path / "inbox"
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(track_db)}
    cfg.raw["download"]["inbox_dir"] = str(inbox)
    cfg.raw["organize"]["enabled"] = False
    cfg.raw["organize"]["write_tags"] = True

    audio = _make_wav(inbox / "no_breakdown.wav")
    t = _make_track()

    segs = [
        Segment("intro", 0.0, 5.0),
        Segment("main", 5.0, 60.0),
    ]

    monkeypatch.setattr(m, "finalize_quality", lambda trk, p, inb, c: p)
    monkeypatch.setattr(m, "analyze_track", lambda p: {"bpm": 152.0, "key_camelot": "6A", "key_name": "Gm"})
    monkeypatch.setattr(m, "find_first_kick", lambda p: 5.0)
    monkeypatch.setattr(m, "analyze_structure", lambda p: segs)

    out_path = m.finish_file(t, audio, inbox, cfg)
    assert out_path == audio

    assert cues.read_first_kick_tag(out_path) == pytest.approx(5.0, abs=0.001)
    assert cues.read_drop_tag(out_path) is None
    assert cues.read_breakdown_tag(out_path) is None
    assert t.breakdown_ratio == pytest.approx(0.0, abs=0.001)

    with TrackDB(track_db) as db:
        rec = db.get_track_by_path(out_path)
        assert rec is not None
        assert rec.breakdown_ratio == pytest.approx(0.0, abs=0.001)


def test_write_tags_false_ueberspringt_strukturanalyse_und_cues(tmp_path: Path, monkeypatch):
    """organize.write_tags: false überspringt die Strukturanalyse und das Schreiben von Cue-Tags."""
    cfg = Config.load(ROOT / "config.yaml")
    track_db = tmp_path / "tracks.sqlite"
    inbox = tmp_path / "inbox"
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(track_db)}
    cfg.raw["download"]["inbox_dir"] = str(inbox)
    cfg.raw["organize"]["enabled"] = False
    cfg.raw["organize"]["write_tags"] = False

    audio = _make_wav(inbox / "skip_tags.wav")
    t = _make_track()

    called = []

    def _should_not_run(p):
        called.append(True)
        return 0.0

    monkeypatch.setattr(m, "finalize_quality", lambda trk, p, inb, c: p)
    monkeypatch.setattr(m, "analyze_track", lambda p: {"bpm": 152.0, "key_camelot": "6A", "key_name": "Gm"})
    monkeypatch.setattr(m, "find_first_kick", _should_not_run)

    out_path = m.finish_file(t, audio, inbox, cfg)
    assert out_path == audio
    assert not called, "find_first_kick darf bei write_tags=False nicht aufgerufen werden"
    assert t.breakdown_ratio is None
    assert cues.read_first_kick_tag(out_path) is None


def test_leere_segmente_setzen_breakdown_ratio_auf_none(tmp_path: Path, monkeypatch):
    """Keine Segmente erkannt: breakdown_ratio ist None, keine Tags außer evtl. first_kick."""
    cfg = Config.load(ROOT / "config.yaml")
    inbox = tmp_path / "inbox"
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["download"]["inbox_dir"] = str(inbox)
    cfg.raw["organize"]["enabled"] = False
    cfg.raw["organize"]["write_tags"] = True

    audio = _make_wav(inbox / "empty_segs.wav")
    t = _make_track()

    monkeypatch.setattr(m, "finalize_quality", lambda trk, p, inb, c: p)
    monkeypatch.setattr(m, "analyze_track", lambda p: {"bpm": 152.0, "key_camelot": "6A", "key_name": "Gm"})
    monkeypatch.setattr(m, "find_first_kick", lambda p: None)
    monkeypatch.setattr(m, "analyze_structure", lambda p: [])

    out_path = m.finish_file(t, audio, inbox, cfg)
    assert out_path == audio
    assert t.breakdown_ratio is None
    assert cues.read_first_kick_tag(out_path) is None
    assert cues.read_drop_tag(out_path) is None
    assert cues.read_breakdown_tag(out_path) is None
