"""Akzeptanztests: Wartungsknöpfe halten die Lauf-Sperre und protokollieren Läufe."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from sc_digger.models import Config
from sc_digger.runlock import run_lock
from sc_digger.web.tasks import default_runners


def _cfg(tmp_path: Path) -> Config:
    return Config({
        "state": {"db_path": str(tmp_path / "seen.sqlite"),
                  "track_db_path": str(tmp_path / "tracks.sqlite"),
                  "lock_wait_s": 0},
        "health": {"alert_after_bad_runs": 5},
        "backup": {"target_dir": str(tmp_path / "backup")},
    })


def _runs(tmp_path: Path, mode: str) -> list[tuple]:
    db = tmp_path / "seen.sqlite"
    if not db.exists():
        return []
    con = sqlite3.connect(str(db))
    try:
        return con.execute("SELECT ok, found, error FROM runs WHERE mode=? ORDER BY id", (mode,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def test_taste_knopf_schreibt_einen_lauf(tmp_path, monkeypatch):
    monkeypatch.setattr("sc_digger.taste.run_taste",
                        lambda db, now=None: SimpleNamespace(trained=True, scored=3, reason=""))
    text = default_runners(_cfg(tmp_path))["taste"]()
    assert "3" in text
    assert _runs(tmp_path, "taste") == [(1, 1, None)]


def test_taste_knopf_protokolliert_fehler_und_wirft_weiter(tmp_path, monkeypatch):
    def kaputt(db, now=None):
        raise ValueError("Embeddings fehlen")

    monkeypatch.setattr("sc_digger.taste.run_taste", kaputt)
    with pytest.raises(ValueError):
        default_runners(_cfg(tmp_path))["taste"]()
    (ok, found, error), = _runs(tmp_path, "taste")
    assert (ok, found) == (0, 0)
    assert "ValueError" in error and "Embeddings fehlen" in error


def test_audit_knopf_schreibt_einen_lauf(tmp_path, monkeypatch):
    monkeypatch.setattr("sc_digger.audit.run_audit",
                        lambda cfg, **kw: SimpleNamespace(scanned=4, ok=4, adopted_tracks=[]))
    default_runners(_cfg(tmp_path))["audit"]()
    assert _runs(tmp_path, "audit") == [(1, 1, None)]


def test_backup_knopf_schreibt_genau_einen_lauf(tmp_path, monkeypatch):
    monkeypatch.setattr("sc_digger.backup.run_backup", lambda cfg, *, today, notify=None: 0)
    default_runners(_cfg(tmp_path))["backup"]()
    assert _runs(tmp_path, "backup") == [(1, 1, None)]


def test_backup_knopf_fehlschlag_ist_lauf_und_fehler(tmp_path, monkeypatch):
    monkeypatch.setattr("sc_digger.backup.run_backup", lambda cfg, *, today, notify=None: 1)
    with pytest.raises(RuntimeError, match="Sicherung fehlgeschlagen"):
        default_runners(_cfg(tmp_path))["backup"]()
    (ok, found, _), = _runs(tmp_path, "backup")
    assert (ok, found) == (0, 0)


@pytest.mark.parametrize("aktion", ["taste", "audit", "backup", "backfill-energy"])
def test_belegte_sperre_verhindert_den_knopf(tmp_path, monkeypatch, aktion):
    aufrufe = []
    monkeypatch.setattr("sc_digger.taste.run_taste", lambda db, now=None: aufrufe.append("taste"))
    monkeypatch.setattr("sc_digger.audit.run_audit", lambda cfg, **kw: aufrufe.append("audit"))
    monkeypatch.setattr("sc_digger.backup.run_backup", lambda cfg, *, today, notify=None: aufrufe.append("backup") or 0)
    monkeypatch.setattr("sc_digger.backfill.backfill_energy", lambda db, **kw: aufrufe.append("energie") or {})
    with run_lock(tmp_path, "discover"):
        with pytest.raises(RuntimeError, match="Lauf-Sperre belegt"):
            default_runners(_cfg(tmp_path))[aktion]()
    assert aufrufe == []


def test_knopf_hat_waehrend_des_laufs_die_sperre(tmp_path, monkeypatch):
    from sc_digger.runlock import RunLockBusy

    gesehen = []

    def pruefe(db, now=None):
        try:
            with run_lock(tmp_path, "discover", wait_s=0):
                gesehen.append("frei")
        except RunLockBusy:
            gesehen.append("belegt")
        return SimpleNamespace(trained=False, scored=0, reason="test")

    monkeypatch.setattr("sc_digger.taste.run_taste", pruefe)
    default_runners(_cfg(tmp_path))["taste"]()
    assert gesehen == ["belegt"]
    with run_lock(tmp_path, "discover"):  # danach wieder frei
        pass
