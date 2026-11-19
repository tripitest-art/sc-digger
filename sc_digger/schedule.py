"""Zeitplan der Cron-Jobs im Container als lesbare Liste.

Die Jobs selbst stehen in `Dockerfile` (discover) und `entrypoint.sh` (alle übrigen). Diese
Liste beschreibt sie nur, sie erzeugt keine Crontab: So bleibt der Betrieb unverändert, und das
Dashboard kann trotzdem zeigen, was wann läuft. `tests/test_schedule.py` und der Akzeptanztest
gleichen die Liste mit beiden Dateien ab, damit sie nicht still veraltet.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScheduledJob:
    key: str
    label: str
    cron: str
    marker: str


JOBS: tuple[ScheduledJob, ...] = (
    ScheduledJob("backup", "Datenbank-Sicherung", "30 6 * * *", "sc_digger.backup run"),
    ScheduledJob("audit", "Übernommen-Abgleich", "45 6 * * *", "audit --check-adopted"),
    ScheduledJob("taste", "Taste-Score", "5 7 * * *", "sc_digger.main taste"),
    ScheduledJob("canary", "API-Kanarienvogel", "15 7 * * *", "sc_digger.canary"),
    # " >>" am Ende, sonst träfe der Marker auch audit, scout und taste.
    ScheduledJob("discover", "Discover-Lauf", "30 7 * * *", "python -m sc_digger.main >>"),
    ScheduledJob("scout", "Store-Scout", "0 18 * * *", "sc_digger.main scout"),
    ScheduledJob("watch", "Verfügbarkeits-Wächter", "0 19 * * 0", "sc_digger.watch"),
    ScheduledJob("kaufliste", "Kaufliste", "0 20 * * 0", "send_kaufliste"),
)

_WOCHENTAGE = ("sonntags", "montags", "dienstags", "mittwochs", "donnerstags", "freitags", "samstags")


def _parse(cron: str) -> tuple[int, int, int | None] | None:
    """(Minute, Stunde, Wochentag oder None für täglich); None, wenn die Form nicht passt."""
    if not isinstance(cron, str):
        return None
    teile = cron.split()
    if len(teile) != 5 or teile[2] != "*" or teile[3] != "*":
        return None
    try:
        minute, stunde = int(teile[0]), int(teile[1])
        tag = None if teile[4] == "*" else int(teile[4]) % 7
    except ValueError:
        return None
    if not (0 <= minute < 60 and 0 <= stunde < 24):
        return None
    return minute, stunde, tag


def describe_cron(cron: str) -> str:
    """Deutsche Kurzform eines Cron-Ausdrucks, sonst der Text unverändert."""
    zerlegt = _parse(cron)
    if zerlegt is None:
        return cron
    minute, stunde, tag = zerlegt
    wann = "täglich" if tag is None else _WOCHENTAGE[tag]
    return f"{wann} {stunde:02d}:{minute:02d}"


def jobs_by_time() -> list[ScheduledJob]:
    """Tägliche Jobs nach Uhrzeit, danach wöchentliche nach Wochentag (Sonntag zuletzt)."""
    def schluessel(job: ScheduledJob):
        zerlegt = _parse(job.cron)
        if zerlegt is None:
            return (2, 0, 0, 0)
        minute, stunde, tag = zerlegt
        if tag is None:
            return (0, 0, stunde, minute)
        return (1, (tag - 1) % 7, stunde, minute)

    return sorted(JOBS, key=schluessel)
