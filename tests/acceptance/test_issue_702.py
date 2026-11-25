"""Akzeptanztests: Artist und Titel aus Tags oder Dateinamen in die Track-DB (Issue <N>)."""
from __future__ import annotations

import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from sc_digger.audit import audit_file, names_for_file, names_from_filename, read_existing_tags
from sc_digger.backfill import backfill_names
from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.tasks import action_labels, default_runners

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("stem, erwartet", [
    ("Herr Blaubeer - Evil Society", ("Herr Blaubeer", "Evil Society")),
    ("01 Herr Blaubeer - Evil Society", ("Herr Blaubeer", "Evil Society")),
    ("01. Herr Blaubeer - Evil Society", ("Herr Blaubeer", "Evil Society")),
    ("01 - Herr Blaubeer - Evil Society", ("Herr Blaubeer", "Evil Society")),
    ("808 State - Pacific", ("808 State", "Pacific")),
    ("A & B - Titel - Teil 2", ("A & B", "Titel - Teil 2")),
    ("Artist - Titel (Original Mix) [1234567890]", ("Artist", "Titel (Original Mix)")),
    ("01 whoops", (None, "whoops")),
    ("", (None, None)),
])
def test_names_from_filename(stem, erwartet):
    assert names_from_filename(stem) == erwartet


def _wav(path: Path) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)
    return path


def test_wav_ohne_tags_liefert_none(tmp_path):
    meta = read_existing_tags(_wav(tmp_path / "Foo - Bar.wav"))
    assert meta["artist"] is None and meta["title"] is None


def test_wav_id3_tags_werden_gelesen_und_schlagen_den_dateinamen(tmp_path):
    from mutagen.id3 import TIT2, TPE1
    from mutagen.wave import WAVE

    path = _wav(tmp_path / "Falscher Name - Falscher Titel.wav")
    audio = WAVE(str(path))
    audio.add_tags()
    audio.tags.add(TPE1(encoding=3, text=["Tag Artist"]))
    audio.tags.add(TIT2(encoding=3, text=["Tag Titel"]))
    audio.save()

    assert read_existing_tags(path)["artist"] == "Tag Artist"
    assert names_for_file(path) == ("Tag Artist", "Tag Titel")


def test_names_for_file_ohne_datei_nimmt_den_dateinamen(tmp_path):
    assert names_for_file(tmp_path / "fehlt" / "01 Herr Blaubeer - Evil Society.wav") == (
        "Herr Blaubeer", "Evil Society")


def _report(name: str) -> dict:
    return {"file": name, "codec": "pcm_s16le", "ext": "wav", "bitrate_kbps": 1411,
            "sample_rate": 44100, "cutoff_hz": 21000, "ok": True, "reason": "", "duration": 200.0}


def test_audit_file_speichert_artist_aus_dem_dateinamen(tmp_path):
    audio = tmp_path / "02 Herr Blaubeer - Leaders and Followers.wav"
    audio.write_bytes(b"dummy")
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        with patch("sc_digger.audit.check_file", return_value=_report(audio.name)), \
             patch("sc_digger.audit.read_existing_tags",
                   return_value={"bpm": 150.0, "key": "8A", "artist": None, "title": None}):
            audit_file(audio, Config.load(ROOT / "config.yaml"), db)
        rec = db.get_track_by_path(audio)
    assert rec.artist == "Herr Blaubeer"
    assert rec.title == "Leaders and Followers"


def test_audit_file_tag_artist_hat_vorrang(tmp_path):
    audio = tmp_path / "Datei Artist - Datei Titel.wav"
    audio.write_bytes(b"dummy")
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        with patch("sc_digger.audit.check_file", return_value=_report(audio.name)), \
             patch("sc_digger.audit.read_existing_tags",
                   return_value={"bpm": 150.0, "key": "8A", "artist": "Tag Artist", "title": ""}):
            audit_file(audio, Config.load(ROOT / "config.yaml"), db)
        rec = db.get_track_by_path(audio)
    assert rec.artist == "Tag Artist"
    assert rec.title == "Datei Titel"


def _row(db: TrackDB, path: Path, **kw) -> int:
    rec = db.upsert_track(TrackRecord(path=str(path), mtime=0.0, size=0,
                                      status=TrackStatus.ARCHIVE, **kw))
    return rec.id


def test_backfill_names_fuellt_nur_leere_felder(tmp_path):
    sammlung = tmp_path / "Schranz"
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _row(db, sammlung / "Herr Blaubeer - Evil Society.wav")
        _row(db, sammlung / "Ueberrest - Surrender.wav", title="Mein Titel")
        _row(db, sammlung / "Anderer - Name.wav", artist="Bleibt", title="Auch")
        _row(db, sammlung / "01 whoops.wav")

        res = backfill_names(db)

        assert res["seen"] == 3
        assert res["set"] == 2
        assert res["skipped"] == 1
        assert db.get_track_by_path(sammlung / "Herr Blaubeer - Evil Society.wav").artist == "Herr Blaubeer"
        assert db.get_track_by_path(sammlung / "Herr Blaubeer - Evil Society.wav").title == "Evil Society"
        geteilt = db.get_track_by_path(sammlung / "Ueberrest - Surrender.wav")
        assert (geteilt.artist, geteilt.title) == ("Ueberrest", "Mein Titel")
        fest = db.get_track_by_path(sammlung / "Anderer - Name.wav")
        assert (fest.artist, fest.title) == ("Bleibt", "Auch")
        assert not db.get_track_by_path(sammlung / "01 whoops.wav").artist


def test_backfill_names_probelauf_schreibt_nichts(tmp_path):
    path = tmp_path / "Herr Blaubeer - Evil Society.wav"
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _row(db, path)
        res = backfill_names(db, dry_run=True)
        assert res["would_set"] == 1 and res["set"] == 0
        assert not db.get_track_by_path(path).artist


def test_wartungsaktion_backfill_names(tmp_path):
    track_db = tmp_path / "tracks.sqlite"
    cfg = Config(raw={
        "state": {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(track_db),
                  "health_db_path": str(tmp_path / "seen.sqlite")},
        "download": {"inbox_dir": str(tmp_path / "inbox"), "collection_dir": str(tmp_path / "Schranz")},
    })
    with TrackDB(track_db) as db:
        _row(db, tmp_path / "Schranz" / "Herr Blaubeer - Evil Society.wav")
        _row(db, tmp_path / "Schranz" / "01 whoops.wav")

    assert action_labels()["backfill-names"] == "Artist aus Dateinamen ergänzen"
    msg = default_runners(cfg)["backfill-names"]()
    assert msg == "Artist/Titel: 1 ergänzt, 1 ohne Artist im Namen"
