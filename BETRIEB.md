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
`./update.sh --force` baut immer neu. Der
Schlüssel liegt auf dem Server unter `/home/claude/.ssh/sc_digger_deploy` und ist per
`core.sshCommand` in der Repo-Konfiguration hinterlegt; eingetragen ist er auf GitHub unter
*Settings → Deploy keys* (ohne Schreibrecht).
