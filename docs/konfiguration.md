# Konfiguration

Alle Optionen stehen in `config.yaml`; die folgende Liste nennt die Werte aus dieser Datei.
Fehlende optionale Schlüssel fallen auf die Defaults im Code zurück.

| Schlüssel | Bedeutung | Standard |
|---|---|---|
| `search.tags` | Genre-Tags, die pro Lauf durchsucht werden | `[schranz, hardtechno, industrial techno, hard techno]` |
| `search.exploration_tags` | benachbarte Tags, die nur gelegentlich durchsucht werden | `[acid techno, warehouse techno]` |
| `search.exploration_probability` | Wahrscheinlichkeit pro Lauf, dass ein Explorations-Tag dazukommt | `0.0` |
| `search.bpm_min` / `search.bpm_max` | BPM-Fenster für Filter und BPM-Oktav-Korrektur | `150` / `165` |
| `search.bpm_unknown_policy` | Umgang mit Tracks ohne BPM-Angabe (`keep` / `drop`) | `keep` |
| `search.max_age_days` | nur Tracks der letzten N Tage | `14` |
| `search.max_duration_min` | maximale Trackdauer in Minuten (DJ-Set-Schwelle) | `12` |
| `search.set_title_patterns` | Regex-Muster (Groß/Klein egal) für kurze Mixe/Podcasts in `discover` | siehe Datei |
| `search.limit_per_tag` | maximale Treffer pro Tag und Lauf | `100` |
| `search.followed_users` | Profil-URLs, deren Uploads zusätzlich durchsucht werden | `[]` |
| `search.reference_accounts` | Profile, deren Reposts/Likes Treffer mit Bonus liefern | siehe Datei |
| `search.reference_limit` | maximale Feed-Einträge pro Referenz-Account | `50` |
| `scoring.min_plays` | Mindest-Playzahl, damit ein Track bewertet wird | `300` |
| `scoring.min_like_ratio` | harte Untergrenze für das Like-Verhältnis | `0.01` |
| `scoring.min_percentile` | Mindest-Perzentil im Kandidatenpool | `40` |
| `scoring.weights.*` | Gewichte `like_ratio`, `repost_ratio`, `velocity`, `comment_ratio`, `recency` | `0.35 / 0.25 / 0.20 / 0.10 / 0.10` |
| `scoring.reference_boost` | Bonus auf der 0–100-Skala für Referenz-Hits | `15` |
| `scoring.spam_penalty` | Malus bei Promo-Verdacht | `20` |
| `scoring.spam_phrases` | Phrasen, die auf Repost-Tausch/Promo hindeuten | siehe Datei |
| `scoring.blocked_accounts` | Uploads dieser Profile werden aussortiert | `[]` |
| `scoring.artist_reputation.*` | Reputation aus bewerteten Tracks: `enabled`, `boost_per_like`, `boost_per_download`, `boost_per_star`, `max_boost`, `min_dislikes_for_penalty`, `penalty` | `true`, `5.0`, `2.0`, `2.0`, `20.0`, `3`, `15.0` |
| `download.collection_dir` | Pfad zur Sammlung (Duplikat-Check, Audit) | `/music/Schranz` |
| `download.inbox_dir` | Zielordner für geprüfte Downloads | `/music/inbox` |
| `download.auto_download_native_only` | nur native Originale laden; `false` schaltet Cloud-Downloads ein | `true` |
| `download.cloud_max_mb` | maximale Größe für Cloud-Downloads (MB) | `500` |
| `download.intake_dir` | Eingangsordner für manuell geladene Tracks | `/music/inbox/_eingang` |
| `download.intake_min_age_s` | Mindestalter unveränderter Dateien im Eingang | `120` |
| `organize.enabled` | Inbox nach BPM/Key einsortieren | `true` |
| `organize.bpm_bucket_size` | Breite der BPM-Ordner | `5` |
| `organize.default_genre` | Genre, das geschrieben wird, wenn der Track keines liefert | `Schranz` |
| `organize.detect_bpm` / `organize.detect_key` | BPM und Key via librosa erkennen | `true` / `true` |
| `organize.bpm_plausible_min` / `organize.bpm_plausible_max` | plausibler Genre-Bereich für die Oktav-Korrektur | `120` / `200` |
| `organize.bpm_audio_min_confidence` | ab dieser Konfidenz (0–1) schlägt die Audio-BPM eine abweichende Uploader-Angabe; `1.0` = Text-BPM gewinnt immer | `0.6` |
| `organize.write_tags` | Tags für Artist, Titel, BPM, Key, Genre und URL schreiben; `TKEY`/`initialkey` tragen den Camelot-Key, der Klarname steht in `TXXX:INITIALKEY` | `true` |
| `organize.write_tags_padding_kb` | Reserve in kB hinter den ID3-Tags (MP3, AIFF, WAV), damit Rekordbox später Cues schreiben kann, ohne die Datei umzukopieren; `0` = mutagen-Standard | `4` |
| `organize.wav_to_flac` | WAV-Downloads in der Inbox vor dem Taggen verlustfrei nach FLAC umwandeln (WAV trägt keine brauchbaren Tags) | `false` |
| `quality.accepted_lossless` | als verlustfrei akzeptierte Endungen | `[wav, aiff, aif, flac, m4a]` |
| `quality.min_mp3_bitrate_kbps` | Mindestbitrate für CBR-MP3 | `320` |
| `quality.min_cutoff_hz_for_320` | Cutoff-Grenze für MP3-Container | `19000` |
| `quality.min_cutoff_hz_for_lossless` | Cutoff-Grenze für verlustfreie Container | `19500` |
| `quality.min_loudness_range_lu` | untere LRA-Grenze (Brickwall-Verdacht) | `2.5` |
| `quality.max_true_peak_dbfs` | obere True-Peak-Grenze (Brickwall-Verdacht) | `3.0` |
| `loudness.normalize_inbox` | Pegel der neuen Inbox-Dateien anpassen | `true` |
| `loudness.target_lufs` | Ziel-Lautheit in LUFS | `-8.5` |
| `loudness.max_true_peak_dbfs` | True-Peak-Grenze beim Anheben | `-0.5` |
| `fingerprint.check_downloads` | neue Downloads gegen die Sammlung abgleichen | `true` |
| `curator_mining.min_appearances` | Mindestzahl Treffer, damit ein Profil vorgeschlagen wird | `2` |
| `curator_mining.max_likers_per_track` | maximale API-Abfragen pro Track | `50` |
| `curator_mining.min_collection_tracks` | Mindestanzahl Tracks in der Sammlung für Sammlungs-Favoriten | `3` |
| `preview.duration_s` | Dauer des Snippets in Sekunden | `20.0` |
| `preview.riser_s` | Vorlaufzeit vor Drop in Sekunden | `5.0` |
| `state.db_path` | SQLite-DB für gesehene Tracks, Läufe und Retry-Queue | `/data/seen.sqlite` |
| `state.track_db_path` | SQLite-Track-DB (WAL-Modus, nicht auf NFS) | `/data/tracks.sqlite` |
| `retry.max_attempts` | Gesamtzahl der Versuche pro Original-Download | `3` |
| `health.alert_after_bad_runs` | Alarm nach N erfolglosen Läufen in Folge | `2` |
| `health.max_hours_since_run` | Alarm, wenn der letzte Lauf älter ist als dieser Wert | `36` |
| `telegram.max_items_per_digest` | Obergrenze der Tracks im Digest (nicht in `playlist`/`check`) | `25` |
| `telegram.feedback_buttons` | Inline-Buttons für Feedback anzeigen | `true` |
| `cues.auto_queue` | Cue-Jobs für neue Inbox-Tracks in die Queue legen (GPU-Worker) | `true` |
| `embeddings.auto_queue` | Embedding-Jobs für neue Inbox-Tracks in die Queue legen (GPU-Worker) | `false` |
| `rekordbox.xml_enabled` | Rekordbox-XML schreiben | `true` |
| `rekordbox.xml_path` | Zielpfad der XML | `/music/inbox/sc-digger.xml` |
| `rekordbox.path_map` | Mapping Server-Pfad zu Laptop-Pfad | `/music/inbox` → `Z:/Highres/_sc-digger-inbox` |
| `rekordbox.cues_enabled` | Memory Cue „First Kick“ aus dem Tag `SCDIGGER_FIRST_KICK` in die XML schreiben | `true` |
| `rekordbox.cues_as_hotcues` | Cues als Hot Cues A–H schreiben | `true` |
| `rekordbox.cues_as_memory` | Cues als Memory Cues schreiben | `true` |
| `rekordbox.cues_phrase_align` | Drop und Breakdown am 16/32-Beat-Phrasengitter ausrichten | `true` |
| `rekordbox.hot_cue_layout` | Pad-Layout der Hot Cues (`legacy` oder `standard_8pad`) | `legacy` |
| `rekordbox.grid_anchor` | Beatgrid-Anker `<TEMPO>` an `first_kick_s` setzen (verlässliches Beatgrid bei 150+ BPM) | `true` |
| `rekordbox.phrase_warning` | Warn-Cues an Drops/Breakdowns setzen, die außerhalb des Phrasengitters liegen | `true` |
| `rekordbox.active_loop` | Automatischen Active Loop am Outro-Start anlegen | `false` |
| `rekordbox.active_loop_beats` | Länge des Active Loops in Beats | `32` |
| `rekordbox.playlists_by_bpm` | Zusätzliche Playlists pro BPM-Bereich (Ordner BPM) | `true` |
| `rekordbox.playlists_by_key` | Zusätzliche Playlists pro Camelot-Key (Ordner Key) | `true` |
| `rekordbox.playlists_by_style` | Playlists nach Hauptclustern der Schranz-Matrix anlegen | `false` |
| `rekordbox.playlists_by_subgenre` | Playlists nach Subgenres der Schranz-Matrix anlegen | `false` |
| `rekordbox.playlists_by_version` | Playlists nach Versionstypen anlegen | `false` |
| `rekordbox.style_colors` | Track-Farbe nach Versionstyp setzen | `false` |
| `rekordbox.matrix_colors` | Track-Farbe aus Hauptclustern der Schranz-Matrix ableiten | `false` |
| `rekordbox.style_comments` | Track-Kommentar im Stil-Format überschreiben | `false` |
| `rekordbox.compact_style_comments` | Kompakte Stil-Kürzel in Track-Kommentare schreiben | `false` |
| `rekordbox.import_xml_path` | Pfad zur exportierten Rekordbox-XML als Rückkanal — der tägliche Lauf importiert sie nach dem XML-Export (siehe [Rekordbox](funktionen.md#rekordbox)); leer = aus | `""` |
| `rekordbox.import_match_by_name` | Dateinamen-Fallback beim Rekordbox-Import (aus: nur exakte Pfad-Treffer über path_map) | `true` |
| `digest.kaufliste_max_items` | Obergrenze der Kaufliste | `30` |
| `digest.sunday_summary` | Wochenstatistik im Sonntagslauf senden | `false` |
| `digest.stats_chart` | Balkendiagramm zum Wochen-Digest | `false` |
| `digest.trend_radar` | Trend-Abschnitt (Trending Artists / Top-Tracks) im Wochen-Digest | `true` |
| `digest.trend_radar_days` | Zeitfenster der Trend-Berechnung in Tagen | `7` |
| `watch.enabled` | Wöchentlichen Verfügbarkeits-Wächter aktivieren | `true` |
| `watch.max_age_days` | Maximales Alter von 👍-Kandidaten in Tagen (1..730, Standard 180) | `180` |
| `scout.bandcamp.feeds` | URLs für Bandcamp-RSS-Feeds von Labels und Artists | `[]` |
| `scout.beatport.charts` | URLs für Beatport-Charts | `[]` |

## Lokale Override-Datei

Die versionierte `config.yaml` bleibt die einzige Wahrheit. Änderungen ohne Git – später aus der
Weboberfläche – legt `Config.load` per Deep-Merge aus einer optionalen lokalen Override-Datei
darüber. Den Pfad nennt die Umgebungsvariable `SC_DIGGER_CONFIG_LOCAL` (im Container z. B.
`/data/config.local.yaml`). Fehlt die Datei oder ist sie leer, ändert sich nichts am Betrieb.
Kaputtes YAML scheitert laut mit Dateinamen, statt still ignoriert zu werden. Listen und Skalare
aus der Override-Datei ersetzen den Wert aus `config.yaml`, Zuordnungen werden rekursiv gemischt.
Pflegen lässt sie sich von Hand oder über den Web-Konfigeditor (siehe
[Web-Dashboard](funktionen.md#web-dashboard)).

## Konfiguration prüfen

Typen, Wertebereiche und Tippfehler prüfen:

```bash
python -m sc_digger.schema [--config config.yaml] [--local /data/config.local.yaml]
```

Je Problem eine Zeile `FEHLER <pfad>: <meldung>` bzw. `WARNUNG <pfad>: <meldung>`, sonst
`OK: Konfiguration gültig`. Der Exit-Code ist 1 bei mindestens einem Fehler, Warnungen allein
ändern ihn nicht. Die Prüfung verändert keine Datei.
