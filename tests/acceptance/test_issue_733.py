"""Akzeptanztests: Leerzustände mit nächstem Schritt (Issue <N>)."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.app import config_sections, create_app
from sc_digger.web.ops import OPS_SETTINGS_HREF, OpsItem

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-leer"


def _cfg(tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml", local=tmp_path / "keine-override.yaml")
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    cfg.raw["download"] = {**cfg.raw["download"], "inbox_dir": str(inbox), "min_free_gb": 0}
    cfg.raw["backup"] = {"target_dir": "", "keep_daily": 7, "keep_weekly": 4}
    return cfg


def _client(tmp_path: Path, *, editor: bool) -> TestClient:
    kw = {}
    if editor:
        (tmp_path / "config.local.yaml").write_text("", encoding="utf-8")
        kw = {"base_path": ROOT / "config.yaml", "local_path": tmp_path / "config.local.yaml"}
    return TestClient(create_app(_cfg(tmp_path), password=PASSWORD, **kw))


def test_einstell_links_zeigen_auf_vorhandene_abschnitte():
    assert OPS_SETTINGS_HREF == {"disk": "/config/health-alarm", "canary": "/config/api-kanarienvogel"}
    slugs = {f"/config/{s.slug}" for s in config_sections()}
    assert set(OPS_SETTINGS_HREF.values()) <= slugs
    assert OpsItem("disk", "Speicherplatz", "off", "x").settings_href == "/config/health-alarm"
    assert OpsItem("backup", "Sicherung", "off", "x").settings_href is None


def test_betriebskarte_mit_editor_verlinkt_die_einstellung(tmp_path):
    html = _client(tmp_path, editor=True).get("/", auth=("sc", PASSWORD)).text
    assert '<a href="/config/health-alarm" class="meta meta-sm" data-ops-link="disk">Einstellen →</a>' in html
    assert 'data-ops-link="backup"' in html and "Sicherung und Wiederherstellung" in html


def test_betriebskarte_ohne_editor_ohne_link(tmp_path):
    html = _client(tmp_path, editor=False).get("/", auth=("sc", PASSWORD)).text
    assert 'data-ops-link="disk"' not in html
    assert 'data-ops-link="backup"' in html


def test_hinweis_bei_null_uebernommen(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(path="/music/Schranz/A - B.wav", mtime=1.0, size=1,
                                    status=TrackStatus.ARCHIVE, artist="A", title="B"))
    html = _client(tmp_path, editor=False).get("/", auth=("sc", PASSWORD)).text
    start = html.index('data-hint="adopted"')
    abschnitt = html[start:start + 400]
    assert "06:45" in abschnitt and 'href="/audit"' in abschnitt


def test_kein_hinweis_wenn_etwas_uebernommen_ist(tmp_path):
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(path="/music/Schranz/A - B.wav", mtime=1.0, size=1,
                                    status=TrackStatus.ARCHIVE, artist="A", title="B",
                                    adopted_at="2026-11-25 06:45:00"))
    html = _client(tmp_path, editor=False).get("/", auth=("sc", PASSWORD)).text
    assert 'data-hint="adopted"' not in html


def test_kuratoren_leer_nennt_den_naechsten_schritt(tmp_path):
    html = _client(tmp_path, editor=False).get("/kuratoren", auth=("sc", PASSWORD)).text
    assert "Keine Artists mit mindestens" in html
    start = html.index('data-hint="kuratoren-leer"')
    abschnitt = html[start:start + 500]
    assert 'href="/#maintenance-card"' in abschnitt and "Artist aus Dateinamen ergänzen" in abschnitt


def test_smartlists_leer_verweist_auf_den_track_explorer(tmp_path):
    html = _client(tmp_path, editor=False).get("/smartlists", auth=("sc", PASSWORD)).text
    assert "Noch keine Smartlists angelegt." in html
    start = html.index('data-hint="smartlists-leer"')
    assert 'href="/tracks"' in html[start:start + 400]
