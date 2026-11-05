"""Akzeptanztests B4 Teil 1: BPM-/Key-Sicherheit in der Track-DB und im Set-Builder."""
import subprocess
from pathlib import Path

import pytest

from sc_digger import db as db_mod
from sc_digger import dj_export
from sc_digger import main as m
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config, Track
from sc_digger.structure import Segment
from sc_digger.web.database import load_tracks_for_set_builder

ROOT = Path(__file__).resolve().parents[2]


def _rec(path: str, bpm: float, camelot: str, **kw) -> TrackRecord:
    return TrackRecord(path=path, mtime=1.0, size=1, bpm=bpm, key_camelot=camelot,
                       artist="A", title=Path(path).stem, status="inbox", **kw)


def test_migration_16_ist_registriert():
    eintraege = {v: name for v, name, _ in db_mod.MIGRATIONS}
    assert eintraege.get(16) == "0016_tracks_analysis_confidence"
    assert db_mod.check_migrations() == []


def test_track_record_speichert_sicherheitswerte(tmp_path: Path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(_rec("/inbox/a.flac", 150.0, "8A",
                             bpm_confidence=0.42, key_confidence=0.3, key_margin=0.02))
        got = db.get_track_by_path("/inbox/a.flac")
    assert got is not None
    assert got.bpm_confidence == pytest.approx(0.42)
    assert got.key_confidence == pytest.approx(0.3)
    assert got.key_margin == pytest.approx(0.02)


def test_upsert_ohne_werte_ueberschreibt_nicht(tmp_path: Path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(_rec("/inbox/a.flac", 150.0, "8A", bpm_confidence=0.9, key_margin=0.2))
        db.upsert_track(_rec("/inbox/a.flac", 150.0, "8A"))
        got = db.get_track_by_path("/inbox/a.flac")
    assert got.bpm_confidence == pytest.approx(0.9)
    assert got.key_margin == pytest.approx(0.2)


def test_schwellen_sind_konstanten_und_gelten_im_entry():
    assert dj_export.RELIABLE_BPM_MIN_CONFIDENCE == pytest.approx(0.5)
    assert dj_export.RELIABLE_KEY_MIN_MARGIN == pytest.approx(0.05)
    grenz = dj_export.Entry(path=Path("x.flac"), bpm=150.0, camelot="8A",
                            bpm_confidence=0.5, key_margin=0.05)
    knapp = dj_export.Entry(path=Path("y.flac"), bpm=150.0, camelot="8A",
                            bpm_confidence=0.49, key_margin=0.049)
    assert grenz.reliable_bpm and grenz.reliable_key
    assert not knapp.reliable_bpm and not knapp.reliable_key


def _set_builder_db(tmp_path: Path) -> Path:
    path = tmp_path / "tracks.sqlite"
    with TrackDB(path) as db:
        db.upsert_track(_rec("/inbox/sicher.flac", 150.0, "8A",
                             bpm_confidence=0.8, key_confidence=0.7, key_margin=0.12))
        db.upsert_track(_rec("/inbox/bpm_unsicher.flac", 151.0, "8A",
                             bpm_confidence=0.3, key_confidence=0.7, key_margin=0.12))
        db.upsert_track(_rec("/inbox/key_unsicher.flac", 152.0, "9A",
                             bpm_confidence=0.8, key_confidence=0.1, key_margin=0.01))
        db.upsert_track(_rec("/inbox/altbestand.flac", 153.0, "9A"))
    return path


def test_set_builder_liest_sicherheitswerte(tmp_path: Path):
    entries = load_tracks_for_set_builder(_set_builder_db(tmp_path))
    by_name = {e.path.name: e for e in entries}
    assert set(by_name) == {"sicher.flac", "bpm_unsicher.flac", "key_unsicher.flac", "altbestand.flac"}
    assert by_name["sicher.flac"].bpm_confidence == pytest.approx(0.8)
    assert by_name["sicher.flac"].key_confidence == pytest.approx(0.7)
    assert by_name["sicher.flac"].key_margin == pytest.approx(0.12)
    assert by_name["sicher.flac"].is_anchor
    assert not by_name["bpm_unsicher.flac"].reliable_bpm
    assert not by_name["key_unsicher.flac"].reliable_key
    assert by_name["altbestand.flac"].bpm_confidence == 0.0


def test_set_builder_meidet_unsichere(tmp_path: Path):
    entries = load_tracks_for_set_builder(_set_builder_db(tmp_path), avoid_uncertain=True)
    assert sorted(e.path.name for e in entries) == ["altbestand.flac", "sicher.flac"]


def test_avoid_uncertain_filtert_vor_dem_limit(tmp_path: Path):
    path = tmp_path / "tracks.sqlite"
    with TrackDB(path) as db:
        for i in range(3):
            db.upsert_track(_rec(f"/inbox/u{i}.flac", 150.0, "8A", bpm_confidence=0.1))
        db.upsert_track(_rec("/inbox/gut.flac", 150.0, "8A", bpm_confidence=0.9, key_margin=0.2))
    entries = load_tracks_for_set_builder(path, avoid_uncertain=True, limit=1)
    assert [e.path.name for e in entries] == ["gut.flac"]


def _make_wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                    "-i", "anoisesrc=d=1:c=pink:r=44100:a=0.2", "-ac", "1", str(path)],
                   check=True)
    return path


def _make_track() -> Track:
    t = Track(id=999, title="Schranz Banger", url="https://soundcloud.com/test/schranz-banger",
              artist="Test Producer", artist_url="", created_at="", duration_ms=240000,
              genre="Schranz", tags=[], description="", bpm=152.0, plays=0, likes=0,
              reposts=0, comments=0, downloadable=True, has_downloads_left=True,
              purchase_url=None, purchase_title=None)
    t.quality_report = {"ok": True, "clipped": False, "bitrate_kbps": 1411.0, "ext": "wav"}
    return t


def test_finish_file_speichert_sicherheitswerte(tmp_path: Path, monkeypatch):
    cfg = Config.load(ROOT / "config.yaml")
    track_db = tmp_path / "tracks.sqlite"
    inbox = tmp_path / "inbox"
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(track_db)}
    cfg.raw["download"]["inbox_dir"] = str(inbox)
    cfg.raw["organize"]["enabled"] = False
    cfg.raw["organize"]["write_tags"] = True
    audio = _make_wav(inbox / "track.wav")

    monkeypatch.setattr(m, "finalize_quality", lambda trk, p, inb, c: p)
    monkeypatch.setattr(m, "analyze_track", lambda p: {
        "bpm": 152.0, "bpm_confidence": 0.81, "bpm_octave": 1,
        "key_camelot": "6A", "key_name": "Gm", "key_confidence": 0.44, "key_margin": 0.07,
    })
    monkeypatch.setattr(m, "find_first_kick", lambda p: 5.0)
    monkeypatch.setattr(m, "analyze_structure", lambda p: [Segment("intro", 0.0, 5.0),
                                                           Segment("main", 5.0, 60.0)])

    out_path = m.finish_file(_make_track(), audio, inbox, cfg)

    with TrackDB(track_db) as db:
        rec = db.get_track_by_path(out_path)
    assert rec is not None
    assert rec.bpm_confidence == pytest.approx(0.81)
    assert rec.key_confidence == pytest.approx(0.44)
    assert rec.key_margin == pytest.approx(0.07)
