# Betrieb

Betriebswissen für Server und Deploy. Für normale Worker-Aufgaben nicht nötig; es steht hier
statt in `AGENTS.md`, weil Antigravity Regeldateien bei 12.000 Zeichen abschneidet.

- **Server:** Debian-Container im Heimnetz, Repo in `/root/sc-digger`, Docker Compose.
  Host, IP und SSH-Benutzer stehen bewusst **nicht** im (öffentlichen) Repo, sondern in
  `BETRIEB.local.md` (von Git ignoriert, Vorlage: `BETRIEB.example.md`) und im claude.ai-Projekt
  „sc-digger“. Fehlt beides: Stephan fragen, nicht raten. SSH-Schlüssel je Sitzung, nie im Repo.
- **Mounts:** `/music/Schranz` (Sammlung, **ro**), `/music/inbox` (Downloads), beide NFS vom NAS.
- **Zeitplan:** täglich 07:30 `discover` per Cron im Container; Bot läuft dauerhaft.
- **Zugangsdaten** in `/root/sc-digger/.env`, nur über `./set-secret.sh NAME` setzen:
  `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SOUNDCLOUD_AUTH_TOKEN`.
- **Logs:** `docker compose logs --tail 50` · **Health:** Tabelle `runs` in `/data/seen.sqlite`.
- **Health-Status ablesen:**
  - `docker compose ps` zeigt den Status in der Health-Spalte (`healthy` oder `unhealthy`).
  - Detaillierte Meldung der Prüfungen: `docker compose exec sc-digger python -m sc_digger.healthcheck`
  - Bei Problemen oder Entwarnung wird automatisch eine Textmeldung an Telegram gesendet (falls Tokens gesetzt sind).
- **Worker-Sandbox** (eigener Container für Coding-Agenten, nicht der sc-digger-Server):
  Einrichtung und Betrieb stehen in einem separaten, privaten Repository.
- **Testlauf ohne Nebenwirkungen:** `docker compose exec sc-digger python -m sc_digger.main --dry-run --no-telegram -v`

## Zugangsdaten setzen

Nie per Hand in die `.env` tippen, sondern auf dem Server:

```bash
cd /root/sc-digger && ./set-secret.sh SOUNDCLOUD_AUTH_TOKEN   # oder TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
```

Verdeckte Eingabe, Formatprüfung, ersetzt die alte Zeile, startet neu und testet die Verbindung.

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
