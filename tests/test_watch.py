"""Eigene Tests für den Verfügbarkeits-Wächter (Issue #454)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import pytest

from sc_digger import watch
from sc_digger.db import TrackDB, TrackRecord
from sc_digger.models import Config, DownloadKind, Track
from sc_digger.schema import get_field
from sc_digger.soundcloud import SoundCloudClient

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 1, 4, 19, 0, 0)


def _sc(sc_id: int) -> Track:
    return Track(
        id=sc_id,
        title=f"Track {sc_id}",
        url=f"https://soundcloud.com/artist/track-{sc_id}",
        artist="Artist",
        artist_url="https://soundcloud.com/artist",
        created_at="2026-01-01T00:00:00Z",
        duration_ms=300000,
        genre="Schranz",
        tags=[],
        description="",
        bpm=None,
        plays=100,
        likes=10,
        reposts=1,
        comments=0,
        downloadable=False,
        has_downloads_left=False,
        purchase_url=None,
        purchase_title=None,
    )


def test_tracks_by_ids_batching_and_chunking():
    """tracks_by_ids teilt IDs in Chunks der gewünschten batch_size auf."""
    client = SoundCloudClient()
    calls: list[str] = []

    def fake_get(route: str, params: dict):
        assert route == "/tracks"
        ids_str = params["ids"]
        calls.append(ids_str)
        id_list = [int(x) for x in ids_str.split(",")]
        return [{"id": i, "title": f"Track {i}", "kind": "track"} for i in id_list]

    client._get = fake_get
    tracks = client.tracks_by_ids([1, 2, 3, 4, 5], batch_size=2)

    assert len(tracks) == 5
    assert calls == ["1,2", "3,4", "5"]


def test_tracks_by_ids_handles_chunk_error(caplog):
    """Fehler in einem Chunk wird geloggt und die restlichen Chunks werden geladen."""
    client = SoundCloudClient()
    calls: list[str] = []

    def fake_get(route: str, params: dict):
        ids_str = params["ids"]
        calls.append(ids_str)
        if ids_str == "3,4":
            raise RuntimeError("Chunk fehlgeschlagen")
        id_list = [int(x) for x in ids_str.split(",")]
        return [{"id": i, "title": f"Track {i}", "kind": "track"} for i in id_list]

    client._get = fake_get
    tracks = client.tracks_by_ids([1, 2, 3, 4, 5], batch_size=2)

    # 1,2 und 5 erfolgreich, 3,4 übersprungen
    assert [t.id for t in tracks] == [1, 2, 5]
    assert any("Fehler beim Batch-Abruf" in rec.message for rec in caplog.records)


def test_tracks_by_ids_empty_input():
    """Leere ID-Liste ruft _get nicht auf und gibt leere Liste zurück."""
    client = SoundCloudClient()
    client._get = lambda *a, **k: pytest.fail("_get sollte nicht aufgerufen werden")
    assert client.tracks_by_ids([]) == []


def test_watch_schema_definition():
    """Prüft, ob die Schema-Felder für watch korrekt registriert sind."""
    enabled_field = get_field("watch.enabled")
    assert enabled_field.kind == "bool"
    assert enabled_field.editable is True

    max_age_field = get_field("watch.max_age_days")
    assert max_age_field.kind == "int"
    assert max_age_field.min == 1
    assert max_age_field.max == 730


def test_run_watch_dry_run_does_not_write_db(tmp_path: Path, capsys):
    """dry_run=True gibt Nachrichten auf stdout aus und verändert availability_watch nicht."""
    db_path = tmp_path / "tracks.sqlite"
    seen_path = tmp_path / "seen.sqlite"
    with TrackDB(db_path) as db:
        db.set_sc_feedback(201, "like", "https://soundcloud.com/artist/201")

    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {"db_path": str(seen_path), "track_db_path": str(db_path)}
    cfg.raw["watch"] = {"enabled": True, "max_age_days": 180}

    def fake_fetch(ids):
        t = _sc(201)
        t.downloadable = True
        t.has_downloads_left = True
        return [t]

    code = watch.run_watch(cfg, fetch=fake_fetch, now=T0, dry_run=True)
    assert code == 0

    # In availability_watch darf wegen dry_run keine Zeile stehen
    with TrackDB(db_path) as db:
        count = db.db.execute("SELECT count(*) FROM availability_watch").fetchone()[0]
    assert count == 0


def test_format_available_message_split_separation():
    """Stellt sicher, dass auch in Folgebänden alle Einträge sauber durch doppelte Newlines getrennt sind."""
    # 40 Einträge mit je ~200 Zeichen füllen mehr als eine Nachricht
    changes = [
        watch.AvailabilityChange(
            sc_id=i,
            title=f"Titel {i} " + "X" * 150,
            artist=f"Artist {i}",
            url=f"https://soundcloud.com/artist/track-{i}",
            kind=DownloadKind.NATIVE,
            link=f"https://example.com/dl/{i}",
        )
        for i in range(40)
    ]
    msgs = watch.format_available_message(changes)
    assert len(msgs) >= 2

    # Erste Nachricht hat den Header
    assert msgs[0].startswith("🔓 <b>Jetzt verfügbar</b>\n\n")

    # Alle Folgenachrichten haben keinen Header
    for msg in msgs[1:]:
        assert not msg.startswith("🔓 <b>Jetzt verfügbar</b>")
        assert msg.startswith("• ")

    # In allen Nachrichten müssen mehrere Einträge durch "\n\n" getrennt sein (kein Verkleben zu "link• Artist")
    for msg in msgs:
        assert len(msg) <= 4000
        items = msg.split("\n\n")
        # Bei msg 0 ist items[0] der Header
        track_items = items[1:] if msg.startswith("🔓 <b>Jetzt verfügbar</b>") else items
        assert len(track_items) >= 2
        for itm in track_items:
            assert itm.startswith("• ")

