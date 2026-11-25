"""Akzeptanztests: Aktionen und SoundCloud-Links in der Schnellsuche (Rest von #598) (Issue <N>)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web.app import create_app
from sc_digger.web.navigation import SC_LINK_PATTERN, QuickAction, quick_actions
from sc_digger.web.tasks import action_labels

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "sc_digger" / "web" / "templates"
PASSWORD = "geheim-aktionen"


def _app(tmp_path: Path):
    cfg = Config.load(ROOT / "config.yaml", local=tmp_path / "keine-override.yaml")
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    cfg.raw.setdefault("download", {})["inbox_dir"] = str(inbox)
    return create_app(cfg, password=PASSWORD)


def _json_block(html: str, block_id: str):
    m = re.search(rf'<script type="application/json" id="{block_id}">(.*?)</script>', html, re.S)
    assert m, block_id
    return json.loads(m.group(1))


def test_aktionen_in_fester_reihenfolge():
    erwartet = [QuickAction("Rekordbox-XML neu schreiben", "/api/rekordbox/sync"),
                QuickAction("Workstation-Lauf starten", "/api/gpu-wake")]
    erwartet += [QuickAction(label, f"/api/maintenance/{action}") for action, label in action_labels().items()]
    assert list(quick_actions()) == erwartet
    assert all(a.group == "Aktion" for a in quick_actions())


def test_jede_aktion_hat_einen_post_endpunkt(tmp_path):
    posts = {r.path for r in _app(tmp_path).routes if "POST" in (getattr(r, "methods", None) or set())}
    for a in quick_actions():
        pfad = "/api/maintenance/{action}" if a.endpoint.startswith("/api/maintenance/") else a.endpoint
        assert pfad in posts, a.endpoint


def test_soundcloud_links_werden_erkannt():
    treffer = re.compile(SC_LINK_PATTERN, re.IGNORECASE)
    for link in ("https://soundcloud.com/artist/track", "http://m.soundcloud.com/a/b",
                 "https://on.soundcloud.com/AbC12", "HTTPS://WWW.SOUNDCLOUD.COM/a/sets/b"):
        assert treffer.match(link), link
    for kein in ("soundcloud", "https://soundcloud.com/", "https://evil.example/soundcloud.com/a",
                 "Rekordbox"):
        assert not treffer.match(kein), kein


def test_seite_liefert_aktionen_und_muster(tmp_path):
    html = TestClient(_app(tmp_path)).get("/", auth=("sc", PASSWORD)).text
    assert _json_block(html, "quick-search-actions") == [
        {"label": a.label, "endpoint": a.endpoint, "group": a.group} for a in quick_actions()]
    assert _json_block(html, "quick-search-sc-pattern") == SC_LINK_PATTERN


def test_skript_fragt_nach_und_sendet_json():
    text = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "wirklich starten?" in text and "window.confirm(" in text
    assert "'Content-Type': 'application/json'" in text
    assert "Link im URL-Scanner prüfen" in text and "'/mix?url=' + encodeURIComponent(" in text


def test_url_scanner_prueft_den_mitgegebenen_link(tmp_path):
    text = (TEMPLATES / "mix.html").read_text(encoding="utf-8")
    assert 'id="scan"' in text
    assert "URLSearchParams(window.location.search).get('url')" in text
    start = text.index("URLSearchParams(window.location.search).get('url')")
    assert "checkUrl()" in text[start:start + 400]
    assert "'abgelehnt': ['badge-err', 'Quarantäne']" in text
    resp = TestClient(_app(tmp_path)).get("/mix?url=https%3A%2F%2Fsoundcloud.com%2Fa%2Fb", auth=("sc", PASSWORD))
    assert resp.status_code == 200
