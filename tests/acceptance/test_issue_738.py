"""Akzeptanztests: Telegram-Digest in fester Form (Kopf mit Verteilung, Datenzeile, Hinweise)."""
import re

import pytest

from sc_digger import output as out
from sc_digger.models import DownloadKind
from tests.test_modes import mk


def alle(tracks, **kw) -> str:
    """Alle Nachrichten eines Digests als ein Text."""
    return "".join(m.text for m in out.build_digest_messages(tracks, None, **kw))


def datenzeile(text: str) -> str:
    """Erste eingerückte Zeile des Digests, ohne Einrückung."""
    return [z for z in text.splitlines() if z.startswith("    ")][0].strip()


def test_digest_head_nennt_titel_und_verteilung():
    tracks = [mk(1), mk(2), mk(3)]
    tracks[0].download_kind = DownloadKind.NATIVE
    tracks[1].download_kind, tracks[1].download_link = DownloadKind.HYPEDDIT, "https://hypeddit.com/x"
    tracks[2].download_kind = DownloadKind.STORE
    assert out.digest_head(tracks, "sc-digger – 3 neue Treffer") == (
        "<b>sc-digger – 3 neue Treffer</b>\n1 ✅ · 1 🚪 · 1 🛒"
    )


def test_digest_head_ohne_header_und_ohne_tracks():
    assert out.digest_head([], None) == "<b>sc-digger – 0 neue Treffer</b>"


def test_datenzeile_hat_feste_reihenfolge_und_deutsche_zahlen():
    t = mk(1, playback_count=48211, likes_count=1904)
    t.bpm, t.key_camelot = 152.0, "8A"
    t.quality_report = {"ok": True, "ext": "mp3", "bitrate_kbps": 320, "reason": "konstant 320 kbps"}
    assert datenzeile(alle([t])) == "152 BPM · 8A · ✅ MP3 320 · ▶ 48,2k · ♥ 3,9 %"


def test_reichweite_unter_10000_ohne_tausenderpunkt():
    assert "▶ 8210" in datenzeile(alle([mk(1, playback_count=8210)]))


def test_qualitaet_warnung_und_kein_grund_in_der_datenzeile():
    t = mk(1)
    t.quality_report = {"ok": False, "ext": "m4a", "bitrate_kbps": 256, "reason": "nur 256 kbps"}
    zeile = datenzeile(alle([t]))
    assert "⚠️ M4A 256" in zeile
    assert "nur 256 kbps" not in zeile


@pytest.mark.parametrize("kind, kopf", [
    (DownloadKind.NATIVE, "✅ Direkt"),
    (DownloadKind.HYPEDDIT, "🚪 Gate"),
    (DownloadKind.STORE, "🛒 Store"),
    (DownloadKind.NONE, "🎧 Stream"),
])
def test_gruppenkoepfe_kurz(kind, kopf):
    t = mk(1)
    t.download_kind, t.download_link = kind, None
    assert f"<b>{kopf}</b>" in alle([t])


def test_hinweise_stehen_als_eigene_eingerueckte_zeilen():
    t = mk(1)
    t.download_kind, t.download_link = DownloadKind.WETRANSFER, "https://we.tl/t-abc"
    t.download_error = "Cloud-Download fehlgeschlagen: HTTP 404"
    text = alle([t])
    assert '    ⏳ <a href="https://we.tl/t-abc">WeTransfer (läuft ab)</a>' in text
    assert "    ⚠️ Cloud-Download fehlgeschlagen: HTTP 404" in text


def test_nummer_set_stern_und_exploration_bleiben():
    t = mk(1)
    t.reference_hit, t.set_minutes, t.exploration_tag = True, 58, "acid techno"
    text = alle([t], header="T", numbered=True)
    assert re.search(r"<b>1\.</b> <a href=\"https://soundcloud\.com/a/t1\">Artist1 – Track 1</a>", text)
    assert "🎛️ Set, 58 min" in text and "⭐" in text
    assert "    🔍 via #acid techno" in text


def test_titellink_bleibt_auch_mit_dashboard_adresse():
    t = mk(1)
    pfad = str(t.url)
    assert f'<a href="{pfad}">Artist1 – Track 1</a>' in alle([t])
