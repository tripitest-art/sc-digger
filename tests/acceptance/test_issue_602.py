"""Akzeptanztests: Duplikate-Seite – Key-Farbe, Knöpfe nach Fehler, Ladehinweis."""
import re
from pathlib import Path

from sc_digger.db import TrackDB, TrackRecord, TrackStatus
from sc_digger.fingerprint import encode_fingerprint
from sc_digger.models import Config
from sc_digger.web.app import create_app
from tests.test_web_tracks_ui import TestClient

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "sc_digger" / "web" / "templates" / "duplicates.html").read_text(encoding="utf-8")
PASSWORD = "geheim-dup"


def _client(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    fp = encode_fingerprint([0xAAAAAAAA] * 80)
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        for pfad, groesse in (("archive/a.wav", 1000), ("inbox/a.mp3", 500)):
            db.upsert_track(TrackRecord(
                path=pfad, mtime=1.0, size=groesse, artist="Artist", title="Track",
                bpm=150.0, key_camelot="8A", status=TrackStatus.ARCHIVE,
                fingerprint=fp, fingerprint_duration=300.0,
            ))
    return TestClient(create_app(cfg, password=PASSWORD))


def _funktion(name):
    start = TEMPLATE.index(f"async function {name}(")
    ende = TEMPLATE.find("async function ", start + 1)
    return TEMPLATE[start:ende if ende != -1 else len(TEMPLATE)]


def test_key_pille_nutzt_data_camelot(tmp_path):
    html = _client(tmp_path).get("/duplicates", auth=("sc", PASSWORD)).text
    assert '<span class="camelot-pill" data-camelot="8A">8A</span>' in html
    assert "data-key=" not in html


def test_knoepfe_werden_nach_dem_verschieben_immer_freigegeben():
    teil = _funktion("moveDuplicates")
    finally_block = teil[teil.index("finally"):]
    assert "moveAllBtn.disabled = false" in finally_block
    # Die Gruppen-Knöpfe (allBtns = button[data-sc-id]) werden im finally wieder frei.
    assert re.search(r"(allBtns|button\[data-sc-id\])[^;]*?disabled\s*=\s*false", finally_block, re.S), \
        "Gruppen-Knöpfe werden im finally nicht freigegeben"


def test_ladehinweis_vor_dem_abruf_und_danach_leer():
    teil = _funktion("loadInboxDuplicates")
    hinweis = teil.index("statusEl.textContent = 'Lade Inbox-Dubletten…'")
    abruf = teil.index("fetch('/api/inbox-duplicates')")
    assert hinweis < abruf
    assert "statusEl.textContent = ''" in teil[abruf:]
