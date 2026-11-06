import json
from pathlib import Path

import pytest

from sc_digger import main as m
from sc_digger.db import MIGRATIONS, TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.taste import (
    TasteModel,
    TasteRun,
    build_training_set,
    load_taste_model,
    run_taste,
    training_data,
)

ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-11-06 07:05:00"


def _track(db, name, *, emb=None, model="maest", feedback=None, **felder):
    rec = db.upsert_track(TrackRecord(path=f"/inbox/{name}.mp3", mtime=1.0, size=1, **felder))
    if feedback is not None:
        db.set_feedback(rec.id, feedback)   # upsert_track schreibt feedback bewusst nie
    if emb is not None:
        db.set_embedding(rec.id, model, emb)
    return rec.id


def _fixture(db, *, negative=5):
    ids = {
        "like": _track(db, "like", emb=[2.0, 0.1], feedback="like"),
        "adopted": _track(db, "adopted", emb=[2.1, 0.0], adopted_at="2026-11-01 10:00:00"),
        "rating": _track(db, "rating", emb=[1.9, -0.1], rb_rating=4),
        "sc_like": _track(db, "sc_like", emb=[2.2, 0.2], sc_id=101),
        "archiv": _track(db, "archiv", emb=[1.8, 0.0], sc_id=102),
        "konflikt": _track(db, "konflikt", emb=[2.0, -0.2], sc_id=103, feedback="dislike"),
    }
    _track(db, "archivzeile", sc_id=102, adopted_at="2026-11-02 10:00:00", status="archive")
    db.set_sc_feedback(101, "like")
    db.set_sc_feedback(103, "like")
    for i in range(negative):
        if i < 2:
            ids[f"dislike{i}"] = _track(db, f"dislike{i}", emb=[-2.0 - i / 10, 0.0], feedback="dislike")
        else:
            ids[f"sc_dislike{i}"] = _track(db, f"sc_dislike{i}", emb=[-2.0 + i / 10, 0.1], sc_id=200 + i)
            db.set_sc_feedback(200 + i, "dislike")
    ids["later"] = _track(db, "later", emb=[0.0, 5.0], feedback="later")
    ids["neu"] = _track(db, "neu", emb=[2.0, 0.0])
    ids["ohne_embedding"] = _track(db, "ohne_embedding", feedback="like")
    ids["anderes_modell"] = _track(db, "anderes_modell", emb=[9.0, 9.0], model="mfcc", feedback="like")
    db.db.commit()
    return ids


def _score(db, track_id):
    return db.db.execute("SELECT taste_score FROM tracks WHERE id = ?", (track_id,)).fetchone()[0]


def test_migration_18():
    assert (18, "0018_taste_score") in [(v, n) for v, n, _ in MIGRATIONS]


def test_training_data_nutzt_alle_bewertungsquellen(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        ids = _fixture(db)
        modell, samples = training_data(db)
        assert modell == "maest"
        labels = [label for _, label in samples]
        assert labels == [1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0], "Reihenfolge nach Track-ID, Konflikt zählt positiv"
        assert samples[3][0] == pytest.approx([2.2, 0.2]), "sc_feedback 'like' über sc_id"
        assert samples[4][0] == pytest.approx([1.8, 0.0]), "Archivzeile mit derselben sc_id ist übernommen"
        assert build_training_set(db) == samples
        assert ids["later"] and [0.0, 5.0] not in [v for v, _ in samples]


def test_training_data_leer(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        assert training_data(db) == (None, [])


def test_run_taste_trainiert_speichert_und_bewertet(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        ids = _fixture(db)
        db.db.execute("UPDATE tracks SET taste_score = 0.9 WHERE id = ?", (ids["ohne_embedding"],))
        db.db.commit()
        r = run_taste(db, now=NOW)
        assert r == TasteRun(True, "maest", 6, 5, 13, "")
        for name in ("like", "adopted", "rating", "sc_like", "archiv", "konflikt", "neu"):
            assert _score(db, ids[name]) > 0.5, name
        for name in ("dislike0", "dislike1", "sc_dislike2", "sc_dislike3", "sc_dislike4"):
            assert _score(db, ids[name]) < 0.5, name
        assert _score(db, ids["ohne_embedding"]) is None, "alter Score ohne Embedding wird gelöscht"
        assert _score(db, ids["anderes_modell"]) is None
        assert 0.0 <= _score(db, ids["later"]) <= 1.0

        row = db.db.execute("SELECT * FROM taste_model").fetchall()
        assert len(row) == 1
        assert (row[0]["id"], row[0]["embedding_model"], row[0]["trained_at"], row[0]["n_pos"], row[0]["n_neg"]) \
            == (1, "maest", NOW, 6, 5)
        geladen = load_taste_model(db)
        assert geladen is not None and geladen[0] == "maest"
        assert TasteModel.from_dict(json.loads(row[0]["model_json"])) == geladen[1]
        assert geladen[1].score([2.0, 0.0]) == pytest.approx(_score(db, ids["neu"]))

        assert run_taste(db, now="2026-11-07 07:05:00").trained is True
        assert db.db.execute("SELECT COUNT(*), MAX(trained_at) FROM taste_model").fetchone()[:] == (1, "2026-11-07 07:05:00")


def test_zu_wenige_beispiele_aendert_nichts(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        ids = _fixture(db, negative=4)
        db.db.execute("UPDATE tracks SET taste_score = 0.42 WHERE id = ?", (ids["neu"],))
        db.db.commit()
        r = run_taste(db, now=NOW)
        assert r == TasteRun(False, "maest", 6, 4, 0,
                             "zu wenige Beispiele: 6 gefällt, 4 gefällt nicht (mindestens 5 je Klasse)")
        assert _score(db, ids["neu"]) == pytest.approx(0.42)
        assert load_taste_model(db) is None


def test_dry_run_schreibt_nichts(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        ids = _fixture(db)
        assert run_taste(db, now=NOW, dry_run=True) == TasteRun(True, "maest", 6, 5, 13, "")
        assert _score(db, ids["neu"]) is None
        assert load_taste_model(db) is None


def test_load_taste_model_kaputtes_json(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.db.execute("INSERT INTO taste_model VALUES (1, 'maest', ?, 5, 5, '{kaputt')", (NOW,))
        db.db.commit()
        assert load_taste_model(db) is None


def test_cli_taste(tmp_path, capsys):
    cfg = Config.load(ROOT / "config.yaml")
    track_db = tmp_path / "tracks.sqlite"
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"), "track_db_path": str(track_db)}
    with TrackDB(track_db) as db:
        _fixture(db)

    text = m.run_taste_cmd(cfg, dry_run=True, now=NOW)
    assert text == "Taste-Score: Modell maest aus 6 gefällt / 5 gefällt nicht trainiert, 13 Tracks würden bewertet"
    text = m.run_taste_cmd(cfg, now=NOW)
    assert text == "Taste-Score: Modell maest aus 6 gefällt / 5 gefällt nicht trainiert, 13 Tracks bewertet"
    assert text in capsys.readouterr().out

    leer = tmp_path / "leer.sqlite"
    cfg.raw["state"]["track_db_path"] = str(leer)
    assert m.run_taste_cmd(cfg, now=NOW) == (
        "Taste-Score: nicht trainiert, zu wenige Beispiele: 0 gefällt, 0 gefällt nicht (mindestens 5 je Klasse)")


def test_cron_eintrag():
    text = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    zeile = "5 7 * * * . /app/cron.env; cd /app && python -m sc_digger.main taste >> /proc/1/fd/1 2>&1"
    assert zeile in text
    assert 'grep -q "sc_digger.main taste"' in text
    assert text.index(zeile) < text.index("\ncron\n")
