# Betrieb

Betriebswissen für Server und Deploy. Für normale Worker-Aufgaben nicht nötig; es steht hier
statt in `AGENTS.md`, weil Antigravity Regeldateien bei 12.000 Zeichen abschneidet.

- **Server:** Debian-Container im Heimnetz, Repo in `/root/sc-digger`, Docker Compose.
  Host, IP und SSH-Benutzer stehen bewusst **nicht** im (öffentlichen) Repo, sondern in
  `BETRIEB.local.md` (von Git ignoriert, Vorlage: `BETRIEB.example.md`) und im claude.ai-Projekt
  „sc-digger“. Fehlt beides: Stephan fragen, nicht raten. SSH-Schlüssel je Sitzung, nie im Repo.
- **Mounts:** `/music/Schranz` (Sammlung, **ro**), `/music/inbox` (Downloads), beide NFS vom NAS.
- **Zeitplan:** täglich 06:30 SQLite-Sicherung, 06:45 Übernommen-Abgleich (`audit --check-adopted`), 07:05 Taste-Score (`taste`), 07:15 API-Kanarienvogel (`canary`) und 07:30 `discover` per Cron im Container; Bot läuft dauerhaft.
  Schreibende Läufe warten aufeinander (Lauf-Sperre `/data/pipeline.lock`, höchstens `state.lock_wait_s` = 1800 s).
  Exit-Code 75 heißt: Sperre blieb belegt, Lauf übersprungen. Wer sie hält: `docker compose exec sc-digger cat /data/pipeline.lock`.
- **Zugangsdaten** in `/root/sc-digger/.env`, nur über `./set-secret.sh NAME` setzen:
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SOUNDCLOUD_AUTH_TOKEN`, außerdem `SC_DIGGER_WAKE_URL`
  für den Dashboard-Knopf „Workstation-Lauf starten“ (siehe „GPU-Worker“).
- **Dienste außerhalb des Containers** (systemd auf dem Server und auf der Workstation) richtet
  `update.sh` nicht ein. Fehlen sie, merkt man es nur an Symptomen; siehe „Prüfliste: Dienste
  außerhalb des Containers“.
- **Logs:** `docker compose logs --tail 50` · **Health:** Tabelle `runs` in `/data/seen.sqlite`.
- **Health-Status ablesen:**
  - `docker compose ps` zeigt den Status in der Health-Spalte (`healthy` oder `unhealthy`).
  - Detaillierte Meldung der Prüfungen: `docker compose exec sc-digger python -m sc_digger.healthcheck`
  - Bei Problemen oder Entwarnung wird automatisch eine Textmeldung an Telegram gesendet (falls Tokens gesetzt sind).
- **Worker-Sandbox** (eigener Container für Coding-Agenten, nicht der sc-digger-Server):
  Einrichtung und Betrieb stehen in einem separaten, privaten Repository.
- **Testlauf ohne Nebenwirkungen:** `docker compose exec sc-digger python -m sc_digger.main --dry-run --no-telegram -v`
- **API-Kanarienvogel manuell testen:** `docker compose exec sc-digger python -m sc_digger.canary --no-telegram`
  - Exit 0: Alles gut, SoundCloud-API-Schema vollständig.
  - Exit 1: Format-Abweichung! Fehlende Felder werden ausgegeben und lösen Health-Alarm `api_schema` aus → `_to_track` in `sc_digger/soundcloud.py` prüfen.
  - Exit 3: Netzwerk- oder SoundCloud-Verbindungsfehler (kein Health-Eintrag).

Welche Jobs im Container wann laufen, listet `sc_digger/schedule.py` (`python -c "from sc_digger.schedule import jobs_by_time, describe_cron; [print(describe_cron(j.cron), j.label) for j in jobs_by_time()]"`); die Liste wird per Test mit `Dockerfile` und `entrypoint.sh` abgeglichen.

## Zugangsdaten setzen

Nie per Hand in die `.env` tippen, sondern auf dem Server:

```bash
cd /root/sc-digger && ./set-secret.sh SOUNDCLOUD_AUTH_TOKEN   # oder TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
```

Verdeckte Eingabe, Formatprüfung, ersetzt die alte Zeile, startet neu und testet die Verbindung.

**SoundCloud-Login abgelaufen (`sc_login`-Alarm):**
SoundCloud lässt OAuth-Tokens gelegentlich ablaufen. Meldet der tägliche Lauf einen Alarm `🚨 sc-digger: sc_login ... SoundCloud-Login abgelaufen → neuen Token mit ./set-secret.sh setzen`, verlinkt sc-digger Original-Downloads bis zur Erneuerung nur und schont die Retry-Queue. Zum Beheben:
1. Im Browser auf soundcloud.com einloggen und den Wert des Cookies `oauth_token` aus den Entwickler-Tools (Application → Cookies) kopieren.
2. Auf dem Server `./set-secret.sh SOUNDCLOUD_AUTH_TOKEN` ausführen und den neuen Token einfügen.
3. Beim nächsten Lauf prüft der Guard den neuen Token und sendet automatisch die Entwarnung (`✅ sc-digger: sc_login läuft wieder`).

**Speicherplatz-Wächter (`disk`-Alarm):**
Inbox, `_rejected/` und Previews belegen mit der Zeit viel Speicherplatz. Ist das Volume voll, scheitern Downloads und Tagging mitten in der Datei. Der Wächter ist standardmäßig aus (`health.min_free_gb: 0`). Auf dem Server empfiehlt sich ein Schwellenwert von mindestens 20 GB.

Einschalten in der lokalen Override-Datei (z. B. `config.local.yaml`) oder über den Konfigeditor:
```yaml
health:
  min_free_gb: 20
```
Meldet der tägliche Lauf einen Alarm `🚨 sc-digger: disk ... Speicher knapp: X GB frei, Minimum 20.0 GB ... Downloads sind pausiert, bis wieder genug Platz frei ist → Inbox und <code>_rejected</code> aufräumen`:
1. Inbox auf dem Zielvolume prüfen und alte/übernommene Tracks oder unbrauchbare Dateien in `_rejected/` aufräumen bzw. löschen.
2. Alternativ `health.min_free_gb` in der Konfiguration/im Konfigeditor reduzieren, falls der verbleibende Platz bewusst ausreicht.
3. Beim nächsten Lauf misst der Wächter den freien Speicherplatz erneut; sobald wieder genug Platz frei ist, laufen Downloads normal weiter und es erfolgt automatisch eine Entwarnung (`✅ sc-digger: disk läuft wieder`).

## Update nach einem Merge in `main`

```bash
cd /root/sc-digger && ./update.sh
```

Holt den neuen Stand von GitHub über einen Deploy-Key (nur lesen), baut neu, startet und prüft,
ob der neue Container antwortet. Der zuletzt erfolgreich ausgerollte Stand steht in `.last-build`;
scheitert Build oder Start, meldet das Skript `FEHLER` und versucht es beim nächsten Aufruf erneut.
Nach dem Rauchtest schreibt es außerdem `data/version.json` (Commit-Kurz-Hash, Zeitpunkt UTC);
die Statusseite (`/`) zeigt das neben „Stand:“ als „Version: …“, solange die Datei existiert.
`./update.sh --force` baut immer neu. Der
Schlüssel liegt auf dem Server unter `/home/claude/.ssh/sc_digger_deploy` und ist per
`core.sshCommand` in der Repo-Konfiguration hinterlegt; eingetragen ist er auf GitHub unter
*Settings → Deploy keys* (ohne Schreibrecht).

## Auto-Deploy

Ein systemd-Timer rollt neue Stände von `main` selbst aus: `./update.sh`, danach den
Container-Healthcheck, bei Fehlschlag automatisch `git reset --hard` auf den letzten guten
Stand und erneut `./update.sh --force`. Das Ergebnis geht per Telegram an Stephan. Ohne den
Timer blieb ein gemergter PR liegen, bis jemand von Hand `./update.sh` aufrief.

**Was der Timer tut**

- Läuft nur auf `main` (nie auf Branches) und nur, wenn das Repo keine lokalen Änderungen an
  versionierten Dateien hat. Lokale Änderungen werden **nie** per `reset --hard` überschrieben;
  stattdessen einmal je Ziel-Hash eine Warnung per Telegram.
- Merkt sich den zuletzt erfolgreich ausgerollten Stand und einen als fehlerhaft erkannten
  Commit. Ein `bad`-Commit wird nicht erneut versucht, erst ein neuerer `main`-Stand wieder.
- Ein `git fetch`-Fehler ist kein Deploy-Fehler (kein Rollback, keine Meldung). Ein Fehlschlag
  von Build oder Healthcheck dagegen schon: dann Rollback auf den letzten guten Stand.
- Ist kein früherer Stand bekannt (kein `.last-build`), gibt es keinen Rollback; die Meldung
  verlangt manuelles Prüfen.
- Der Stand steht in `/root/sc-digger/.deploy-state.json` (atomar geschrieben),
  `docker compose exec` reicht die Telegram-Meldung in den Container.

**Einrichtung (einmalig, nach dem Merge)**

```bash
cd /root/sc-digger && git pull
cp deploy/sc-digger-deploy.service deploy/sc-digger-deploy.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sc-digger-deploy.timer
```

**Prüfen und Stoppen**

```bash
systemctl list-timers sc-digger-deploy.timer      # nächster Lauf
systemctl status sc-digger-deploy.service         # letzter Lauf
journalctl -u sc-digger-deploy.service -n 50      # Ausgabe des letzten Laufs
systemctl disable --now sc-digger-deploy.timer    # Auto-Deploy wieder aus
```

Der Timer ist durch `flock /run/sc-digger-deploy.lock` gegen Überholung geschützt; ein
laufender Deploy wird nicht von einem zweiten überholt.

## Prüfliste: Dienste außerhalb des Containers

Diese Teile laufen nicht im Container und werden weder von `update.sh` noch vom Auto-Deploy
angelegt. Nach dem Neuaufsetzen eines Rechners oder wenn ein Symptom unten auftritt, alle prüfen.
Am 25.11.2026 fehlte der Weck-Timer seit seiner Einführung, und die Job-Queue stand vier Wochen.

| Teil | Wo | Prüfen | Einrichten |
|---|---|---|---|
| `sc-digger-deploy.timer` | Server | `systemctl list-timers sc-digger-deploy.timer` | Abschnitt „Auto-Deploy“ |
| `sc-digger-gpu-wake.timer` mit `/root/sc-digger/gpu-wake.env` | Server | `systemctl list-timers sc-digger-gpu-wake.timer`, `ls -l /root/sc-digger/gpu-wake.env` | „GPU-Worker“, Einrichtung auf dem Server |
| `SC_DIGGER_WAKE_URL` in `/root/sc-digger/.env` | Server | `grep -c '^SC_DIGGER_WAKE_URL=' /root/sc-digger/.env` (zeigt 1) | „GPU-Worker“, Einrichtung auf dem Server |
| `sc-digger-gpu-worker.service` mit `/etc/sc-digger-gpu-worker.env` | Workstation | `systemctl is-enabled sc-digger-gpu-worker` | „GPU-Worker“, Einrichtung auf der Workstation |
| Klon der Workstation auf aktuellem Stand | Workstation | `git -C /home/stephanv/sc-digger log -1 --oneline` gegen `main` | „GPU-Worker“, Workstation aktuell halten |
| Schutz im Idle-Skript | Workstation | `grep -n sc-digger-gpu-worker /usr/local/bin/ollama-idle-shutdown.sh` | „GPU-Worker“, Schutz im Idle-Skript |

**Symptome und was dahinter steckt**

- Statusseite zeigt „Version … · deployt <altes Datum>“, obwohl seitdem PRs gemergt wurden: Der
  Auto-Deploy läuft nicht (Timer fehlt oder scheitert, `journalctl -u sc-digger-deploy.service`).
  Bis dahin von Hand `./update.sh`.
- Karte „⚡ KI-Workstation & Job-Queue“ zeigt Jobs als „Anstehend“, das „Letzte Lebenszeichen“
  ist aber Tage alt: Weck-Timer fehlt (`journalctl -u sc-digger-gpu-wake` ohne Einträge) oder der
  Runner auf der Workstation startet nicht. Der Wake-Proxy zeigt unter `/proxy/status`, ob und wann
  er zuletzt geweckt hat (`wakes_total`, `last_wake_by`).
- Knopf „Workstation-Lauf starten“ meldet „kein Wake-Proxy konfiguriert“: `SC_DIGGER_WAKE_URL`
  fehlt in der `.env` des Containers. `gpu-wake.env` allein reicht nicht, die liest nur der Timer.

## GPU-Worker: Workstation täglich wecken (Cue-Jobs)

Die Cue-Jobs (First Kick, Drop, Breakdown) rechnet die Workstation `nobara-pc`. Sie soll nicht
dauernd laufen, deshalb weckt der Server sie einmal am Tag, wenn Jobs offen sind.

**Ablauf**

1. Der Tageslauf (07:30) reiht Cue-Jobs für neue Inbox-Tracks ein.
2. Um 08:30 startet `sc-digger-gpu-wake.timer` den Dienst `sc-digger-gpu-wake`. Der zählt die
   offenen Cue-Jobs (`python -m sc_digger.gpu_wake`, im Container). Sind es 0, passiert nichts.
   Sonst schickt er eine Anfrage an den Wake-Proxy; der weckt den PC und antwortet, sobald er
   da ist (Kaltstart 35 bis 50 s). Schlägt das fehl, kommt eine Telegram-Meldung, Exit 1.
3. Auf der Workstation startet `sc-digger-gpu-worker` beim Hochfahren, holt die Cue-Jobs per
   HTTP vom Dashboard und beendet sich nach 300 s ohne Job (`--idle-exit 300`).
4. Das vorhandene `ollama-idle-shutdown` fährt den PC danach herunter.

**Cue-Engine:** Der Runner rechnet Cues standardmäßig mit all-in-one (`--cue-engine allin1`:
`allin1_infer.analyze`, Modell `harmonix-all`, `beat_this`-Beats, GPU) aus `allin1_env`. Fehlt
`allin1_infer` in der Umgebung, steht im Journal eine WARNING mit „allin1“ und der Runner nutzt
die lokale `librosa`-Analyse (`--cue-engine local` wählt sie ausdrücklich, ohne Warnung). Die
Engine steht im Ergebnis (`engine`); schlägt die Analyse selbst fehl, scheitert der Job, es gibt
keinen stillen Rückfall. Alte Cues in der Datenbank bleiben unverändert.

**Schutz im Idle-Skript:** `/usr/local/bin/ollama-idle-shutdown.sh` darf den PC nicht abschalten,
solange der Runner arbeitet. Dafür steht im Skript (als root, vor der Abschaltung) diese Zeile.
Ob sie da ist, zeigt `grep -n sc-digger-gpu-worker /usr/local/bin/ollama-idle-shutdown.sh`; fehlt
sie, eintragen:

```bash
systemctl is-active --quiet sc-digger-gpu-worker && exit 0
```

Probelauf: Runner mit `systemctl start sc-digger-gpu-worker` starten, dann das Skript von Hand
ausführen (`sudo /usr/local/bin/ollama-idle-shutdown.sh`). Der PC darf nicht herunterfahren,
solange der Runner läuft; nach seinem Ende (`systemctl is-active sc-digger-gpu-worker` zeigt
`inactive`) schon.

**Einrichtung auf dem Server (einmalig, nach dem Merge)**

Die Adresse des Wake-Proxys steht in `gpu-wake.env`, nicht im Repo und nicht im Befehl:

```bash
cd /root/sc-digger && git pull
read -rsp "Wake-Adresse: " WAKE_URL; echo
install -m 600 /dev/null gpu-wake.env
printf 'SC_DIGGER_WAKE_URL=%s\n' "$WAKE_URL" > gpu-wake.env; unset WAKE_URL
cp deploy/sc-digger-gpu-wake.service deploy/sc-digger-gpu-wake.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sc-digger-gpu-wake.timer
```

Der Knopf „Workstation-Lauf starten“ im Dashboard läuft im Container und sieht `gpu-wake.env`
nicht. Dieselbe Adresse gehört deshalb zusätzlich in die `.env`; danach den Container neu
erstellen, damit er die Variable lädt:

```bash
cd /root/sc-digger && ./set-secret.sh SC_DIGGER_WAKE_URL
docker compose up -d
```

Gleich danach einmal von Hand wecken und das Ergebnis ansehen; erwartet ist
„<n> offene Cue-Job(s), wecke die Workstation“ ohne Fehlerzeile:

```bash
systemctl start sc-digger-gpu-wake.service; journalctl -u sc-digger-gpu-wake -n 20 --no-pager
```

**Einrichtung auf der Workstation (einmalig)**

Voraussetzung: ein Klon von sc-digger unter `/home/stephanv/sc-digger` und das venv
`/home/stephanv/allin1_env` (`allin1`, `beat-this`, `torch`). Die Env-Datei enthält API-Adresse
und Dashboard-Passwort:

```bash
read -rp "Dashboard-Adresse: " API_URL
read -rsp "Dashboard-Passwort: " PW; echo
sudo install -m 600 -o root -g root /dev/null /etc/sc-digger-gpu-worker.env
printf 'SC_DIGGER_API_URL=%s\nSC_DIGGER_WEB_PASSWORD=%s\n' "$API_URL" "$PW" \
  | sudo tee /etc/sc-digger-gpu-worker.env > /dev/null
unset API_URL PW
sudo cp deploy/workstation/sc-digger-gpu-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable sc-digger-gpu-worker.service
```

Nur `enable`, nicht `--now`: der Runner startet beim nächsten Hochfahren. Die Datei ist für
root lesbar; der Dienst liest sie als root über `EnvironmentFile=` und reicht die Werte an
den Prozess von `stephanv` weiter.

**Workstation aktuell halten**

Der Runner rechnet mit dem Code aus dem Klon `/home/stephanv/sc-digger`. Der aktualisiert sich
nicht selbst, weder durch `update.sh` noch durch den Auto-Deploy auf dem Server. Nach einem Merge,
der `sc_digger/worker.py`, `sc_digger/cue_infer.py`, `sc_digger/structure.py`,
`sc_digger/analysis.py` oder `deploy/workstation/` ändert, auf der Workstation nachziehen:

```bash
sudo systemctl stop ollama-idle-shutdown.timer      # PC während des Updates wach halten
git -C /home/stephanv/sc-digger pull
git -C /home/stephanv/sc-digger log -1 --oneline    # muss dem Stand von main entsprechen
sudo cp /home/stephanv/sc-digger/deploy/workstation/sc-digger-gpu-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start ollama-idle-shutdown.timer
```

Das venv `allin1_env` dabei nicht neu aufsetzen; neue Abhängigkeiten nennt der jeweilige PR.
Beispiel, warum das zählt: Ein Klon auf dem Stand vom 29.10.2026 kannte die all-in-one-Analyse
(#426) noch nicht und rechnete Cues mit der lokalen Analyse, obwohl `allin1_env` vorhanden war.

**Prüfen**

```bash
systemctl list-timers sc-digger-gpu-wake.timer           # Server: nächster Lauf
journalctl -u sc-digger-gpu-wake.service -n 50           # Server: Ausgabe des letzten Laufs
systemctl start sc-digger-gpu-wake.service               # Server: sofort wecken (nur bei offenen Jobs)
systemctl status sc-digger-gpu-worker                    # Workstation: Runner
journalctl -u sc-digger-gpu-worker -n 50                 # Workstation: abgearbeitete Jobs
```

**Von Hand starten**

Statt auf den täglichen Timer (08:30) zu warten oder `systemctl start sc-digger-gpu-wake.service` auf dem Server auszuführen, kann der Weckruf auch direkt aus dem Web-Dashboard gestartet werden: In der Karte „⚡ KI-Workstation & Job-Queue" stößt der Knopf „Workstation-Lauf starten" dieselbe Logik an. Gibt es keine offenen Cue-Jobs, bleibt die Workstation aus. Die Sperrzeit von 300 Sekunden (`gpu_wake.cooldown_s`) schützt vor Mehrfachklicks während des Kaltstarts.

## Inbox auf den Laptop syncen (Syncthing)

Die Inbox (`/music/inbox`) wird per Syncthing auf den Laptop gespiegelt, damit Rekordbox die
Dateien unter dem Laptop-Pfad findet. Einrichtung und Betrieb stehen nur hier; Zugangsdaten,
GUI-Passwort und Geräte-IDs gehören nicht ins Repo.

**Grundregeln**

- Nur `/music/inbox` wird geteilt. **Nie** `/music/Schranz` (Regel 4: Sammlung bleibt unverändert).
- Richtung: Server-Ordner „Nur senden“, Laptop-Ordner „Nur empfangen“. Änderungen am Laptop
  fließen nie zurück in die Inbox.
- Nur im LAN: in beiden Geräten NAT-Traversal, globale Erkennung und Relaying aus.
- `.stignore` im Inbox-Ordner: `_rejected` und `.cloud-tmp`.

**Server (Container im LXC, unabhängig vom sc-digger-Stack)**

`/root/syncthing/docker-compose.yml`:

```yaml
services:
  syncthing:
    image: syncthing/syncthing:latest
    container_name: syncthing
    restart: unless-stopped
    network_mode: host
    environment:
      - PUID=0
      - PGID=0
    volumes:
      - /root/syncthing/config:/var/syncthing/config
      - /music/inbox:/var/syncthing/inbox
```

Start: `cd /root/syncthing && docker compose up -d`. GUI: `http://<server>:8384` (nur LAN,
Passwort setzen, Wert nicht ins Repo). Ordner-Pfad im Container: `/var/syncthing/inbox`.

**Laptop (Windows, natives Syncthing)**

- Installation: `winget install Syncthing.Syncthing` (neues PowerShell-Fenster öffnen, damit
  `syncthing` im PATH ist).
- Start ohne Fenster: `Start-Process syncthing -ArgumentList "--no-browser --no-console" -WindowStyle Hidden`
- Autostart per Verknüpfung im Startup-Ordner (ein geplanter Task scheitert ohne Adminrechte):

```powershell
$p = (Get-Command syncthing).Source
$s = (New-Object -ComObject WScript.Shell).CreateShortcut("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Syncthing.lnk")
$s.TargetPath = $p; $s.Arguments = "--no-browser --no-console"; $s.WindowStyle = 7; $s.Save()
```

- Ziel: `C:\Users\user\Music\Highres\_sc-digger-inbox`. GUI: `http://127.0.0.1:8384`.
- SyncTrayzor wird von der Windows-Smart-App-Control blockiert. Smart App Control nicht
  abschalten, stattdessen das native Syncthing nehmen.

**Rekordbox-Pfad (lokale Überschreibung)**

Der Laptop-Pfad steht nicht in `config.yaml` (dort bleibt `Z:/Highres/_sc-digger-inbox`,
`tests/acceptance/test_issue_37.py` prüft das), sondern in der lokalen Override-Datei auf dem
Server (`SC_DIGGER_CONFIG_LOCAL`, Standard `/data/config.local.yaml`):

```yaml
rekordbox:
  path_map:
    /music/inbox: "C:/Users/user/Music/Highres/_sc-digger-inbox"
```

Die Override-Datei gilt auch für die Cron-Läufe. Prüfen lässt sich das mit
`docker compose exec sc-digger grep -c SC_DIGGER_CONFIG_LOCAL /app/cron.env` (Ergebnis 1).

Prüfen: `docker compose exec sc-digger python -m sc_digger.schema --local /data/config.local.yaml`

**Ablauf im Alltag**

1. Der tägliche Lauf legt neue Tracks in die Inbox; Syncthing überträgt sie auf den Laptop.
2. Im Web-Dashboard (`http://<server>:8080/digest`) neue Tracks durchhören, bewerten (👍/👎) und bei "Gate"-Tracks den Link direkt am PC öffnen/lösen.
3. Geladene Originale unter `http://<server>:8080/intake` per Drag & Drop hochladen; sie werden automatisch geprüft, analysiert, getaggt und in die Inbox einsortiert.
4. In Telegram `/sync_xml` senden: der Bot schickt die aktuelle Rekordbox-XML.
5. In Rekordbox die XML neu laden und den Import-Dialog mit „Ja“ bestätigen (nur dann werden
   Cues aus der XML übernommen).

**Stolperfallen**

- Erst Syncthing-Status „Aktuell“ abwarten, dann XML laden, sonst fehlen Dateien.
- Dateinamen mit NFD-Umlauten (getrennte Zeichen) findet Rekordbox unter Windows nicht;
  Abhilfe ist Issue #199 (NFC beim Sortieren und in der XML).
- Ist das Web-GUI nicht erreichbar (`ERR_CONNECTION_REFUSED`), läuft Syncthing nicht;
  „Failed to acquire lock“ heißt, dass noch eine alte Instanz läuft.

## Sicherung und Wiederherstellung

Die SQLite-Datenbanken (`seen.sqlite`, `tracks.sqlite`) und die lokale Override-Datei (`config.local.yaml`) werden täglich um 06:30 Uhr über die SQLite Backup-API gesichert, geprüft und rotiert (`sc_digger.backup`).

### Ziel

Ab Werk sichert sc-digger nach `/data/backup/<Datum>/` (`backup.target_dir: "/data/backup"`), also in das vorhandene Daten-Volume `./data`. Das schützt vor kaputten Dateien, nicht vor einem Plattenausfall. Anpassen lässt sich das Ziel in der lokalen Override-Datei (`/data/config.local.yaml`):

```yaml
backup:
  target_dir: "/data/backup"
  keep_daily: 7
  keep_weekly: 4
```

Jeder Lauf steht in der Tabelle `runs` (Modus `backup`); die Tagesablauf-Karte zeigt das Ergebnis. Ist `backup.target_dir` leer oder schlägt die Sicherung wiederholt fehl, kommt einmal ein Telegram-Alarm (`alert_after_bad_runs`), bei der nächsten erfolgreichen Sicherung eine Entwarnung.

### Ziel auf dem NAS

Für Schutz gegen Plattenausfall wird der NAS-Ordner auf dem Host unter `data/backup` eingebunden. `docker-compose.yml` bleibt unverändert, weil `./data` schon ein Volume ist. Schritte auf dem Proxmox-Host (Platzhalter in spitzen Klammern):

1. Ordner auf der NAS-Ablage anlegen, z. B. `<nas>/backups/sc-digger`. Eigentümer ist root des unprivilegierten Containers (UID-Mapping, meist 100000).
2. Als Mountpoint in den sc-digger-Container einbinden, Ziel `<projektordner>/data/backup`: `pct set <ct> -mpN <quelle>,mp=<ziel>`.
3. Container neu starten, `docker compose exec sc-digger python -m sc_digger.backup run` ausführen und prüfen, dass der Tagesordner auf dem NAS erscheint.

Ohne diesen Schritt sichert sc-digger in das lokale `data/backup`.

### Prüfen

Manuelle Sicherung im Container ausführen:

```bash
docker compose exec sc-digger python -m sc_digger.backup run
```

Erfolg und Struktur prüfen:
- Tagesordner unter dem Sicherungsverzeichnis: `ls -la /data/backup/$(date +%F)`
- Enthält `seen.sqlite`, `tracks.sqlite` und `config.local.yaml`.
- Logausgabe prüfen (keine Fehler, Exit-Code 0).

### Zurückspielen bei gestopptem Container

**Wichtig:** Vor dem Zurückspielen muss der Container gestoppt werden, damit keine Schreibvorgänge oder offenen WAL-Verbindungen die Wiederherstellung stören:

```bash
# 1. Container stoppen
docker compose stop sc-digger

# 2. Datenbank aus dem gewünschten Tagesordner wiederherstellen (geprüft und mit Sicherung des alten Stands nach .vor-restore)
docker compose run --rm sc-digger python -m sc_digger.backup restore /data/backup/2026-11-05/tracks.sqlite /data/tracks.sqlite --force
docker compose run --rm sc-digger python -m sc_digger.backup restore /data/backup/2026-11-05/seen.sqlite /data/seen.sqlite --force

# 3. Container wieder starten
docker compose start sc-digger
```

## Dashboard-Adresse für Telegram-Links

Damit die Titel im Telegram-Digest auf die Track-Detailseite führen und `/heute` antippbare Links nennt, setzt man `web.public_url` in der lokalen Override-Datei auf dem Server (`/data/config.local.yaml`) oder im Konfigeditor (Abschnitt Web). Die Adresse (z. B. die Tailscale-Adresse des Servers) gehört nie ins Repository:

```yaml
web:
  public_url: "https://dash.example"
```

Leer bleibt alles wie bisher: Titel führen auf SoundCloud, `/heute` nennt relative Pfade.

## Entwicklungs-Ablauf ansehen

Das Hilfsskript `scripts/ablauf.py` wertet offene Issues und Pull Requests direkt über die GitHub-CLI (`gh`) oder lokale JSON-Dateien aus und liefert eine Übersicht nach Status (`idee`, `bereit`, `blockiert`, `in-arbeit`, `fertig`) sowie über hängende Fälle.

### Aufruf

Auf der Entwicklungsbox (mit installiertem `gh`):

```bash
python scripts/ablauf.py
```

Als JSON ausgeben (z. B. für automatisierte Weiterverarbeitung):

```bash
python scripts/ablauf.py --format json
```

Ohne Netzwerk / mit Fixtures testen:

```bash
python scripts/ablauf.py --issues issues.json --prs prs.json [--geschlossen closed.json]
```

### Bedeutung der Befundarten

Das Skript erkennt vier Arten von hängenden Fällen:

- `pr-ohne-review` (Gewicht 2, Handlung nötig): Ein offener PR (kein Draft) wartet seit mehr als 2 Tagen auf ein Review-Urteil (`reviewDecision` ist leer oder `REVIEW_REQUIRED`).
- `blockiert-frei` (Gewicht 2, Handlung nötig): Ein Issue trägt das Label `blockiert`, alle in der Zeile `Wartet auf: #...` genannten Abhängigkeiten sind jedoch bereits geschlossen. Das Issue kann freigegeben werden (`blockiert` entfernen, `bereit` setzen).
- `in-arbeit-ohne-pr` (Gewicht 2, Handlung nötig): Ein Issue steht auf `in-arbeit`, aber es existiert kein offener PR auf einem passenden Feature-Branch (`feature/issue-<n>`).
- `regel-8` (Gewicht 1, Hinweis): Mindestens zwei Issues mit `in-arbeit` tragen gleichzeitig das Label `berührt-main.py`. Laut Regel 8 darf immer nur ein Worker an `sc_digger/main.py` arbeiten.

