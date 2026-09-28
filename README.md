# sc-digger

SoundCloud-Discovery für Schranz / Hard Techno: findet neue Tracks, bewertet sie
genre-relativ, gleicht mit deiner Sammlung ab, lädt **nur native Downloads**, prüft
die Audioqualität (Bitrate + Spektrum), erkennt **BPM + Tonart** aus dem Audio,
schreibt **ID3-Tags** und sortiert Tracks automatisch in **BPM/Key-Ordner**.
Zum Schluss gibt's einen Telegram-Digest.

## Ablauf
1. Discovery: Tag-Suche + Uploads gefolgter Künstler/Labels
2. BPM-Fenster (aus Titel/Tags/Beschreibung geschätzt; unbekannt = behalten)
3. Scoring per Perzentil (Likes/Reposts/Comments pro Play + Aktualität)
4. Filter: schon gemeldet (SQLite) / schon in deiner Sammlung (Fuzzy-Match)
5. Klassifizierung: native | hypeddit | droploud | toneden | artistunion | store | cloud | none
6. Native Downloads laden (scdl) -> ffprobe + Spektrum-Check -> Fakes nach `_rejected/`
7. **Audio-Analyse: BPM via Beat-Tracking, Tonart via Chroma-Profiling (Camelot-Key)**
8. **ID3/Vorbis-Tags schreiben (Artist, Title, BPM, Key, Genre, SoundCloud-URL)**
9. **Auto-Organize: Tracks nach `inbox/<BPM-Range>/<Key>/` sortieren**
10. Telegram-Digest, gruppiert nach Download-Weg

Gates werden **nur erkannt und verlinkt**, nicht automatisch durchlaufen.

## Modi

```bash
# 1) discover (Standard): Tag-/Künstler-Suche
python -m sc_digger.main

# 2) playlist: Playlist gegen deine Sammlung prüfen
python -m sc_digger.main playlist https://soundcloud.com/user/sets/name
python -m sc_digger.main playlist https://soundcloud.com/user --likes     # Likes eines Profils

# 3) check: wie der Telegram-Bot – Playlist-Link -> alle Tracks, Track-Link -> Station
python -m sc_digger.main check https://soundcloud.com/artist/track

# 4) similar: Was schlägt der SoundCloud-Algorithmus zu einem Track vor?
python -m sc_digger.main similar https://soundcloud.com/artist/track            # Related Tracks
python -m sc_digger.main similar https://soundcloud.com/artist/track --radio    # Track-Radio
python -m sc_digger.main similar https://soundcloud.com/artist/track --filter   # + BPM/Scoring
```

Zusätzlich für jeden Modus: `--dry-run` (nichts laden/senden/organisieren), `--no-telegram`
(Ausgabe in der Konsole), `-v`.

| | discover | playlist | similar |
|---|---|---|---|
| BPM-/Score-Filter | ja | **nein** (du hast sie bewusst gewählt) | nur mit `--filter` |
| "schon gemeldet"-State | ja | **nein** (beliebig oft prüfbar) | nein |
| Sammlungs-Abgleich | ja | ja | ja |
| Analyse/Tagging/Organize | ja | ja | ja |
| Digest gekürzt auf `max_items` | ja | **nein**, zeigt alles | ja |

## Start
```bash
cp .env.example .env        # Bot-Token + Chat-ID eintragen
# Pfade in docker-compose.yml und config.yaml anpassen
docker compose build

# Erst prüfen, ob SoundCloud erreichbar ist (client_id-Ermittlung):
docker compose run --rm sc-digger python -m sc_digger.main --dry-run -v

docker compose up -d        # täglich 07:30 per cron
```

## Tests
```bash
pip install -r requirements.txt pytest && pytest tests -q
```

## Telegram-Bot
Der Container startet einen Bot-Listener: Schick ihm einen SoundCloud-Link (auch
`on.soundcloud.com`-Kurzlinks aus der App) und du bekommst die komplette Playlist bzw. die
Station dazu, inkl. Sammlungsabgleich, Download-Einordnung und Export-Datei. Er reagiert nur
auf `TELEGRAM_CHAT_ID`. Fehlen die Telegram-Daten, pausiert der Bot, der tägliche Lauf
läuft trotzdem.

## Original-Downloads (SOUNDCLOUD_AUTH_TOKEN)
SoundCloud gibt Original-Dateien nur eingeloggt heraus. Ohne `SOUNDCLOUD_AUTH_TOKEN` in der
`.env` werden native Downloads nur verlinkt („Original manuell laden“). Stream-Rips werden
nie geladen (`scdl --only-original`).

## Update auf dem Server
```bash
cd /root/sc-digger && git pull && docker compose up -d --build
```

## Health-Alarm
Jeder `discover`-Lauf wird in der State-DB protokolliert (Rohtreffer vor Filtern, Fehler).
Liefern `health.alert_after_bad_runs` Läufe in Folge 0 Rohtreffer oder brechen ab, kommt
einmalig ein 🚨-Alarm per Telegram, sobald es wieder läuft eine ✅-Entwarnung. Ein Tag ohne
*neue* Tracks löst keinen Alarm aus.

## BPM-Oktav-Korrektur
Die Audio-BPM wird per ×2/÷2 korrigiert: Vorrang hat eine BPM-Angabe des Uploaders im Text,
danach das Suchfenster (`search.bpm_min/max`), danach der plausible Bereich
(`organize.bpm_plausible_min/max`). Echte Tempi außerhalb des Fensters (z. B. 140) bleiben erhalten.
Korrekturen erscheinen als Notiz am Track.

## Bekannte Schwachstellen
- `soundcloud.py` nutzt die inoffizielle api-v2. Wenn die client_id-Ermittlung bricht,
  ist das die einzige Datei, die angepasst werden muss.
- SoundCloud hat kein BPM-Feld; der Vorfilter in `discover` nutzt nur BPM-Angaben aus dem
  Freitext (daher `bpm_unknown_policy: keep`). Audio-BPM gibt es erst nach dem Download.
- Score-Perzentile brauchen genug Kandidaten (>= ~30); bei wenigen Treffern
  `max_age_days` oder `limit_per_tag` erhöhen.
