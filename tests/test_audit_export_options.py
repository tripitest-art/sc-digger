"""Tests für die Export-Optionen des Sammlungs-XML (Layout, Anker, Comment, Phrasenraster, #381)."""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from sc_digger import audit, cues
from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config


def _seed(
    tmp_path: Path,
    *,
    kick: float | None = 2.0,
    drop: float | None = 100.0,
    duration: float | None = 300.0,
    lufs: float | None = None,
) -> Path:
    """Legt eine Datei mit Cue-Tags und den passenden TrackRecord in einer TrackDB an."""
    audio = tmp_path / "Schranz" / "t.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"\x00" * 100)
    if kick is not None:
        assert cues.write_first_kick_tag(audio, kick)
    if drop is not None:
        assert cues.write_drop_tag(audio, drop)
    db_file = tmp_path / "t.sqlite"
    with TrackDB(str(db_file)) as db:
        db.upsert_track(TrackRecord(
            path=str(audio), mtime=1700000000.0, size=100, artist="A", title="Banger",
            bpm=150.0, key_camelot="8A", duration=duration, lufs=lufs,
            quality_status=QualityStatus.OK, status=TrackStatus.ARCHIVE,
        ))
    return db_file


def _export(tmp_path: Path, *, db_file: Path | None = None, **kw) -> ET.Element:
    db_file = db_file or _seed(tmp_path)
    out = tmp_path / "out.xml"
    with TrackDB(str(db_file)) as db:
        n = audit.export_collection_xml(db, out, {str(tmp_path): "C:/Music"}, **kw)
    assert n == 1
    return ET.parse(out).getroot().find("COLLECTION").find("TRACK")


def _starts(track: ET.Element) -> dict[str, str]:
    return {m.get("Name"): m.get("Start") for m in track.findall("POSITION_MARK")}


def test_phrase_align_zieht_drop_auf_das_phrasengitter(tmp_path: Path):
    """phrase_align_cues=True rastet den Drop; ohne die Option bleibt der Tag-Zeitpunkt."""
    ohne = _export(tmp_path / "a", phrase_align_cues=False)
    mit = _export(tmp_path / "b", phrase_align_cues=True)
    assert _starts(ohne)["Drop"] == "100.000"
    # 150 BPM, Kick 2.0 s: 4-Beat-Taktanfang bei 99.6 s ist der nächste zulässige Rasterpunkt.
    assert _starts(mit)["Drop"] == "99.600"


def test_grid_anchor_entfaellt_ohne_ersten_kick(tmp_path: Path):
    """Ohne first_kick_s (kein Cue-Tag) bleibt grid_anchor=True ohne TEMPO-Knoten."""
    db_file = _seed(tmp_path, kick=None)
    track = _export(tmp_path, db_file=db_file, grid_anchor=True)
    assert track.find("TEMPO") is None


def test_total_time_ohne_dauer_ist_null(tmp_path: Path):
    """duration=None in der TrackDB -> TotalTime="0" wie beim bisherigen Verhalten."""
    db_file = _seed(tmp_path, duration=None)
    track = _export(tmp_path, db_file=db_file)
    assert track.get("TotalTime") == "0"


def test_total_time_nutzt_gespeicherte_dauer(tmp_path: Path):
    """duration aus der TrackDB landet gerundet in TotalTime."""
    db_file = _seed(tmp_path, duration=311.6)
    track = _export(tmp_path, db_file=db_file)
    assert track.get("TotalTime") == "312"


def test_kompakter_comment_traegt_lufs(tmp_path: Path):
    """compact_style_comments nutzt LUFS aus der TrackDB und endet mit ']'."""
    db_file = _seed(tmp_path, lufs=-7.5)
    comment = _export(tmp_path, db_file=db_file, compact_style_comments=True).get("Comments")
    assert comment.startswith("[150|8A|")
    assert "|D1:1:40" in comment
    assert comment.endswith("L-7.5]")


def test_export_lehnt_pfad_in_der_sammlung_weiter_ab(tmp_path: Path):
    """Auch mit den neuen Optionen bleibt die Sammlung read-only (ValueError)."""
    db_file = _seed(tmp_path)
    coll = tmp_path / "Schranz"
    with TrackDB(str(db_file)) as db:
        with pytest.raises(ValueError):
            audit.export_collection_xml(
                db, coll / "x.xml", {str(tmp_path): "C:/Music"},
                collection_dir=coll, hot_cue_layout="standard_8pad", grid_anchor=True,
            )


def test_run_audit_ohne_optionschluessel_nutzt_standardwerte(tmp_path: Path, monkeypatch):
    """Fehlende Schlüssel in cfg["rekordbox"]: Anker an (Standard True), Hot- und Memory-Cues an."""
    db_file = _seed(tmp_path)
    xml_out = tmp_path / "run.xml"
    cfg = Config({
        "download": {"collection_dir": str(tmp_path / "Schranz"), "inbox_dir": str(tmp_path / "inbox")},
        "state": {"track_db_path": str(db_file)},
        "rekordbox": {"path_map": {str(tmp_path): "C:/Music"}},
    })
    monkeypatch.setattr(
        audit, "audit_collection",
        lambda collection_dir, cfg, db, *, force=False: audit.AuditSummary(),
    )
    monkeypatch.setattr(audit, "fill_fingerprints", lambda db, **kw: (0, 0))
    audit.run_audit(cfg, export_xml_path=xml_out)

    track = ET.parse(xml_out).getroot().find("COLLECTION").find("TRACK")
    assert track.find("TEMPO") is not None
    marks = track.findall("POSITION_MARK")
    hot = {m.get("Name") for m in marks if m.get("Num") != "-1"}
    memory = {m.get("Name") for m in marks if m.get("Num") == "-1"}
    assert hot == {"First Kick", "Drop"} and memory == hot
    assert track.get("Comments") == ""
