"""Akzeptanztests: Inbox-Tracks tragen ihre SoundCloud-ID, damit 👍/👎 Embedding und Taste-Score erreichen."""
import subprocess
from pathlib import Path

from sc_digger import main as m
from sc_digger.db import JobType, TrackDB, TrackRecord, TrackStatus
from sc_digger.embeddings import backfill_embedding_jobs
from sc_digger.models import Config, Track
from sc_digger.taste import training_data

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


def _make_track(track_id: int) -> Track:
    t = Track(
        id=track_id,
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


def _finish(tmp_path: Path, monkeypatch, track_id: int, name: str) -> tuple[Path, Path]:
    cfg = Config.load(ROOT / "config.yaml")
    track_db = tmp_path / "tracks.sqlite"
    inbox = tmp_path / "inbox"
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(track_db)}
    cfg.raw["download"]["inbox_dir"] = str(inbox)
    cfg.raw["organize"]["enabled"] = False
    cfg.raw["organize"]["write_tags"] = True
    cfg.raw["cues"] = {"auto_queue": False}
    cfg.raw["embeddings"] = {"auto_queue": False}

    audio = _make_wav(inbox / name)
    monkeypatch.setattr(m, "finalize_quality", lambda trk, p, inb, c: p)
    monkeypatch.setattr(m, "analyze_track", lambda p: {"bpm": 152.0, "key_camelot": "6A", "key_name": "Gm"})
    monkeypatch.setattr(m, "find_first_kick", lambda p: 5.0)
    monkeypatch.setattr(m, "analyze_structure", lambda p: [])
    out_path = m.finish_file(_make_track(track_id), audio, inbox, cfg)
    assert out_path == audio
    return track_db, out_path


def _row(db: TrackDB, path: Path, sc_id: int | None = None) -> TrackRecord:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return db.upsert_track(
        TrackRecord(path=str(path), mtime=1.0, size=1, status=TrackStatus.INBOX, sc_id=sc_id)
    )


# --- finish_file --------------------------------------------------------------------


def test_finish_file_speichert_die_soundcloud_id(tmp_path, monkeypatch):
    track_db, out_path = _finish(tmp_path, monkeypatch, 4711, "[4711] Test Producer - Schranz Banger.wav")
    with TrackDB(track_db) as db:
        assert db.get_track_by_path(out_path).sc_id == 4711


def test_finish_file_ohne_soundcloud_id_bleibt_leer(tmp_path, monkeypatch):
    # Manuell geladene Dateien aus _eingang haben id=0 (intake.track_from_file).
    track_db, out_path = _finish(tmp_path, monkeypatch, 0, "Handupload.wav")
    with TrackDB(track_db) as db:
        assert db.get_track_by_path(out_path).sc_id is None


# --- TrackDB.fill_sc_ids_from_paths -------------------------------------------------


def test_fill_sc_ids_traegt_nur_fehlende_ids_aus_dem_dateinamen_nach(tmp_path):
    inbox = tmp_path / "inbox"
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        a = _row(db, inbox / "152" / "8A" / "[123] Artist - Titel.flac")
        b = _row(db, inbox / "[456] Schon verknuepft.wav", sc_id=789)
        c = _row(db, inbox / "Ohne ID.wav")
        d = _row(db, inbox / "[999]" / "Ordner zaehlt nicht.wav")

        assert db.fill_sc_ids_from_paths() == 1

        assert db.get_track_by_id(a.id).sc_id == 123
        assert db.get_track_by_id(b.id).sc_id == 789  # vorhandene ID wird nie überschrieben
        assert db.get_track_by_id(c.id).sc_id is None
        assert db.get_track_by_id(d.id).sc_id is None
        assert db.fill_sc_ids_from_paths() == 0  # zweiter Lauf ändert nichts


def test_fill_sc_ids_probelauf_schreibt_nichts(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        a = _row(db, tmp_path / "inbox" / "[123] Artist - Titel.wav")
        assert db.fill_sc_ids_from_paths(dry_run=True) == 1
        assert db.get_track_by_id(a.id).sc_id is None


# --- Wirkung auf Embeddings und Taste-Score -----------------------------------------


def test_embedding_backfill_findet_bewertete_inbox_tracks(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        a = _row(db, tmp_path / "inbox" / "[123] Artist - Titel.wav")
        db.set_sc_feedback(123, "like")

        summary = backfill_embedding_jobs(db)

        assert summary.sc_id_filled == 1
        assert summary.queued == 1
        assert db.get_track_by_id(a.id).sc_id == 123
        assert db.find_active_job(JobType.EMBEDDING, a.id) is not None


def test_embedding_backfill_probelauf_fuellt_keine_ids(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        a = _row(db, tmp_path / "inbox" / "[123] Artist - Titel.wav")
        db.set_sc_feedback(123, "like")

        summary = backfill_embedding_jobs(db, dry_run=True)

        assert summary.sc_id_filled == 1  # würde nachgetragen
        assert db.get_track_by_id(a.id).sc_id is None
        assert db.find_active_job(JobType.EMBEDDING, a.id) is None


def test_bewertung_erreicht_das_training_nach_dem_nachtragen(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        a = _row(db, tmp_path / "inbox" / "[123] Gefaellt.wav")
        b = _row(db, tmp_path / "inbox" / "[124] Gefaellt nicht.wav")
        db.set_embedding(a.id, "maest", [0.1, 0.2, 0.3])
        db.set_embedding(b.id, "maest", [0.3, 0.2, 0.1])
        db.set_sc_feedback(123, "like")
        db.set_sc_feedback(124, "dislike")

        assert training_data(db) == (None, [])

        db.fill_sc_ids_from_paths()

        model, samples = training_data(db)
        assert model == "maest"
        assert sorted(label for _vec, label in samples) == [0, 1]
