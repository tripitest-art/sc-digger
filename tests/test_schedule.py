"""Eigene Tests für den Zeitplan der Container-Jobs (Issue #608, #680)."""
from pathlib import Path

from sc_digger.schedule import JOBS, Job, ScheduledJob, describe_cron, jobs_by_time

ROOT = Path(__file__).resolve().parents[1]

# Bekannte Modi, die in der runs-Tabelle protokolliert werden können
BEKANNTE_MODI = {"discover", "api_schema", "availability_watch", "sc_login", "disk", "rekordbox_import"}


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
