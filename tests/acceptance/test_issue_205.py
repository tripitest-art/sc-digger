"""Akzeptanztests: Status „übernommen“ aus der Sammlung zurückmelden (ROADMAP 3.12)."""
import random
from pathlib import Path
import pytest

from sc_digger import audit
from sc_digger.audit import mark_adopted_tracks, run_audit
from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.fingerprint import decode_fingerprint, encode_fingerprint
from sc_digger.models import Config


def rand_fp(seed: int, n: int = 300) -> list[int]:
    rng = random.Random(seed)
    return [rng.getrandbits(32) for _ in range(n)]


def flip(values: list[int], p: float, seed: int) -> list[int]:
    """Kippt Bits mit Wahrscheinlichkeit p zur Simulation von Re-Encodings."""
    rng = random.Random(seed)
    return [v ^ sum(1 << b for b in range(32) if rng.random() < p) for v in values]


FP_A = rand_fp(101)
FP_B = rand_fp(102)
FP_C = rand_fp(103)


def _setup_db_records(db: TrackDB) -> None:
    # 1. Inbox-Track A (noch kein Feedback): soll übernommen werden
    r1 = db.upsert_track(TrackRecord(
        path="/music/inbox/150/5A/Artist A - Track 1.wav",
        mtime=1700000000.0,
        size=10000000,
        artist="Artist A",
        title="Track 1",
        status=TrackStatus.INBOX,
    ))
    db.set_fingerprint(r1.id, encode_fingerprint(FP_A), 300.0)

    # 2. Inbox-Track B (explizites Dislike 👎): soll übernommen werden, aber Dislike bleibt erhalten
    r2 = db.upsert_track(TrackRecord(
        path="/music/inbox/155/8A/Artist B - Track 2.wav",
        mtime=1700000100.0,
        size=12000000,
        artist="Artist B",
        title="Track 2",
        status=TrackStatus.INBOX,
    ))
    db.set_feedback(r2.id, "dislike")
    db.set_fingerprint(r2.id, encode_fingerprint(FP_B), 250.0)

    # 3. Inbox-Track C (kein Treffer in der Sammlung): bleibt unübernommen
    r3 = db.upsert_track(TrackRecord(
        path="/music/inbox/160/1A/Artist C - Track 3.wav",
        mtime=1700000200.0,
        size=8000000,
        artist="Artist C",
        title="Track 3",
        status=TrackStatus.INBOX,
    ))
    db.set_fingerprint(r3.id, encode_fingerprint(FP_C), 200.0)

    # 4. Archiv-Track A in Sammlung (umkodiert/leicht modifiziert, anderer Dateiname)
    r4 = db.upsert_track(TrackRecord(
        path="/music/Schranz/Classics/01_Track_1_Remastered.flac",
        mtime=1700000300.0,
        size=11000000,
        artist="Artist A",
        title="Track 1",
        status=TrackStatus.ARCHIVE,
    ))
    db.set_fingerprint(r4.id, encode_fingerprint(flip(FP_A, 0.02, seed=42)), 300.2)

    # 5. Archiv-Track B in Sammlung (anderer Ordner, gleicher Klang wie Inbox B)
    r5 = db.upsert_track(TrackRecord(
        path="/music/Schranz/Hard/Artist_B_Track_2_Final.wav",
        mtime=1700000400.0,
        size=12000000,
        artist="Artist B",
        title="Track 2",
        status=TrackStatus.ARCHIVE,
    ))
    db.set_fingerprint(r5.id, encode_fingerprint(FP_B), 250.0)


def test_mark_adopted_tracks_erkennt_uebernahme_per_fingerprint(tmp_path):
    db_file = tmp_path / "test_tracks.sqlite"
    with TrackDB(db_file) as db:
        _setup_db_records(db)
        fixed_ts = "2026-10-12 14:00:00"
        adopted = mark_adopted_tracks(db, now=fixed_ts)

        # Es müssen genau 2 Tracks erkannt werden (Track A und Track B)
        assert len(adopted) == 2

        inbox_map = {inb.path: (inb, arc) for inb, arc in adopted}

        # Track A Prüfung: Feedback wurde zu 'like', adopted_at gesetzt
        assert "/music/inbox/150/5A/Artist A - Track 1.wav" in inbox_map
        inb_a, arc_a = inbox_map["/music/inbox/150/5A/Artist A - Track 1.wav"]
        assert inb_a.adopted_at == fixed_ts
        assert inb_a.feedback == "like"
        assert arc_a.path == "/music/Schranz/Classics/01_Track_1_Remastered.flac"

        # Track B Prüfung: Dislike blieb erhalten, adopted_at trotzdem gesetzt
        assert "/music/inbox/155/8A/Artist B - Track 2.wav" in inbox_map
        inb_b, arc_b = inbox_map["/music/inbox/155/8A/Artist B - Track 2.wav"]
        assert inb_b.adopted_at == fixed_ts
        assert inb_b.feedback == "dislike"
        assert arc_b.path == "/music/Schranz/Hard/Artist_B_Track_2_Final.wav"

        # Track C Prüfung: nicht übernommen
        rec_c = db.get_track_by_path("/music/inbox/160/1A/Artist C - Track 3.wav")
        assert rec_c is not None
        assert rec_c.adopted_at is None

        # Archiv-Tracks wurden nicht mit adopted_at markiert
        arc_rec_a = db.get_track_by_path("/music/Schranz/Classics/01_Track_1_Remastered.flac")
        assert arc_rec_a.adopted_at is None


def test_mark_adopted_tracks_ist_idempotent(tmp_path):
    db_file = tmp_path / "test_tracks.sqlite"
    with TrackDB(db_file) as db:
        _setup_db_records(db)
        first_run = mark_adopted_tracks(db, now="2026-10-12 14:00:00")
        assert len(first_run) == 2

        # Zweiter Aufruf darf bereits übernommene Tracks nicht erneut melden
        second_run = mark_adopted_tracks(db, now="2026-10-12 15:00:00")
        assert second_run == []


def test_mark_adopted_tracks_schuetzt_dislike_in_db(tmp_path):
    db_file = tmp_path / "test_tracks.sqlite"
    with TrackDB(db_file) as db:
        r_inbox = db.upsert_track(TrackRecord(
            path="/music/inbox/disliked.wav",
            mtime=1.0,
            size=10,
            status=TrackStatus.INBOX,
        ))
        db.set_feedback(r_inbox.id, "dislike")
        db.set_fingerprint(r_inbox.id, encode_fingerprint(FP_A), 300.0)

        r_archive = db.upsert_track(TrackRecord(
            path="/music/Schranz/found.wav",
            mtime=2.0,
            size=10,
            status=TrackStatus.ARCHIVE,
        ))
        db.set_fingerprint(r_archive.id, encode_fingerprint(FP_A), 300.0)

        mark_adopted_tracks(db, now="2026-10-12 12:00:00")

        # Aus DB neu laden
        reloaded = db.get_track_by_id(r_inbox.id)
        assert reloaded.feedback == "dislike"
        assert reloaded.adopted_at == "2026-10-12 12:00:00"


def test_sammlung_bleibt_vollstaendig_unveraendert_goldene_regel_4(tmp_path):
    schranz_dir = tmp_path / "Schranz"
    schranz_dir.mkdir()
    sample_file = schranz_dir / "sample.wav"
    sample_content = b"RIFF....WAVEfmt ...."
    sample_file.write_bytes(sample_content)
    stat_before = sample_file.stat()

    db_file = tmp_path / "test_tracks.sqlite"
    with TrackDB(db_file) as db:
        r_arch = db.upsert_track(TrackRecord(
            path=str(sample_file),
            mtime=stat_before.st_mtime,
            size=stat_before.st_size,
            status=TrackStatus.ARCHIVE,
        ))
        db.set_fingerprint(r_arch.id, encode_fingerprint(FP_A), 300.0)

        r_inb = db.upsert_track(TrackRecord(
            path="/music/inbox/sample.wav",
            mtime=1.0,
            size=100,
            status=TrackStatus.INBOX,
        ))
        db.set_fingerprint(r_inb.id, encode_fingerprint(FP_A), 300.0)

        mark_adopted_tracks(db)

    stat_after = sample_file.stat()
    assert sample_file.read_bytes() == sample_content
    assert stat_after.st_mtime == stat_before.st_mtime
    assert stat_after.st_size == stat_before.st_size


def test_run_audit_mit_check_adopted_meldet_uebernommene_tracks(tmp_path, monkeypatch, capsys):
    cfg_path = Path(__file__).resolve().parents[2] / "config.yaml"
    cfg = Config.load(cfg_path)
    coll = tmp_path / "Schranz"
    coll.mkdir()
    cfg.raw["download"] = {**cfg["download"], "collection_dir": str(coll)}
    cfg.raw["state"] = {**cfg["state"], "track_db_path": str(tmp_path / "tracks.sqlite")}

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        _setup_db_records(db)

    monkeypatch.setattr(audit, "audit_collection", lambda *a, **k: audit.AuditSummary())
    monkeypatch.setattr(audit, "fill_fingerprints", lambda *a, **k: (0, 0))

    summary = run_audit(cfg, path=coll, check_adopted=True)
    assert len(summary.adopted_tracks) == 2
    out = capsys.readouterr().out
    assert "Übernommen" in out or "ÜBERNOMMENE TRACKS" in out


def test_track_db_migration_und_get_adopted_tracks(tmp_path):
    db_file = tmp_path / "test_migration.sqlite"
    with TrackDB(db_file) as db:
        r = db.upsert_track(TrackRecord(
            path="/music/inbox/track1.mp3",
            mtime=1.0,
            size=100,
            status=TrackStatus.INBOX,
            adopted_at="2026-10-12 10:00:00",
        ))
        assert r.adopted_at == "2026-10-12 10:00:00"

        adopted_list = db.get_adopted_tracks()
        assert len(adopted_list) == 1
        assert adopted_list[0].path == "/music/inbox/track1.mp3"
        assert adopted_list[0].adopted_at == "2026-10-12 10:00:00"
