from pathlib import Path
import pytest
from sc_digger.bot import rejected_reply, retry_reply
from sc_digger.models import Config, Track
from sc_digger.output import State

ROOT = Path(__file__).resolve().parents[2]
CFG = Config.load(ROOT / "config.yaml")


def _mk_track(track_id: int, title: str, url: str, artist: str = "Artist") -> Track:
    return Track(
        id=track_id,
        title=title,
        url=url,
        artist=artist,
        artist_url="",
        created_at="",
        duration_ms=180000,
        genre="Schranz",
        tags=[],
        description="",
        bpm=150.0,
        plays=0,
        likes=0,
        reposts=0,
        comments=0,
        downloadable=True,
        has_downloads_left=True,
        purchase_url=None,
        purchase_title=None,
    )


def test_state_mark_and_unmark_rejected(tmp_path):
    db_file = tmp_path / "seen.sqlite"
    t = _mk_track(12345, "Schranz Track 1", "https://soundcloud.com/artist/track-1")

    with State(db_file) as state:
        state.mark_rejected(t, reason="Spektrum endet bei 14 kHz", cutoff_hz=14000.0)
        assert state.is_seen(12345) is False  # Darf NICHT als seen markiert sein!

        rejected = state.list_rejected()
        assert len(rejected) == 1
        assert rejected[0]["id"] == 12345
        assert rejected[0]["title"] == "Schranz Track 1"
        assert "14 kHz" in rejected[0]["reason"]
        assert rejected[0]["cutoff_hz"] == 14000.0

        # Unmark entfernt aus Quarantäne
        deleted = state.unmark_rejected(12345)
        assert deleted is True
        assert len(state.list_rejected()) == 0


def test_bot_rejected_and_retry_reply(tmp_path, monkeypatch):
    db_file = tmp_path / "seen.sqlite"
    cfg = Config({**CFG.raw, "state": {**CFG["state"], "db_path": str(db_file)}})

    t1 = _mk_track(101, "Track Alpha", "https://sc.com/a", artist="DJ A")
    t2 = _mk_track(102, "Track Beta", "https://sc.com/b", artist="DJ B")

    with State(db_file) as state:
        state.mark_rejected(t1, reason="Codec nicht akzeptiert")
        state.mark_rejected(t2, reason="Spektrum unmessbar")

    # /rejected listet Tracks auf
    msg = rejected_reply(cfg, "/rejected")
    assert "Track Alpha" in msg
    assert "Track Beta" in msg
    assert "101" in msg and "102" in msg

    # /retry 101 setzt Track Alpha zurück
    res_retry = retry_reply(cfg, "/retry 101")
    assert "101" in res_retry
    assert "zurückgesetzt" in res_retry.lower() or "freigegeben" in res_retry.lower()

    # Nach retry ist 101 nicht mehr in /rejected
    with State(db_file) as state:
        assert state.is_seen(101) is False
        assert len(state.list_rejected()) == 1
        assert state.list_rejected()[0]["id"] == 102
