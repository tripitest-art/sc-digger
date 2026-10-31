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

    @property
    def type(self) -> str:
        """Alias für `kind` (Feldtyp). `type` ist verbreiteter Name und wird u. a. von
        den Akzeptanztests aus Issue #268 (get_field(...).type) erwartet; der kanonische
        Attributname im Code bleibt `kind`."""
        return self.kind


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
    _f("search.tags", "list", "Genre-Tags",
       "Genre-Tags für die tägliche SoundCloud-Suche (z. B. schranz, hardtechno, industrial techno). Jeder Tag liefert bis zu 'limit_per_tag' Treffer."),
    _f("search.exploration_tags", "list", "Explorations-Tags",
       "Verwandte Rand-Genres (z. B. acid techno, warehouse techno), die gelegentlich per Tag-Rotation zugeschaltet werden."),
    _f("search.exploration_probability", "float", "Wahrscheinlichkeit Exploration",
       "Wahrscheinlichkeit pro Lauf (0.0 = aus bis 1.0 = 100%), dass ein zufälliger Explorationstag zusätzlich durchsucht wird.",
       min=0.0, max=1.0),
    _f("search.bpm_min", "int", "BPM Minimum",
       "Untere Grenze des gewünschten BPM-Fensters (Standard 150). Dient auch als Richtwert für die Oktav-Korrektur (x2/÷2).",
       min=1),
    _f("search.bpm_max", "int", "BPM Maximum",
       "Obere Grenze des gewünschten BPM-Fensters (Standard 165).", min=1),
    _f("search.bpm_unknown_policy", "choice", "Unbekannte BPM",
       "Umgang mit Tracks ohne BPM-Angabe im Freitext: 'keep' (per Audioanalyse prüfen) oder 'drop' (sofort verwerfen).",
       choices=("keep", "drop")),
    _f("search.max_age_days", "int", "Maximales Alter (Tage)",
       "Upload-Zeitfenster in Tagen (Standard 14). Ältere Tracks werden im täglichen Lauf übersprungen.", min=1),
    _f("search.max_duration_min", "int", "Maximale Dauer (Minuten)",
       "Maximale Tracklänge in Minuten (Standard 12). Längere Tracks gelten als DJ-Sets/Podcasts und werden in discover aussortiert.", min=1),
    _f("search.set_title_patterns", "list", "Titelmuster (Regex)",
       "Regex-Muster (z. B. podcast, mix, mixtape) zum Aussortieren von Sets im Titel, selbst wenn die Dauer unter dem Limit liegt."),
    _f("search.limit_per_tag", "int", "Treffer pro Tag",
       "Maximale Trefferanzahl, die SoundCloud api-v2 pro Genre-Tag und Lauf abruft (Standard 100).", min=1),
    _f("search.followed_users", "list", "Gefolgte Profile",
       "Profil-URLs von Künstlern oder Labels, deren Uploads prioritär durchsucht werden."),
    _f("search.reference_accounts", "list", "Referenz-Accounts",
       "Kuratoren & Vorbild-DJs: Reposts und Likes dieser Profile dienen als Signalquelle und erhalten den reference_boost-Bonus."),
    _f("search.reference_limit", "int", "Limit Referenz-Accounts",
       "Maximale Feed-Einträge (Likes/Reposts), die je Referenz-Account abgefragt werden (Standard 50).", min=1),
    # --- scoring ---
    _f("scoring.min_plays", "int", "Mindest-Plays",
       "Mindestanzahl Plays (Standard 300), damit ein Track bewertet wird. Schützt vor statistischen Verzerrungen bei Kleinst-Uploads.", min=0),
    _f("scoring.min_like_ratio", "float", "Mindest-Like-Ratio",
       "Harte Untergrenze für das Verhältnis Likes/Plays (Standard 0.01 = 1%). Spam und minderwertige Tracks fliegen heraus.",
       min=0.0, max=1.0),
    _f("scoring.min_percentile", "float", "Mindest-Perzentil",
       "Mindest-Perzentil im Tagespool (Standard 40 = obere 60%). Nur Tracks oberhalb dieser Schwelle schaffen es in den Digest.",
       min=0.0, max=100.0),
    _f("scoring.weights.like_ratio", "float", "Gewicht Like-Ratio",
       "Gewichtung des Like-Verhältnisses an der Gesamtpunktzahl (Standard 0.35).", min=0.0, max=1.0),
    _f("scoring.weights.repost_ratio", "float", "Gewicht Repost-Ratio",
       "Gewichtung des Repost-Verhältnisses an der Gesamtpunktzahl (Standard 0.25).", min=0.0, max=1.0),
    _f("scoring.weights.velocity", "float", "Gewicht Velocity",
       "Gewichtung des Engagement-Tempos (Likes pro Stunde seit Upload) im Score (Standard 0.20).", min=0.0, max=1.0),
    _f("scoring.weights.comment_ratio", "float", "Gewicht Comment-Ratio",
       "Gewichtung des Kommentar-Verhältnisses im Score (Standard 0.10).", min=0.0, max=1.0),
    _f("scoring.weights.recency", "float", "Gewicht Aktualität",
       "Gewichtung der Frische/Aktualität im Score (Standard 0.10).", min=0.0, max=1.0),
    _f("scoring.reference_boost", "float", "Bonus Referenz-Account",
       "Punktebonus (0-100-Skala), wenn ein Referenz-Account den Track repostet oder geliked hat.",
       min=0.0, max=100.0),
    _f("scoring.spam_penalty", "float", "Abzug Promo-Verdacht",
       "Punkteabzug (Standard 20) bei Erkennung typischer Promo-Phrasen (Repost-Tausch o. ä.).", min=0.0, max=100.0),
    _f("scoring.spam_phrases", "list", "Promo-Phrasen",
       "Textstellen in Titel/Beschreibung/Tags, die auf Repost-Tausch oder Promo-Dienste hindeuten."),
    _f("scoring.blocked_accounts", "list", "Sperrliste",
       "Sperrliste: Uploads dieser Accounts (Profil-URL oder Kurzname) fliegen in discover sofort heraus."),
    _f("scoring.artist_reputation.enabled", "bool", "Artist-Reputation aktiv",
       "Reputation aus deinen historischen Likes, Dislikes und Downloads in das Scoring einbeziehen."),
    _f("scoring.artist_reputation.boost_per_like", "float", "Bonus je Like",
       "Bonuspunkte je 👍 aus Track- oder Store-Feedback (Standard +5.0).", min=0.0),
    _f("scoring.artist_reputation.boost_per_download", "float", "Bonus je Download",
       "Bonuspunkte je Track des Künstlers in deiner Sammlung/Inbox ohne negatives Feedback (Standard +2.0).", min=0.0),
    _f("scoring.artist_reputation.max_boost", "float", "Maximaler Reputations-Bonus",
       "Obergrenze des Reputations-Bonus für einen Künstler (Standard +20.0).", min=0.0),
    _f("scoring.artist_reputation.min_dislikes_for_penalty", "int", "Dislikes bis Malus",
       "Ab so vielen 👎 ohne Likes/Downloads greift der Reputations-Malus (Standard 3).", min=1),
    _f("scoring.artist_reputation.penalty", "float", "Reputations-Malus",
       "Punkteabzug bei negativem Künstlerruf (Standard 15.0).", min=0.0),
    # --- download ---
    _f("download.collection_dir", "str", "Sammlungsordner",
       "Pfad zur bestehenden Master-Sammlung (schreibgeschützt, nur für den Duplikat-Check).",
       editable=False),
    _f("download.inbox_dir", "str", "Inbox-Ordner",
       "Zielordner für geprüfte, pegelnormalisierte und getaggte Downloads.", editable=False),
    _f("download.auto_download_native_only", "bool", "Nur native Downloads",
       "true: nur offizielle SoundCloud-Originale laden; false: zusätzlich Cloud-Links (Dropbox/Drive) automatisch laden."),
    _f("download.cloud_max_mb", "int", "Cloud-Maximalgröße (MB)",
       "Maximale Dateigröße für Downloads aus Cloud-Links in MB (Standard 500 MB).", min=1),
    _f("download.intake_dir", "str", "Eingangsordner",
       "Eingangsordner für manuell am PC geladene Tracks (z. B. aus Gates); durchläuft die volle Pipeline.",
       editable=False),
    _f("download.intake_min_age_s", "int", "Ruhezeit Eingang (s)",
       "Sicherheits-Wartezeit in Sekunden (Standard 120s), bis kopierte/entpackte Dateien im Eingang verarbeitet werden.", min=0),
    # --- organize ---
    _f("organize.enabled", "bool", "Sortieren aktiv",
       "Heruntergeladene Tracks in der Inbox nach BPM/Key einsortieren (in Unterordner <BPM>/<Camelot>/)."),
    _f("organize.bpm_bucket_size", "int", "BPM-Bucket-Größe",
       "Breite der BPM-Ordner in BPM (Standard 5 = 150-155, 155-160 etc.).", min=1),
    _f("organize.default_genre", "str", "Standard-Genre",
       "Standard-Genre für Tags, falls der Uploader kein Genre angegeben hat (Standard: Schranz)."),
    _f("organize.detect_bpm", "bool", "BPM erkennen",
       "Genaue BPM per librosa aus dem Audiomaterial erkennen."),
    _f("organize.detect_key", "bool", "Key erkennen",
       "Musikalische Tonart (Camelot-Key) per Chroma-Analyse aus dem Audio ermitteln."),
    _f("organize.bpm_plausible_min", "int", "Plausibles BPM Minimum",
       "Plausibler Tempobereich: Erkannte Halbtempi (z. B. 78 BPM) werden auf 156 BPM verdoppelt (Standard 120).", min=1),
    _f("organize.bpm_plausible_max", "int", "Plausibles BPM Maximum",
       "Obergrenze für Oktavkorrekturen (Standard 200).", min=1),
    _f("organize.bpm_audio_min_confidence", "float", "Mindest-Konfidenz Audio-BPM",
       "Ab dieser Konfidenz (0 bis 1, Standard 0.6) schlägt die erkannte Audio-BPM eine abweichende "
       "Uploader-Angabe; die Text-BPM bleibt als TEXTBPM im Tag, der Digest markiert sie mit ⚠️. "
       "1.0 lässt die Text-BPM immer gewinnen.", min=0.0, max=1.0),
    _f("organize.write_tags", "bool", "Tags schreiben",
       "ID3- und Vorbis-Tags automatisch in die Zieldatei schreiben (Artist, Title, BPM, Key, Genre, URL, Lautheit). "
       "TKEY/initialkey tragen den Camelot-Key, der Klarname steht in TXXX:INITIALKEY."),
    _f("organize.write_tags_padding_kb", "float", "Tag-Reserve (kB)",
       "Reserve in kB hinter den ID3-Tags (MP3, AIFF, WAV), damit Rekordbox später Cues schreiben kann, "
       "ohne die Datei umzukopieren (Standard 4, 0 = mutagen-Standard).", min=0.0),
    _f("organize.wav_to_flac", "bool", "WAV nach FLAC",
       "WAV-Downloads in der Inbox verlustfrei nach FLAC umwandeln, bevor getaggt wird (Standard aus)."),
    # --- quality ---
    _f("quality.accepted_lossless", "list", "Verlustfreie Formate",
       "Dateiendungen, die als verlustfreies Original akzeptiert werden (Standard: wav, aiff, aif, flac, m4a). "
       "Der Codec muss zusätzlich verlustfrei sein (pcm_*, flac, alac etc.). m4a steht für ALAC; "
       "AAC in .m4a wird weiterhin am Codec abgewiesen."),
    _f("quality.min_mp3_bitrate_kbps", "int", "Mindest-MP3-Bitrate",
       "Mindest-Bitrate für CBR-MP3 in kbps (Standard 320 kbps). Niedrigere Bitraten werden aussortiert.", min=1),
    _f("quality.min_cutoff_hz_for_320", "int", "Spektrum-Grenze für 320er",
       "FFT-Spektrum-Cutoff für MP3 in Hz (Standard 19.000 Hz). Liegt das Spektrum darunter, handelt es sich um einen Fake-Transcode.", min=0),
    _f("quality.min_cutoff_hz_for_lossless", "int", "Spektrum-Grenze verlustfrei",
       "FFT-Spektrum-Cutoff für verlustfreie Dateien in Hz (Standard 19.500 Hz). Erkennt künstlich hochkonvertierte Rips.", min=0),
    _f("quality.min_loudness_range_lu", "float", "Mindest-LRA (LU)",
       "Loudness Range (LRA) in LU (Standard 2.5 LU). Ein kleinerer Dynamikumfang gilt als überkomprimierter Brickwall-Master (_rejected/clipped/).", min=0.0),
    _f("quality.max_true_peak_dbfs", "float", "Maximaler True Peak (dBFS)",
       "Maximal tolerierter Inter-Sample-Peak in dBFS (Standard 3.0 dBFS). Extrem übersteuerte Master landen in _rejected/clipped/."),
    # --- loudness ---
    _f("loudness.normalize_inbox", "bool", "Pegel normalisieren",
       "Neue Downloads in der Inbox auf den Schranz-Zielpegel normalisieren (linear, samplegenau, ohne Limiter, nie die Sammlung)."),
    _f("loudness.target_lufs", "float", "Ziel-Lautheit (LUFS)",
       "Ziel-Lautheit nach EBU R128 in LUFS (Standard -8.5 LUFS). Garantiert pegelgleiche Tracks im DJ-Set."),
    _f("loudness.max_true_peak_dbfs", "float", "True-Peak-Grenze (dBFS)",
       "Headroom-Schutz beim Anheben leiser Tracks in dBFS (Standard -0.5 dBFS True Peak). Verhindert Verzerrung ohne Limiter."),
    # --- fingerprint ---
    _f("fingerprint.check_downloads", "bool", "Fingerprint-Check",
       "Neue Downloads per akustischem Chromaprint-Fingerabdruck mit der Sammlung abgleichen; Dubletten wandern nach _rejected/duplicate/."),
    # --- curator_mining ---
    _f("curator_mining.min_appearances", "int", "Mindest-Auftritte",
       "Mindestanzahl Interaktionen (Likes/Reposts) auf deine 👍-Tracks, damit ein Profil als Kuratoren-Tipp vorgeschlagen wird.",
       min=1),
    _f("curator_mining.max_likers_per_track", "int", "Max. Liker pro Track",
       "API-Begrenzung: Wie viele Liker/Reposter pro Track höchstens abgefragt werden (Standard 50).", min=1),
    _f("curator_mining.min_collection_tracks", "int", "Mindest-Tracks in Sammlung",
       "Mindestanzahl Tracks eines Künstlers in der Sammlung (Archiv/Übernommen), damit er als Curator vorgeschlagen wird (Standard 3).",
       min=1),
    # --- state ---
    _f("state.db_path", "str", "State-DB", "Pfad zur Seen-SQLite-Datenbank (gesehene Tracks, Läufe und Retry-Queue).", editable=False),
    _f("state.track_db_path", "str", "Track-DB",
       "Pfad zur zentralen SQLite-Track-DB (WAL-Modus, nie auf NFS/Netzlaufwerken betreiben).", editable=False),
    # --- preview ---
    _f("preview.stream_enabled", "bool", "Stream-Preview",
       "/preview sc:<SoundCloud-ID>: Erlaubt Snippets direkt aus dem SoundCloud-Stream für nicht heruntergeladene Tracks.",
       editable=False),
    _f("preview.duration_s", "float", "Preview-Dauer",
       "Dauer des Sprachnachrichten-Snippets für /preview in Sekunden (Standard 20.0s).", min=5.0, max=60.0),
    _f("preview.riser_s", "float", "Preview-Riser",
       "Drop-Vorlaufzeit: Startet das Vorschau-Snippet N Sekunden vor dem ersten Drop zum Spannungsaufbau (Standard 5.0s).",
       min=0.0, max=30.0),
    # --- retry ---
    _f("retry.max_attempts", "int", "Download-Versuche",
       "Fehlgeschlagene Original-Downloads in nachfolgenden Läufen erneut versuchen (Gesamtzahl Versuche, Standard 3).",
       min=1),
    # --- health ---
    _f("health.alert_after_bad_runs", "int", "Alarm nach schlechten Läufen",
       "Alarm per Telegram senden, wenn so viele discover-Läufe in Folge scheitern oder 0 Rohtreffer liefern (Standard 2).",
       min=1),
    _f("health.max_hours_since_run", "int", "Max. Stunden seit Lauf",
       "Alarm per Telegram auslösen, wenn der letzte discover-Lauf älter als diese Stundenzahl ist (Standard 36h).", min=1),
    # --- telegram ---
    _f("telegram.max_items_per_digest", "int", "Max. Digest-Einträge",
       "Höchstzahl der Tracks pro täglichem Telegram-Digest (Standard 25).", min=1),
    _f("telegram.feedback_buttons", "bool", "Feedback-Buttons",
       "Interaktive Inline-Buttons (👍/👎/⏳) unter jeder Digest-Nachricht zur Track-Bewertung anzeigen."),
    # --- cues ---
    _f("cues.auto_queue", "bool", "Cue-Jobs automatisch einreihen",
       "Für jeden neuen Inbox-Track einen Cue-Job (JobType.CUES) für den GPU-Worker einreihen; "
       "der Worker lädt die Datei über die Job-API und meldet First Kick, Drops und Breakdown zurück."),
    _f("embeddings.auto_queue", "bool", "Embedding-Jobs automatisch einreihen",
       "Für jeden neuen Inbox-Track einen Embedding-Job (JobType.EMBEDDING) für den GPU-Worker einreihen; "
       "ohne Worker mit Embedding-Modell bleiben die Jobs offen (Standard: aus)."),
    # --- rekordbox ---
    _f("rekordbox.xml_enabled", "bool", "XML-Export aktiv",
       "Rekordbox-XML mit Playlists pro Kalenderwoche automatisch in die Inbox schreiben."),
    _f("rekordbox.xml_path", "str", "XML-Zielpfad",
       "Zielpfad der Rekordbox-XML-Datei (z. B. /music/inbox/sc-digger.xml).", editable=False),
    _f("rekordbox.path_map", "map", "Pfad-Zuordnung",
       "Server-Pfad -> Laptop-Pfad (aus Sicht von Rekordbox auf dem DJ-Laptop).", editable=False),
    _f("rekordbox.cues_enabled", "bool", "Cues exportieren",
       "Ermittelte Cues (First Kick, Main Drop, Breakdowns) in die Rekordbox-XML schreiben."),
    _f("rekordbox.cues_as_hotcues", "bool", "Cues als Hot Cues",
       "Cues als Hot Cues auf die Controller-Pads A–H legen (z. B. Pad A = First Kick [grün], Pad B = Main Drop)."),
    _f("rekordbox.cues_as_memory", "bool", "Cues als Memory Cues",
       "Cues zusätzlich oder alternativ als klassische Rekordbox Memory Cues (Num=-1) schreiben."),
    _f("rekordbox.cues_phrase_align", "bool", "Cues auf Phrasenraster",
       "Drop- und Breakdown-Cues im Rekordbox-Export exakt auf das 16/32-Beat-Phrasengitter ausrichten."),
    _f("rekordbox.hot_cue_layout", "str", "Hot-Cue-Layout (Pads A–H)",
       "Pad-Layout der Hot Cues: 'legacy' (First Kick, Drop, Drop 2, Breakdown) oder 'standard_8pad' "
       "(feste Pads A–H: Main/Late Entry, Drop 1, Percussion Loop bzw. Breakdown, Breakdown, Drop 2, Snare Fill, Outro)."),
    _f("rekordbox.grid_anchor", "bool", "Beatgrid-Anker",
       "Mathematisch exakten Beatgrid-Anker (<TEMPO>) an first_kick_s in die Rekordbox-XML schreiben."),
    _f("rekordbox.phrase_warning", "bool", "Warn-Cue bei Off-Phrase",
       "Roten Memory-Warn-Cue (⚠️ Off-Phrase) an Drops/Breakdowns setzen, die außerhalb des 16/32-Beat-Phrasengitters liegen (nur bei cues_as_memory)."),
    _f("rekordbox.active_loop", "bool", "Active-Loop-Notbremse",
       "Kurz vor Track-Ende einen aktiven Memory Loop setzen, damit lange Übergänge nicht in die Stille laufen."),
    _f("rekordbox.active_loop_beats", "int", "Active-Loop-Länge (Beats)",
       "Länge des Notbremsen-Loops in Beats vor Track-Ende (Standard 16 = 4 Takte à 4 Beats).", min=1),
    _f("rekordbox.playlists_by_bpm", "bool", "Playlists nach BPM",
       "Zusätzlich Playlists pro BPM-Bereich (Ordner sc-digger/BPM) in die Rekordbox-XML schreiben."),
    _f("rekordbox.playlists_by_key", "bool", "Playlists nach Key",
       "Zusätzlich Playlists pro Camelot-Key (Ordner sc-digger/Key) in die Rekordbox-XML schreiben."),
    _f("rekordbox.playlists_by_style", "bool", "Playlists nach Stil",
       "Zusätzlich Playlists nach den 4 Schranz-Matrix-Clustern (Ordner sc-digger/Stile) schreiben."),
    _f("rekordbox.playlists_by_subgenre", "bool", "Playlists nach Subgenre",
       "Zusätzlich Playlists nach den 13 Subgenres (Ordner sc-digger/Subgenres) schreiben."),
    _f("rekordbox.playlists_by_version", "bool", "Playlists nach Version",
       "Zusätzlich Playlists nach Versionstyp (Edit, Bootleg, Tool etc. unter sc-digger/Versionen) schreiben."),
    _f("rekordbox.style_colors", "bool", "Track-Farben nach Stil",
       "Rekordbox-Track-Farbe (Colour) anhand des Versionstyps setzen (Tool=Blau, Edit=Grün, Bootleg=Rot, VIP=Gelb)."),
    _f("rekordbox.style_comments", "bool", "Track-Kommentare nach Stil",
       "Rekordbox-Track-Kommentar im Stil-Format überschreiben (Stil, Tempo, Tonart, Flussachse, DJ-Notiz)."),
    _f("rekordbox.matrix_colors", "bool", "Track-Farben nach Schranz-Matrix",
       "Rekordbox-Track-Farbe (Colour) anhand des Subgenres setzen (Schranz-Matrix: Rot=Industrial/Terror/Crossbreed, Blau=Classic, Grün=Tribal/Hardgroove, Gelb=Melodic, Orange=Neo, Pink=Bounce, Purple=Acid/Psy/Reverse Bass/EBM). Hat Vorrang vor style_colors."),
    _f("rekordbox.compact_style_comments", "bool", "Kompakter Display-Kommentar",
       "Rekordbox-Track-Kommentar kompakt für kleine CDJ/XDJ-Displays überschreiben ([BPM|Key|Substil|D1:Drop1|D2:Drop2|L<LUFS>]). Hat Vorrang vor style_comments."),
    _f("rekordbox.import_xml_path", "str", "Import-XML-Pfad",
       "Optionaler Pfad zur exportierten Rekordbox-XML aus dem Syncthing-Rückkanal vom Laptop. Der tägliche Lauf importiert sie nach dem XML-Export (Rating, Plays, BPM, Key, Cues, Auto-Like); leer = aus.", editable=False),
    _f("rekordbox.import_match_by_name", "bool", "Rückkanal: Namens-Fallback",
       "Dateinamen-Fallback beim Rekordbox-Import; false erfordert exakte Pfad-Treffer über path_map."),
    # --- digest ---
    _f("digest.kaufliste_max_items", "int", "Max. Kaufliste",
       "Maximale Einträge in der wöchentlichen Telegram-Kaufliste für Store-Tracks (Standard 30).", min=0),
    _f("digest.sunday_summary", "bool", "Wochen-Zusammenfassung",
       "Am Ende des sonntäglichen discover-Laufs die Wochenstatistik per Telegram senden."),
    _f("digest.stats_chart", "bool", "Statistik-Diagramm",
       "Dem Wochen-Digest ein Balkendiagramm (PNG, matplotlib) als Foto beilegen."),
    _f("digest.trend_radar", "bool", "Trend-Radar",
       "Trend-Abschnitt ('Trending Artists' & 'Top-Tracks') im Sonntags-Digest anzeigen."),
    _f("digest.trend_radar_days", "int", "Trend-Zeitfenster (Tage)",
       "Betrachtungszeitraum in Tagen für die Trend- und Wachstumsberechnung (Standard 7 Tage).", min=1),
    # --- scout ---
    _f("scout.bandcamp.feeds", "list", "Bandcamp-Feeds",
       "RSS-Feed-URLs von Bandcamp-Labels/Artists (z. B. https://label.bandcamp.com/feed) als automatische Fundquelle."),
    _f("scout.beatport.charts", "list", "Beatport-Charts",
       "Chart-URLs von Beatport (z. B. https://www.beatport.com/genre/hard-techno/2/top-100) als Kauflisten-Quelle."),
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
    "preview": "Live-Preview",
    "retry": "Wiederholung",
    "health": "Health-Alarm",
    "telegram": "Telegram",
    "cues": "Cues & Struktur",
    "rekordbox": "Rekordbox",
    "digest": "Digest",
    "scout": "Multi-Platform-Scout",
    "scout.bandcamp": "Multi-Platform-Scout: Bandcamp",
    "scout.beatport": "Multi-Platform-Scout: Beatport",
}

SECTION_DESCRIPTIONS: dict[str, str] = {
    "search": "Steuert, welche Tags, Profile und Referenz-Kuratoren täglich durchsucht werden, sowie das Alters- und BPM-Suchfenster.",
    "scoring": "Berechnet den genre-relativen Perzentil-Score. Filtert irrelevante Tracks, Promo-Netzwerke und Spam zuverlässig heraus.",
    "scoring.weights": "Feinjustierung der Gewichtung von Likes, Reposts, Spielgeschwindigkeit (Velocity), Kommentaren und Alter im Gesamt-Score.",
    "scoring.artist_reputation": "Berücksichtigt dein bisheriges Feedback (👍/👎) und vorhandene Downloads, um Lieblingskünstler zu belohnen und unpassende Artists abzuwerten.",
    "download": "Bestimmt Download-Pfade, Cloud-Unterstützung (Dropbox/Google Drive) und Sicherheitszeiten für den manuellen Eingangsordner.",
    "organize": "Regelt die Strukturierung der Inbox nach BPM und Tonart sowie das Schreiben von ID3- und Vorbis-Metadaten.",
    "quality": "EBU R128 Lautheitsprüfung und FFT-Spektralanalyse zum zuverlässigen Schutz vor gefakten 320er-MP3s und Transcodes.",
    "loudness": "Lineare, samplegenaue Normalisierung neuer Downloads auf den Schranz-Referenzpegel (-8.5 LUFS) ohne Limiter.",
    "fingerprint": "Akustischer Chromaprint-Abgleich zur Erkennung identischer Aufnahmen vor dem Einsortieren.",
    "curator_mining": "Ermittelt SoundCloud-Profile, die auffällig oft deine gelikten Tracks favorisieren, als neue Kuratoren-Tipps.",
    "state": "Pfade zu den lokalen SQLite-Datenbanken (gesehene Tracks, History und Track-Metadaten).",
    "preview": "Konfiguriert die Dauer und den Drop-Vorlauf für kurze Audio-Vorschauschnipsel (Sprachnachrichten).",
    "retry": "Automatische Wiederholung fehlgeschlagener Downloads in nachfolgenden Läufen.",
    "health": "Schwellenwerte für Telegram-Benachrichtigungen bei Ausfällen oder leerer Trefferausbeute.",
    "telegram": "Einstellungen für den täglichen Digest und interaktive Feedback-Schaltflächen.",
    "cues": "Reiht Cue- und Strukturanalyse-Jobs für neue Inbox-Tracks in die Job-Queue ein, die ein GPU-Worker über die Job-API abarbeitet.",
    "rekordbox": "Export von Wochen-, BPM-, Key- und Stil-Playlists sowie Rekordbox-Cues (Hot Cues A–H, First Kick, Drops).",
    "digest": "Konfiguration der wöchentlichen Kaufliste, Sonntags-Reports und des Wachstums-/Trend-Radars.",
    "scout": "Zusätzliche Multi-Platform-Erkennung von Neuerscheinungen über Bandcamp-Feeds und Beatport-Charts.",
    "scout.bandcamp": "Bandcamp-RSS-Feeds für Neuerscheinungen relevanter Labels und Künstler.",
    "scout.beatport": "Beatport-Chart-Quellen für Track-Empfehlungen in der Kaufliste.",
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
