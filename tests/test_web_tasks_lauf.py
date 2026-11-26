"""Eigene Tests für die Ausführung von Wartungsknöpfen unter der Lauf-Sperre (Issue #759)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sc_digger.db import TrackDB
from sc_digger.models import Config
from sc_digger.runlock import RunLockBusy, run_lock
from sc_digger.web.tasks import (
    OHNE_SPERRE,
    PROTOKOLLIERTE_AKTIONEN,
    default_runners,
    run_geschuetzt,
)


def _cfg(tmp_path: Path, **extra) -> Config:
    raw = {
        "state": {
            "db_path": str(tmp_path / "seen.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
            "lock_wait_s": 0,
        },
        "health": {"alert_after_bad_runs": 5},
        "backup": {"target_dir": str(tmp_path / "backup")},
    }
    raw.update(extra)
    return Config(raw)


def _runs(tmp_path: Path, mode: str) -> list[tuple]:
    db = tmp_path / "seen.sqlite"
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        return con.execute(
            "SELECT ok, found, error FROM runs WHERE mode=? ORDER BY id", (mode,)
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def test_konstanten_protokollierte_aktionen_und_ohne_sperre():
    assert PROTOKOLLIERTE_AKTIONEN == frozenset({"scout", "taste", "audit"})
    assert OHNE_SPERRE == frozenset({"watch"})


def test_scout_knopf_schreibt_einen_lauf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _cfg(
        tmp_path,
        scout={"bandcamp": {"feeds": ["https://example.com/feed"]}},
    )
    with TrackDB(cfg["state"]["track_db_path"]):
        pass

    monkeypatch.setattr("sc_digger.main.run_scout", lambda c: 5)

    msg = default_runners(cfg)["scout"]()
    assert "Scout fertig" in msg
    assert _runs(tmp_path, "scout") == [(1, 1, None)]


def test_scout_knopf_protokolliert_fehler_und_wirft_weiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg = _cfg(
        tmp_path,
        scout={"bandcamp": {"feeds": ["https://example.com/feed"]}},
    )
    with TrackDB(cfg["state"]["track_db_path"]):
        pass

    def kaputt(c):
        raise RuntimeError("Bandcamp antwortet nicht")

    monkeypatch.setattr("sc_digger.main.run_scout", kaputt)

    with pytest.raises(RuntimeError, match="Bandcamp antwortet nicht"):
        default_runners(cfg)["scout"]()

    (ok, found, error), = _runs(tmp_path, "scout")
    assert (ok, found) == (0, 0)
    assert "RuntimeError" in error and "Bandcamp antwortet nicht" in error


def test_fehlt_state_db_path_kein_protokoll_kein_absturz(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = Config({
        "state": {
            "track_db_path": str(tmp_path / "tracks.sqlite"),
        }
    })
    with TrackDB(cfg["state"]["track_db_path"]):
        pass

    # taste ohne db_path
    from types import SimpleNamespace
    monkeypatch.setattr("sc_digger.taste.run_taste",
                        lambda db, now=None: SimpleNamespace(trained=True, scored=1, reason=""))
    res = default_runners(cfg)["taste"]()
    assert "1 Tracks bewertet" in res
    assert not (tmp_path / "seen.sqlite").exists()

    # run_geschuetzt direkt ohne db_path
    assert run_geschuetzt(cfg, "audit", lambda: "audit ok") == "audit ok"
    assert run_geschuetzt(cfg, "backfill-energy", lambda: "energie ok") == "energie ok"


def test_watch_bleibt_ohne_sperre(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr("sc_digger.watch.run_watch", lambda c: 0)

    # Sperre ist durch discover belegt
    with run_lock(tmp_path, "discover"):
        # watch muss trotzdem laufen, da in OHNE_SPERRE
        res = default_runners(cfg)["watch"]()
        assert res == "Wächter fertig"


def test_nicht_protokollierte_aktion_schreibt_nicht_in_runs(tmp_path: Path):
    cfg = _cfg(tmp_path)
    res = run_geschuetzt(cfg, "backfill-energy", lambda: "energie fertig")
    assert res == "energie fertig"
    assert _runs(tmp_path, "backfill-energy") == []


def test_run_geschuetzt_belegt_wirft_runtime_error(tmp_path: Path):
    cfg = _cfg(tmp_path)
    aufgerufen = False

    def fn():
        nonlocal aufgerufen
        aufgerufen = True
        return "ja"

    with run_lock(tmp_path, "discover"):
        with pytest.raises(RuntimeError, match="Lauf-Sperre belegt"):
            run_geschuetzt(cfg, "taste", fn)

    assert not aufgerufen
    assert _runs(tmp_path, "taste") == []
