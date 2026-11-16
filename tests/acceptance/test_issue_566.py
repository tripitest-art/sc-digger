"""Akzeptanztests: Handkorrekturen im Verknüpfungsvorschlag und Job-Requueue."""
from types import SimpleNamespace

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.linksource import propose_links
from sc_digger.main import run_requeue_cmd
from sc_digger.models import Config


def _cand(sc_id: int, artist: str, title: str) -> SimpleNamespace:
    return SimpleNamespace(id=sc_id, artist=artist, title=title,
                           url=f"https://soundcloud.test/{sc_id}")


def _record(track_id: int, path: str, artist: str, title: str) -> TrackRecord:
    return TrackRecord(id=track_id, path=path, mtime=1.0, size=2, artist=artist, title=title)


def test_ohne_overrides_wird_vorgeschlagen():
    records = [_record(1, "/x/a.flac", "Artist", "Werk")]
    candidates = [_cand(4711, "Artist", "Werk")]
    proposals = propose_links(records, candidates)
    assert [p.sc_id for p in proposals] == [4711]


def test_override_schliesst_den_kandidaten_aus():
    records = [_record(1, "/x/a.flac", "Artist", "Werk")]
    candidates = [_cand(4711, "Artist", "Werk")]
    for overrides in ({4711: "not_duplicate"}, {4711: "already_in_collection"}):
        assert propose_links(records, candidates, overrides=overrides) == []


def test_override_traegt_nicht_auf_andere_kandidaten_ab():
    records = [_record(1, "/x/a.flac", "Artist", "Werk")]
    candidates = [_cand(4711, "Artist", "Werk"), _cand(4712, "Artist", "Werk")]
    proposals = propose_links(records, candidates, overrides={4711: "not_duplicate"})
    assert [p.sc_id for p in proposals] == [4712]


def _seed_job(pfad, *, status: str, minutes_old: int) -> int:
    with TrackDB(pfad) as db:
        t = db.upsert_track(TrackRecord(path="/x/a.flac", mtime=1.0, size=2))
        cur = db.db.execute(
            "INSERT INTO jobs (track_id, job_type, status, updated_at) "
            "VALUES (?, 'embedding', ?, datetime('now', '-%d minutes'))" % minutes_old,
            (t.id, status),
        )
        db.db.commit()
        return int(cur.lastrowid)


def test_requeue_setzt_haengende_jobs_zurueck(tmp_path):
    pfad = tmp_path / "t.sqlite"
    job_id = _seed_job(pfad, status="running", minutes_old=90)
    cfg = Config({"state": {"track_db_path": str(pfad)}})
    text = run_requeue_cmd(cfg, timeout_minutes=60)
    assert "1" in text
    with TrackDB(pfad) as db:
        status = db.db.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    assert status == "pending"


def test_requeue_meldet_nichts_zu_tun(tmp_path):
    pfad = tmp_path / "t.sqlite"
    _seed_job(pfad, status="pending", minutes_old=90)
    cfg = Config({"state": {"track_db_path": str(pfad)}})
    text = run_requeue_cmd(cfg, timeout_minutes=60)
    assert "keine hängenden Jobs" in text
