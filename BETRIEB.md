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
- **Testlauf ohne Nebenwirkungen:** `docker compose exec sc-digger python -m sc_digger.main --dry-run --no-telegram -v`

Deploy nach einem Merge in `main`: `cd /root/sc-digger && ./update.sh` auf dem Server.
