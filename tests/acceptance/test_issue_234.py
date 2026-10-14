from pathlib import Path
import sqlite3
from sc_digger.db import TrackDB
from sc_digger.web.database import query_digest

def test_gate_items_persistence_and_query_digest(tmp_path: Path):
    track_db_file = tmp_path / "tracks.sqlite"
    state_db_file = tmp_path / "seen.sqlite"

    # 1. TrackDB Migration & upsert_gate_item prüfen
    with TrackDB(track_db_file) as db:
        db.upsert_gate_item(
            sc_id=301,
            title="Hyped Track",
            artist="DJ Gate",
            gate_url="https://hypeddit.com/track/301",
            gate_type="hypeddit",
        )
        items = db.get_gate_items()
        assert len(items) == 1
        assert items[0]["sc_id"] == 301
        assert items[0]["gate_url"] == "https://hypeddit.com/track/301"
        assert items[0]["gate_type"] == "hypeddit"

    # 2. seen.sqlite anlegen
    with sqlite3.connect(state_db_file) as sdb:
        sdb.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        sdb.execute(
            "INSERT INTO seen VALUES (301, 'https://soundcloud.com/djgate/track', 'DJ Gate - Hyped Track', '2026-10-14 10:00:00')"
        )
        sdb.execute(
            "INSERT INTO seen VALUES (302, 'https://soundcloud.com/djstream/track', 'DJ Stream - Normal Track', '2026-10-14 11:00:00')"
        )

    # 3. query_digest mit Filter download_kind='gate'
    res_gate = query_digest(state_db_file, track_db_file, download_kind="gate")
    assert res_gate.total == 1
    assert res_gate.items[0].sc_id == 301
    assert res_gate.items[0].download_kind == "gate"
    assert res_gate.items[0].gate_url == "https://hypeddit.com/track/301"

    # 4. Normaler Track ohne Gate/Store/Inbox bleibt 'stream'
    res_all = query_digest(state_db_file, track_db_file)
    assert res_all.total == 2
    stream_item = [i for i in res_all.items if i.sc_id == 302][0]
    assert stream_item.download_kind == "stream"
    assert stream_item.gate_url is None
