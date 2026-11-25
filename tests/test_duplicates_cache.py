"""Eigene Tests zum Duplikat-Zwischenspeicher (Issue #734)."""
from __future__ import annotations

import json
from pathlib import Path

from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.fingerprint import bit_error_rate, encode_fingerprint
from sc_digger.web.duplicates_cache import CACHE_NAME, cached_duplicate_groups


def _add(db: TrackDB, name: str) -> None:
    db.upsert_track(TrackRecord(path=f"/music/{name}.wav", mtime=1.0, size=1, status=TrackStatus.ARCHIVE,
                                fingerprint=encode_fingerprint([1, 2, 3] * 30), fingerprint_duration=300.0))


def test_bit_error_rate_randfaelle():
    assert bit_error_rate([], []) == 1.0
    assert bit_error_rate([1] * 10, [1] * 10) == 1.0  # unter min_overlap
    assert bit_error_rate([-1] * 60, [0xFFFFFFFF] * 60) == 0.0  # negative Werte werden normalisiert
    assert bit_error_rate([0] * 60, [0xFFFFFFFF] * 60) == 1.0


def test_defekter_zwischenspeicher_wird_neu_berechnet(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    cache = tmp_path / CACHE_NAME
    with TrackDB(db_path) as db:
        _add(db, "a")
    cache.write_text("kein json", encoding="utf-8")
    res = cached_duplicate_groups(db_path, cache, compute=lambda p: [{"count": 2, "tracks": []}])
    assert res.stale is False and res.groups == [{"count": 2, "tracks": []}]
    assert json.loads(cache.read_text(encoding="utf-8"))["groups"] == res.groups
    assert not cache.with_name(cache.name + ".tmp").exists()


def test_fehlende_db_rechnet_und_wirft_nicht(tmp_path: Path):
    res = cached_duplicate_groups(tmp_path / "fehlt.sqlite", tmp_path / CACHE_NAME, compute=lambda p: [])
    assert res.groups == [] and res.stale is False
    assert not (tmp_path / "fehlt.sqlite").exists()


def test_fehlerhafter_job_gibt_den_pfad_frei(tmp_path: Path):
    db_path = tmp_path / "tracks.sqlite"
    cache = tmp_path / CACHE_NAME
    with TrackDB(db_path) as db:
        _add(db, "a")
    cached_duplicate_groups(db_path, cache, compute=lambda p: [], background=lambda j: j())
    with TrackDB(db_path) as db:
        _add(db, "b")

    def kaputt(p):
        raise RuntimeError("boom")

    cached_duplicate_groups(db_path, cache, compute=kaputt, background=lambda j: j())
    wartend: list = []
    res = cached_duplicate_groups(db_path, cache, compute=lambda p: [], background=wartend.append)
    assert res.stale is True and len(wartend) == 1  # zweiter Job startbar, Pfad war freigegeben
