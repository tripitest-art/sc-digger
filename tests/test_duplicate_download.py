"""Zusätzliche Tests für reject_if_duplicate und Duplikaterkennung bei Downloads."""
import random
from pathlib import Path

import pytest

from sc_digger import output as out
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.fingerprint import Fingerprint, encode_fingerprint
from sc_digger.models import Config, DownloadKind, Track


def _rand_fp(seed: int, n: int = 300) -> list[int]:
    rng = random.Random(seed)
    return [rng.getrandbits(32) for _ in range(n)]


FP_A = _rand_fp(10)
FP_B = _rand_fp(20)


def _make_track(**kw) -> Track:
    base = dict(
        id=42,
        title="Test Track",
        url="https://soundcloud.com/test/track",
        artist="Test Artist",
        artist_url="https://soundcloud.com/test",
        created_at="2026-09-28T12:00:00Z",
        duration_ms=240000,
        genre="Schranz",
        tags=["schranz"],
        description="",
        bpm=155.0,
        plays=5000,
        likes=250,
        reposts=50,
        comments=10,
        downloadable=True,
        has_downloads_left=True,
        purchase_url=None,
        purchase_title=None,
    )
    base.update(kw)
    return Track(**base)


@pytest.fixture
def dup_env(tmp_path, monkeypatch):
    cfg = Config.load(Path(__file__).resolve().parents[1] / "config.yaml")
    db_path = tmp_path / "tracks.sqlite"
    cfg.raw["state"] = {**cfg["state"], "track_db_path": str(db_path)}
    cfg.raw["fingerprint"] = {"check_downloads": True}

    with TrackDB(db_path) as db:
        rec = db.upsert_track(TrackRecord(path="/music/Schranz/Existing & Rare <Tune>.wav", mtime=100.0, size=1000))
        db.set_fingerprint(rec.id, encode_fingerprint(FP_A), 240.0)

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    dl_file = inbox / "Download.wav"
    dl_file.write_bytes(b"new-content")

    monkeypatch.setattr(
        out, "compute_fingerprint",
        lambda p, **k: Fingerprint(FP_A, 240.2)
    )

    return cfg, inbox, dl_file


def test_existing_target_file_in_rejected_duplicate_is_overwritten(dup_env):
    """Ist in _rejected/duplicate/ schon eine Datei gleichen Namens, wird sie ersetzt, ohne Fehler."""
    cfg, inbox, dl_file = dup_env
    rej_dir = inbox / "_rejected" / "duplicate"
    rej_dir.mkdir(parents=True)
    existing_rej = rej_dir / dl_file.name
    existing_rej.write_bytes(b"old-stale-content")

    t = _make_track()
    res = out.reject_if_duplicate(t, dl_file, inbox, cfg)

    assert res is None
    assert not dl_file.exists()
    assert existing_rej.exists()
    assert existing_rej.read_bytes() == b"new-content"
    assert t.duplicate_of == "/music/Schranz/Existing & Rare <Tune>.wav"


def test_track_without_duplicate_of_has_no_recycle_line_in_digest():
    """Ein Track ohne duplicate_of bekommt im Digest keine ♻️-Zeile."""
    t = _make_track(duplicate_of=None)
    msgs = out.build_digest_messages([t], None)
    text = "".join(m.text for m in msgs)
    assert "♻️" not in text
    assert "Schon in der Sammlung" not in text


def test_track_with_duplicate_of_escapes_html_and_shows_filename_only():
    """duplicate_of wird HTML-escaped und zeigt nur den Dateinamen ohne Server-Pfad."""
    t = _make_track(duplicate_of="/music/Schranz/Subfolder/A & B <Special>.wav")
    msgs = out.build_digest_messages([t], None)
    text = "".join(m.text for m in msgs)

    assert "♻️ Schon in der Sammlung: A &amp; B &lt;Special&gt;.wav" in text
    assert "/music/Schranz" not in text
    assert "Subfolder" not in text
