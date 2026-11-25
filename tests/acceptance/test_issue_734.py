"""Akzeptanztests: Duplikat-Seite schnell durch numpy und Zwischenspeicher (Issue <N>)."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.fingerprint import bit_error_rate, encode_fingerprint
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.duplicates_cache import (CACHE_NAME, cache_path_for, cached_duplicate_groups,
                                            fingerprint_signature)

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-duplikate"


def _referenz_ber(a, b, *, max_offset=80, min_overlap=50):
    """Die bisherige reine Python-Fassung als Messlatte."""
    a = [v & 0xFFFFFFFF for v in a]
    b = [v & 0xFFFFFFFF for v in b]
    best = 1.0
    for off in range(-max_offset, max_offset + 1):
        sa, sb = max(off, 0), max(-off, 0)
        m = min(len(a) - sa, len(b) - sb)
        if m < min_overlap:
            continue
        r = sum((x ^ y).bit_count() for x, y in zip(a[sa:sa + m], b[sb:sb + m])) / (32 * m)
        if r < best:
            best = r
            if best == 0.0:
                break
    return best


def _werte(seed: int, n: int) -> list[int]:
    """Feste Pseudo-Zufallswerte (LCG), damit der Test ohne random auskommt."""
    out, x = [], seed
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0xFFFFFFFF
        out.append(x)
    return out


def test_bit_error_rate_liefert_dieselben_werte():
    for k in range(25):
        a = [v - (1 << 31) for v in _werte(k + 1, 40 + 15 * k)]
        if k % 3 == 0:
            b = [v ^ (1 << (i % 32)) if i % 5 == 0 else v for i, v in enumerate(a)][k:]
        else:
            b = _werte(1000 + k, 40 + 13 * k)
        assert abs(bit_error_rate(a, b) - _referenz_ber(a, b)) < 1e-12


def _add(db: TrackDB, name: str, values: list[int], duration: float, status=TrackStatus.ARCHIVE):
    db.upsert_track(TrackRecord(path=f"/music/{name}.wav", mtime=1.0, size=1, status=status,
                                fingerprint=encode_fingerprint(values), fingerprint_duration=duration))


def test_signatur(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    assert fingerprint_signature(db_path) is None
    assert not db_path.exists()
    with TrackDB(db_path) as db:
        _add(db, "a", [1, 2, 3] * 30, 300.0)
    erste = fingerprint_signature(db_path)
    assert erste is not None
    with TrackDB(db_path) as db:
        _add(db, "b", [4, 5, 6] * 30, 301.0)
    assert fingerprint_signature(db_path) != erste


def test_zwischenspeicher_rechnet_nur_bei_aenderungen(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    cache = tmp_path / CACHE_NAME
    with TrackDB(db_path) as db:
        _add(db, "a", [1, 2, 3] * 30, 300.0)
    aufrufe: list[int] = []

    def compute(path):
        aufrufe.append(1)
        return [{"count": len(aufrufe), "tracks": []}]

    def sofort(job):
        job()

    erst = cached_duplicate_groups(db_path, cache, compute=compute, background=sofort)
    assert (erst.groups, erst.stale) == ([{"count": 1, "tracks": []}], False)
    assert erst.computed_at
    assert json.loads(cache.read_text(encoding="utf-8"))["groups"] == erst.groups

    zweit = cached_duplicate_groups(db_path, cache, compute=compute, background=sofort)
    assert (zweit.groups, zweit.stale, len(aufrufe)) == (erst.groups, False, 1)

    with TrackDB(db_path) as db:
        _add(db, "b", [4, 5, 6] * 30, 301.0)
    alt = cached_duplicate_groups(db_path, cache, compute=compute, background=sofort)
    assert (alt.groups, alt.stale) == ([{"count": 1, "tracks": []}], True)
    neu = cached_duplicate_groups(db_path, cache, compute=compute, background=sofort)
    assert (neu.groups, neu.stale, len(aufrufe)) == ([{"count": 2, "tracks": []}], False, 2)


def test_nur_eine_hintergrundberechnung_gleichzeitig(tmp_path):
    db_path = tmp_path / "tracks.sqlite"
    cache = tmp_path / CACHE_NAME
    with TrackDB(db_path) as db:
        _add(db, "a", [1, 2, 3] * 30, 300.0)
    cached_duplicate_groups(db_path, cache, compute=lambda p: [], background=lambda job: job())
    with TrackDB(db_path) as db:
        _add(db, "b", [4, 5, 6] * 30, 301.0)
    wartend: list = []
    for _ in range(3):
        res = cached_duplicate_groups(db_path, cache, compute=lambda p: [], background=wartend.append)
        assert res.stale is True
    assert len(wartend) == 1
    wartend[0]()
    assert cached_duplicate_groups(db_path, cache, compute=lambda p: [],
                                   background=wartend.append).stale is False


def _cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml", local=tmp_path / "keine-override.yaml")
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    return cfg


def test_seite_und_api_nutzen_den_zwischenspeicher(tmp_path):
    assert cache_path_for(tmp_path / "seen.sqlite") == tmp_path / "duplicate_groups.json"
    werte = _werte(3, 200)
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        _add(db, "Sammlung - Titel", werte, 300.0)
        _add(db, "Inbox - Titel", werte, 300.5, status=TrackStatus.INBOX)
    client = TestClient(create_app(_cfg(tmp_path), password=PASSWORD))

    api = client.get("/api/duplicates", auth=("sc", PASSWORD)).json()
    assert api["ok"] is True and api["total_groups"] == 1 and api["stale"] is False and api["computed_at"]
    assert (tmp_path / "duplicate_groups.json").is_file()

    html = client.get("/duplicates", auth=("sc", PASSWORD)).text
    assert "data-duplicates-stand" in html
    assert "data-duplicates-stale" not in html
