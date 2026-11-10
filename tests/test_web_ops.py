"""Tests für sc_digger.web.ops und die Betriebskarte auf der Statusseite."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from sc_digger.health import Health
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.ops import OpsItem, collect_ops

ROOT = Path(__file__).resolve().parents[1]
JETZT = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
GB = 1024 ** 3
PASSWORD = "test-pw-ops"
AUTH = ("sc", PASSWORD)


def _make_cfg(tmp_path: Path, *, min_free_gb: float = 0, backup_dir: str = "") -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["download"] = {
        **cfg.raw.get("download", {}),
        "inbox_dir": str(tmp_path / "inbox"),
        "min_free_gb": min_free_gb,
    }
    cfg.raw["backup"] = {"target_dir": backup_dir}
    cfg.raw["state"] = {
        **cfg.raw.get("state", {}),
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def test_collect_ops_frozen_and_keys(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    items = collect_ops(cfg, now=JETZT)
    assert len(items) == 3
    assert [i.key for i in items] == ["disk", "backup", "canary"]
    for it in items:
        assert isinstance(it, OpsItem)
        # frozen dataclass check
        try:
            it.state = "other"  # type: ignore
            assert False, "OpsItem sollte unveränderlich (frozen) sein"
        except (AttributeError, TypeError):
            pass


def test_disk_guard_values_and_errors(tmp_path: Path):
    cfg = _make_cfg(tmp_path, min_free_gb=5)

    def usage_ok(pfad):
        return SimpleNamespace(free=10 * GB)

    items = {i.key: i for i in collect_ops(cfg, usage=usage_ok, now=JETZT)}
    assert items["disk"].state == "ok"
    assert items["disk"].text == "10.0 GB frei (Minimum 5 GB)"

    def usage_low(pfad):
        return SimpleNamespace(free=int(2.5 * GB))

    items = {i.key: i for i in collect_ops(cfg, usage=usage_low, now=JETZT)}
    assert items["disk"].state == "warn"
    assert items["disk"].text == "Nur 2.5 GB frei (Minimum 5 GB)"

    def usage_err(pfad):
        raise OSError("Dateisystem nicht erreichbar")

    items = {i.key: i for i in collect_ops(cfg, usage=usage_err, now=JETZT)}
    assert items["disk"].state == "warn"
    assert items["disk"].text == "Messung fehlgeschlagen"


def test_backup_relative_days(tmp_path: Path):
    ziel = tmp_path / "backups"
    cfg = _make_cfg(tmp_path, backup_dir=str(ziel))

    # Heute (0 Tage)
    (ziel / "2026-11-06").mkdir(parents=True)
    items = {i.key: i for i in collect_ops(cfg, now=JETZT)}
    assert items["backup"].state == "ok"
    assert items["backup"].text == "Letzte Sicherung: 2026-11-06 (heute)"

    # Gestern (1 Tag)
    (ziel / "2026-11-06").rmdir()
    (ziel / "2026-11-05").mkdir(parents=True)
    items = {i.key: i for i in collect_ops(cfg, now=JETZT)}
    assert items["backup"].state == "ok"
    assert items["backup"].text == "Letzte Sicherung: 2026-11-05 (gestern)"

    # 2 Tage (Grenzfall ok)
    (ziel / "2026-11-05").rmdir()
    (ziel / "2026-11-04").mkdir(parents=True)
    items = {i.key: i for i in collect_ops(cfg, now=JETZT)}
    assert items["backup"].state == "ok"
    assert items["backup"].text == "Letzte Sicherung: 2026-11-04 (vor 2 Tagen)"

    # 3 Tage (warn)
    (ziel / "2026-11-04").rmdir()
    (ziel / "2026-11-03").mkdir(parents=True)
    items = {i.key: i for i in collect_ops(cfg, now=JETZT)}
    assert items["backup"].state == "warn"
    assert items["backup"].text == "Letzte Sicherung: 2026-11-03 (vor 3 Tagen)"


def test_canary_empty_and_error_variations(tmp_path: Path):
    cfg = _make_cfg(tmp_path)
    state_db = tmp_path / "seen.sqlite"

    # DB existiert, hat aber noch keine Tabelle runs
    conn = sqlite3.connect(str(state_db))
    conn.execute("CREATE TABLE foo (x INT)")
    conn.commit()
    conn.close()

    items = {i.key: i for i in collect_ops(cfg, now=JETZT)}
    assert items["canary"].state == "off"
    assert items["canary"].text == "noch kein Lauf"

    # Tabelle runs existiert, aber nur andere Modi
    with Health(str(state_db)) as h:
        h.record("discover", ok=True, found=5)
    items = {i.key: i for i in collect_ops(cfg, now=JETZT)}
    assert items["canary"].state == "off"
    assert items["canary"].text == "noch kein Lauf"

    # api_schema fehlerhaft ohne Fehlermeldung
    with Health(str(state_db)) as h:
        h.record("api_schema", ok=False, found=0, error="")
    items = {i.key: i for i in collect_ops(cfg, now=JETZT)}
    assert items["canary"].state == "warn"
    assert items["canary"].text == "Felder fehlen"


def test_status_page_renders_betriebskarte(tmp_path: Path):
    # Setup mit ok, warn und off
    ziel = tmp_path / "backup_dest"
    (ziel / "2026-11-06").mkdir(parents=True)
    cfg = _make_cfg(tmp_path, min_free_gb=10, backup_dir=str(ziel))

    # Canary auf Fehler setzen
    with Health(cfg.raw["state"]["db_path"]) as h:
        h.record("api_schema", ok=False, found=0, error="secret_token_123")

    def _usage(pfad):
        return SimpleNamespace(free=50 * GB)

    client = TestClient(create_app(cfg, password=PASSWORD, disk_usage=_usage))
    resp = client.get("/", auth=AUTH)
    assert resp.status_code == 200

    html = resp.text
    # Überschrift der Betriebskarte
    assert '<h2 class="card-title">Betrieb</h2>' in html
    # Labels
    assert "Speicherplatz" in html
    assert "Sicherung" in html
    assert "API-Kanarienvogel" in html

    # Badges und Texte
    assert "ops-warn" in html
    assert "Warnung" in html
    assert "ops-ok" in html
    assert "OK" in html
    assert "50.0 GB frei" in html
    assert "Letzte Sicherung: 2026-11-06" in html
    assert "Felder fehlen: [REDACTED]" in html or "Felder fehlen: secret_token_123" in html or "Felder fehlen:" in html


def test_status_page_renders_betriebskarte_warnung_bei_wenig_speicher(tmp_path: Path):
    cfg = _make_cfg(tmp_path, min_free_gb=10)

    def _usage(pfad):
        return SimpleNamespace(free=1 * GB)

    client = TestClient(create_app(cfg, password=PASSWORD, disk_usage=_usage))
    resp = client.get("/", auth=AUTH)
    assert resp.status_code == 200

    html = resp.text
    assert "Nur 1.0 GB frei" in html
    assert "ops-warn" in html
    assert "Warnung" in html
