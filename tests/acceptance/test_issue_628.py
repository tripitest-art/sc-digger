"""Akzeptanztests: Knopf „Workstation-Lauf starten" auf der Statusseite (Issue #<N>)."""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as web_app
from sc_digger.web import workstation

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-gw-4711"
AUTH = ("sc", PASSWORD)
WAKE_ENV = "SC_DIGGER_WAKE_URL"


def _setup(tmp_path, monkeypatch, *, jobs=2, wake_url="http://wake.invalid/run"):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
        "gpu_wake_state": str(tmp_path / "gpu_wake.json"),
    }
    cfg.raw["gpu_wake"] = {"cooldown_s": 300}
    if wake_url is None:
        monkeypatch.delenv(WAKE_ENV, raising=False)
    else:
        monkeypatch.setenv(WAKE_ENV, wake_url)
    calls = []
    monkeypatch.setattr(workstation, "count_pending_jobs", lambda *_a, **_k: jobs)
    monkeypatch.setattr(workstation, "wake_workstation",
                        lambda url, **_k: calls.append(url) or True)
    return TestClient(web_app.create_app(cfg, password=PASSWORD)), tmp_path, calls


def test_knopf_weckt_die_workstation_bei_offenen_jobs(tmp_path, monkeypatch):
    client, tmp, calls = _setup(tmp_path, monkeypatch, jobs=3)
    r = client.post("/api/gpu-wake", auth=AUTH)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "gestartet"
    assert body["pending"] == 3
    assert body["started_at"]
    assert calls == ["http://wake.invalid/run"]
    state = json.loads((tmp / "gpu_wake.json").read_text(encoding="utf-8"))
    assert state["status"] == "gestartet"
    assert state["pending"] == 3


def test_ohne_offene_jobs_wird_nicht_geweckt(tmp_path, monkeypatch):
    client, _tmp, calls = _setup(tmp_path, monkeypatch, jobs=0)
    r = client.post("/api/gpu-wake", auth=AUTH)
    assert r.status_code == 200
    assert r.json()["status"] == "keine-jobs"
    assert r.json()["pending"] == 0
    assert calls == []


def test_zweiter_klick_im_cooldown_weckt_nicht_noch_einmal(tmp_path, monkeypatch):
    client, _tmp, calls = _setup(tmp_path, monkeypatch, jobs=2)
    assert client.post("/api/gpu-wake", auth=AUTH).status_code == 202
    second = client.post("/api/gpu-wake", auth=AUTH)
    assert second.status_code == 409
    assert second.json()["error"] == "lauf aktiv"
    assert second.json()["started_at"]
    assert len(calls) == 1


def test_ohne_wake_adresse_antwortet_die_route_502(tmp_path, monkeypatch):
    client, tmp, calls = _setup(tmp_path, monkeypatch, jobs=2, wake_url=None)
    r = client.post("/api/gpu-wake", auth=AUTH)
    assert r.status_code == 502
    assert "Wake-Proxy" in r.json()["error"]
    assert calls == []
    assert not (tmp / "gpu_wake.json").exists()


def test_fehlschlag_beim_wecken_wird_502_und_vermerkt(tmp_path, monkeypatch):
    client, tmp, _calls = _setup(tmp_path, monkeypatch, jobs=2)
    monkeypatch.setattr(workstation, "wake_workstation", lambda url, **_k: False)
    r = client.post("/api/gpu-wake", auth=AUTH)
    assert r.status_code == 502
    assert r.json()["pending"] == 2
    state = json.loads((tmp / "gpu_wake.json").read_text(encoding="utf-8"))
    assert state["status"] == "fehler"
    assert state["pending"] == 2


def test_status_ohne_datei_ist_unbekannt(tmp_path, monkeypatch):
    client, _tmp, _calls = _setup(tmp_path, monkeypatch)
    r = client.get("/api/gpu-wake/status", auth=AUTH)
    assert r.status_code == 200
    assert r.json()["status"] == "unbekannt"
    assert r.json()["started_at"] is None
    assert r.json()["pending"] is None


def test_status_nach_einem_start(tmp_path, monkeypatch):
    client, _tmp, _calls = _setup(tmp_path, monkeypatch, jobs=1)
    assert client.post("/api/gpu-wake", auth=AUTH).status_code == 202
    r = client.get("/api/gpu-wake/status", auth=AUTH)
    assert r.status_code == 200
    assert r.json()["status"] == "gestartet"
    assert r.json()["pending"] == 1


def test_knopf_steht_in_der_workstation_karte(tmp_path, monkeypatch):
    client, _tmp, _calls = _setup(tmp_path, monkeypatch)
    html = client.get("/", auth=AUTH).text
    assert 'id="gpu-wake"' in html
    assert "Workstation-Lauf starten" in html
    assert "/api/gpu-wake" in html
