# AGENTS.md – Einweisung für alle Agenten (Claude, Gemini, Codex, …)

Diese Datei ist die gemeinsame Arbeitsgrundlage. Lies sie vollständig, bevor du etwas
änderst. Wenn du eine Regel hier für falsch hältst: im Pull Request begründen, nicht
stillschweigend abweichen.

## Worum es geht

`sc-digger` ist ein Crate-Digging-Tool für Schranz/Hard Techno. Es sucht täglich neue
SoundCloud-Tracks, bewertet sie genre-relativ, gleicht sie mit der Sammlung ab, lädt
freigegebene Originale, prüft deren Qualität, analysiert BPM/Key, taggt und sortiert sie
in eine Inbox und schickt einen Telegram-Digest. Ein Telegram-Bot prüft Playlists und
Track-Stations auf Zuruf. Details und Ausbaustufen: `ROADMAP.md`.

## Goldene Regeln

1. **GitHub `main` ist die einzige Wahrheit.** Nie Code direkt auf dem Server oder in
   losen Kopien ändern. Der Server holt nur (Deploy-Key ohne Schreibrecht).
2. **Ein Branch pro Aufgabe, nach `main` nur per Pull Request.** Branch-Namen:
   `feature/<kurz>`, `fix/<kurz>`, `docs/<kurz>`. Vor dem Start `main` aktualisieren.
3. **Tests müssen grün sein.** `python -m pytest -q` vor jedem Push. Neue Logik bekommt
   Tests; behobene Fehler bekommen einen Test, der den Fehler reproduziert.
4. **Die Sammlung wird nie verändert.** `/music/Schranz` ist schreibgeschützt eingebunden
   und bleibt es. Rekordbox verknüpft Cues über Dateipfade; verschieben, umbenennen oder
   Audio bearbeiten zerstört sie. Sortieren und Taggen nur in der Inbox.
5. **Zugangsdaten nie in Repo, Logs, Fehlermeldungen oder Chat.** Telegram-API immer über
   `output.telegram_call()` (nie `requests` direkt, die URL enthält den Token). Logging
   über `redact.install_redacting_logging()`. Keine `.env`-Inhalte ausgeben, auch nicht
   „maskiert“ – Namen und Längen reichen.
6. **Fair zu Artists.** Gates (Hypeddit, Droploud, …) werden erkannt und verlinkt, nie
   umgangen: keine Wegwerf-Adressen, keine Link-Leaks, keine Fake-Accounts.
   Stream-Rips werden nicht geladen (`scdl --only-original`).
7. **Nicht still scheitern.** Fehler, die den täglichen Lauf betreffen, müssen im Health-
   Alarm oder im Digest sichtbar werden.
8. **Nicht zwei Agenten in denselben Dateien.** `sc_digger/main.py` ist der Engpass (alle
   Modi laufen dort zusammen). Wer dort arbeitet, schreibt es ins Issue.

## Ablauf pro Aufgabe

1. Issue lesen (oder anlegen lassen). Umfang klein halten, ein Thema pro Pull Request.
2. `git checkout main && git pull && git checkout -b feature/<kurz>`
   Mehrere Agenten auf einem Rechner: jeder in eigenem Worktree
   (`git worktree add ../sc-digger-<kurz> -b feature/<kurz>`).
3. Umsetzen, Tests schreiben, `python -m pytest -q`.
4. Committen (Nachricht: *warum*, nicht nur *was*), Branch pushen.
5. Pull Request öffnen und die Vorlage ausfüllen. Kann das Werkzeug keinen PR öffnen:
   Branch pushen und dem Menschen den Link `https://github.com/tripitest-art/sc-digger/pull/new/<branch>` geben.
6. Review durch einen Agenten, der den Code nicht geschrieben hat, oder durch Stephan.
   Merge nur bei grünem Testlauf (GitHub Actions) und abgeschlossenem Review.
7. Deploy: `cd /root/sc-digger && ./update.sh` auf dem Server (nur nach Merge in `main`).

## Architektur

| Datei | Zuständig für |
|---|---|
| `sc_digger/main.py` | CLI und Modi (`discover`, `playlist`, `similar`, `check`), gemeinsame Pipeline `process()`, Auslieferung `deliver()` |
| `sc_digger/bot.py` | Telegram-Listener; ruft `main.run_link()`; beendet sich nie selbst (sonst stirbt der Cron) |
| `sc_digger/soundcloud.py` | Inoffizielle api-v2 (client_id aus dem Frontend), Playlists inkl. Stub-Nachladen, Station/Related, Referenz-Accounts (soundcloud-v2-Lib) |
| `sc_digger/pipeline.py` | Text-BPM, Genre-Relevanz, Perzentil-Scoring, Download-Klassifizierung |
| `sc_digger/collection.py` | Duplikat-Abgleich mit der Sammlung (Fuzzy-Match, Remixer beachten) |
| `sc_digger/output.py` | State-DB, Original-Download (scdl), `finalize_quality`, Telegram-Digest, Export-Datei, `telegram_call` |
| `sc_digger/quality.py` | ffprobe, Spektrum-Cutoff (Fake-Erkennung), EBU R128 / LRA (Brickwall) |
| `sc_digger/analysis.py` | BPM/Key per librosa, BPM-Oktav-Korrektur `resolve_bpm` |
| `sc_digger/organize.py` | Inbox-Sortierung `<BPM>/<Camelot>/`, Tags schreiben |
| `sc_digger/db.py` | Zentrale Track-DB (Phase 2), Metadaten-Index, Audio-Fingerprints, Jobs-Queue, versionierte Migrationen |
| `sc_digger/health.py` | Laufprotokoll, Alarm bei wiederholt leeren/fehlerhaften Läufen |
| `sc_digger/redact.py` | Zugangsdaten aus Texten und Logs entfernen |
| `config.yaml` | Einzige Konfiguration (Tags, Referenz-Accounts, Schwellwerte). Ist die Produktivkonfiguration. |
| `entrypoint.sh` | Schreibt `cron.env` (Cron hat sonst weder PATH noch Secrets), startet cron und Bot |
| `set-secret.sh` / `update.sh` | Zugangsdaten setzen / Update ausrollen (auf dem Server) |

## Betrieb

- **Server:** Proxmox `proxmox1`, LXC 107 `sc-digger` (Debian), `192.168.0.110`, Repo in
  `/root/sc-digger`, Docker Compose. SSH-Benutzer `claude` (Schlüssel je Sitzung, nie im Repo).
- **Mounts:** `/music/Schranz` (Sammlung, **ro**), `/music/inbox` (Downloads), beide NFS vom NAS.
- **Zeitplan:** täglich 07:30 `discover` per Cron im Container; Bot läuft dauerhaft.
- **Zugangsdaten** in `/root/sc-digger/.env`, nur über `./set-secret.sh NAME` setzen:
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SOUNDCLOUD_AUTH_TOKEN`.
- **Logs:** `docker compose logs --tail 50` · **Health:** Tabelle `runs` in `/data/seen.sqlite`.
- **Testlauf ohne Nebenwirkungen:** `docker compose exec sc-digger python -m sc_digger.main --dry-run --no-telegram -v`

## Entwickeln

```bash
pip install -r requirements.txt pytest     # braucht ffmpeg im PATH
python -m pytest -q                         # alle Tests, ~10 s
python -m sc_digger.main --dry-run --no-telegram -v   # lokal, braucht Netz zu SoundCloud
```

Konventionen: Python 3.12, Typ-Hinweise, Kommentare und Log-Meldungen auf Deutsch,
Kommentare erklären das *Warum*. Netzwerk in Tests immer durch Fakes ersetzen
(Beispiele: `FakeSC` in `tests/test_modes.py`, `FakeLinkSC` in `tests/test_merge.py`).

## Bekannte Stolperfallen (alle schon einmal passiert)

- **Cron-Umgebung ist leer:** kein `/usr/local/bin`, keine `.env`. Deshalb `cron.env`.
- **scdl meldet Fehler mit Exit-Code 0.** Erfolg nur daran messen, ob eine Datei entstand.
- **Originale nur mit Login.** Ohne `SOUNDCLOUD_AUTH_TOKEN` gibt es sie nicht; ohne
  `--only-original` lädt scdl still den Stream.
- **Playlists liefern die meisten Tracks nur als Stub** (nur `id`). Nachladen, sonst fehlen sie.
- **Telegram-Token steckt in jeder API-URL** und damit in `requests`-Fehlern.
- **`TELEGRAM_CHAT_ID` ist eine Zahl**, nicht der Bot-Name.
- **Windows-Zeilenenden** brechen `entrypoint.sh`; `.gitattributes` erzwingt LF.
- **Zwei Code-Stände** (Server-Kopie und Repo) liefen schon einmal auseinander. Siehe Regel 1.
