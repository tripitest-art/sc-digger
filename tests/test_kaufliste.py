"""Eigene Tests für Kaufliste: DB-Operationen, Sortierung, Limits, Escaping, Chunks und Bot-Befehl."""
from datetime import datetime, timedelta, timezone
import html
from pathlib import Path
import re
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
    with patch.object(out, "telegram_call", side_effect=lambda *a, **k: sent.append(k.get("json", {}).get("text", ""))):
        out.send_kaufliste(cfg)

    assert len(sent) == 1
    text = sent[0]
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
    with patch.object(out, "telegram_call", side_effect=lambda *a, **k: sent.append(k.get("json", {}).get("text", ""))):
        out.send_kaufliste(cfg)

    assert len(sent) == 1
    text = sent[0]
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
    """entrypoint.sh enthält den wöchentlichen Cron-Eintrag für Sonntag 20:00 und hängt ihn nur einmal an."""
    content = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    assert "0 20 * * 0" in content
    assert "send_kaufliste(Config.load('config.yaml'))" in content
    assert "! grep -q \"send_kaufliste\"" in content


# --- Tests für Review-Punkte M1 - M5 ---

def test_build_digest_messages_default_cfg_upserts_store_track(tmp_path, monkeypatch):
    """M1: Aufruf wie in main.py:132 ohne cfg lädt config.yaml und trägt Store-Tracks ein."""
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(Config, "load", lambda path="config.yaml": cfg)

    t_store = Track(
        id=42, title="Banger", url="https://soundcloud.com/a/42", artist="Artist",
        artist_url="", created_at="2026-09-29T10:00:00Z", duration_ms=300000,
        genre="", tags=[], description="", bpm=None, plays=1000, likes=50,
        reposts=10, comments=2, downloadable=False, has_downloads_left=False,
        purchase_url="https://bandcamp.com/42", purchase_title="Bandcamp",
        download_kind=DownloadKind.STORE,
    )
    t_other = Track(
        id=43, title="Free DL", url="https://soundcloud.com/a/43", artist="Artist 2",
        artist_url="", created_at="2026-09-29T10:00:00Z", duration_ms=300000,
        genre="", tags=[], description="", bpm=None, plays=1000, likes=50,
        reposts=10, comments=2, downloadable=True, has_downloads_left=True,
        purchase_url=None, purchase_title=None,
        download_kind=DownloadKind.NATIVE,
    )

    # Aufruf exakt wie in main.py:132: build_digest_messages(fresh, max_items, header=..., numbered=...)
    messages = out.build_digest_messages([t_store, t_other], max_items=10, header="Test Header", numbered=True)
    assert len(messages) >= 1

    with TrackDB(cfg["state"]["track_db_path"]) as db:
        items = db.get_store_items()
    assert len(items) == 1
    assert items[0]["sc_id"] == 42
    assert items[0]["title"] == "Banger"

    # K1: Store-Tracks, die wegen max_items nicht im Digest erscheinen, werden nicht eingetragen
    t_store2 = Track(
        id=99, title="Not Shown", url="https://soundcloud.com/a/99", artist="Artist 3",
        artist_url="", created_at="2026-09-29T10:00:00Z", duration_ms=300000,
        genre="", tags=[], description="", bpm=None, plays=1000, likes=50,
        reposts=10, comments=2, downloadable=False, has_downloads_left=False,
        purchase_url="https://bandcamp.com/99", purchase_title="Bandcamp",
        download_kind=DownloadKind.STORE,
    )
    out.build_digest_messages([t_store2], max_items=0, header="Empty", numbered=False)
    with TrackDB(cfg["state"]["track_db_path"]) as db:
        items = db.get_store_items()
    assert not any(i["sc_id"] == 99 for i in items)


def test_entrypoint_cron_command_executes(tmp_path, monkeypatch):
    """M2: Der in entrypoint.sh definierte Python-Befehl ist syntaktisch korrekt und ausführbar."""
    cfg = _cfg(tmp_path)
    content = (ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    m = re.search(r'python -c [\\"\']+(.+?)[\\"\']+(?: >>|\s)', content)
    assert m, "Python-Befehl in entrypoint.sh nicht gefunden"
    py_cmd = m.group(1).replace(r'\"', '"')

    monkeypatch.setattr(Config, "load", lambda path="config.yaml": cfg)
    called = []
    monkeypatch.setattr(out, "telegram_call", lambda *a, **k: called.append(k))

    # Befehl ausführen
    exec(py_cmd, {"__name__": "__main__"})
    assert len(called) == 1


def test_send_kaufliste_html_escaped(tmp_path):
    """M3: HTML-Sonderzeichen in Artist, Title und URL werden sauber escaped."""
    cfg = _cfg(tmp_path)
    db_path = cfg["state"]["track_db_path"]
    with TrackDB(db_path) as db:
        db.upsert_store_item(
            1,
            title="Track <1> & 'Best'",
            artist="Artist <A> & <B>",
            purchase_url="https://bandcamp.com/buy?a=1&b=2",
            purchase_title="Shop & Gate <Z>",
        )

    sent = []
    with patch.object(out, "telegram_call", side_effect=lambda *a, **k: sent.append(k.get("json", {}).get("text", ""))):
        out.send_kaufliste(cfg)

    assert len(sent) == 1
    text = sent[0]
    assert "Artist &lt;A&gt; &amp; &lt;B&gt;" in text
    assert "Track &lt;1&gt; &amp; &#x27;Best&#x27;" in text or "Track &lt;1&gt; &amp; 'Best'" in text
    assert "https://bandcamp.com/buy?a=1&amp;b=2" in text
    assert "Shop &amp; Gate &lt;Z&gt;" in text
    assert "<A>" not in text
    assert "& " not in text


def test_send_kaufliste_splits_under_4096_chars(tmp_path):
    """M4: 30 lange Einträge werden in Nachrichten mit jeweils <= 4096 Zeichen aufgeteilt."""
    cfg = _cfg(tmp_path)
    db_path = cfg["state"]["track_db_path"]
    with TrackDB(db_path) as db:
        for i in range(1, 31):
            db.upsert_store_item(
                i,
                title=f"Extended Club Dance Floor Mix Edition 2026 Remastered Version Track #{i:02d}",
                artist=f"Very Long Artist Name Feat. Collaborator One & Two {i:02d}",
                purchase_url=f"https://www.beatport.com/track/extended-club-dance-floor-mix-edition-2026-remastered/{i}",
                purchase_title="Beatport Exclusive Release",
            )

    sent = []
    with patch.object(out, "telegram_call", side_effect=lambda *a, **k: sent.append(k.get("json", {}).get("text", ""))):
        out.send_kaufliste(cfg)

    assert len(sent) > 1, f"Erwartete mehrere Nachrichten bei 30 langen Einträgen, erhielt {len(sent)}"
    assert all(len(t) <= 4096 for t in sent), "Eine der Telegram-Nachrichten überschreitet das Limit von 4096 Zeichen"
    full_text = "".join(sent)
    for i in range(1, 31):
        assert f"Track #{i:02d}" in full_text


def test_bot_kaufliste_command_error_handled(tmp_path):
    """M5: Fehler beim Versenden der Kaufliste wird gefangen, geloggt und an den Benutzer gemeldet."""
    cfg = _cfg(tmp_path)
    sent_msgs = []
    with patch.object(out, "send_kaufliste", side_effect=RuntimeError("DB locked")), \
         patch.object(bot, "_send_text", side_effect=lambda c, chat, msg: sent_msgs.append((chat, msg))):
        bot.handle_kaufliste_command(cfg, chat_id="99999")

    assert len(sent_msgs) == 1
    chat, msg = sent_msgs[0]
    assert chat == "99999"
    assert "Kaufliste fehlgeschlagen" in msg
