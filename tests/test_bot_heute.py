"""Eigene Tests für den Bot-Befehl /heute (Issue #650)."""
from pathlib import Path

from sc_digger import bot
from sc_digger.models import Config
from sc_digger.web.today import TodayItem

ROOT = Path(__file__).resolve().parents[1]


def _cfg() -> Config:
    return Config.load(ROOT / "config.yaml")


ITEMS = [
    TodayItem("watch", 2, "neu verfügbar", "/kaufliste#watch"),
    TodayItem("unrated", 3, "unbewertete Vorschläge", "/a"),
    TodayItem("gates", 5, "offene Gates", "/b"),
]


def test_argumente_werden_ignoriert(monkeypatch):
    monkeypatch.setattr(bot, "collect_today", lambda _c: list(ITEMS))
    assert bot.heute_reply(_cfg(), "/heute egal") == bot.heute_reply(_cfg(), "/heute")


def test_reihenfolge_folgt_collect_today(monkeypatch):
    monkeypatch.setattr(bot, "collect_today", lambda _c: list(ITEMS))
    zeilen = bot.heute_reply(_cfg(), "/heute").splitlines()
    assert zeilen[1:] == [
        "• 2 neu verfügbar → /kaufliste#watch",
        "• 3 unbewertete Vorschläge → /a",
        "• 5 offene Gates → /b",
    ]


def test_leer_gibt_genau_zwei_zeilen(monkeypatch):
    monkeypatch.setattr(bot, "collect_today", lambda _c: [])
    assert bot.heute_reply(_cfg(), "/heute").splitlines() == [
        "📋 Heute zu tun", "• nichts offen — gute Arbeit."]
