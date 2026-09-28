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

# 3) similar: Was schlägt der SoundCloud-Algorithmus zu einem Track vor?
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

## Bekannte Schwachstellen
- `soundcloud.py` nutzt die inoffizielle api-v2. Wenn die client_id-Ermittlung bricht,
  ist das die einzige Datei, die angepasst werden muss.
- SoundCloud hat kein BPM-Feld; die Schätzung aus Freitext greift nur, wenn der
  Uploader die BPM nennt. Deshalb Policy `bpm_unknown_policy: keep`.
- Score-Perzentile brauchen genug Kandidaten (>= ~30); bei wenigen Treffern
  `max_age_days` oder `limit_per_tag` erhöhen.
