"""Akzeptanztests: Inbox-Dubletten per Dashboard ansehen und verschieben."""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.runlock import run_lock
from sc_digger.web.app import create_app
from sc_digger.web.inbox_cleanup import list_inbox_duplicates, move_inbox_duplicates

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-inbox-cleanup"
AUTH = ("x", PASSWORD)
JSON = {"content-type": "application/json"}
ORIGIN = {"origin": "http://testserver"}


def make_inbox(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "[111] Song.mp3").write_bytes(b"a" * 2048)
    (inbox / "[111] Song_1.mp3").write_bytes(b"b" * 1024)
    (inbox / "[222] Other.mp3").write_bytes(b"c" * 512)
    (inbox / "[222] Other_1.mp3").write_bytes(b"d" * 512)
    (inbox / "[333] Solo.mp3").write_bytes(b"e" * 512)
    return inbox


def make_cfg(tmp_path, inbox):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw["download"] = {**cfg.raw.get("download", {}), "inbox_dir": str(inbox)}
    return cfg


def client_for(tmp_path, inbox):
    return TestClient(create_app(make_cfg(tmp_path, inbox), password=PASSWORD))


def test_liste_findet_gruppen_mit_relativen_pfaden(tmp_path):
    inbox = make_inbox(tmp_path)
    res = list_inbox_duplicates(inbox)
    assert res["ok"] is True
    assert res["total_groups"] == 2
    assert res["extra_files"] == 2
    assert res["freeable_mb"] == round((1024 + 512) / 1024 / 1024, 1)
    first = res["groups"][0]
    assert first["soundcloud_id"] == "111"
    assert first["keep"]["path"] == "[111] Song.mp3"
    assert [d["path"] for d in first["duplicates"]] == ["[111] Song_1.mp3"]
    assert first["keep"]["key"] is None
    assert str(tmp_path) not in repr(res)


def test_liste_ohne_inbox_ist_leer(tmp_path):
    res = list_inbox_duplicates(tmp_path / "gibt-es-nicht")
    assert res == {"ok": True, "total_groups": 0, "extra_files": 0,
                   "freeable_mb": 0.0, "groups": []}


def test_verschieben_behaelt_das_original_und_loescht_nichts(tmp_path):
    inbox = make_inbox(tmp_path)
    status, body = move_inbox_duplicates(inbox, tmp_path)
    assert status == 200
    assert body == {"ok": True, "moved": 2, "remaining_groups": 0}
    ziel = inbox / "_rejected" / "duplicate"
    assert (ziel / "[111] Song_1.mp3").read_bytes() == b"b" * 1024
    assert (inbox / "[111] Song.mp3").is_file()
    assert (inbox / "[333] Solo.mp3").is_file()
    status2, body2 = move_inbox_duplicates(inbox, tmp_path)
    assert (status2, body2["moved"]) == (200, 0)


def test_verschieben_einer_gruppe_laesst_die_andere(tmp_path):
    inbox = make_inbox(tmp_path)
    status, body = move_inbox_duplicates(inbox, tmp_path, soundcloud_id="222")
    assert status == 200
    assert body == {"ok": True, "moved": 1, "remaining_groups": 1}
    assert (inbox / "[111] Song_1.mp3").is_file()
    assert not (inbox / "[222] Other_1.mp3").exists()


def test_ungueltige_id_gibt_400_und_nichts_passiert(tmp_path):
    inbox = make_inbox(tmp_path)
    status, body = move_inbox_duplicates(inbox, tmp_path, soundcloud_id="abc")
    assert status == 400
    assert body == {"ok": False, "error": "soundcloud_id muss aus Ziffern bestehen"}
    assert (inbox / "[111] Song_1.mp3").is_file()


def test_belegte_sperre_gibt_409_und_nichts_passiert(tmp_path):
    inbox = make_inbox(tmp_path)
    with run_lock(tmp_path, "discover"):
        status, body = move_inbox_duplicates(inbox, tmp_path)
    assert status == 409
    assert body["ok"] is False
    assert body["error"].startswith("Lauf-Sperre belegt")
    assert (inbox / "[111] Song_1.mp3").is_file()


def test_get_route_liefert_die_liste(tmp_path):
    inbox = make_inbox(tmp_path)
    resp = client_for(tmp_path, inbox).get("/api/inbox-duplicates", auth=AUTH)
    assert resp.status_code == 200
    assert resp.json()["total_groups"] == 2
    assert (inbox / "[111] Song_1.mp3").is_file()


def test_post_route_verschiebt(tmp_path):
    inbox = make_inbox(tmp_path)
    resp = client_for(tmp_path, inbox).post(
        "/api/inbox-duplicates/move", auth=AUTH, headers={**JSON, **ORIGIN},
        json={"soundcloud_id": "111"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "moved": 1, "remaining_groups": 1}
    assert (inbox / "_rejected" / "duplicate" / "[111] Song_1.mp3").is_file()


def test_post_route_ohne_body_verschiebt_alles(tmp_path):
    inbox = make_inbox(tmp_path)
    resp = client_for(tmp_path, inbox).post("/api/inbox-duplicates/move", auth=AUTH, headers=JSON)
    assert resp.status_code == 200
    assert resp.json()["moved"] == 2


def test_post_route_schutz(tmp_path):
    inbox = make_inbox(tmp_path)
    client = client_for(tmp_path, inbox)
    assert client.post("/api/inbox-duplicates/move", headers=JSON).status_code == 401
    fremd = client.post("/api/inbox-duplicates/move", auth=AUTH,
                        headers={**JSON, "origin": "http://boese.example"})
    assert fremd.status_code == 403
    text = client.post("/api/inbox-duplicates/move", auth=AUTH,
                       headers={"content-type": "text/plain"}, content=b"x")
    assert text.status_code == 415
    kaputt = client.post("/api/inbox-duplicates/move", auth=AUTH, headers=JSON, content=b"{")
    assert kaputt.status_code == 400
    assert (inbox / "[111] Song_1.mp3").is_file()


def test_seite_hat_abschnitt_und_skript(tmp_path):
    inbox = make_inbox(tmp_path)
    resp = client_for(tmp_path, inbox).get("/duplicates", auth=AUTH)
    assert resp.status_code == 200
    for teil in ('id="inbox-dup-section"', 'id="inbox-dup-move-all"', 'id="inbox-dup-status"',
                 "/api/inbox-duplicates"):
        assert teil in resp.text