"""Akzeptanztests: Kaufliste – Store-Tracks sammeln und als Telegram-Digest senden."""
from pathlib import Path
from unittest.mock import patch

import pytest

from sc_digger.db import TrackDB
from sc_digger.models import Config, DownloadKind, Track
import sc_digger.output as out


def _cfg(tmp_path) -> Config:
    cfg = Config.load(Path(__file__).resolve().parents[2] / "config.yaml")
    cfg.raw["state"] = {**cfg["state"], "track_db_path": str(tmp_path / "t.sqlite")}
    cfg.raw.setdefault("digest", {})["kaufliste_max_items"] = 30
    return cfg


def mk_store(sc_id=1, title="Tune", artist="Artist",
             purchase_url="https://bandcamp.com/tune", purchase_title="Bandcamp") -> Track:
    return Track(
        id=sc_id, title=title, url=f"https://soundcloud.com/a/{sc_id}",
        artist=artist, artist_url="", created_at="2026-09-29T10:00:00Z",
        duration_ms=300000, genre="", tags=[], description="", bpm=None,
        plays=1000, likes=50, reposts=10, comments=2, downloadable=False,
        has_downloads_left=False, purchase_url=purchase_url, purchase_title=purchase_title,
        download_kind=DownloadKind.STORE,
    )


def test_config_default():
    import yaml
    raw = yaml.safe_load((Path(__file__).resolve().parents[2] / "config.yaml").read_text(encoding="utf-8"))
    assert raw["digest"]["kaufliste_max_items"] >= 1


def test_upsert_store_item_and_get(tmp_path):
    with TrackDB(str(tmp_path / "t.sqlite")) as db:
        db.upsert_store_item(1, "Tune", "Artist", "https://bandcamp.com/tune", "Bandcamp")
        db.upsert_store_item(1, "Tune", "Artist", "https://bandcamp.com/tune", "Bandcamp")  # idempotent
        items = db.get_store_items()
    assert len(items) == 1
    assert items[0]["sc_id"] == 1
    assert items[0]["title"] == "Tune"


def test_digest_upserts_store_tracks(tmp_path, monkeypatch):
    """Wenn der Digest aufgebaut wird und ein Store-Track enthalten ist, landet er in store_items."""
    cfg = _cfg(tmp_path)
    t = mk_store()
    calls = []
    monkeypatch.setattr(out, "telegram_call", lambda *a, **k: calls.append(a))
    out.build_digest_messages([t], cfg)   # oder finalize / send – je nach Implementierung
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        items = db.get_store_items()
    assert any(i["sc_id"] == 1 for i in items)


def test_send_kaufliste_formats_correctly(tmp_path):
    cfg = _cfg(tmp_path)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        db.upsert_store_item(1, "Tune", "Artist", "https://bandcamp.com/tune", "Bandcamp")
    sent = []
    with patch.object(out, "telegram_call", side_effect=lambda *a, **k: sent.append(a)):
        out.send_kaufliste(cfg)
    text = "".join(str(s) for s in sent)
    assert "Artist" in text and "Tune" in text and "bandcamp.com" in text


def test_send_kaufliste_empty(tmp_path):
    cfg = _cfg(tmp_path)
    sent = []
    with patch.object(out, "telegram_call", side_effect=lambda *a, **k: sent.append(a)):
        out.send_kaufliste(cfg)
    text = "".join(str(s) for s in sent)
    assert "leer" in text.lower() or len(sent) == 0   # leere Liste → kurze Meldung oder nichts


def test_bot_kaufliste_command(tmp_path):
    """/kaufliste Bot-Befehl ruft send_kaufliste auf."""
    from sc_digger import bot as b
    cfg = _cfg(tmp_path)
    called = []
    with patch.object(out, "send_kaufliste", side_effect=lambda c: called.append(c)):
        b.handle_kaufliste_command(cfg)
    assert called
