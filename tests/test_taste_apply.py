"""Eigene Tests zu #562: Taste-Modell nach dem Embedding anwenden."""
import logging

from sc_digger import embeddings, taste
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.taste import TasteModel, load_and_score, save_taste_model

MODELL = TasteModel(weights=[1.0, 0.0], bias=0.0, mean=[0.0, 0.0], std=[1.0, 1.0])


def _track(db, pfad="/x/a.flac"):
    return db.upsert_track(TrackRecord(path=pfad, mtime=1.0, size=2))


def _score(db, track_id):
    return db.db.execute("SELECT taste_score FROM tracks WHERE id = ?", (track_id,)).fetchone()[0]


def _speichern(db):
    save_taste_model(db, MODELL, "mfcc-stat-v1", n_pos=10, n_neg=10,
                     trained_at="2026-11-16 10:00:00")


def test_fremdes_modell_ueberschreibt_alten_wert_mit_null(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        t = _track(db)
        db.set_embedding(t.id, "andere-v1", [1.0, 2.0])
        db.db.execute("UPDATE tracks SET taste_score = 0.9 WHERE id = ?", (t.id,))
        db.db.commit()
        _speichern(db)
        assert load_and_score(db, track_ids=[t.id]) == 0
        assert _score(db, t.id) is None


def test_zweiter_aufruf_aendert_nichts(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        t = _track(db)
        db.set_embedding(t.id, "mfcc-stat-v1", [1.0, 2.0])
        _speichern(db)
        assert load_and_score(db, track_ids=[t.id]) == 1
        erster = _score(db, t.id)
        assert load_and_score(db, track_ids=[t.id]) == 1
        assert _score(db, t.id) == erster


def test_unbekannte_ids_werden_uebergangen(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        t = _track(db)
        db.set_embedding(t.id, "mfcc-stat-v1", [1.0, 2.0])
        _speichern(db)
        assert load_and_score(db, track_ids=[t.id, 9999]) == 1


def test_kaputtes_modell_json_ergibt_null_ohne_ausnahme(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        t = _track(db)
        db.set_embedding(t.id, "mfcc-stat-v1", [1.0, 2.0])
        _speichern(db)
        db.db.execute("UPDATE taste_model SET model_json = '{kaputt'")
        db.db.commit()
        assert load_and_score(db) == 0
        assert load_and_score(db, track_ids=[t.id]) == 0
        assert _score(db, t.id) is None


def test_apply_bleibt_true_und_warnt_bei_panne(tmp_path, monkeypatch, caplog):
    def boom(*a, **k):
        raise RuntimeError("Modellpanne")

    monkeypatch.setattr(taste, "load_and_score", boom)
    with TrackDB(tmp_path / "t.sqlite") as db:
        t = _track(db)
        job = embeddings.enqueue_embedding_job(db, t)
        with caplog.at_level(logging.WARNING, logger="sc_digger.embeddings"):
            assert embeddings.apply_embedding_result(
                db, job.id, {"model": "mfcc-stat-v1", "vector": [1.0, 2.0]}) is True
        assert db.get_embedding(t.id) is not None
        assert "Modellpanne" in caplog.text
