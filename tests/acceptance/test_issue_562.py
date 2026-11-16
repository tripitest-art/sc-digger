"""Akzeptanztests: Taste-Modell direkt nach dem Embedding anwenden."""
from sc_digger import embeddings
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.taste import TasteModel, load_and_score, save_taste_model

MODELL = TasteModel(weights=[1.0, 0.0], bias=0.0, mean=[0.0, 0.0], std=[1.0, 1.0])
TRAINED_AT = "2026-11-16 10:00:00"


def _track(db, pfad):
    return db.upsert_track(TrackRecord(path=pfad, mtime=1.0, size=2))


def _score(db, track_id):
    return db.db.execute("SELECT taste_score FROM tracks WHERE id = ?", (track_id,)).fetchone()[0]


def test_ohne_modell_null_und_keine_aenderung(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        t = _track(db, "/x/a.flac")
        db.set_embedding(t.id, "mfcc-stat-v1", [1.0, 2.0])
        assert load_and_score(db) == 0
        assert _score(db, t.id) is None


def test_mit_modell_wird_der_score_geschrieben(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        t = _track(db, "/x/a.flac")
        db.set_embedding(t.id, "mfcc-stat-v1", [1.0, 2.0])
        save_taste_model(db, MODELL, "mfcc-stat-v1", n_pos=10, n_neg=10, trained_at=TRAINED_AT)
        assert load_and_score(db) == 1
        wert = _score(db, t.id)
        assert wert is not None and 0.70 < wert < 0.76  # sigmoid(1.0) = 0.731


def test_track_ids_grenzt_die_bewertung_ein(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        a = _track(db, "/x/a.flac")
        b = _track(db, "/x/b.flac")
        for t in (a, b):
            db.set_embedding(t.id, "mfcc-stat-v1", [1.0, 2.0])
        save_taste_model(db, MODELL, "mfcc-stat-v1", n_pos=10, n_neg=10, trained_at=TRAINED_AT)
        assert load_and_score(db, track_ids=[b.id]) == 1
        assert _score(db, a.id) is None
        assert _score(db, b.id) is not None


def test_fremdes_embedding_modell_bekommt_keinen_wert(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        t = _track(db, "/x/a.flac")
        db.set_embedding(t.id, "discogs-maest-30s-pw-129e", [1.0, 2.0])
        save_taste_model(db, MODELL, "mfcc-stat-v1", n_pos=10, n_neg=10, trained_at=TRAINED_AT)
        assert load_and_score(db) == 0
        assert _score(db, t.id) is None


def test_embedding_ergebnis_bewertet_den_track_sofort(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        t = _track(db, "/x/a.flac")
        save_taste_model(db, MODELL, "mfcc-stat-v1", n_pos=10, n_neg=10, trained_at=TRAINED_AT)
        job = embeddings.enqueue_embedding_job(db, t)
        assert job is not None and job.id is not None
        assert embeddings.apply_embedding_result(
            db, job.id, {"model": "mfcc-stat-v1", "vector": [1.0, 2.0]}) is True
        wert = _score(db, t.id)
        assert wert is not None and 0.70 < wert < 0.76


def test_apply_bleibt_bei_fehlendem_modell_erfolgreich(tmp_path):
    with TrackDB(tmp_path / "t.sqlite") as db:
        t = _track(db, "/x/a.flac")
        job = embeddings.enqueue_embedding_job(db, t)
        assert job is not None and job.id is not None
        assert embeddings.apply_embedding_result(
            db, job.id, {"model": "mfcc-stat-v1", "vector": [1.0, 2.0]}) is True
        assert db.get_embedding(t.id) is not None
        assert _score(db, t.id) is None
