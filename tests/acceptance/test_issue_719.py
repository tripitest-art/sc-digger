"""Akzeptanztests: Deep-Links aus Telegram ins Dashboard (Rest von #594, Teil 1) (Issue <N>)."""
from __future__ import annotations

from pathlib import Path

import yaml

import sc_digger.bot as bot
from sc_digger.models import Config
from sc_digger.output import build_digest_messages, dashboard_url, track_detail_url
from sc_digger.schema import get_field
from sc_digger.web.today import TodayItem
from tests.test_modes import mk

ROOT = Path(__file__).resolve().parents[2]


def _cfg(public_url: str, tmp_path: Path) -> Config:
    cfg = Config.load(ROOT / "config.yaml", local=tmp_path / "keine-override.yaml")
    cfg.raw["state"] = {"db_path": str(tmp_path / "seen.sqlite"),
                        "track_db_path": str(tmp_path / "tracks.sqlite")}
    cfg.raw.setdefault("web", {})["public_url"] = public_url
    return cfg


def test_dashboard_url(tmp_path):
    assert dashboard_url(None, "/digest") is None
    assert dashboard_url(_cfg("", tmp_path), "/digest") is None
    assert dashboard_url(_cfg("ftp://dash.example", tmp_path), "/digest") is None
    cfg = _cfg("https://dash.example:8443/", tmp_path)
    assert dashboard_url(cfg, "/digest?status=open&feedback=none") == (
        "https://dash.example:8443/digest?status=open&feedback=none")
    assert dashboard_url(cfg, "kaufliste#watch") == "https://dash.example:8443/kaufliste#watch"


def test_track_detail_url(tmp_path):
    cfg = _cfg("http://dash.example", tmp_path)
    assert track_detail_url(cfg, 42) == "http://dash.example/track/sc/42"
    assert track_detail_url(cfg, None) is None
    assert track_detail_url(cfg, 0) is None
    assert track_detail_url(_cfg("", tmp_path), 42) is None


def test_digest_titel_fuehrt_auf_die_detailseite(tmp_path):
    msgs = build_digest_messages([mk(42)], cfg=_cfg("https://dash.example", tmp_path))
    text = "".join(m.text for m in msgs)
    assert '<a href="https://dash.example/track/sc/42">Artist42' in text
    assert '<a href="https://soundcloud.com/a/t42">Artist42' not in text


def test_ohne_adresse_bleibt_der_soundcloud_link(tmp_path):
    msgs = build_digest_messages([mk(42)], cfg=_cfg("", tmp_path))
    text = "".join(m.text for m in msgs)
    assert '<a href="https://soundcloud.com/a/t42">Artist42' in text
    assert "/track/sc/42" not in text


def test_heute_nennt_volle_links(tmp_path, monkeypatch):
    items = [TodayItem("unrated", 3, "3 Vorschläge unbewertet", "/digest?status=open&feedback=none")]
    monkeypatch.setattr(bot, "collect_today", lambda cfg, **kw: items)
    mit = bot.heute_reply(_cfg("https://dash.example", tmp_path), "/heute")
    assert "→ https://dash.example/digest?status=open&feedback=none" in mit
    ohne = bot.heute_reply(_cfg("", tmp_path), "/heute")
    assert "→ /digest?status=open&feedback=none" in ohne


def test_feld_im_schema_und_standard_leer():
    feld = get_field("web.public_url")
    assert feld is not None and feld.kind == "str"
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    assert raw["web"]["public_url"] == ""
