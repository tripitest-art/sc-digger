"""Eigene Tests fürs Vorhören im Track-Explorer (Issue #350).

Ergänzen den Akzeptanztest um Content-Type, Suffix-Ranges, die leere Datei, Symlinks aus
der Inbox hinaus und das Rendern des Abspiel-Knopfs. Alles ohne Netzwerk.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.audio import parse_range_header, resolve_playable_path

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-350"
AUTH = ("sc", PASSWORD)


def make_cfg(tmp_path: Path):
    inbox = tmp_path / "inbox"
    collection = tmp_path / "sammlung"
    inbox.mkdir()
    collection.mkdir()
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    cfg.raw.setdefault("download", {})
    cfg.raw["download"]["inbox_dir"] = str(inbox)
    cfg.raw["download"]["collection_dir"] = str(collection)
    return cfg, inbox, collection


def add_track(tmp_path: Path, path: Path, **kwargs) -> int:
    size = path.stat().st_size if path.exists() else 0
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(path=str(path), mtime=1.0, size=size, **kwargs))
        return db.get_track_by_path(path).id


def client_for(cfg: Config) -> TestClient:
    return TestClient(create_app(cfg, password=PASSWORD))


# ------------------------------------------------------------------ Pfadprüfung
def test_symlink_aus_der_inbox_heraus_wird_abgelehnt(tmp_path: Path):
    _, inbox, _ = make_cfg(tmp_path)
    outside = tmp_path / "fremd"
    outside.mkdir()
    secret = outside / "geheim.mp3"
    secret.write_bytes(b"geheim")
    link = inbox / "link.mp3"
    link.symlink_to(secret)

    assert resolve_playable_path(str(link), [inbox]) is None


# ------------------------------------------------------------------ Range-Header
def test_suffix_range_groesser_als_die_datei_umfasst_alles():
    assert parse_range_header("bytes=-500", 100) == (0, 99)


def test_suffix_range_null_ist_ungueltig():
    with pytest.raises(ValueError):
        parse_range_header("bytes=-0", 100)


# ------------------------------------------------------------------ Route
@pytest.mark.parametrize("suffix", [".mp3", ".flac", ".wav", ".aiff", ".aif", ".m4a", ".ogg", ".opus"])
def test_content_type_je_endung(tmp_path: Path, suffix: str):
    cfg, inbox, _ = make_cfg(tmp_path)
    f = inbox / f"t{suffix}"
    f.write_bytes(b"abc")
    tid = add_track(tmp_path, f)

    r = client_for(cfg).get(f"/api/audio/{tid}", auth=AUTH)

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/")


def test_suffix_range_liefert_die_letzten_bytes(tmp_path: Path):
    cfg, inbox, _ = make_cfg(tmp_path)
    f = inbox / "t.mp3"
    f.write_bytes(bytes(range(100)))
    tid = add_track(tmp_path, f)

    r = client_for(cfg).get(
        f"/api/audio/{tid}", auth=AUTH, headers={"Range": "bytes=-10"}
    )

    assert r.status_code == 206
    assert r.content == bytes(range(90, 100))
    assert r.headers["content-range"] == "bytes 90-99/100"


def test_leere_datei_wird_als_ganze_datei_ausgeliefert(tmp_path: Path):
    cfg, inbox, _ = make_cfg(tmp_path)
    f = inbox / "leer.mp3"
    f.write_bytes(b"")
    tid = add_track(tmp_path, f)

    r = client_for(cfg).get(f"/api/audio/{tid}", auth=AUTH)

    assert r.status_code == 200
    assert r.content == b""
    assert r.headers["content-length"] == "0"
    assert r.headers["accept-ranges"] == "bytes"


def test_datei_aus_der_sammlung_wird_ausgeliefert(tmp_path: Path):
    cfg, _, collection = make_cfg(tmp_path)
    f = collection / "archiv.flac"
    f.write_bytes(b"flac")
    tid = add_track(tmp_path, f)

    r = client_for(cfg).get(f"/api/audio/{tid}", auth=AUTH)

    assert r.status_code == 200
    assert r.content == b"flac"


def test_symlink_in_der_inbox_auf_fremde_datei_ist_404(tmp_path: Path):
    cfg, inbox, _ = make_cfg(tmp_path)
    outside = tmp_path / "fremd"
    outside.mkdir()
    secret = outside / "geheim.mp3"
    secret.write_bytes(b"geheim")
    link = inbox / "link.mp3"
    link.symlink_to(secret)
    tid = add_track(tmp_path, link)

    assert client_for(cfg).get(f"/api/audio/{tid}", auth=AUTH).status_code == 404


def test_ungueltiger_range_header_ist_416(tmp_path: Path):
    cfg, inbox, _ = make_cfg(tmp_path)
    f = inbox / "t.mp3"
    f.write_bytes(b"abc")
    tid = add_track(tmp_path, f)

    r = client_for(cfg).get(
        f"/api/audio/{tid}", auth=AUTH, headers={"Range": "bytes=abc"}
    )

    assert r.status_code == 416
    assert r.headers["content-range"] == "bytes */3"


# ------------------------------------------------------------------ Oberfläche
def test_abspiel_knopf_nur_bei_vorhandener_datei(tmp_path: Path):
    cfg, inbox, _ = make_cfg(tmp_path)
    vorhanden = inbox / "da.mp3"
    vorhanden.write_bytes(b"abc")
    da_id = add_track(tmp_path, vorhanden)
    fehlt = inbox / "fehlt.mp3"
    fehlt_id = add_track(tmp_path, fehlt)

    r = client_for(cfg).get("/tracks", auth=AUTH)

    assert r.status_code == 200
    assert f'class="audio-preview"' in r.text
    assert f"/api/audio/{da_id}" in r.text
    assert f"/api/audio/{fehlt_id}" not in r.text
    assert r.text.count('class="audio-preview"') == 1
