"""Schema der `config.yaml`: Feldtypen, Wertebereiche, Validierung und Deep-Merge.

Warum es das gibt: Eine spätere Weboberfläche soll Konfigurationswerte ändern, ohne die
versionierte `config.yaml` anzufassen. Dieses Modul beschreibt jedes Blatt der Config als
`Field`, prüft Werte vor einem Lauf und mischt eine lokale Override-Datei über die Basis.
Die Validierung läuft nur auf Zuruf (`python -m sc_digger.schema`), nie automatisch beim
Start – so ändert sich am Betrieb ohne Override nichts.
"""
from __future__ import annotations

import argparse
import copy
import re
import sys
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class Field:
    path: str                       # Punkt-Pfad, z. B. "scoring.weights.like_ratio"
    kind: str                       # "bool" | "int" | "float" | "str" | "choice" | "list" | "map"
    label: str                      # kurze deutsche Bezeichnung für ein Formular
    help: str                       # Erklärung, sinngemäß aus dem Kommentar in config.yaml
    min: float | None = None        # nur int/float
    max: float | None = None        # nur int/float
    choices: tuple[str, ...] = ()   # nur choice
    editable: bool = True           # False für Betriebspfade


@dataclass(frozen=True)
class Problem:
    level: str      # "error" | "warning"
    path: str       # Punkt-Pfad des betroffenen Feldes ("" bei Fehlern der obersten Ebene)
    message: str    # deutsch, ein Satz


def _f(path: str, kind: str, label: str, help: str, *, min: float | None = None,
       max: float | None = None, choices: tuple[str, ...] = (),
       editable: bool = True) -> Field:
    return Field(path=path, kind=kind, label=label, help=help, min=min, max=max,
                 choices=choices, editable=editable)


# EIN Eintrag je Blatt-Schlüssel der config.yaml, eindeutige paths. Reihenfolge entspricht
# der config.yaml, damit ein Formular sie direkt übernehmen kann.
FIELDS: tuple[Field, ...] = (
    # --- search ---
    _f("search.tags", "list", "Genre-Tags", "Genre-Tags, die pro Lauf durchsucht werden."),
    _f("search.exploration_tags", "list", "Explorations-Tags",
       "Benachbarte Tags, die nur gelegentlich (Tag-Rotation) durchsucht werden."),
    _f("search.exploration_probability", "float", "Wahrscheinlichkeit Exploration",
       "Wahrscheinlichkeit pro Lauf, dass ein Explorations-Tag zusätzlich durchsucht wird.",
       min=0.0, max=1.0),
    _f("search.bpm_min", "int", "BPM Minimum",
       "Untere Grenze des BPM-Fensters; fehlende BPM werden geschätzt, nicht aussortiert.",
       min=1),
    _f("search.bpm_max", "int", "BPM Maximum",
       "Obere Grenze des BPM-Fensters.", min=1),
    _f("search.bpm_unknown_policy", "choice", "Unbekannte BPM",
       "Umgang mit Tracks ohne BPM-Angabe: behalten oder verwerfen.",
       choices=("keep", "drop")),
    _f("search.max_age_days", "int", "Maximales Alter (Tage)",
       "Nur Tracks der letzten N Tage berücksichtigen.", min=1),
    _f("search.max_duration_min", "int", "Maximale Dauer (Minuten)",
       "Längere Tracks gelten als DJ-Sets und werden in discover aussortiert.", min=1),
    _f("search.set_title_patterns", "list", "Titelmuster (Regex)",
       "Regex-Muster für kurze Mixe/Podcasts, die discover zusätzlich aussortiert."),
    _f("search.limit_per_tag", "int", "Treffer pro Tag",
       "Pro Tag maximal so viele Treffer abrufen.", min=1),
    _f("search.followed_users", "list", "Gefolgte Profile",
       "Zusätzliche Quellen: Uploads von Künstlern/Labels, denen du folgst."),
    _f("search.reference_accounts", "list", "Referenz-Accounts",
       "Profile, deren Reposts/Likes als Signalquelle dienen und einen Score-Bonus bekommen."),
    _f("search.reference_limit", "int", "Limit Referenz-Accounts",
       "Wie viele Aktivitäten je Referenz-Account höchstens abgefragt werden.", min=1),
    # --- scoring ---
    _f("scoring.min_plays", "int", "Mindest-Plays",
       "Mindestanzahl Plays, damit ein Track bewertet wird.", min=0),
    _f("scoring.min_like_ratio", "float", "Mindest-Like-Ratio",
       "Harte Untergrenze für das Verhältnis Likes/Plays, damit Spam rausfliegt.",
       min=0.0, max=1.0),
    _f("scoring.min_percentile", "float", "Mindest-Perzentil",
       "Score muss im oberen Bereich der Kandidatenmenge liegen (Genre-relative Bewertung).",
       min=0.0, max=100.0),
    _f("scoring.weights.like_ratio", "float", "Gewicht Like-Ratio",
       "Gewicht des Like-Verhältnisses im Score.", min=0.0, max=1.0),
    _f("scoring.weights.repost_ratio", "float", "Gewicht Repost-Ratio",
       "Gewicht des Repost-Verhältnisses im Score.", min=0.0, max=1.0),
    _f("scoring.weights.velocity", "float", "Gewicht Velocity",
       "Gewicht der Likes pro Stunde seit Upload im Score.", min=0.0, max=1.0),
    _f("scoring.weights.comment_ratio", "float", "Gewicht Comment-Ratio",
       "Gewicht des Kommentar-Verhältnisses im Score.", min=0.0, max=1.0),
    _f("scoring.weights.recency", "float", "Gewicht Aktualität",
       "Gewicht der Aktualität im Score.", min=0.0, max=1.0),
    _f("scoring.reference_boost", "float", "Bonus Referenz-Account",
       "Bonuspunkte (0-100-Skala), wenn ein Referenz-Account den Track repostet/liked.",
       min=0.0, max=100.0),
    _f("scoring.spam_penalty", "float", "Abzug Promo-Verdacht",
       "Abzugspunkte (0-100-Skala) bei Promo-Netzwerk-Verdacht.", min=0.0, max=100.0),
    _f("scoring.spam_phrases", "list", "Promo-Phrasen",
       "Textstellen in Titel/Beschreibung/Tags, die auf Repost-Tausch oder Promo-Dienste hindeuten."),
    _f("scoring.blocked_accounts", "list", "Sperrliste",
       "Uploads dieser Accounts (Profil-URL oder Kurzname) fliegen in discover raus."),
    _f("scoring.artist_reputation.enabled", "bool", "Artist-Reputation aktiv",
       "Reputation aus der Track-DB in das Scoring einbeziehen."),
    _f("scoring.artist_reputation.boost_per_like", "float", "Bonus je Like",
       "Bonuspunkte je 👍 aus Track- oder Store-Feedback.", min=0.0),
    _f("scoring.artist_reputation.boost_per_download", "float", "Bonus je Download",
       "Bonuspunkte je Track in Sammlung/Inbox ohne Feedback.", min=0.0),
    _f("scoring.artist_reputation.max_boost", "float", "Maximaler Reputations-Bonus",
       "Obergrenze des Reputations-Bonus.", min=0.0),
    _f("scoring.artist_reputation.min_dislikes_for_penalty", "int", "Dislikes bis Malus",
       "Ab so vielen 👎 ohne Likes/Downloads greift der Malus.", min=1),
    _f("scoring.artist_reputation.penalty", "float", "Reputations-Malus",
       "Abzug bei Malus.", min=0.0),
    # --- download ---
    _f("download.collection_dir", "str", "Sammlungsordner",
       "Ordner mit der bestehenden Sammlung (nur für den Duplikat-Check).",
       editable=False),
    _f("download.inbox_dir", "str", "Inbox-Ordner",
       "Zielordner für automatisch geladene, geprüfte Tracks.", editable=False),
    _f("download.auto_download_native_only", "bool", "Nur native Downloads",
       "true: nur native SoundCloud-Originale laden; false schaltet Cloud-Downloads ein."),
    _f("download.cloud_max_mb", "int", "Cloud-Maximalgröße (MB)",
       "Maximale Dateigröße für Cloud-Downloads.", min=1),
    _f("download.intake_dir", "str", "Eingangsordner",
       "Manuell geladene Tracks (z. B. aus Gates) werden wie ein Download verarbeitet.",
       editable=False),
    _f("download.intake_min_age_s", "int", "Ruhezeit Eingang (s)",
       "Dateien erst verarbeiten, wenn sie so lange unverändert sind.", min=0),
    # --- organize ---
    _f("organize.enabled", "bool", "Sortieren aktiv",
       "Heruntergeladene Tracks in der Inbox nach BPM/Key sortieren (nie im Archiv)."),
    _f("organize.bpm_bucket_size", "int", "BPM-Bucket-Größe",
       "BPM-Bucket-Größe für die Ordnerstruktur (z. B. 5 -> 150-155).", min=1),
    _f("organize.default_genre", "str", "Standard-Genre", "Standard-Genre für Tags."),
    _f("organize.detect_bpm", "bool", "BPM erkennen",
       "BPM per librosa aus dem Audio erkennen."),
    _f("organize.detect_key", "bool", "Key erkennen",
       "Tonart per librosa aus dem Audio erkennen."),
    _f("organize.bpm_plausible_min", "int", "Plausibles BPM Minimum",
       "Untergrenze der Genre-Plausibilität für die Oktav-Korrektur.", min=1),
    _f("organize.bpm_plausible_max", "int", "Plausibles BPM Maximum",
       "Obergrenze der Genre-Plausibilität für die Oktav-Korrektur.", min=1),
    _f("organize.write_tags", "bool", "Tags schreiben",
       "ID3/Vorbis-Tags automatisch schreiben (Artist, Title, BPM, Key, Genre, URL)."),
    # --- quality ---
    _f("quality.accepted_lossless", "list", "Verlustfreie Formate",
       "Dateiendungen, die als verlustfrei akzeptiert werden."),
    _f("quality.min_mp3_bitrate_kbps", "int", "Mindest-MP3-Bitrate",
       "Mindestbitrate für MP3 in kbps.", min=1),
    _f("quality.min_cutoff_hz_for_320", "int", "Spektrum-Grenze für 320er",
       "Untergrenze des Spektrum-Cutoffs, damit ein 320er als echt gilt.", min=0),
    _f("quality.min_cutoff_hz_for_lossless", "int", "Spektrum-Grenze verlustfrei",
       "Untergrenze des Spektrum-Cutoffs für verlustfreie Dateien.", min=0),
    _f("quality.min_loudness_range_lu", "float", "Mindest-LRA (LU)",
       "Kleinerer Dynamikumfang gilt als Brickwall-Master.", min=0.0),
    _f("quality.max_true_peak_dbfs", "float", "Maximaler True Peak (dBFS)",
       "Obergrenze des True Peaks; darüber gilt der Track als übersteuert."),
    # --- loudness ---
    _f("loudness.normalize_inbox", "bool", "Pegel normalisieren",
       "Neue Downloads in der Inbox auf den Zielpegel bringen (nie die Sammlung)."),
    _f("loudness.target_lufs", "float", "Ziel-Lautheit (LUFS)",
       "Ziel-Lautheit nach EBU R128 in LUFS."),
    _f("loudness.max_true_peak_dbfs", "float", "True-Peak-Grenze (dBFS)",
       "Maximaler True-Peak beim Anheben leiser Tracks (Headroom-Schutz ohne Limiter)."),
    # --- fingerprint ---
    _f("fingerprint.check_downloads", "bool", "Fingerprint-Check",
       "Neue Downloads per Chromaprint mit der Sammlung abgleichen."),
    # --- curator_mining ---
    _f("curator_mining.min_appearances", "int", "Mindest-Auftritte",
       "Mindestanzahl Auftritte über alle 👍-Tracks, damit ein Profil vorgeschlagen wird.",
       min=1),
    _f("curator_mining.max_likers_per_track", "int", "Max. Liker pro Track",
       "Wie viele Liker/Reposter pro Track höchstens abgefragt werden.", min=1),
    # --- state ---
    _f("state.db_path", "str", "State-DB", "Pfad zur Seen-SQLite-Datenbank.", editable=False),
    _f("state.track_db_path", "str", "Track-DB",
       "Pfad zur Track-SQLite-Datenbank (nie auf NFS, da WAL-Modus).", editable=False),
    # --- retry ---
    _f("retry.max_attempts", "int", "Download-Versuche",
       "Fehlgeschlagene Original-Downloads beim nächsten Lauf erneut versuchen (Versuche gesamt).",
       min=1),
    # --- health ---
    _f("health.alert_after_bad_runs", "int", "Alarm nach schlechten Läufen",
       "Alarm per Telegram, wenn so viele discover-Läufe in Folge scheitern oder leer sind.",
       min=1),
    _f("health.max_hours_since_run", "int", "Max. Stunden seit Lauf",
       "Alarm, wenn der letzte Lauf älter als diese Stundenzahl ist.", min=1),
    # --- telegram ---
    _f("telegram.max_items_per_digest", "int", "Max. Digest-Einträge",
       "Höchstzahl der Tracks pro Telegram-Digest.", min=1),
    _f("telegram.feedback_buttons", "bool", "Feedback-Buttons",
       "Inline-Buttons (👍/👎/⏳) unter jeder Digest-Nachricht anzeigen."),
    # --- rekordbox ---
    _f("rekordbox.xml_enabled", "bool", "XML-Export aktiv",
       "Rekordbox-XML mit Playlists pro Woche in die Inbox schreiben."),
    _f("rekordbox.xml_path", "str", "XML-Zielpfad",
       "Zielpfad der Rekordbox-XML.", editable=False),
    _f("rekordbox.path_map", "map", "Pfad-Zuordnung",
       "Server-Pfad -> Laptop-Pfad (aus Sicht von Rekordbox).", editable=False),
    # --- digest ---
    _f("digest.kaufliste_max_items", "int", "Max. Kaufliste",
       "Maximale Einträge in der wöchentlichen Kaufliste.", min=0),
    _f("digest.sunday_summary", "bool", "Wochen-Zusammenfassung",
       "Am Ende des sonntäglichen discover-Laufs die Wochenstatistik senden."),
    _f("digest.stats_chart", "bool", "Statistik-Diagramm",
       "Dem Wochen-Digest ein Balkendiagramm (PNG, matplotlib) als Foto beilegen."),
    _f("digest.trend_radar", "bool", "Trend-Radar",
       "Trend-Abschnitt (Trending Artists / Top-Tracks) im Wochen-Digest anzeigen."),
    _f("digest.trend_radar_days", "int", "Trend-Zeitfenster (Tage)",
       "Zeitfenster für die Trend-Berechnung im Wochen-Digest.", min=1),
    # --- scout ---
    _f("scout.bandcamp.feeds", "list", "Bandcamp-Feeds",
       "RSS-Feed-URLs von Bandcamp-Labels/Artists als zusätzliche Fundquelle."),
    _f("scout.beatport.charts", "list", "Beatport-Charts",
       "Chart-URLs von Beatport als zusätzliche Fundquelle."),
)

SECTION_TITLES: dict[str, str] = {
    "search": "Suche",
    "scoring": "Bewertung",
    "scoring.weights": "Bewertung: Gewichte",
    "scoring.artist_reputation": "Bewertung: Artist-Reputation",
    "download": "Download",
    "organize": "Sortieren & Taggen",
    "quality": "Qualitätsprüfung",
    "loudness": "Lautheit",
    "fingerprint": "Fingerabdruck",
    "curator_mining": "Curator-Mining",
    "state": "Zustand",
    "retry": "Wiederholung",
    "health": "Health-Alarm",
    "telegram": "Telegram",
    "rekordbox": "Rekordbox",
    "digest": "Digest",
    "scout": "Multi-Platform-Scout",
    "scout.bandcamp": "Multi-Platform-Scout: Bandcamp",
    "scout.beatport": "Multi-Platform-Scout: Beatport",
}

_FIELD_BY_PATH: dict[str, Field] = {f.path: f for f in FIELDS}
_SECTION_PATHS: frozenset[str] = frozenset(
    ".".join(f.path.split(".")[: i])
    for f in FIELDS
    for i in range(1, len(f.path.split(".")))
)

_KIND_NAMES = {
    "bool": "Wahrheitswert (true/false)",
    "int": "ganze Zahl",
    "float": "Zahl",
    "str": "Text",
    "choice": "erlaubter Wert",
    "list": "Liste aus Text",
    "map": "Zuordnung Text -> Text",
}


def get_field(path: str) -> Field | None:
    """Liefert das Feld zum Punkt-Pfad oder None."""
    return _FIELD_BY_PATH.get(path)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _leaf_problems(field: Field, value: Any) -> list[Problem]:
    """Typprüfung, Wertebereich, choices und Regex für ein einzelnes Blatt."""
    path, kind = field.path, field.kind
    if kind == "bool":
        if not isinstance(value, bool):
            return [Problem("error", path, f"Erwartet {_KIND_NAMES[kind]}, bekommen {type(value).__name__}.")]
    elif kind == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            return [Problem("error", path, f"Erwartet {_KIND_NAMES[kind]}, bekommen {type(value).__name__}.")]
    elif kind == "float":
        if not _is_number(value):
            return [Problem("error", path, f"Erwartet {_KIND_NAMES[kind]}, bekommen {type(value).__name__}.")]
    elif kind == "str":
        if not isinstance(value, str):
            return [Problem("error", path, f"Erwartet {_KIND_NAMES[kind]}, bekommen {type(value).__name__}.")]
    elif kind == "choice":
        if not isinstance(value, str):
            return [Problem("error", path, f"Erwartet {_KIND_NAMES[kind]}, bekommen {type(value).__name__}.")]
        if value not in field.choices:
            erlaubt = ", ".join(field.choices)
            return [Problem("error", path, f"Wert {value!r} ist nicht erlaubt (erlaubt: {erlaubt}).")]
    elif kind == "list":
        if not isinstance(value, list):
            return [Problem("error", path, f"Erwartet {_KIND_NAMES[kind]}, bekommen {type(value).__name__}.")]
        if not all(isinstance(x, str) for x in value):
            return [Problem("error", path, "Listeneinträge müssen Text sein.")]
        if path == "search.set_title_patterns":
            return _regex_problems(path, value)
    elif kind == "map":
        if not isinstance(value, Mapping):
            return [Problem("error", path, f"Erwartet {_KIND_NAMES[kind]}, bekommen {type(value).__name__}.")]
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            return [Problem("error", path, "Zuordnung muss Text auf Text abbilden.")]
    else:  # pragma: no cover - durch test_fields_are_unique_and_documented abgesichert
        return [Problem("error", path, f"Unbekannter Feldtyp {kind!r}.")]

    if kind in ("int", "float"):
        if field.min is not None and value < field.min:
            return [Problem("error", path, f"Wert {value} ist kleiner als das Minimum {field.min}.")]
        if field.max is not None and value > field.max:
            return [Problem("error", path, f"Wert {value} ist größer als das Maximum {field.max}.")]
    return []


def _regex_problems(path: str, patterns: list[str]) -> list[Problem]:
    problems: list[Problem] = []
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            problems.append(Problem("error", path, f"Ungültiger regulärer Ausdruck {pattern!r}: {exc}."))
    return problems


def _walk(mapping: Mapping[str, Any], prefix: str, problems: list[Problem]) -> None:
    """Rekursiv durch die Config; unbekannte Blätter/Abschnitte werden zu Warnungen."""
    for key, value in mapping.items():
        path = f"{prefix}.{key}" if prefix else key
        field = _FIELD_BY_PATH.get(path)
        if field is not None:
            problems.extend(_leaf_problems(field, value))
            continue
        if path in _SECTION_PATHS:
            if not isinstance(value, Mapping):
                problems.append(Problem("error", path, "Abschnitt muss eine Zuordnung sein."))
            else:
                _walk(value, path, problems)
            continue
        problems.append(Problem("warning", path, f"Unbekannter Schlüssel {path!r}."))


def _cross_field_problems(raw: Mapping[str, Any]) -> list[Problem]:
    problems: list[Problem] = []
    search = raw.get("search")
    if isinstance(search, Mapping):
        lo, hi = search.get("bpm_min"), search.get("bpm_max")
        if _is_number(lo) and _is_number(hi) and lo > hi:
            problems.append(Problem("error", "search.bpm_min",
                                    "search.bpm_min liegt über search.bpm_max."))
    organize = raw.get("organize")
    if isinstance(organize, Mapping):
        lo, hi = organize.get("bpm_plausible_min"), organize.get("bpm_plausible_max")
        if _is_number(lo) and _is_number(hi) and lo >= hi:
            problems.append(Problem("error", "organize.bpm_plausible_min",
                                    "organize.bpm_plausible_min muss kleiner als "
                                    "organize.bpm_plausible_max sein."))
    scoring = raw.get("scoring")
    if isinstance(scoring, Mapping):
        weights = scoring.get("weights")
        if isinstance(weights, Mapping) and all(_is_number(v) for v in weights.values()):
            total = sum(weights.values())
            if abs(total - 1.0) > 0.01:
                problems.append(Problem("warning", "scoring.weights",
                                        f"Summe der Gewichte ist {total:.2f}, erwartet 1.0."))
    return problems


def validate_config(raw: Mapping[str, Any]) -> list[Problem]:
    """Prüft die (zusammengeführte) Config. Verändert raw nie. Fehlende Schlüssel sind kein Problem."""
    if not isinstance(raw, Mapping):
        return [Problem("error", "", "Die Konfiguration muss eine Zuordnung sein.")]
    problems: list[Problem] = []
    _walk(raw, "", problems)
    problems.extend(_cross_field_problems(raw))
    return problems


def deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Neues dict. Zuordnungen werden rekursiv gemischt; Listen und Skalare aus override ersetzen
    den Wert aus base. Keine Eingabe wird verändert, und das Ergebnis teilt keine veränderlichen
    Objekte mit den Eingaben (deepcopy)."""
    result: dict[str, Any] = {key: copy.deepcopy(value) for key, value in base.items()}
    for key, value in override.items():
        if key in result and isinstance(result[key], Mapping) and isinstance(value, Mapping):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def main(argv: list[str] | None = None) -> int:
    """python -m sc_digger.schema [--config config.yaml] [--local PFAD]"""
    parser = argparse.ArgumentParser(prog="python -m sc_digger.schema",
                                     description="Konfiguration prüfen")
    parser.add_argument("--config", default="config.yaml", help="Pfad zur config.yaml")
    parser.add_argument("--local", default=None, help="Pfad zur lokalen Override-Datei")
    args = parser.parse_args(argv)

    from sc_digger.models import Config  # lokal, um einen Importzyklus zu vermeiden

    try:
        cfg = Config.load(args.config, local=args.local)
    except Exception as exc:  # laut scheitern (Regel 7), Meldung nennt die Datei
        print(f"FEHLER {exc}")
        return 1

    problems = validate_config(cfg.raw)
    for problem in problems:
        prefix = "FEHLER" if problem.level == "error" else "WARNUNG"
        print(f"{prefix} {problem.path}: {problem.message}")
    if not problems:
        print("OK: Konfiguration gültig")
    return 1 if any(p.level == "error" for p in problems) else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
