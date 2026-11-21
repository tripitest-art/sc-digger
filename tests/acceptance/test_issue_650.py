"""Akzeptanztests: Bot-Befehl /heute (Issue #<N>)."""
import types
from pathlib import Path

from sc_digger import bot
from sc_digger.models import Config
from sc_digger.web.today import GATES_HREF, UNRATED_HREF, WATCH_HREF, TodayItem

ROOT = Path(__file__).resolve().parents[2]


def _cfg() -> Config:
    return Config.load(ROOT / "config.yaml")


def _items(*eintraege) -> list[TodayItem]:
    return list(eintraege)


def test_heute_listet_die_drei_arten(monkeypatch):
    monkeypatch.setattr(bot, "collect_today", lambda _cfg: _items(
        TodayItem("unrated", 3, "unbewertete Vorschläge", UNRATED_HREF),
        TodayItem("gates", 5, "offene Gates", GATES_HREF),
        TodayItem("watch", 2, "neu verfügbar", WATCH_HREF),
    ))
    antwort = bot.heute_reply(_cfg(), "/heute")
    zeilen = antwort.splitlines()
    assert zeilen[0] == "📋 Heute zu tun"
    assert f"• 3 unbewertete Vorschläge → {UNRATED_HREF}" in antwort
    assert f"• 5 offene Gates → {GATES_HREF}" in antwort
    assert f"• 2 neu verfügbar → {WATCH_HREF}" in antwort


def test_heute_bei_einem_eintrag_in_der_einzahl(monkeypatch):
    monkeypatch.setattr(bot, "collect_today", lambda _cfg: _items(
        TodayItem("unrated", 1, "unbewertete Vorschläge", UNRATED_HREF),
        TodayItem("gates", 1, "offene Gates", GATES_HREF),
    ))
    antwort = bot.heute_reply(_cfg(), "/heute")
    assert "• 1 unbewerteter Vorschlag →" in antwort
    assert "• 1 offenes Gate →" in antwort


def test_heute_ohne_offene_aufgaben(monkeypatch):
    monkeypatch.setattr(bot, "collect_today", lambda _cfg: _items())
    antwort = bot.heute_reply(_cfg(), "/heute")
    assert antwort.splitlines()[0] == "📋 Heute zu tun"
    assert "nichts offen" in antwort


def test_heute_wirft_nie(monkeypatch):
    def kaputt(_cfg):
        raise RuntimeError("Datenbank weg")

    monkeypatch.setattr(bot, "collect_today", kaputt)
    antwort = bot.heute_reply(_cfg(), "/heute")
    assert "Heute-Liste nicht lesbar" in antwort


def test_befehl_ist_verdrahtet():
    quelle = Path(bot.__file__).read_text(encoding="utf-8")
    assert '"/heute"' in quelle
    assert "heute_reply" in quelle
