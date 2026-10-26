"""Akzeptanztests: Embedding-Jobs im täglichen Lauf einreihen (embeddings.auto_queue)."""
import subprocess
from pathlib import Path

from sc_digger import main as m
from sc_digger.db import JobStatus, JobType, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config, Track

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


def _run_finish_file(tmp_path: Path, monkeypatch, emb_cfg: dict | None, *, vorher=None) -> tuple[Path, Path]:
    """Lässt finish_file für eine synthetische Inbox-Datei laufen; gibt Track-DB und Datei zurück.

    vorher(db, audio) läuft vor finish_file und darf die Track-DB vorbereiten.
    """
    cfg = Config.load(ROOT / "config.yaml")
    track_db = tmp_path / "tracks.sqlite"
    inbox = tmp_path / "inbox"
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(track_db)}
    cfg.raw["download"]["inbox_dir"] = str(inbox)
    cfg.raw["organize"]["enabled"] = False
    cfg.raw["organize"]["write_tags"] = True
    cfg.raw["cues"] = {"auto_queue": True}
    if emb_cfg is None:
        cfg.raw.pop("embeddings", None)
    else:
        cfg.raw["embeddings"] = emb_cfg

    audio = _make_wav(inbox / "emb_job.wav")
    if vorher is not None:
        with TrackDB(track_db) as db:
            vorher(db, audio)

    t = _make_track()
    monkeypatch.setattr(m, "finalize_quality", lambda trk, p, inb, c: p)
    monkeypatch.setattr(m, "analyze_track", lambda p: {"bpm": 152.0, "key_camelot": "6A", "key_name": "Gm"})
    monkeypatch.setattr(m, "find_first_kick", lambda p: 5.0)
    monkeypatch.setattr(m, "analyze_structure", lambda p: [])

    out_path = m.finish_file(t, audio, inbox, cfg)
    assert out_path == audio
    return track_db, out_path


def test_auto_queue_true_reiht_genau_einen_embedding_job_ein(tmp_path: Path, monkeypatch):
    track_db, out_path = _run_finish_file(tmp_path, monkeypatch, {"auto_queue": True})

    with TrackDB(track_db) as db:
        rec = db.get_track_by_path(out_path)
        assert rec is not None
        jobs = db.get_pending_jobs(JobType.EMBEDDING)
        assert len(jobs) == 1
        assert jobs[0].status == JobStatus.PENDING
        assert jobs[0].track_id == rec.id
        assert jobs[0].payload["track_id"] == rec.id
        assert jobs[0].payload["path"] == str(out_path)
        assert jobs[0].payload["format"] == "wav"


def test_auto_queue_false_reiht_keinen_embedding_job_ein(tmp_path: Path, monkeypatch):
    track_db, out_path = _run_finish_file(tmp_path, monkeypatch, {"auto_queue": False})

    with TrackDB(track_db) as db:
        assert db.get_pending_jobs(JobType.EMBEDDING) == []
        assert db.get_track_by_path(out_path) is not None
        assert len(db.get_pending_jobs(JobType.CUES)) == 1


def test_fehlender_schluessel_reiht_keinen_embedding_job_ein(tmp_path: Path, monkeypatch):
    track_db, out_path = _run_finish_file(tmp_path, monkeypatch, None)

    with TrackDB(track_db) as db:
        assert db.get_pending_jobs(JobType.EMBEDDING) == []
        assert db.get_track_by_path(out_path) is not None
        assert len(db.get_pending_jobs(JobType.CUES)) == 1


def test_vorhandenes_embedding_erzeugt_keinen_neuen_job(tmp_path: Path, monkeypatch):
    def vorher(db: TrackDB, audio: Path) -> None:
        rec = db.upsert_track(
            TrackRecord(path=str(audio), mtime=1.0, size=1, status=TrackStatus.INBOX)
        )
        db.set_embedding(rec.id, "test-model", [0.1, 0.2, 0.3])

    track_db, out_path = _run_finish_file(tmp_path, monkeypatch, {"auto_queue": True}, vorher=vorher)

    with TrackDB(track_db) as db:
        rec = db.get_track_by_path(out_path)
        assert rec is not None
        assert db.get_embedding(rec.id) is not None
        assert db.get_pending_jobs(JobType.EMBEDDING) == []
