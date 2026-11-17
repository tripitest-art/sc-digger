"""Tests zu Issue #566: Handkorrekturen im Verknüpfungsvorschlag und Subcommand ``requeue``.

Ergänzt die Akzeptanztests um Gleichstand, Mehrdeutigkeit, leere Overrides und die CLI.
"""
import sc_digger.main as main_mod
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.linksource import propose_links
from sc_digger.models import Config
from tests.test_modes import mk


def cand(i, title, artist="uploader"):
    return mk(
        i, title=title, permalink_url=f"https://soundcloud.com/{artist}/t{i}",
        user={"username": artist, "permalink_url": f"https://soundcloud.com/{artist}"},
    )


def record(track_id=1, path="/sammlung/Svetec - Acid Kick.wav"):
    return TrackRecord(id=track_id, path=path, mtime=1.0, size=1)


def test_override_schliesst_nur_einen_von_zwei_gleich_guten_kandidaten_aus():
    """Ohne Override gewinnt bei Gleichstand der erste; mit Override auf ihn der zweite."""
    candidates = [cand(1, "Svetec - Acid Kick"), cand(2, "Svetec - Acid Kick")]
    ohne = propose_links([record()], candidates)
    assert [(p.sc_id, p.ambiguous) for p in ohne] == [(1, True)]

    mit = propose_links([record()], candidates, overrides={1: "not_duplicate"})
    # Der verbleibende Kandidat ist der einzige Treffer -> nicht mehr mehrdeutig.
    assert [(p.sc_id, p.url, p.ambiguous) for p in mit] == [
        (2, "https://soundcloud.com/uploader/t2", False)
    ]


def test_override_auf_den_zweiten_laesst_den_ersten_stehen():
    candidates = [cand(1, "Svetec - Acid Kick"), cand(2, "Svetec - Acid Kick")]
    out = propose_links([record()], candidates, overrides={2: "already_in_collection"})
    assert [(p.sc_id, p.ambiguous) for p in out] == [(1, False)]


def test_override_gilt_fuer_jede_zeile():
    """Der Ausschluss hängt am Kandidaten, nicht an einer einzelnen Sammlungszeile."""
    records = [record(1), record(2, "/sammlung/kopie/Svetec - Acid Kick.wav")]
    candidates = [cand(1, "Svetec - Acid Kick"), cand(2, "Svetec - Acid Kick")]
    out = propose_links(records, candidates, overrides={1: "not_duplicate"})
    assert [(p.record_id, p.sc_id) for p in out] == [(1, 2), (2, 2)]


def test_leere_und_fremde_overrides_aendern_nichts():
    candidates = [cand(1, "Svetec - Acid Kick")]
    erwartet = propose_links([record()], candidates)
    assert [p.sc_id for p in erwartet] == [1]
    assert propose_links([record()], candidates, overrides=None) == erwartet
    assert propose_links([record()], candidates, overrides={}) == erwartet
    assert propose_links([record()], candidates, overrides={999: "not_duplicate"}) == erwartet


def test_overrides_aus_der_datenbank_passen_direkt(tmp_path):
    """``TrackDB.list_match_overrides()`` liefert genau die Form, die ``overrides`` erwartet."""
    with TrackDB(tmp_path / "t.sqlite") as db:
        db.set_match_override(1, "not_duplicate")
        overrides = db.list_match_overrides()
    candidates = [cand(1, "Svetec - Acid Kick"), cand(2, "Svetec - Acid Kick")]
    out = propose_links([record()], candidates, overrides=overrides)
    assert [p.sc_id for p in out] == [2]


def _cli(monkeypatch, argv):
    captured: dict = {}

    def fake_requeue(cfg, **kwargs):
        captured.update(kwargs)
        return ""

    monkeypatch.setattr(main_mod, "run_requeue_cmd", fake_requeue)
    monkeypatch.setattr(main_mod, "install_redacting_logging", lambda *a, **k: None)
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, *a, **k: cls({})))
    monkeypatch.setattr("sys.argv", ["sc-digger", *argv])
    main_mod.cli()
    return captured


def test_cli_requeue_erkennt_timeout_minutes(monkeypatch):
    assert _cli(monkeypatch, ["requeue", "--timeout-minutes", "60"]) == {"timeout_minutes": 60}


def test_cli_requeue_ohne_timeout_nimmt_die_grenze_je_job_typ(monkeypatch):
    assert _cli(monkeypatch, ["requeue"]) == {"timeout_minutes": None}


def test_requeue_laesst_frische_laufende_jobs_in_ruhe(tmp_path, capsys):
    """Ein Job, der noch innerhalb der Zeitgrenze läuft, wird nicht zurückgesetzt."""
    pfad = tmp_path / "t.sqlite"
    with TrackDB(pfad) as db:
        t = db.upsert_track(TrackRecord(path="/x/a.flac", mtime=1.0, size=2))
        for alter in (5, 90, 120):
            db.db.execute(
                "INSERT INTO jobs (track_id, job_type, status, updated_at) "
                "VALUES (?, 'embedding', 'running', datetime('now', ?))",
                (t.id, f"-{alter} minutes"),
            )
        db.db.commit()
    text = main_mod.run_requeue_cmd(
        Config({"state": {"track_db_path": str(pfad)}}), timeout_minutes=60)
    assert text == "Job-Warteschlange: 2 hängende Jobs zurückgesetzt"
    assert capsys.readouterr().out.strip() == text
    with TrackDB(pfad) as db:
        rows = db.db.execute("SELECT status FROM jobs ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["running", "pending", "pending"]
