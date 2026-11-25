"""Akzeptanztests: Durchhör-Modus spielt Hörproben und bestätigt Bewertungen (Issue <N>)."""
from __future__ import annotations

import json
import re
import types
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from sc_digger.models import Config
from sc_digger.web import app as web_app

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "sc_digger" / "web" / "templates" / "durchhoeren.html").read_text(encoding="utf-8")
PASSWORD = "geheim-durchhoeren-neu"
AUTH = ("sc", PASSWORD)


def _eintrag(sc_id, feedback=None, url=None):
    return {"sc_id": sc_id, "artist": f"Artist {sc_id}", "title": f"Titel {sc_id}",
            "url": url if url is not None else f"https://soundcloud.com/a/{sc_id}", "feedback": feedback,
            "status": "open", "download_kind": "native"}


def _client(tmp_path, monkeypatch, items, *, preview: bool, editor: bool = False):
    aufrufe = []

    def fake_query(*a, **k):
        aufrufe.append(k)
        return types.SimpleNamespace(items=items, total=len(items))

    monkeypatch.setattr(web_app, "query_digest", fake_query)
    cfg = Config.load(ROOT / "config.yaml", local=tmp_path / "keine-override.yaml")
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw["download"] = {"inbox_dir": str(tmp_path / "inbox"), "collection_dir": str(tmp_path / "c")}
    cfg.raw.setdefault("web", {})["stream_preview"] = preview
    kw = {}
    if editor:
        (tmp_path / "config.local.yaml").write_text("", encoding="utf-8")
        kw = {"base_path": ROOT / "config.yaml", "local_path": tmp_path / "config.local.yaml"}
    return TestClient(web_app.create_app(cfg, password=PASSWORD, **kw)), aufrufe


def _daten(html: str) -> list[dict]:
    m = re.search(r'<script id="durchhoeren-json" type="application/json">(.*?)</script>', html, re.S)
    assert m
    return json.loads(m.group(1))


def test_nur_offene_unbewertete_vorschlaege(tmp_path, monkeypatch):
    client, aufrufe = _client(tmp_path, monkeypatch, [_eintrag(1)], preview=True)
    client.get("/digest/durchhoeren", auth=AUTH)
    assert aufrufe and aufrufe[0].get("status_filter") == "open" and aufrufe[0].get("feedback") == "none"


def test_mit_hoerprobe_gibt_es_einen_player(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, [_eintrag(4711), _eintrag(4712, feedback="like")], preview=True)
    html = client.get("/digest/durchhoeren", auth=AUTH).text
    assert '<audio id="durchhoeren-audio"' in html and 'id="durchhoeren-preload"' in html
    assert _daten(html) == [{"sc_id": 4711, "artist": "Artist 4711", "title": "Titel 4711",
                             "url": "https://soundcloud.com/a/4711", "preview_url": "/api/preview/sc/4711"}]
    assert 'href="https://soundcloud.com/a/4711" target="_blank" rel="noopener"' in html
    assert "Bewertungs-Ziel" not in html and "Dauer:" not in html
    assert 'data-hint="preview-aus"' not in html


def test_ohne_hoerprobe_ein_hinweis(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, [_eintrag(4711, url="javascript:alert(1)")],
                        preview=False, editor=True)
    html = client.get("/digest/durchhoeren", auth=AUTH).text
    assert '<audio id="durchhoeren-audio"' not in html
    assert 'data-hint="preview-aus"' in html and 'href="/config/web-dashboard"' in html
    assert _daten(html)[0]["preview_url"] is None
    assert _daten(html)[0]["url"] is None
    assert "javascript:" not in html


def test_bewertung_geht_erst_nach_bestaetigung_weiter():
    start = TEMPLATE.index("async function rateCurrent(")
    teil = TEMPLATE[start:TEMPLATE.index("function openDetail(", start)]
    assert "const res = await fetch('/api/feedback'" in teil
    pruefung = teil.index("res.ok")
    assert teil.index("showIndex(currentIdx + 1)") > pruefung
    assert "Speichern fehlgeschlagen" in teil


def test_abspielen_automatisch_und_mit_leertaste():
    assert "e.key === ' '" in TEMPLATE
    assert ".play()" in TEMPLATE and ".pause()" in TEMPLATE
    assert "Leertaste" in TEMPLATE


def test_hoerprobe_ab_werk_an():
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert raw["web"]["stream_preview"] is True
