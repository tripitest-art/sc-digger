"""Akzeptanztests: neue Downloads am Klang mit der Sammlung abgleichen (_rejected/duplicate/)."""
import random
from pathlib import Path

import pytest

from sc_digger import output as out
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.fingerprint import Fingerprint, encode_fingerprint
from sc_digger.models import Config, DownloadKind, Track


def rand_fp(seed: int, n: int = 300) -> list[int]:
    rng = random.Random(seed)
    return [rng.getrandbits(32) for _ in range(n)]


A, B = rand_fp(1), rand_fp(2)
OK = {"ok": True, "clipped": False, "ext": "wav", "bitrate_kbps": 1411, "reason": "WAV"}


def mk(**kw) -> Track:
    base = dict(
        id=1, title="Tune", url="https://soundcloud.com/a/tune", artist="Artist",
        artist_url="", created_at="2026-09-27T10:00:00Z", duration_ms=300000, genre="", tags=[],
        description="", bpm=None, plays=1000, likes=50, reposts=10, comments=2, downloadable=True,
        has_downloads_left=True, purchase_url=None, purchase_title=None,
    )
    base.update(kw)
    return Track(**base)


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")
    cfg.raw["state"] = {**cfg["state"], "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["fingerprint"] = {"check_downloads": True}
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        r = db.upsert_track(TrackRecord(path="/music/Schranz/Artist - Tune.wav", mtime=1.0, size=1))
        db.set_fingerprint(r.id, encode_fingerprint(A), 300.0)
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    f = inbox / "Artist - Tune (Free DL).wav"
    f.write_bytes(b"x")
    calls = []
    monkeypatch.setattr(out, "compute_fingerprint", lambda p, **k: calls.append(Path(p).name) or Fingerprint(A, 300.5))
    return cfg, inbox, f, calls


def test_config_default():
    import yaml
    raw = yaml.safe_load((Path(__file__).resolve().parents[2] / "config.yaml").read_text(encoding="utf-8"))
    assert raw["fingerprint"]["check_downloads"] is True


def test_duplicate_is_moved_and_linked(env):
    cfg, inbox, f, calls = env
    t = mk()
    assert out.reject_if_duplicate(t, f, inbox, cfg) is None
    assert not f.exists()
    assert (inbox / "_rejected" / "duplicate" / f.name).exists()
    assert t.duplicate_of == "/music/Schranz/Artist - Tune.wav"
    assert calls == [f.name]


def test_new_recording_stays(env, monkeypatch):
    cfg, inbox, f, _ = env
    monkeypatch.setattr(out, "compute_fingerprint", lambda p, **k: Fingerprint(B, 300.0))
    t = mk()
    assert out.reject_if_duplicate(t, f, inbox, cfg) == f
    assert f.exists() and t.duplicate_of is None


def test_switched_off(env):
    cfg, inbox, f, calls = env
    cfg.raw["fingerprint"] = {"check_downloads": False}
    assert out.reject_if_duplicate(mk(), f, inbox, cfg) == f
    assert calls == []


@pytest.mark.parametrize("breakage", ["no_fingerprint", "db_error"])
def test_check_failure_keeps_download(env, monkeypatch, breakage, caplog):
    cfg, inbox, f, _ = env
    if breakage == "no_fingerprint":
        monkeypatch.setattr(out, "compute_fingerprint", lambda p, **k: None)
    else:
        monkeypatch.setattr(out, "find_same_recording", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("DB kaputt")))
    t = mk()
    assert out.reject_if_duplicate(t, f, inbox, cfg) == f          # im Zweifel behalten, nie werfen
    assert f.exists() and t.duplicate_of is None
    if breakage == "db_error":
        assert "Duplikat" in caplog.text


def test_finalize_quality_checks_only_good_files(env, monkeypatch):
    cfg, inbox, f, calls = env
    monkeypatch.setattr(out, "check_file", lambda p, c: dict(OK))
    t = mk()
    assert out.finalize_quality(t, f, inbox, cfg) is None
    assert (inbox / "_rejected" / "duplicate" / f.name).exists()

    g = inbox / "clipped.wav"
    g.write_bytes(b"x")
    calls.clear()
    monkeypatch.setattr(out, "check_file", lambda p, c: {**OK, "clipped": True})
    assert out.finalize_quality(mk(), g, inbox, cfg) is None
    assert (inbox / "_rejected" / "clipped" / "clipped.wav").exists()
    assert calls == []                                             # Fakes/Brickwall: kein Fingerprint nötig


def test_digest_shows_existing_file(env):
    t = mk(download_kind=DownloadKind.NATIVE, duplicate_of="/music/Schranz/Artist - Tune.wav", quality_report=dict(OK))
    text = "".join(m.text for m in out.build_digest_messages([t], None))
    assert "♻️ Schon in der Sammlung: Artist - Tune.wav" in text
    assert "/music/Schranz" not in text                            # nur Dateiname, kein Serverpfad
