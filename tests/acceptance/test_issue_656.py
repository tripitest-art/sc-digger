"""Akzeptanztests für die Wartungsaktionen Backfill, Requeue und Sicherung (Issue #656)."""
from __future__ import annotations

from pathlib import Path

from sc_digger.models import Config
from sc_digger.web.tasks import default_runners

ERWARTET = {
    "backfill-energy",
    "backfill-artist",
    "backfill-embeddings",
    "requeue",
    "backup",
}


def _cfg(tmp_path: Path) -> Config:
    return Config(raw={
        "state": {
            "db_path": str(tmp_path / "seen.sqlite"),
            "track_db_path": str(tmp_path / "tracks.sqlite"),
            "health_db_path": str(tmp_path / "seen.sqlite"),
        },
        "download": {
            "inbox_dir": str(tmp_path / "inbox"),
            "collection_dir": str(tmp_path / "collection"),
        },
    })


def test_default_runners_enthalten_die_wartungsaktionen(tmp_path: Path):
    runners = default_runners(_cfg(tmp_path))

    assert ERWARTET <= set(runners)
    assert all(callable(runner) for runner in runners.values())


def test_jede_aktion_hat_eine_bezeichnung(tmp_path: Path):
    from sc_digger.web.tasks import action_labels

    labels = action_labels()

    for action in ERWARTET:
        assert action in labels
        assert labels[action]
