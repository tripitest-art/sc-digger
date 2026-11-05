"""Eigene Tests zum DJ Track-Explorer der Web-Oberfläche (Issue #222).

Ergänzen die Akzeptanztests um Filterkombinationen, Badges, Paginierung mit
Filter-Erhalt, robuster Parameter-Behandlung, HTML-Escaping und den Nachweis,
dass der Explorer die Datenbank nur liest.
"""
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.app import create_app, match_url

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "geheim-222"


def make_cfg(tmp_path):
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return cfg


def client_for(tmp_path):
    return TestClient(create_app(make_cfg(tmp_path), password=PASSWORD))


def seed(tmp_path):
    """Fünf Tracks: zwei Svetec (Archiv), zwei Inbox, ein Rejected-Fake."""
    db_path = tmp_path / "tracks.sqlite"
    with TrackDB(db_path) as db:
        db.upsert_track(TrackRecord(
            path="/music/Schranz/150/a.wav", mtime=1.0, size=1000,
            artist="Svetec", title="Acid Kick", format="wav", bitrate_kbps=1411.0,
            cutoff_hz=21000, quality_status=QualityStatus.OK, bpm=150.0,
            key_camelot="8A", status=TrackStatus.ARCHIVE,
            source_url="https://soundcloud.com/svetec/1",
        ))
        db.upsert_track(TrackRecord(
            path="/music/Schranz/155/b.flac", mtime=2.0, size=2000,
            artist="Svetec", title="Raw 100%", format="flac", bitrate_kbps=900.0,
            cutoff_hz=20000, quality_status=QualityStatus.OK, bpm=155.0,
            key_camelot="8A", status=TrackStatus.ARCHIVE,
            adopted_at="2026-10-01 10:00:00",
        ))
        db.upsert_track(TrackRecord(
            path="/music/inbox/c.mp3", mtime=3.0, size=500,
            artist="Viper XXL", title="Hard Groove", format="mp3", bitrate_kbps=320.0,
            cutoff_hz=19500, quality_status=QualityStatus.CLIPPED, bpm=160.0,
            key_camelot="9A", status=TrackStatus.INBOX,
        ))
        db.upsert_track(TrackRecord(
            path="/music/inbox/d.mp3", mtime=4.0, size=600,
            artist="Kobosil", title="Sturm", format="mp3", bitrate_kbps=320.0,
            cutoff_hz=19900, quality_status=QualityStatus.OK, bpm=147.0,
            key_camelot="4A", status=TrackStatus.INBOX,
        ))
        db.upsert_track(TrackRecord(
            path="/music/rejected/e.mp3", mtime=5.0, size=200,
            artist="Fake DJ", title="Low Quality", format="mp3",
            quality_status=QualityStatus.FAKE_TRANSCODE, bpm=140.0,
            status=TrackStatus.REJECTED,
        ))
    return db_path


def get_tracks(tmp_path, query=""):
    client = client_for(tmp_path)
    return client.get(f"/tracks{query}", auth=("sc", PASSWORD))


# --------------------------------------------------------------- Grundgerüst
def test_tracks_ui_requires_auth(tmp_path):
    seed(tmp_path)
    client = client_for(tmp_path)
    r = client.get("/tracks")
    assert r.status_code == 401
    assert "basic" in r.headers["www-authenticate"].lower()


def test_tracks_ui_renders_filter_form_and_table(tmp_path):
    seed(tmp_path)
    r = get_tracks(tmp_path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # Filterformular mit den erwarteten Feldern
    assert 'action="/tracks"' in r.text
    for name in ("q", "status", "quality", "bpm_min", "bpm_max", "key_camelot", "adopted"):
        assert f'name="{name}"' in r.text
    # Tabelle mit Kopfzeile und Treffern
    assert "Track-Explorer" in r.text
    assert "Artist" in r.text and "Titel" in r.text and "BPM" in r.text
    assert "Svetec" in r.text and "Viper XXL" in r.text and "Kobosil" in r.text
    assert "150" in r.text and "8A" in r.text


def test_tracks_ui_navigation_is_active_tab(tmp_path):
    seed(tmp_path)
    r = get_tracks(tmp_path)
    assert 'href="/tracks" class="nav-tab active"' in r.text


def test_tracks_ui_renders_quality_status_and_adopted_badges(tmp_path):
    seed(tmp_path)
    r = get_tracks(tmp_path)
    assert "badge-ok" in r.text            # Qualität OK / Inbox / übernommen
    assert "badge-warn" in r.text           # Clipped
    assert "badge-err" in r.text            # Fake-Transcode / Abgelehnt
    assert "badge-info" in r.text           # Sammlung
    assert "Fake-Transcode" in r.text
    assert "übernommen" in r.text
    assert "offen" in r.text


def test_tracks_ui_links_source_url(tmp_path):
    seed(tmp_path)
    r = get_tracks(tmp_path)
    assert 'href="https://soundcloud.com/svetec/1"' in r.text


# --------------------------------------------------------------- Filter
def test_tracks_ui_search_filter_narrows_results(tmp_path):
    seed(tmp_path)
    r = get_tracks(tmp_path, "?q=Viper")
    assert "Viper XXL" in r.text
    assert "Svetec" not in r.text
    assert "Kobosil" not in r.text


def test_tracks_ui_status_and_quality_filters(tmp_path):
    seed(tmp_path)
    archive = get_tracks(tmp_path, "?status=archive")
    assert "Svetec" in archive.text and "Viper XXL" not in archive.text
    inbox = get_tracks(tmp_path, "?status=inbox")
    assert "Viper XXL" in inbox.text and "Svetec" not in inbox.text
    clipped = get_tracks(tmp_path, "?quality=clipped")
    assert "Viper XXL" in clipped.text and "Kobosil" not in clipped.text
    fake = get_tracks(tmp_path, "?quality=fake_transcode")
    assert "Fake DJ" in fake.text and "Svetec" not in fake.text


def test_tracks_ui_bpm_and_key_filters(tmp_path):
    seed(tmp_path)
    bpm = get_tracks(tmp_path, "?bpm_min=150&bpm_max=155")
    assert "Svetec" in bpm.text and "Kobosil" not in bpm.text  # 150/155, nicht 147/160
    key = get_tracks(tmp_path, "?key_camelot=8A")
    assert "Svetec" in key.text and "Viper XXL" not in key.text


def test_tracks_ui_adopted_filter(tmp_path):
    seed(tmp_path)
    yes = get_tracks(tmp_path, "?adopted=1")
    assert "Raw 100%" in yes.text and "Hard Groove" not in yes.text
    no = get_tracks(tmp_path, "?adopted=0")
    assert "Hard Groove" in no.text and "Raw 100%" not in no.text


def test_tracks_ui_combined_filters(tmp_path):
    seed(tmp_path)
    r = get_tracks(tmp_path, "?q=Svetec&bpm_min=152&status=archive")
    assert "Raw 100%" in r.text
    assert "Acid Kick" not in r.text        # BPM 150 liegt unter bpm_min


# --------------------------------------------------------------- Paginierung
def test_tracks_ui_pagination_shows_next_and_prev(tmp_path):
    seed(tmp_path)
    first = get_tracks(tmp_path, "?limit=2")
    assert "Seite 1 von 3" in first.text
    assert "offset=2" in first.text         # Weiter-Link
    second = get_tracks(tmp_path, "?limit=2&offset=2")
    assert "Seite 2 von 3" in second.text
    assert "offset=4" in second.text
    assert "offset=0" not in second.text    # Zurück-Link zeigt auf Seite 1 ohne offset


def test_tracks_ui_pagination_keeps_filters_in_links(tmp_path):
    seed(tmp_path)
    r = get_tracks(tmp_path, "?status=archive&limit=1")
    assert "Seite 1 von 2" in r.text
    assert "status=archive" in r.text
    assert "limit=1" in r.text
    assert "offset=1" in r.text


# --------------------------------------------------------------- Robustheit
def test_tracks_ui_invalid_params_are_ignored(tmp_path):
    seed(tmp_path)
    r = get_tracks(tmp_path, "?bpm_min=abc&bpm_max=&adopted=vielleicht&limit=quatsch")
    assert r.status_code == 200
    assert "Svetec" in r.text               # kein gültiger Filter -> alle Treffer
    assert "Kobosil" in r.text


def test_tracks_ui_empty_db_shows_hint(tmp_path):
    r = get_tracks(tmp_path)                # keine DB angelegt
    assert r.status_code == 200
    assert "Keine Treffer" in r.text
    assert "0" in r.text


def test_tracks_ui_escapes_search_value(tmp_path):
    seed(tmp_path)
    r = get_tracks(tmp_path, "?q=%3Cscript%3Ealert(1)%3C/script%3E")
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_tracks_ui_does_not_modify_db(tmp_path):
    db_path = seed(tmp_path)
    before = db_path.read_bytes()
    get_tracks(tmp_path, "?q=Svetec&status=archive&adopted=1")
    assert db_path.read_bytes() == before


# --------------------------------------------------------------- Statusseite
def test_status_page_links_to_tracks_and_lists_collection(tmp_path):
    seed(tmp_path)
    client = client_for(tmp_path)
    r = client.get("/", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert 'href="/tracks"' in r.text
    assert "Sammlung &amp; Track-DB" in r.text
    assert "Tracks gesamt" in r.text


def test_tracks_ui_verlinkt_keine_javascript_urls(tmp_path):
    """source_url stammt aus fremden Feeds: nur http/https darf ins href."""
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        db.upsert_track(TrackRecord(
            path="/music/Schranz/150/x.wav", mtime=1.0, size=10,
            artist="X", title="Y", format="wav", bitrate_kbps=1411.0,
            cutoff_hz=21000, quality_status=QualityStatus.OK, bpm=150.0,
            key_camelot="8A", status=TrackStatus.ARCHIVE,
            source_url="javascript:alert(1)",
        ))
    r = get_tracks(tmp_path)
    assert "javascript:alert(1)" not in r.text


def test_tracks_ui_renders_camelot_pills_quick_chips_and_dj_deck(tmp_path):
    """Prüft die modernisierten UI-Elemente: Camelot-Pills, Schnellfilter und DJ-Deck-Leiste."""
    seed(tmp_path)
    r = get_tracks(tmp_path)
    assert r.status_code == 200
    assert 'class="camelot-pill" data-camelot="8A"' in r.text
    assert 'class="quick-chips"' in r.text
    assert 'id="dj-deck-bar"' in r.text


def test_mix_ui_renders_interactive_camelot_picker(tmp_path):
    """Prüft die interaktive Camelot-Schnellwahl auf /mix."""
    client = client_for(tmp_path)
    r = client.get("/mix?key=8A&bpm=155", auth=("sc", PASSWORD))
    assert r.status_code == 200
    assert 'class="camelot-picker"' in r.text
    assert "selectCamelotKey('8A')" in r.text


# --------------------------------------------------------------- ⚡ Match-Button
def test_match_url_edge_cases():
    """Prüft Randfälle der Hilfsfunktion match_url (Whitespace, Typen, Wertebereich)."""
    assert match_url("  8a  ", 155) == "/mix?key=8A&bpm=155"
    assert match_url("12B", 140.25) == "/mix?key=12B&bpm=140.25"
    assert match_url("4A", 150.0) == "/mix?key=4A&bpm=150"

    # Ungültiger Key
    assert match_url(None, 150.0) is None
    assert match_url("", 150.0) is None
    assert match_url("   ", 150.0) is None
    assert match_url(123, 150.0) is None  # type: ignore[arg-type]

    # Ungültige BPM
    assert match_url("8A", None) is None
    assert match_url("8A", 0) is None
    assert match_url("8A", 0.0) is None
    assert match_url("8A", -150.0) is None
    assert match_url("8A", True) is None  # type: ignore[arg-type]
    assert match_url("8A", False) is None  # type: ignore[arg-type]
    assert match_url("8A", float("nan")) is None
    assert match_url("8A", float("inf")) is None


def test_tracks_ui_renders_match_buttons_and_coexists_with_source_url(tmp_path):
    """Prüft, dass Tracks mit Key/BPM den ⚡ Match-Button erhalten und neben SoundCloud-Link koexistieren."""
    seed(tmp_path)
    r = get_tracks(tmp_path)
    assert r.status_code == 200

    # 4 von 5 Tracks haben Key und BPM (Acid Kick: 8A/150, Raw 100%: 8A/155, Hard Groove: 9A/160, Sturm: 4A/147)
    assert r.text.count("match-btn") == 4
    assert 'href="/mix?key=8A&amp;bpm=150"' in r.text or 'href="/mix?key=8A&bpm=150"' in r.text
    assert 'href="/mix?key=8A&amp;bpm=155"' in r.text or 'href="/mix?key=8A&bpm=155"' in r.text
    assert 'href="/mix?key=9A&amp;bpm=160"' in r.text or 'href="/mix?key=9A&bpm=160"' in r.text
    assert 'href="/mix?key=4A&amp;bpm=147"' in r.text or 'href="/mix?key=4A&bpm=147"' in r.text

    # Acid Kick hat sowohl SoundCloud-Link als auch ⚡ Match
    assert "⚡ Match" in r.text
    assert "SoundCloud ↗" in r.text

    # Low Quality (Fake DJ) hat weder Key noch source_url -> Spalte zeigt '—'
    assert "—" in r.text


