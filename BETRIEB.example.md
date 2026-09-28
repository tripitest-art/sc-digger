# Betrieb – Serverdetails (Vorlage)

Kopieren nach `BETRIEB.local.md` und ausfüllen. `*.local.md` ist in `.gitignore`, die
echte Datei landet also nie im öffentlichen Repo. Hier nur Adressen und Namen eintragen,
**keine** Zugangsdaten (Tokens, Passwörter, Schlüssel): die gehören in `.env` auf dem Server.

| | |
|---|---|
| Hypervisor | `<host>` |
| Container | `<LXC-ID>` `<name>` (Debian) |
| IP | `<ip>` |
| SSH-Benutzer | `<user>` |
| Repo auf dem Server | `/root/sc-digger` |
| Mounts | `/music/Schranz` (ro), `/music/inbox`, beide NFS vom NAS |

Deploy nach einem Merge in `main`:

```bash
ssh <user>@<ip>
cd /root/sc-digger && ./update.sh
```
