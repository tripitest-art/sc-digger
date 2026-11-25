"""Eigene Tests für den Zeitplan der Container-Jobs (Issue #608, #680)."""
from pathlib import Path

from sc_digger.schedule import JOBS, Job, ScheduledJob, describe_cron, jobs_by_time

ROOT = Path(__file__).resolve().parents[1]

# Bekannte Modi, die in der runs-Tabelle protokolliert werden können
BEKANNTE_MODI = {"discover", "api_schema", "availability_watch", "sc_login", "disk", "rekordbox_import", "backup"}


def test_schluessel_sind_eindeutig():
    keys = [j.key for j in JOBS]
    assert len(keys) == len(set(keys))


def test_marker_treffen_nur_ihren_job():
    # Jeder Marker darf in keinem fremden Marker stecken, sonst wäre die Zuordnung mehrdeutig.
    for job in JOBS:
        for other in JOBS:
            if other is not job:
                assert job.marker not in other.marker, (job.key, other.key)


def test_describe_cron_alle_wochentage():
    erwartet = ["sonntags", "montags", "dienstags", "mittwochs", "donnerstags", "freitags", "samstags"]
    for tag, wort in enumerate(erwartet):
        assert describe_cron(f"0 9 * * {tag}") == f"{wort} 09:00"


def test_describe_cron_vertraegt_ungueltiges():
    assert describe_cron("60 7 * * *") == "60 7 * * *"
    assert describe_cron("0 25 * * *") == "0 25 * * *"
    assert describe_cron("0 7 1 * *") == "0 7 1 * *"
    assert describe_cron(None) is None  # type: ignore[arg-type]


def test_jobs_by_time_ist_stabil_und_vollstaendig():
    sortiert = jobs_by_time()
    assert sorted(j.key for j in sortiert) == sorted(j.key for j in JOBS)
    assert all(isinstance(j, ScheduledJob) for j in sortiert)


def test_plan_gegen_crontab_quellen():
    """entrypoint.sh und Dockerfile enthalten alle cron und kennung."""
    quellen = (ROOT / "entrypoint.sh").read_text(encoding="utf-8") + (ROOT / "Dockerfile").read_text(encoding="utf-8")
    for job in JOBS:
        assert job.cron in quellen, f"{job.name}: Zeit {job.cron} fehlt in entrypoint.sh/Dockerfile"
        assert job.kennung in quellen, f"{job.name}: Kennung {job.kennung} fehlt in entrypoint.sh/Dockerfile"


def test_sortierung_und_eindeutige_namen():
    assert [j.uhrzeit for j in JOBS] == sorted(j.uhrzeit for j in JOBS)
    namen = [j.name for j in JOBS]
    assert len(namen) == len(set(namen))


def test_jeder_mode_ist_bekannter_lauf_modus():
    for job in JOBS:
        if job.mode is not None:
            assert job.mode in BEKANNTE_MODI, f"{job.name}: unbekannter mode {job.mode}"


def test_wochentags_jobs_sind_gekennzeichnet():
    # Wächter und Kaufliste laufen wöchentlich sonntags
    wochentags_jobs = [j for j in JOBS if not j.cron.endswith("* * *")]
    assert {j.name for j in wochentags_jobs} == {"watch", "kaufliste"}
    for j in wochentags_jobs:
        assert j.cron.endswith("0")
        assert "sonntags" in describe_cron(j.cron)
        assert j.wochentag == "sonntags"

    # Tägliche Jobs dürfen keinen Wochentag haben
    taegliche_jobs = [j for j in JOBS if j.cron.endswith("* * *")]
    for j in taegliche_jobs:
        assert j.wochentag is None


def test_tagesablauf_rendert_keine_falschen_sonntags_bei_7_uhr_jobs(tmp_path):
    """Prüft, dass 7-Uhr-Jobs (5 7, 15 7, 30 7) nicht fälschlich als 'sonntags' markiert werden."""
    from fastapi.testclient import TestClient
    from sc_digger.models import Config
    from sc_digger.web.app import create_app

    cfg = Config.load(ROOT / "config.yaml")
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
        "health_db_path": str(tmp_path / "health.sqlite"),
    }
    cfg.raw["download"] = {
        "inbox_dir": str(tmp_path / "inbox"),
        "collection_dir": str(tmp_path / "collection"),
    }
    client = TestClient(create_app(cfg, password="test-password"))
    html = client.get("/", auth=("sc", "test-password")).text

    assert "Tagesablauf" in html
    # Sonntags darf nur für 19:00 und 20:00 gerendert werden
    assert html.count("(sonntags)") == 2
    # Sicherstellen, dass 07:05, 07:15, 07:30 ohne (sonntags) dastehen
    assert "07:05\n            \n          </td>" in html or "07:05" in html
    assert "07:05\n            <span class=\"meta-dim\">(sonntags)</span>" not in html
    assert "07:15\n            <span class=\"meta-dim\">(sonntags)</span>" not in html
    assert "07:30\n            <span class=\"meta-dim\">(sonntags)</span>" not in html


def test_tagesablauf_lesefehler_wird_sichtbar_und_geloggt(tmp_path, monkeypatch, caplog):
    """Bei einem Lesefehler in der Health-DB wird log.exception gerufen und ein Fehlerhinweis gerendert."""
    import logging
    from fastapi.testclient import TestClient
    from sc_digger.health import Health
    from sc_digger.models import Config
    from sc_digger.web.app import create_app

    cfg = Config.load(ROOT / "config.yaml")
    health_db = tmp_path / "health.sqlite"
    health_db.touch()  # Datei existiert
    cfg.raw["state"] = {
        "db_path": str(tmp_path / "seen.sqlite"),
        "track_db_path": str(tmp_path / "tracks.sqlite"),
        "health_db_path": str(health_db),
    }
    cfg.raw["download"] = {
        "inbox_dir": str(tmp_path / "inbox"),
        "collection_dir": str(tmp_path / "collection"),
    }

    def raiser(*args, **kwargs):
        raise RuntimeError("simulierter DB-Fehler")

    monkeypatch.setattr(Health, "_last_runs", raiser)

    with caplog.at_level(logging.ERROR):
        client = TestClient(create_app(cfg, password="test-password"))
        html = client.get("/", auth=("sc", "test-password")).text

    assert "Laufhistorie nicht lesbar" in html
    assert "simulierter DB-Fehler" in html
    assert any("Tagesablauf: Fehler beim Lesen der Health-DB" in r.message for r in caplog.records)

    # In der Tagesablauf-Karte darf bei Lesefehler kein 'noch kein Lauf' stehen, sondern 'nicht lesbar'
    tagesablauf_html = html[html.find("⏰ Tagesablauf") :]
    tagesablauf_html = tagesablauf_html[: tagesablauf_html.find("<!-- Letzte Läufe -->")]
    assert "nicht lesbar" in tagesablauf_html
    assert "noch kein Lauf" not in tagesablauf_html
