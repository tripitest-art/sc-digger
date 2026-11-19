"""Akzeptanztests: Zeitplan der Container-Jobs als Liste, abgeglichen mit der Crontab."""
import re
from pathlib import Path

from sc_digger.schedule import JOBS, ScheduledJob, describe_cron, jobs_by_time

ROOT = Path(__file__).resolve().parents[2]
CRON_ZEILE = re.compile(
    r'echo "(\S+ \S+ \S+ \S+ \S+) \. /app/cron\.env; cd /app && (.*?) >> /proc/1/fd/1'
)


def _crontab():
    zeilen = []
    for datei in ("entrypoint.sh", "Dockerfile"):
        text = (ROOT / datei).read_text(encoding="utf-8")
        zeilen += [(m.group(1), m.group(2) + " >>") for m in CRON_ZEILE.finditer(text)]
    return zeilen


def test_liste_enthaelt_genau_die_erwarteten_jobs():
    assert {(j.key, j.cron) for j in JOBS} == {
        ("backup", "30 6 * * *"),
        ("audit", "45 6 * * *"),
        ("taste", "5 7 * * *"),
        ("canary", "15 7 * * *"),
        ("discover", "30 7 * * *"),
        ("scout", "0 18 * * *"),
        ("watch", "0 19 * * 0"),
        ("kaufliste", "0 20 * * 0"),
    }
    assert all(isinstance(j, ScheduledJob) and j.label and j.marker for j in JOBS)


def test_jeder_job_steht_mit_seiner_zeit_in_der_crontab():
    crontab = _crontab()
    for job in JOBS:
        treffer = [cron for cron, befehl in crontab if job.marker in befehl]
        assert treffer == [job.cron], f"{job.key}: {treffer}"


def test_jede_cron_zeile_steht_in_der_liste():
    crontab = _crontab()
    assert len(crontab) == len(JOBS)
    for cron, befehl in crontab:
        passend = [j.key for j in JOBS if j.marker in befehl and j.cron == cron]
        assert len(passend) == 1, f"{cron} {befehl[:60]}: {passend}"


def test_describe_cron():
    assert describe_cron("30 7 * * *") == "täglich 07:30"
    assert describe_cron("5 7 * * *") == "täglich 07:05"
    assert describe_cron("0 19 * * 0") == "sonntags 19:00"
    assert describe_cron("0 19 * * 7") == "sonntags 19:00"
    assert describe_cron("15 6 * * 1") == "montags 06:15"
    assert describe_cron("*/15 * * * *") == "*/15 * * * *"
    assert describe_cron("kaputt") == "kaputt"


def test_jobs_by_time():
    assert [j.key for j in jobs_by_time()] == [
        "backup", "audit", "taste", "canary", "discover", "scout", "watch", "kaufliste",
    ]
