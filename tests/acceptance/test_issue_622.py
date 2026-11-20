"""Akzeptanztests: Hörproben aus dem Stream mit Zwischenspeicher (Befund 8, Teil 1)."""
import os
from pathlib import Path

from sc_digger.models import Config
from sc_digger.schema import FIELDS
from sc_digger.web import stream_preview
from sc_digger.web.app import create_app
from tests.test_web_tracks_ui import TestClient

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-hoer"
AUTH = ("sc", PASSWORD)


def _cfg(tmp_path, an=True, mb=500):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "data" / "seen.sqlite"),
        "track_db_path": str(tmp_path / "data" / "tracks.sqlite"),
    }
    cfg.raw["web"] = {"stream_preview": an, "preview_cache_mb": mb}
    return cfg


class Fake:
    def __init__(self, url="https://cdn.example/stream.mp3", ok=True):
        self.url, self.ok, self.resolved, self.extracted = url, ok, [], []

    def resolve(self, sc_id):
        self.resolved.append(sc_id)
        return self.url

    def extract(self, url, target, *, start_s, duration_s, bitrate_kbps):
        self.extracted.append((url, Path(target).name, start_s, duration_s, bitrate_kbps))
        if self.ok:
            Path(target).write_bytes(b"OggS-snippet")
        return self.ok


def _patch(monkeypatch, fake):
    monkeypatch.setattr(stream_preview, "resolve_stream_url", fake.resolve)
    monkeypatch.setattr(stream_preview, "extract_preview_from_url", fake.extract)


def test_standard_aus_und_in_config():
    cfg = Config.load(ROOT / "config.yaml")
    assert cfg.raw["web"]["stream_preview"] is False
    assert cfg.raw["web"]["preview_cache_mb"] == 500
    pfade = {f.path: f.kind for f in FIELDS}
    assert pfade["web.stream_preview"] == "bool"
    assert pfade["web.preview_cache_mb"] == "int"


def test_aus_bedeutet_kein_netz(tmp_path, monkeypatch):
    fake = Fake()
    _patch(monkeypatch, fake)
    assert stream_preview.get_or_build_preview(_cfg(tmp_path, an=False), 5) is None
    assert stream_preview.get_or_build_preview(_cfg(tmp_path), 0) is None
    assert stream_preview.get_or_build_preview(_cfg(tmp_path), "x") is None
    assert fake.resolved == []


def test_erzeugt_einmal_und_nutzt_den_zwischenspeicher(tmp_path, monkeypatch):
    fake = Fake()
    _patch(monkeypatch, fake)
    cfg = _cfg(tmp_path)
    pfad = stream_preview.get_or_build_preview(cfg, 77)
    assert pfad == tmp_path / "data" / "preview_cache" / "77.ogg"
    assert pfad.read_bytes() == b"OggS-snippet"
    assert fake.extracted == [("https://cdn.example/stream.mp3", "77.ogg", 0.0, 30.0, 64)]
    assert stream_preview.get_or_build_preview(cfg, 77) == pfad
    assert len(fake.extracted) == 1 and fake.resolved == [77]


def test_ohne_stream_oder_bei_fehler_nichts(tmp_path, monkeypatch):
    _patch(monkeypatch, Fake(url=None))
    assert stream_preview.get_or_build_preview(_cfg(tmp_path), 8) is None
    _patch(monkeypatch, Fake(ok=False))
    assert stream_preview.get_or_build_preview(_cfg(tmp_path), 9) is None
    assert not (tmp_path / "data" / "preview_cache" / "9.ogg").exists()


def test_aelteste_zuerst_geloescht(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    for i, name in enumerate(["a.ogg", "b.ogg", "c.ogg"]):
        p = root / name
        p.write_bytes(b"x" * 100)
        os.utime(p, (1_000_000 + i, 1_000_000 + i))
    (root / "notiz.txt").write_bytes(b"y" * 1000)
    assert stream_preview.prune_cache(root, 150) == 2
    assert sorted(p.name for p in root.iterdir()) == ["c.ogg", "notiz.txt"]
    assert stream_preview.prune_cache(root, 150) == 0


def test_route(tmp_path, monkeypatch):
    fake = Fake()
    _patch(monkeypatch, fake)
    client = TestClient(create_app(_cfg(tmp_path), password=PASSWORD))
    antwort = client.get("/api/preview/sc/77", auth=AUTH)
    assert antwort.status_code == 200
    assert antwort.headers["content-type"].startswith("audio/ogg")
    assert antwort.content == b"OggS-snippet"
    assert client.get("/api/preview/sc/77").status_code == 401
    _patch(monkeypatch, Fake(url=None))
    leer = client.get("/api/preview/sc/78", auth=AUTH)
    assert leer.status_code == 404 and leer.json()["error"] == "Keine Hörprobe verfügbar."


def test_route_bei_ausgeschaltetem_schalter(tmp_path, monkeypatch):
    fake = Fake()
    _patch(monkeypatch, fake)
    client = TestClient(create_app(_cfg(tmp_path, an=False), password=PASSWORD))
    antwort = client.get("/api/preview/sc/77", auth=AUTH)
    assert antwort.status_code == 404
    assert antwort.json() == {"ok": False, "error": "Hörproben sind aus (web.stream_preview)."}
    assert fake.resolved == []
