"""Tests für Auto-Cues im Sammlungs-XML-Export und audit --analyze-structure (Issue #342)."""
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from sc_digger import audit, cues
from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.structure import Segment

ROOT = Path(__file__).resolve().parents[1]


def _make_wav(path: Path) -> Path:
    """Kurze Rausch-WAV per ffmpeg (wie tests/test_finish_file_structure.py)."""
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


def _tagged_mp3(tmp_path: Path) -> Path:
    """MP3 mit Cue-Tags: First Kick 5.0 s, Drop 25.0 s, Breakdown 60.0 s (groove), Drop 2 90.0 s."""
    audio = tmp_path / "tagged.mp3"
    audio.write_bytes(b"\x00" * 100)
    assert cues.write_first_kick_tag(audio, 5.0)
    assert cues.write_drop_tag(audio, 25.0)
    assert cues.write_breakdown_tag(audio, 60.0, "groove")
    assert cues.write_drop2_tag(audio, 90.0)
    return audio


def _export(tmp_path: Path, audio: Path, *, hot_cues: bool = True, memory_cues: bool = True):
    """Legt den Track in der DB an und exportiert die Sammlungs-XML."""
    db_file = tmp_path / "test.db"
    xml_out = tmp_path / "collection.xml"
    stat = audio.stat()
    with TrackDB(str(db_file)) as db:
        db.upsert_track(TrackRecord(
            path=str(audio),
            mtime=stat.st_mtime,
            size=stat.st_size,
            artist="DJ Producer",
            title="Schranz Banger",
            bpm=150.0,
            key_camelot="8A",
            quality_status=QualityStatus.OK,
            status=TrackStatus.ARCHIVE,
        ))
        count = audit.export_collection_xml(
            db, xml_out, {str(tmp_path): "C:/Fake/Archive"},
            hot_cues=hot_cues, memory_cues=memory_cues,
        )
    assert count == 1
    return ET.parse(xml_out).getroot().find("COLLECTION").find("TRACK")


def test_export_schreibt_hot_und_memory_marks(tmp_path: Path):
    """Pro Cue-Punkt stehen Hot Cue und Memory Cue im TRACK-Knoten, chronologisch sortiert."""
    track_node = _export(tmp_path, _tagged_mp3(tmp_path))

    marks = track_node.findall("POSITION_MARK")
    assert len(marks) == 8

    nums = [m.get("Num") for m in marks]
    assert nums[:2] == ["0", "-1"]  # First Kick: Hot vor Memory bei gleichem Start

    by_name: dict[str, list[ET.Element]] = {}
    for m in marks:
        by_name.setdefault(m.get("Name"), []).append(m)
    assert {n: len(found) for n, found in by_name.items()} == {
        "First Kick": 2, "Drop": 2, "Drop 2": 2, "Break (Groove)": 2,
    }
    assert sorted(m.get("Start") for m in marks) == ["25.000", "25.000", "5.000", "5.000", "60.000", "60.000", "90.000", "90.000"]
    # Hot Cues tragen Farbe, Memory Cues nicht.
    for m in marks:
        assert m.get("Type") == "0"
        if m.get("Num") == "-1":
            assert m.get("Red") is None
        else:
            assert m.get("Red") is not None


def test_export_ohne_hot_cues_nur_memory_marks(tmp_path: Path):
    """hot_cues=False exportiert nur Memory Cues (Num='-1')."""
    track_node = _export(tmp_path, _tagged_mp3(tmp_path), hot_cues=False)
    marks = track_node.findall("POSITION_MARK")
    assert len(marks) == 4
    assert {m.get("Name") for m in marks} == {"First Kick", "Drop", "Drop 2", "Break (Groove)"}
    assert all(m.get("Num") == "-1" for m in marks)


def test_record_to_inbox_track_liest_alle_cue_tags(tmp_path: Path):
    """_record_to_inbox_track übernimmt Cue-Tags inkl. Breakdown-Art und Drop 2."""
    audio = _tagged_mp3(tmp_path)
    stat = audio.stat()
    rec = TrackRecord(
        path=str(audio), mtime=stat.st_mtime, size=stat.st_size,
        artist="Artist", title="Title", bpm=150.0, status=TrackStatus.ARCHIVE,
    )
    track = audit._record_to_inbox_track(rec)
    assert track.first_kick_s == pytest.approx(5.0, abs=0.001)
    assert track.drop_s == pytest.approx(25.0, abs=0.001)
    assert track.breakdown_s == pytest.approx(60.0, abs=0.001)
    assert track.breakdown_kind == "groove"
    assert track.drop2_s == pytest.approx(90.0, abs=0.001)


def test_record_to_inbox_track_ohne_tags_liefert_none(tmp_path: Path):
    """Datei ohne Cue-Tags: alle Cue-Felder sind None, kein Fehler."""
    audio = _make_wav(tmp_path / "plain.wav")
    stat = audio.stat()
    rec = TrackRecord(
        path=str(audio), mtime=stat.st_mtime, size=stat.st_size,
        artist="A", title="T", bpm=150.0, status=TrackStatus.ARCHIVE,
    )
    track = audit._record_to_inbox_track(rec)
    assert track.first_kick_s is None
    assert track.drop_s is None
    assert track.breakdown_s is None
    assert track.breakdown_kind is None
    assert track.drop2_s is None


def test_audit_file_analyze_structure_speichert_ratio_ohne_dateiaenderung(
    tmp_path: Path, monkeypatch
):
    """--analyze-structure speichert breakdown_ratio in der TrackDB, ohne die Datei zu ändern."""
    audio = _make_wav(tmp_path / "track.wav")
    db_file = tmp_path / "tracks.sqlite"
    cfg = Config({
        "download": {"collection_dir": str(tmp_path / "collection"), "inbox_dir": str(tmp_path)},
        "state": {"db_path": str(tmp_path / "state.sqlite"), "track_db_path": str(db_file)},
    })
    report = {"ok": True, "ext": "wav", "bitrate_kbps": 1411, "sample_rate": 44100, "reason": ""}
    monkeypatch.setattr(audit, "check_file", lambda p, c: report)
    monkeypatch.setattr(audit, "analyze_track", lambda p: {})
    segs = [
        Segment("intro", 0.0, 5.0),
        Segment("main", 5.0, 60.0),
        Segment("breakdown", 60.0, 70.0),
        Segment("main", 70.0, 100.0),
    ]
    monkeypatch.setattr(audit, "find_first_kick", lambda p: 5.0)
    monkeypatch.setattr(audit, "analyze_structure_segments", lambda p: segs)
    monkeypatch.setattr(audit, "find_drop_start", lambda *a, **k: 70.0)

    vorher = audio.read_bytes()

    with TrackDB(str(db_file)) as db:
        res = audit.audit_file(audio, cfg, db, analyze_structure=True)

    expected = round(10.0 / 95.0, 4)  # Breakdown 60–70 s im Hauptteil 5–100 s
    assert res.breakdown_ratio == pytest.approx(expected, abs=1e-4)
    assert res.cached is False
    assert audio.read_bytes() == vorher, "Audiodatei darf nicht verändert werden"
    with TrackDB(str(db_file)) as db:
        rec = db.get_track_by_path(audio)
        assert rec is not None
        assert rec.breakdown_ratio == pytest.approx(expected, abs=1e-4)


def test_audit_file_analyze_structure_trotz_cache_bei_fehlendem_ratio(tmp_path: Path, monkeypatch):
    """Unveränderte Datei ohne breakdown_ratio wird mit --analyze-structure nachanalysiert."""
    audio = _make_wav(tmp_path / "track.wav")
    db_file = tmp_path / "tracks.sqlite"
    cfg = Config({
        "download": {"collection_dir": str(tmp_path / "collection"), "inbox_dir": str(tmp_path)},
        "state": {"db_path": str(tmp_path / "state.sqlite"), "track_db_path": str(db_file)},
    })
    stat = audio.stat()
    with TrackDB(str(db_file)) as db:
        db.upsert_track(TrackRecord(
            path=str(audio), mtime=stat.st_mtime, size=stat.st_size,
            quality_status=QualityStatus.OK, status=TrackStatus.ARCHIVE, breakdown_ratio=None,
        ))
    monkeypatch.setattr(audit, "check_file", lambda p, c: {"ok": True, "ext": "wav", "reason": ""})
    monkeypatch.setattr(audit, "analyze_track", lambda p: {})
    monkeypatch.setattr(audit, "find_first_kick", lambda p: 5.0)
    monkeypatch.setattr(audit, "analyze_structure_segments", lambda p: [
        Segment("intro", 0.0, 5.0), Segment("main", 5.0, 60.0),
    ])
    monkeypatch.setattr(audit, "find_drop_start", lambda *a, **k: None)

    with TrackDB(str(db_file)) as db:
        res = audit.audit_file(audio, cfg, db, analyze_structure=True)
    assert res.cached is False
    assert res.breakdown_ratio == pytest.approx(0.0, abs=1e-4)


def test_run_audit_reicht_analyze_structure_weiter(tmp_path: Path, monkeypatch):
    """run_audit(analyze_structure=True) reicht das Flag an audit_collection weiter."""
    db_file = tmp_path / "tracks.sqlite"
    cfg = Config({
        "download": {"collection_dir": str(tmp_path / "collection"), "inbox_dir": str(tmp_path / "inbox")},
        "state": {"db_path": str(tmp_path / "state.sqlite"), "track_db_path": str(db_file)},
    })

    seen: dict[str, bool] = {}

    def fake_audit_collection(collection_dir, cfg, db, *, force=False, analyze_structure=False):
        seen["analyze_structure"] = analyze_structure
        return audit.AuditSummary()

    monkeypatch.setattr(audit, "audit_collection", fake_audit_collection)
    monkeypatch.setattr(audit, "fill_fingerprints", lambda db, **kw: (0, 0))

    audit.run_audit(cfg, analyze_structure=True)
    assert seen["analyze_structure"] is True

    audit.run_audit(cfg, analyze_structure=False)
    assert seen["analyze_structure"] is False


def test_cli_audit_kennt_analyze_structure():
    """`sc_digger audit --help` führt --analyze-structure auf."""
    out = subprocess.run(
        [sys.executable, "-m", "sc_digger.main", "audit", "--help"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert out.returncode == 0
    assert "--analyze-structure" in out.stdout