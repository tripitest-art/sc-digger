"""Zeitplan der Cron-Jobs im Container als lesbare Liste.

Die Jobs selbst stehen in `Dockerfile` (discover) und `entrypoint.sh` (alle übrigen). Diese
Liste beschreibt sie nur, sie erzeugt keine Crontab: So bleibt der Betrieb unverändert, und das
Dashboard kann trotzdem zeigen, was wann läuft. `tests/test_schedule.py` und der Akzeptanztest
gleichen die Liste mit beiden Dateien ab, damit sie nicht still veraltet.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Job:
    name: str          # "backup"
    uhrzeit: str       # "06:30"
    cron: str          # "30 6 * * *"
    kennung: str       # "sc_digger.backup run"
    mode: str | None   # "backup" oder None, wenn der Job nicht in runs protokolliert
    beschreibung: str  # "Sicherung der SQLite-Datenbanken"

    # Abwärtskompatibilität zu ScheduledJob (Issue #608)
    @property
    def key(self) -> str:
        return self.name

    @property
    def label(self) -> str:
        return self.beschreibung

    @property
    def marker(self) -> str:
        return self.kennung

    @property
    def wochentag(self) -> str | None:
        """Name des Wochentags (z. B. 'sonntags'), falls der Job nur an bestimmten Wochentagen läuft."""
        zerlegt = _parse(self.cron)
        if zerlegt is not None and zerlegt[2] is not None:
            return _WOCHENTAGE[zerlegt[2]]
        return None


# Alias für bestehende Verwendungen
ScheduledJob = Job


JOBS: tuple[Job, ...] = (
    Job(
        name="backup",
        uhrzeit="06:30",
        cron="30 6 * * *",
        kennung="sc_digger.backup run",
        mode=None,
        beschreibung="Sicherung der SQLite-Datenbanken",
    ),
    Job(
        name="audit",
        uhrzeit="06:45",
        cron="45 6 * * *",
        kennung="audit --check-adopted",
        mode=None,
        beschreibung="Übernommen-Abgleich der Sammlung",
    ),
    Job(
        name="taste",
        uhrzeit="07:05",
        cron="5 7 * * *",
        kennung="sc_digger.main taste",
        mode=None,
        beschreibung="Taste-Score trainieren",
    ),
    Job(
        name="canary",
        uhrzeit="07:15",
        cron="15 7 * * *",
        kennung="sc_digger.canary",
        mode="api_schema",
        beschreibung="API-Kanarienvogel",
    ),
    Job(
        name="discover",
        uhrzeit="07:30",
        cron="30 7 * * *",
        kennung="python -m sc_digger.main >>",
        mode="discover",
        beschreibung="Täglicher Discovery-Lauf",
    ),
    Job(
        name="scout",
        uhrzeit="18:00",
        cron="0 18 * * *",
        kennung="sc_digger.main scout",
        mode=None,
        beschreibung="Store-Scout für Bandcamp und Beatport",
    ),
    Job(
        name="watch",
        uhrzeit="19:00",
        cron="0 19 * * 0",
        kennung="sc_digger.watch",
        mode="availability_watch",
        beschreibung="Verfügbarkeits-Wächter",
    ),
    Job(
        name="kaufliste",
        uhrzeit="20:00",
        cron="0 20 * * 0",
        kennung="send_kaufliste",
        mode=None,
        beschreibung="Wöchentlicher Kaufliste-Versand",
    ),
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


def jobs_by_time() -> list[Job]:
    """Tägliche Jobs nach Uhrzeit, danach wöchentliche nach Wochentag (Sonntag zuletzt)."""
    def schluessel(job: Job):
        zerlegt = _parse(job.cron)
        if zerlegt is None:
            return (2, 0, 0, 0)
        minute, stunde, tag = zerlegt
        if tag is None:
            return (0, 0, stunde, minute)
        return (1, (tag - 1) % 7, stunde, minute)

    return sorted(JOBS, key=schluessel)
