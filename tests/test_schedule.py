"""Eigene Tests für den Zeitplan der Container-Jobs (Issue #608)."""
from sc_digger.schedule import JOBS, ScheduledJob, describe_cron, jobs_by_time


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
