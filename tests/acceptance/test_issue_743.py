"""Akzeptanztests: Wartungsmeldungen für Scout, Energie und Embeddings sagen, was die Zahlen bedeuten."""
from __future__ import annotations

from pathlib import Path

from sc_digger.db import TrackDB
from sc_digger.embeddings import BackfillSummary
from sc_digger.models import Config
from sc_digger.web.tasks import default_runners


def _cfg(tmp_path: Path, *, feeds=None, charts=None) -> Config:
    raw = {
        "state": {
            "db_path": str(tmp_path / "seen.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
            "health_db_path": str(tmp_path / "seen.sqlite"),
        },
        "download": {
            "inbox_dir": str(tmp_path / "inbox"),
            "collection_dir": str(tmp_path / "collection"),
        },
    }
    if feeds is not None or charts is not None:
        raw["scout"] = {
            "bandcamp": {"feeds": list(feeds or [])},
            "beatport": {"charts": list(charts or [])},
        }
    return Config(raw=raw)


def _store(db: TrackDB, sc_id: int) -> None:
    db.upsert_store_item(
        sc_id=sc_id, title=f"Titel {sc_id}", artist="Artist",
        purchase_url=f"https://example.bandcamp.com/track/{sc_id}",
    )


# --- Scout --------------------------------------------------------------------------


def test_scout_ohne_quellen_sagt_das_und_laeuft_nicht(tmp_path, monkeypatch):
    def darf_nicht_laufen(cfg, **kw):
        raise AssertionError("run_scout darf ohne Quellen nicht laufen")

    monkeypatch.setattr("sc_digger.main.run_scout", darf_nicht_laufen)
    for cfg in (_cfg(tmp_path), _cfg(tmp_path, feeds=[], charts=[])):
        msg = default_runners(cfg)["scout"]()
        assert msg == "Scout: keine Quellen eingetragen (Konfig → Scout: Bandcamp-Feeds, Beatport-Charts)"


def test_scout_trennt_neue_und_schon_bekannte(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, feeds=["https://label.bandcamp.com/feed"])
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        _store(db, 1)

    def fake_scout(c, **kw):
        with TrackDB(c["state"]["track_db_path"]) as db:
            for sc_id in (1, 2, 3):
                _store(db, sc_id)
        return 3

    monkeypatch.setattr("sc_digger.main.run_scout", fake_scout)
    msg = default_runners(cfg)["scout"]()
    assert msg == "Scout fertig: 2 neu, 1 schon bekannt (1 Feed(s), 0 Chart(s))"


def test_scout_quellen_ohne_tracks_wird_benannt(tmp_path, monkeypatch):
    cfg = _cfg(
        tmp_path,
        feeds=["https://label.bandcamp.com/feed"],
        charts=["https://www.beatport.com/genre/hard-techno/2/top-100"],
    )
    monkeypatch.setattr("sc_digger.main.run_scout", lambda c, **kw: 0)
    msg = default_runners(cfg)["scout"]()
    assert msg == "Scout fertig: 0 neu, 0 schon bekannt (1 Feed(s), 1 Chart(s)) – Quellen lieferten keine Tracks"


# --- Energie ------------------------------------------------------------------------


def test_energie_nennt_vorhandene_und_fehlgeschlagene(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sc_digger.backfill.backfill_energy",
        lambda db, *, dry_run=False: {"set": 0, "skipped": 520, "failed": 2},
    )
    msg = default_runners(_cfg(tmp_path))["backfill-energy"]()
    assert msg == "Energie: 0 gesetzt, 520 schon vorhanden, 2 fehlgeschlagen"


# --- Embeddings ---------------------------------------------------------------------


def test_embeddings_nennt_fehlende_dateien_und_kandidaten(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sc_digger.embeddings.backfill_embedding_jobs",
        lambda db, *, dry_run=False: BackfillSummary(
            candidates=5, queued=2, already_queued=1, missing_file=2
        ),
    )
    msg = default_runners(_cfg(tmp_path))["backfill-embeddings"]()
    assert msg == (
        "Embeddings: 2 eingereiht, 1 bereits aktiv, 2 Datei(en) fehlen "
        "(5 bewertete Tracks ohne Embedding)"
    )


def test_embeddings_ohne_kandidaten(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sc_digger.embeddings.backfill_embedding_jobs",
        lambda db, *, dry_run=False: BackfillSummary(),
    )
    msg = default_runners(_cfg(tmp_path))["backfill-embeddings"]()
    assert msg == (
        "Embeddings: 0 eingereiht, 0 bereits aktiv, 0 Datei(en) fehlen "
        "(0 bewertete Tracks ohne Embedding)"
    )
