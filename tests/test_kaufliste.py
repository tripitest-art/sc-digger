"""Eigene Tests für Kaufliste: DB-Operationen, Sortierung, Limits und Bot-Befehl."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from sc_digger import bot
from sc_digger.db import TrackDB
from sc_digger.models import Config, DownloadKind, Track
import sc_digger.output as out

ROOT = Path(__file__).resolve().parents[1]


def _cfg(tmp_path) -> Config:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {**cfg["state"], "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw.setdefault("digest", {})["kaufliste_max_items"] = 30
    return cfg


def test_send_kaufliste_empty_stdout(tmp_path, capsys):
    """0 Store-Tracks im dry_run Modus -> saubere Ausgabe auf stdout."""
    cfg = _cfg(tmp_path)
    out.send_kaufliste(cfg, dry_run=True)
    captured = capsys.readouterr()
    assert "Kaufliste ist leer." in captured.out


def test_send_kaufliste_multiple_ordered(tmp_path):
    """Mehrere Einträge werden absteigend nach last_seen formatiert."""
    cfg = _cfg(tmp_path)
    db_path = cfg["state"]["track_db_path"]
    with TrackDB(db_path) as db:
        db.upsert_store_item(10, "Alter Track", "Artist A", "https://bc.com/a", "Bandcamp")
        db.upsert_store_item(20, "Neuer Track", "Artist B", "https://bp.com/b", "Beatport")
        # last_seen für Track 10 manuell in die Vergangenheit setzen
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        db.db.execute("UPDATE store_items SET last_seen = ? WHERE sc_id = 10", (past,))
        db.db.commit()

    sent = []
    with patch.object(out, "telegram_call", side_effect=lambda *a, **k: sent.append(a)):
        out.send_kaufliste(cfg)

    assert len(sent) == 1
    text = sent[0][2]
    # Neuer Track muss vor Altem Track stehen
    pos_b = text.find("Artist B – Neuer Track")
    pos_a = text.find("Artist A – Alter Track")
    assert pos_b != -1 and pos_a != -1
    assert pos_b < pos_a
    assert "→ Beatport: https://bp.com/b" in text
    assert "→ Bandcamp: https://bc.com/a" in text


def test_send_kaufliste_max_items_limit(tmp_path):
    """kaufliste_max_items beschränkt die Anzahl der gesendeten Einträge."""
    cfg = _cfg(tmp_path)
    cfg.raw["digest"]["kaufliste_max_items"] = 2
    db_path = cfg["state"]["track_db_path"]
    with TrackDB(db_path) as db:
        db.upsert_store_item(1, "Track 1", "Artist 1", "https://bc.com/1")
        db.upsert_store_item(2, "Track 2", "Artist 2", "https://bc.com/2")
        db.upsert_store_item(3, "Track 3", "Artist 3", "https://bc.com/3")

    sent = []
    with patch.object(out, "telegram_call", side_effect=lambda *a, **k: sent.append(a)):
        out.send_kaufliste(cfg)

    assert len(sent) == 1
    text = sent[0][2]
    # Maximal 2 Einträge
    assert text.count("🛒") == 2


def test_get_store_items_filters_only_liked(tmp_path):
    """only_liked=True liefert nur Store-Items mit like-Feedback."""
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_store_item(1, "Track 1", "Artist 1", "https://bc.com/1")
        db.upsert_store_item(2, "Track 2", "Artist 2", "https://bc.com/2")
        db.set_sc_feedback(1, "like")
        db.set_sc_feedback(2, "dislike")

        all_items = db.get_store_items()
        assert len(all_items) == 2

        liked_items = db.get_store_items(only_liked=True)
        assert len(liked_items) == 1
        assert liked_items[0]["sc_id"] == 1


def test_get_store_items_since_days(tmp_path):
    """since_days filtert Store-Items anhand last_seen."""
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_store_item(1, "Aktuell", "Artist 1", "https://bc.com/1")
        db.upsert_store_item(2, "Alt", "Artist 2", "https://bc.com/2")
        old_date = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        db.db.execute("UPDATE store_items SET last_seen = ? WHERE sc_id = 2", (old_date,))
        db.db.commit()

        recent = db.get_store_items(since_days=5)
        assert len(recent) == 1
        assert recent[0]["sc_id"] == 1


def test_upsert_store_item_updates_existing(tmp_path):
    """Erneuter Upsert aktualisiert last_seen und Metadaten, behält first_seen."""
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_store_item(1, "Alt Title", "Alt Artist", "https://bc.com/old", "Old Store")
        old_time = "2026-01-01 00:00:00"
        db.db.execute("UPDATE store_items SET first_seen = ?, last_seen = ? WHERE sc_id = 1", (old_time, old_time))
        db.db.commit()

        db.upsert_store_item(1, "Neu Title", "Neu Artist", "https://bc.com/new", "New Store")
        items = db.get_store_items()
        assert len(items) == 1
        it = items[0]
        assert it["title"] == "Neu Title"
        assert it["artist"] == "Neu Artist"
        assert it["purchase_url"] == "https://bc.com/new"
        assert it["purchase_title"] == "New Store"
        assert it["first_seen"] == old_time
        assert it["last_seen"] > old_time


def test_bot_handle_message_kaufliste(tmp_path):
    """Nachricht /kaufliste an den Bot löst Kaufliste-Versand aus."""
    cfg = _cfg(tmp_path)
    called = []
    with patch.object(bot.output, "send_kaufliste", side_effect=lambda c, **kw: called.append((c, kw))):
        bot.handle_message(cfg, None, "12345", "/kaufliste")
    assert len(called) == 1
    assert called[0][1]["chat_id"] == "12345"


def test_entrypoint_has_cron_entry():
    """entrypoint.sh enthält den wöchentlichen Cron-Eintrag für Sonntag 20:00."""
    content = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    assert "0 20 * * 0" in content
    assert "send_kaufliste" in content
