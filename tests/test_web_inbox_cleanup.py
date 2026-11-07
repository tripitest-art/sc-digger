"""Eigene Tests für Inbox-Cleanup im Web-Dashboard (/duplicates & /api/inbox-duplicates)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.runlock import run_lock
from sc_digger.web.app import create_app
from sc_digger.web.inbox_cleanup import list_inbox_duplicates, move_inbox_duplicates

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "secret-inbox-cleanup-test"
AUTH = ("sc", PASSWORD)
JSON_HEADER = {"content-type": "application/json"}
ORIGIN_HEADER = {"origin": "http://testserver"}


def _make_cfg(tmp_path: Path, inbox: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw["download"] = {**cfg.raw.get("download", {}), "inbox_dir": str(inbox)}
    return cfg


def _client(tmp_path: Path, inbox: Path) -> TestClient:
    return TestClient(create_app(_make_cfg(tmp_path, inbox), password=PASSWORD))


def test_list_inbox_duplicates_leere_oder_fehlende_inbox(tmp_path: Path):
    # Fehlende Inbox
    res_none = list_inbox_duplicates(tmp_path / "nicht_da")
    assert res_none == {
        "ok": True,
        "total_groups": 0,
        "extra_files": 0,
        "freeable_mb": 0.0,
        "groups": [],
    }

    # Leere Inbox
    empty = tmp_path / "leer"
    empty.mkdir()
    res_empty = list_inbox_duplicates(empty)
    assert res_empty == {
        "ok": True,
        "total_groups": 0,
        "extra_files": 0,
        "freeable_mb": 0.0,
        "groups": [],
    }


def test_list_inbox_duplicates_ausnahme_faengt_ab(tmp_path: Path):
    # Bei unerwarteter Ausnahme darf die Funktion nie werfen
    with patch("sc_digger.cleanup.audit_inbox_duplicates", side_effect=RuntimeError("Boom")):
        res = list_inbox_duplicates(tmp_path)
        assert res == {
            "ok": True,
            "total_groups": 0,
            "extra_files": 0,
            "freeable_mb": 0.0,
            "groups": [],
        }


def test_move_inbox_duplicates_namenskollision_und_rejected_wird_nicht_erneut_gescannt(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    rej_dir = inbox / "_rejected" / "duplicate"
    rej_dir.mkdir(parents=True)

    # Vorhandene kollidierende Zieldatei
    (rej_dir / "[444] Collision_1.mp3").write_bytes(b"alt")

    (inbox / "[444] Collision.mp3").write_bytes(b"x" * 2048)
    (inbox / "[444] Collision_1.mp3").write_bytes(b"y" * 1024)

    status, body = move_inbox_duplicates(inbox, tmp_path)
    assert status == 200
    assert body == {"ok": True, "moved": 1, "remaining_groups": 0}

    # Kollision muss durchnummeriert worden sein (_1_1)
    assert (rej_dir / "[444] Collision_1.mp3").read_bytes() == b"alt"
    assert (rej_dir / "[444] Collision_1_1.mp3").read_bytes() == b"y" * 1024
    assert (inbox / "[444] Collision.mp3").is_file()

    # Zweiter Lauf: _rejected wird von scan_inbox ignoriert, remaining_groups bleibt 0
    status2, body2 = move_inbox_duplicates(inbox, tmp_path)
    assert status2 == 200
    assert body2 == {"ok": True, "moved": 0, "remaining_groups": 0}


def test_move_inbox_duplicates_unbekannte_id_verschiebt_nichts(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "[555] Song.mp3").write_bytes(b"a" * 1024)
    (inbox / "[555] Song_1.mp3").write_bytes(b"b" * 1024)

    status, body = move_inbox_duplicates(inbox, tmp_path, soundcloud_id="999999")
    assert status == 200
    assert body == {"ok": True, "moved": 0, "remaining_groups": 1}
    assert (inbox / "[555] Song_1.mp3").is_file()


def test_move_inbox_duplicates_oserror_gibt_500_ohne_pfade(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "[666] Song.mp3").write_bytes(b"a" * 1024)
    (inbox / "[666] Song_1.mp3").write_bytes(b"b" * 1024)

    with patch("sc_digger.cleanup.move_duplicates", side_effect=OSError("Disk full")):
        status, body = move_inbox_duplicates(inbox, tmp_path)
        assert status == 500
        assert body == {"ok": False, "error": "Verschieben fehlgeschlagen, siehe Container-Log."}
        assert str(tmp_path) not in repr(body)


def test_api_routes_fehlerfaelle(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    client = _client(tmp_path, inbox)

    # 400 bei ungültiger ID
    resp = client.post(
        "/api/inbox-duplicates/move",
        auth=AUTH,
        headers={**JSON_HEADER, **ORIGIN_HEADER},
        json={"soundcloud_id": "nicht-numerisch"},
    )
    assert resp.status_code == 400
    assert resp.json()["ok"] is False

    # 400 bei ungültigem Payload-Typ (Array statt Dict)
    resp = client.post(
        "/api/inbox-duplicates/move",
        auth=AUTH,
        headers={**JSON_HEADER, **ORIGIN_HEADER},
        content=b'["123"]',
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Ungültiger Payload"

    # 409 bei belegter Sperre
    with run_lock(tmp_path, "test-runner"):
        resp = client.post(
            "/api/inbox-duplicates/move",
            auth=AUTH,
            headers={**JSON_HEADER, **ORIGIN_HEADER},
            json={},
        )
        assert resp.status_code == 409
        assert resp.json()["ok"] is False


def test_duplicates_html_aufbau_und_rendering(tmp_path: Path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    client = _client(tmp_path, inbox)

    resp = client.get("/duplicates", auth=AUTH)
    assert resp.status_code == 200
    content = resp.text
    # Prüfe Abschnitte und IDs
    assert 'id="inbox-dup-section"' in content
    assert 'id="inbox-dup-move-all"' in content
    assert 'id="inbox-dup-status"' in content
    assert 'id="inbox-dup-container"' in content
    assert 'Gleiche SoundCloud-ID in der Inbox' in content
    assert 'Keine Dubletten in der Inbox.' in content
    assert 'data-sc-id' in content
    assert 'confirm(' in content
