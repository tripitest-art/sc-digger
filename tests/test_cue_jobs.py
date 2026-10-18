"""Modultests für Cue-Jobs in der Queue (Issue #349): Edge Cases zu den Akzeptanztests."""
from pathlib import Path
import subprocess

from fastapi.testclient import TestClient

from sc_digger import main as m
from sc_digger.cues import apply_cue_result
from sc_digger.db import JobType, TrackDB, TrackRecord, enqueue_cue_job
from sc_digger.models import Config, Track
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "test-cue-jobs-pwd"
AUTH = ("sc", PASSWORD)


def _make_cfg(tmp_path: Path, inbox_dir: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw["download"] = {
        "inbox_dir": str(inbox_dir),
        "collection_dir": str(tmp_path / "collection"),
    }
    cfg.raw["cues"] = {"auto_queue": True}
    return cfg


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


def test_enqueue_cue_job_ohne_track_id_gibt_none(tmp_path: Path):
    """Ein Track ohne ID (noch nicht persistiert) kann keinen Job bekommen."""
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert enqueue_cue_job(db, TrackRecord(path="inbox/neu.mp3", mtime=1.0, size=1)) is None
        assert db.get_pending_jobs(JobType.CUES) == []


def test_apply_cue_result_lehnt_ungueltiges_ergebnis_ab(tmp_path: Path):
    """Kein Objekt als Ergebnis oder unbrauchbare breakdown_ratio: False statt Exception."""
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        rec = db.upsert_track(TrackRecord(path="inbox/track.mp3", mtime=1.0, size=1))
        job = enqueue_cue_job(db, rec)

        assert apply_cue_result(db, job.id, ["kein", "objekt"]) is False  # type: ignore[arg-type]
        assert apply_cue_result(db, job.id, {"breakdown_ratio": "keine Zahl"}) is False

        unchanged = db.get_track_by_id(rec.id)
        assert unchanged is not None
        assert unchanged.rb_cues is None
        assert unchanged.breakdown_ratio is None


def test_apply_cue_result_unbekannter_job_oder_track(tmp_path: Path):
    """Unbekannte Job-ID und Job ohne zugehörigen Track ergeben False."""
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert apply_cue_result(db, 99999, {"first_kick_s": 1.0}) is False

        rec = db.upsert_track(TrackRecord(path="inbox/track.mp3", mtime=1.0, size=1))
        job = enqueue_cue_job(db, rec)
        # Der Fremdschlüssel verhindert Jobs ohne Track; für den Testfall den Track entfernen.
        db.db.execute("DELETE FROM tracks WHERE id = ?", (rec.id,))
        db.db.commit()
        assert apply_cue_result(db, job.id, {"first_kick_s": 1.0}) is False


def test_apply_cue_result_fehlende_datei_wirft_nicht(tmp_path: Path):
    """Datei fehlt in der Inbox: DB wird aktualisiert, Tags entfallen, kein Fehler."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    missing = inbox / "weg.mp3"

    with TrackDB(tmp_path / "tracks.sqlite") as db:
        rec = db.upsert_track(TrackRecord(path=str(missing), mtime=1.0, size=1))
        job = enqueue_cue_job(db, rec)

        assert apply_cue_result(
            db, job.id, {"first_kick_s": 8.0, "cues": [{"name": "First Kick", "time": 8.0}]},
            inbox_dir=inbox,
        ) is True

        updated = db.get_track_by_id(rec.id)
        assert updated is not None
        assert "First Kick" in str(updated.rb_cues)
    assert not missing.exists()


def test_job_audio_ohne_anmeldung_ist_401(tmp_path: Path):
    """Ohne Basic-Auth liefert der Audio-Endpunkt keinen Byte aus."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    audio = inbox / "track.mp3"
    audio.write_bytes(b"\x00" * 10)
    cfg = _make_cfg(tmp_path, inbox)
    client = TestClient(create_app(cfg, password=PASSWORD))

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        rec = db.upsert_track(TrackRecord(path=str(audio), mtime=1.0, size=10))
        job = enqueue_cue_job(db, rec)

    assert client.get(f"/api/jobs/{job.id}/audio").status_code == 401


def test_job_audio_ausserhalb_der_wurzeln_ist_404(tmp_path: Path):
    """Dateien außerhalb von Inbox und Sammlung werden nicht ausgeliefert."""
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    outside = tmp_path / "woanders"
    outside.mkdir()
    audio = outside / "fremd.mp3"
    audio.write_bytes(b"\x00" * 10)
    cfg = _make_cfg(tmp_path, inbox)
    client = TestClient(create_app(cfg, password=PASSWORD))

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        rec = db.upsert_track(TrackRecord(path=str(audio), mtime=1.0, size=10))
        job = enqueue_cue_job(db, rec)

    assert client.get(f"/api/jobs/{job.id}/audio", auth=AUTH).status_code == 404


def _run_finish_file(tmp_path: Path, monkeypatch, cues_cfg: dict) -> tuple[Path, Path]:
    """Lässt finish_file für eine synthetische Inbox-Datei laufen und gibt DB und Datei zurück."""
    cfg = Config.load(ROOT / "config.yaml")
    track_db = tmp_path / "tracks.sqlite"
    inbox = tmp_path / "inbox"
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(track_db)}
    cfg.raw["download"]["inbox_dir"] = str(inbox)
    cfg.raw["organize"]["enabled"] = False
    cfg.raw["organize"]["write_tags"] = True
    cfg.raw["cues"] = cues_cfg

    audio = _make_wav(inbox / "cue_job.wav")
    t = _make_track()
    monkeypatch.setattr(m, "finalize_quality", lambda trk, p, inb, c: p)
    monkeypatch.setattr(m, "analyze_track", lambda p: {"bpm": 152.0, "key_camelot": "6A", "key_name": "Gm"})
    monkeypatch.setattr(m, "find_first_kick", lambda p: 5.0)
    monkeypatch.setattr(m, "analyze_structure", lambda p: [])

    out_path = m.finish_file(t, audio, inbox, cfg)
    assert out_path == audio
    return track_db, out_path


def test_finish_file_reiht_cue_job_ein(tmp_path: Path, monkeypatch):
    """Nach dem Persistieren in der Track-DB liegt genau ein Cues-Job mit Pfad-Payload vor."""
    track_db, out_path = _run_finish_file(tmp_path, monkeypatch, {"auto_queue": True})

    with TrackDB(track_db) as db:
        jobs = db.get_pending_jobs(JobType.CUES)
        assert len(jobs) == 1
        assert jobs[0].payload["path"] == str(out_path)
        rec = db.get_track_by_path(out_path)
        assert rec is not None
        assert jobs[0].track_id == rec.id


def test_finish_file_ohne_auto_queue_reiht_keinen_job_ein(tmp_path: Path, monkeypatch):
    """cues.auto_queue=false: nur die Track-DB wird geschrieben, keine Queue-Einträge."""
    track_db, out_path = _run_finish_file(tmp_path, monkeypatch, {"auto_queue": False})

    with TrackDB(track_db) as db:
        assert db.get_pending_jobs(JobType.CUES) == []
        assert db.get_track_by_path(out_path) is not None
