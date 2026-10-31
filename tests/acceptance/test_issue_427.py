"""Akzeptanztests: Tabellen im Dashboard sortieren, filtern und Zeilenmenge wählen (Tracks, Digest)."""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from sc_digger.db import QualityStatus, TrackDB, TrackRecord, TrackStatus
from sc_digger.models import Config
from sc_digger.web.app import create_app

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "geheim-sort"
AUTH = ("sc", PASSWORD)


# --- Hilfen ---------------------------------------------------------------------


def client_for(tmp_path: Path) -> TestClient:
    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
    }
    return TestClient(create_app(cfg, password=PASSWORD))


def seed_tracks(tmp_path: Path, count: int = 4) -> None:
    """Vier Tracks mit unterschiedlichen Werten; einer ohne BPM (NULL muss beim Sortieren nach hinten)."""
    rows = [
        ("Svetec", "Acid Kick", 150.0, "8A", "wav", QualityStatus.OK, TrackStatus.ARCHIVE),
        ("Kobosil", "Sturm", 170.0, "4A", "mp3", QualityStatus.OK, TrackStatus.ARCHIVE),
        ("Viper XXL", "Hard Groove", 160.0, "9A", "mp3", QualityStatus.CLIPPED, TrackStatus.INBOX),
    ]
    with TrackDB(tmp_path / "tracks.sqlite") as db:
        for i, (artist, title, bpm, key, fmt, quality, status) in enumerate(rows, start=1):
            db.upsert_track(TrackRecord(
                path=f"/music/Schranz/{i}/{i}.{fmt}", mtime=float(i), size=1000,
                artist=artist, title=title, format=fmt, bitrate_kbps=320.0,
                cutoff_hz=20000, quality_status=quality, bpm=bpm, key_camelot=key, status=status,
            ))
        db.upsert_track(TrackRecord(
            path="/music/inbox/ohne-bpm.mp3", mtime=9.0, size=1000,
            artist="Ohne BPM", title="Unbekannt", format="mp3", bitrate_kbps=320.0,
            quality_status=QualityStatus.OK, bpm=None, key_camelot=None, status=TrackStatus.INBOX,
        ))
        for i in range(count):
            db.upsert_track(TrackRecord(
                path=f"/music/Schranz/masse/{i}.mp3", mtime=100.0 + i, size=1000,
                artist=f"Masse {i:03d}", title=f"Füll {i:03d}", format="mp3",
                bpm=145.0, key_camelot="5A", quality_status=QualityStatus.OK,
                status=TrackStatus.INBOX,
            ))


def _body_rows(html: str) -> list[list[str]]:
    """Zellen der Tabelle in Dokumentreihenfolge, Tags entfernt."""
    body = html.split("<tbody", 1)[1].split("</tbody>", 1)[0]
    return [[re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", cell)).strip()
             for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            for row in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)]


def _column(html: str, name: str) -> list[str]:
    """Werte einer Spalte; ``name`` darf durch Sortier-Zusätze ergänzt sein (“BPM ▲”)."""
    head = html.split("<thead", 1)[1].split("</thead>", 1)[0]
    titles = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", th)).strip()
              for th in re.findall(r"<th[^>]*>(.*?)</th>", head, re.S)]
    index = next(i for i, title in enumerate(titles) if title.startswith(name))
    return [row[index] for row in _body_rows(html) if len(row) > index]


# --- /tracks: Sortieren ----------------------------------------------------------


def test_tracks_sortiert_nach_bpm_absteigend(tmp_path):
    seed_tracks(tmp_path)
    html = client_for(tmp_path).get("/tracks?sort=bpm&dir=desc", auth=AUTH).text
    werte = [float(v.replace("BPM", "").strip()) for v in _column(html, "BPM") if v not in ("—", "-", "")]
    assert werte == sorted(werte, reverse=True), werte
    assert werte[0] == 170.0 and 150.0 in werte


def test_tracks_sortiert_nach_artist_aufsteigend(tmp_path):
    seed_tracks(tmp_path)
    html = client_for(tmp_path).get("/tracks?sort=artist&dir=asc", auth=AUTH).text
    namen = [v for v in _column(html, "Artist") if v]
    assert namen == sorted(namen, key=str.lower), namen


def test_tracks_null_bpm_steht_bei_beiden_richtungen_hinten(tmp_path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)
    for richtung in ("asc", "desc"):
        werte = _column(client.get(f"/tracks?sort=bpm&dir={richtung}", auth=AUTH).text, "BPM")
        assert werte, "keine Zeilen"
        # Der Track ohne BPM wird als Strich gerendert und muss am Ende stehen.
        assert werte[-1] in ("—", "-", ""), (richtung, werte)


def test_unbekanntes_sortierfeld_wird_ignoriert(tmp_path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)
    antwort = client.get("/tracks?sort=__böse__&dir=seitwärts", auth=AUTH)
    assert antwort.status_code == 200
    standard = client.get("/tracks", auth=AUTH).text
    assert _column(antwort.text, "Artist") == _column(standard, "Artist")


def test_sortierfeld_ist_kein_sql_einschleuspunkt(tmp_path):
    seed_tracks(tmp_path)
    client = client_for(tmp_path)
    antwort = client.get("/tracks?sort=bpm%3B%20DROP%20TABLE%20tracks%3B--", auth=AUTH)
    assert antwort.status_code == 200
    # Tabelle steht noch: der nächste Aufruf liefert weiterhin Treffer.
    assert client.get("/tracks", auth=AUTH).text.count("<tr") >= 2


# --- /tracks: Seitenmenge --------------------------------------------------------


def test_seitenmenge_ist_waehlbar(tmp_path):
    seed_tracks(tmp_path)
    html = client_for(tmp_path).get("/tracks", auth=AUTH).text
    block = re.search(r'<select[^>]*name="limit"[^>]*>(.*?)</select>', html, re.S)
    assert block, "Auswahl für die Zeilenmenge fehlt"
    werte = re.findall(r'<option[^>]*value="(\d+)"', block.group(1))
    assert {"25", "50", "100", "200"} <= set(werte), werte


def test_seitenmenge_wird_auf_das_maximum_begrenzt(tmp_path):
    seed_tracks(tmp_path, count=250)
    html = client_for(tmp_path).get("/tracks?limit=5000", auth=AUTH).text
    assert len(_body_rows(html)) == 200


def test_seitenmenge_wirkt_und_total_bleibt_sichtbar(tmp_path):
    seed_tracks(tmp_path, count=30)
    html = client_for(tmp_path).get("/tracks?limit=25", auth=AUTH).text
    assert len(_body_rows(html)) == 25
    assert str(34) in html  # 4 Beispiel-Tracks + 30 Füll-Tracks


# --- /tracks: Sortierung und Filter zusammen ------------------------------------


def test_sortierlinks_halten_die_filter(tmp_path):
    seed_tracks(tmp_path)
    html = client_for(tmp_path).get("/tracks?q=svetec&sort=bpm&dir=desc", auth=AUTH).text
    kopf = html.split("</thead>", 1)[0]
    links = re.findall(r'href="(/tracks\?[^"]*sort=[^"]*)"', kopf)
    assert links, "Tabellenkopf enthält keine Sortierlinks"
    for link in links:
        assert "q=svetec" in link, link
    # Erneuter Klick auf dieselbe Spalte dreht die Richtung.
    assert any("dir=asc" in link for link in links), links


def test_sortierung_bleibt_beim_blaettern_erhalten(tmp_path):
    seed_tracks(tmp_path, count=10)
    html = client_for(tmp_path).get(
        "/tracks?sort=artist&dir=asc&limit=2&offset=2", auth=AUTH
    ).text
    namen = [v for v in _column(html, "Artist") if v]
    assert namen == sorted(namen, key=str.lower), namen
    pager = re.findall(r'href="(/tracks\?[^"]*offset=\d+[^"]*)"', html)
    assert pager and all("sort=artist" in link for link in pager), pager


# --- /digest ---------------------------------------------------------------------


def seed_digest(tmp_path: Path) -> None:
    import sqlite3

    with sqlite3.connect(tmp_path / "seen.sqlite") as db:
        db.execute(
            "CREATE TABLE seen (id INTEGER PRIMARY KEY, url TEXT, title TEXT, reported_at TEXT)"
        )
        for i, (title, when) in enumerate(
            [("Zebra", "2026-10-01 10:00:00"), ("Anfang", "2026-10-03 10:00:00"),
             ("Mitte", "2026-10-02 10:00:00")], start=1
        ):
            db.execute(
                "INSERT INTO seen VALUES (?, ?, ?, ?)",
                (i, f"https://soundcloud.com/dj/{i}", f"DJ - {title}", when),
            )


def test_digest_sortiert_nach_datum(tmp_path):
    seed_digest(tmp_path)
    html = client_for(tmp_path).get("/digest?sort=date&dir=desc", auth=AUTH).text
    daten = [v for v in _column(html, "Datum") if v]
    assert daten == sorted(daten, reverse=True), daten


def test_digest_sortiert_nach_titel_und_hat_seitenmenge(tmp_path):
    seed_digest(tmp_path)
    html = client_for(tmp_path).get("/digest?sort=title&dir=asc&limit=25", auth=AUTH).text
    titel = [v for v in _column(html, "Titel") if v]
    assert titel == sorted(titel, key=str.lower), titel
    assert re.search(r'<select[^>]*name="limit"', html), "Auswahl für die Zeilenmenge fehlt"


def test_digest_ignoriert_unbekanntes_sortierfeld(tmp_path):
    seed_digest(tmp_path)
    client = client_for(tmp_path)
    assert client.get("/digest?sort=;DROP TABLE seen;", auth=AUTH).status_code == 200
    assert client.get("/digest", auth=AUTH).text.count("<tr") >= 2