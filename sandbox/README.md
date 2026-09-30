# Worker-Sandbox: Einrichtung und Betrieb per SSH

Anleitung für Stephan und für eine lokale Agenten-Instanz (z. B. Claude Code auf dem PC), die die
Sandbox per SSH einrichtet, aktualisiert und Fehler sucht. Regeln: `AGENTS.md`; Betriebswissen
zum Server: `BETRIEB.md`.

## Was die Sandbox ist

Ein unprivilegierter Debian-Container auf Proxmox (`agent-sandbox`, derzeit CT 112) **ohne
Mounts**: kein Zugriff auf Sammlung, Inbox oder andere Container. Darin:

- Repo unter `/root/sc-digger`, Python-Umgebung `/root/venv` (für `pytest`)
- [Qwen Code](https://github.com/QwenLM/qwen-code) (Node 22) mit Qwen3-Coder aus Ollama auf
  dem Gaming-PC, Einstellungen in `/root/.qwen/settings.json`. [OpenCode](https://opencode.ai)
  bleibt installiert: `AGENT=opencode bash sandbox/install.sh` schaltet um, `AGENT=qwen-code` zurück.
- `gh`, angemeldet mit einem eigenen fein granularen Token nur für dieses Repo
- Timer `qwen-worker.timer`: alle 5 min (`TAKT`) ein Durchlauf von `/root/worker_tick.py`
  (Kopie von `sandbox/worker_tick.py`). Pro Durchlauf höchstens ein Auftrag:
  1. Nacharbeit: offener PR zu einem Issue mit `agent-qwen`, neuestes Review
     „Änderungen nötig“ und jünger als der letzte Commit. Das Review steht wörtlich im Auftrag.
  2. PR-Text nachtragen: offener PR zu einem `agent-qwen`-Issue, in dessen Text `Closes #N`,
     die `Worker:`-Zeile oder eine Überschrift der PR-Vorlage fehlt. Das prüft der Taktgeber
     selbst; das Modell meldet auch „fertig“, wenn es den Schritt ausgelassen hat.
  3. Sonst ein neues Issue mit `worker-task`, `bereit`, `agent-qwen`, ohne `blockiert`.

  Jeder Auftrag nennt die Schritte mit Befehl und enthält Issue bzw. Review und die PR-Vorlage
  wörtlich.

  **Nur wenn der Gaming-PC an ist (Vorgabe, `WAKE_PC=0`).** Jeder Durchlauf liest zuerst
  `/proxy/status` des WoL-Proxys, das den PC nie weckt. Ist der PC aus oder im Bildmodus, hört
  er auf, ohne GitHub zu fragen. Nur wenn er an ist und Chat aktiv, sucht er Arbeit; welche
  Modelle geladen sind, liest er ebenfalls aus dem Proxy-Status, damit auch ein PC, der gerade
  ausgeht, nicht geweckt wird. Hast du gerade ein anderes Modell geladen (LibreChat, Home
  Assistant), wartet er. Zustandsmeldungen stehen nur beim Wechsel im Log, nicht alle 5 min.
  Arbeitet der Worker, hält das geladene Modell den PC wach (`ollama-inhibit`); 15 min nach
  der letzten Anfrage entlädt Ollama es, 5 min später schaltet KDE ab.

  **Mit Wecken (`WAKE_PC=1 TAKT=30min bash sandbox/install.sh`).** Erst bei Arbeit fragt der
  Durchlauf `/api/ps`; ist der PC aus, weckt ihn der Proxy per WoL und der Takt wartet bis zu
  `WAKE_WAIT` (240 s, länger als der Proxy selbst).

  Je Auftrag höchstens zwei Versuche
  (`/root/worker-state.json`); nach einer Nacharbeit prüft er, ob ein neuer Commit oder ein
  geänderter PR-Text ankam.

Dateien: `host-create.sh` (Proxmox-Host), `install.sh` (im Container, beliebig oft),
`worker_tick.py` (Taktgeber, Tests in `tests/test_worker_tick.py`).

## Sicherheitsregeln für die lokale Instanz

- **SSH nur in die Sandbox**, nie auf den Proxmox-Host und nie auf den sc-digger-Server.
  Alles, was `pct` braucht, macht Stephan (Schritt 1). Eine Instanz mit GitHub-Schreibrechten
  bekommt keinen Zugang zum Hypervisor.
- **Tokens gibt nur Stephan ein**, verdeckt (`read -rsp`). Nie Tokens, `hosts.yml` oder
  `.env`-Inhalte ausgeben, auch nicht gekürzt. Logs vor dem Zeigen filtern:
  `grep -v -i -E "authorization|private_|token"`.
- **Keine Änderungen nur im Container.** Skripte und Einstellungen ändern sich per PR in
  `sandbox/`, danach `install.sh` erneut ausführen (Goldene Regel 1).
- Die lokale Instanz ist **nicht** der Worker. Sie richtet ein, beobachtet und meldet. Code für
  Issues schreibt der Worker in der Sandbox, Reviews macht eine andere Modellfamilie.

## Schritt 1: Container anlegen (Stephan, Proxmox-Host)

```bash
curl -fsSLO https://raw.githubusercontent.com/tripitest-art/sc-digger/main/sandbox/host-create.sh
CTID=$(pvesh get /cluster/nextid) bash host-create.sh
```

Am Ende steht die IP des Containers. In `BETRIEB.local.md` eintragen (nie ins Repo).
Existiert die Sandbox schon, entfällt der Schritt.

## Schritt 2: SSH-Zugang für die lokale Instanz

Auf dem Rechner der lokalen Instanz einen eigenen Schlüssel nur für die Sandbox erzeugen:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/agent-sandbox -N "" -C "agent-sandbox"
cat ~/.ssh/agent-sandbox.pub
```

Stephan trägt den **öffentlichen** Schlüssel (eine Zeile, beginnt mit `ssh-ed25519`) auf dem
Proxmox-Host ein, `<CTID>` ist die Nummer aus Schritt 1:

```bash
read -rp "Öffentlicher Schlüssel: " K
pct exec <CTID> -- bash -c "mkdir -p -m 700 /root/.ssh && echo '$K' >> /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys && systemctl enable --now ssh"
```

Auf dem Rechner der lokalen Instanz, `<ip>` aus `BETRIEB.local.md`:

```
# ~/.ssh/config
Host agent-sandbox
    HostName <ip>
    User root
    IdentityFile ~/.ssh/agent-sandbox
    IdentitiesOnly yes
```

Test: `ssh agent-sandbox hostname` gibt `agent-sandbox` aus.

## Schritt 3: GitHub-Token (Stephan)

Neuer fein granularer Token (GitHub → Settings → Developer settings → Fine-grained tokens):
nur `tripitest-art/sc-digger`; Contents, Issues, Pull requests: Read and write; Actions: Read;
**kein** Workflows, **keine** Administration; Ablauf 30 Tage. Im Container (`pct enter <CTID>`
oder `ssh agent-sandbox`), den Token beim Prompt einfügen (unsichtbar):

```bash
read -rsp "Token: " T; echo; echo "$T" | gh auth login --with-token; unset T; gh auth setup-git; gh auth status 2>&1 | head -3
```

Danach `bash /root/sc-digger/sandbox/install.sh`: erst mit Anmeldung schaltet es den Timer ein.
Läuft der Token ab, bleibt der Timer an, aber jeder Durchlauf scheitert; dann Schritt 3 wiederholen.

## Aktualisieren nach einem Merge in `sandbox/`

```bash
ssh agent-sandbox 'cd /root/sc-digger && git checkout -q main && git pull -q && bash sandbox/install.sh'
```

Die letzten Zeilen zeigen: GitHub angemeldet, Ollama erreichbar, Ergebnis von `pytest`, nächster
Timerlauf.

## Beobachten und steuern

```bash
ssh agent-sandbox 'tail -n 40 /root/worker.log' | grep -v -i -E "authorization|private_|token"
ssh agent-sandbox 'systemctl list-timers qwen-worker.timer --no-pager'
ssh agent-sandbox 'systemctl start --no-block qwen-worker.service'   # Durchlauf sofort
ssh agent-sandbox 'systemctl stop qwen-worker.timer'                 # anhalten
ssh agent-sandbox 'systemctl start qwen-worker.timer'                # wieder an
ssh agent-sandbox 'cat /root/worker-state.json'                      # Versuche je Auftrag
ssh agent-sandbox 'rm -f /root/worker-state.json'                    # Versuche zurücksetzen
```

Versuche nur zurücksetzen, wenn ein Lauf an der Umgebung scheiterte (Tabelle unten), nicht am
Modell. Ob ein Lauf etwas bewirkt hat, zeigt GitHub (neuer Commit, PR-Text, Kommentar), nicht
die Abschlussmeldung des Modells: es meldet auch „fertig“, wenn es nichts getan hat.

Log-Meldungen:

| Meldung | Bedeutung |
|---|---|
| `Gaming-PC aus, kein Wecken, warte.` / `Gaming-PC im Bildmodus, warte.` | PC nicht bereit; der Worker schaut alle 5 min, meldet es aber nur einmal |
| `Gaming-PC an, Chat aktiv: suche Arbeit.` | PC ist (wieder) bereit; danach folgt das Ergebnis der Suche |
| `Keine Arbeit.` | kein passendes Issue, keine fällige Nacharbeit (nur beim Wechsel) |
| `Gaming-PC ist aus, wecke ihn …` / `Gaming-PC wach nach n s` | nur mit `WAKE_PC=1`: Auftrag steht an, PC wird per WoL geweckt (aus S5 etwa 45–50 s) |
| `Gaming-PC im Bildmodus` | ComfyUI läuft; nächste Runde, ohne Ollama zu fragen |
| `Ollama nicht erreichbar` / `Ollama belegt` | Wecken gescheitert (Proxy gibt nach 180 s auf), Netz weg oder anderes Modell geladen; nächste Runde |
| `… (Versuch n): starte Qwen Code.` | Auftrag läuft (bis 90 min). Darunter je Befehl `$ …` mit gekürzter Ausgabe, `✗ Fehler:` bei Fehlschlag |
| `WARNUNG …: im Text von PR #n fehlt …` | PR-Text unvollständig; der nächste Durchlauf trägt ihn als eigenen Auftrag nach |
| `… neuer Commit …` / `… PR-Text von #n geändert` | Nacharbeit angekommen; Inhalt auf GitHub prüfen |
| `issueN: PR #m von feature/issue-N ist offen` | neues Issue umgesetzt; Review steht an |
| `WARNUNG issueN: kein PR von feature/issue-N` | Issue nicht erledigt; Log davor lesen, besonders wo das Modell aufhörte |
| `WARNUNG …: weder Commit noch PR-Text` | Nacharbeit nicht erledigt; Log davor lesen, besonders Fehler von `gh` |
| `schon 2 Versuche, wartet auf Stephan` | Auftrag liegt, bis Stephan entscheidet |
| Python-Traceback | Fehler im Taktgeber oder bei `gh`; siehe Tabelle |

## Bekannte Fehler (alle schon passiert)

| Symptom | Ursache | Abhilfe |
|---|---|---|
| `gh … returned non-zero exit status 4` im Timer, von Hand geht es | systemd-Dienst ohne `HOME` | steht in `install.sh` (`Environment=HOME=/root`); `install.sh` erneut ausführen |
| `gh pr edit` meldet `GraphQL: Projects (classic) is being deprecated …`, PR-Text bleibt alt; Modell meldet trotzdem Erfolg | `gh` 2.23 aus Debian fragt noch Projects (classic) ab | `install.sh` holt `gh` aus dem Paketarchiv von GitHub; `install.sh` erneut ausführen, Versuche zurücksetzen |
| `permission requested: external_directory (/tmp/*); auto-rejecting` | OpenCode schreibt nur im Projektordner | Hilfsdateien nach `.git/` (wird nie committet), nicht nach `/tmp` |
| Modell meldet „alle Tests grün, 705 passed“, am PR ändert sich nichts | lief auf `main` statt auf dem PR-Branch, Review nicht gelesen | Auftrag enthält `gh pr checkout` und das Review wörtlich; bei Wiederholung Nacharbeit einer stärkeren Familie geben |
| PR-Text mit eigenen Überschriften, ohne `Closes #N`, Tests abgehakt, die es nicht gibt | Auftrag nannte nur den Dateinamen der Vorlage | Vorlage steht wörtlich im Auftrag; Taktgeber prüft den Text selbst und gibt einen Nachtrag-Auftrag |
| Modell hört mitten im Satz auf („Ich werde nun …“) | Modell beendet den Zug ohne Werkzeugaufruf | zweiter Versuch in der nächsten Runde; häuft es sich, Issue auf `agent-gemini` umlabeln |
| Langes Einfügen in der Proxmox-Konsole bricht ab | Browser-Konsole verträgt keine langen mehrzeiligen Texte | per SSH arbeiten oder Dateien aus dem Repo nehmen (dieser Ordner) |
| Einrichtung „fehlt alles“, obwohl sie lief | im falschen Container (zwei hießen `agent-sandbox`) | `pct list`, nur einen Container behalten |
| Worker sehr langsam | Modell teilt sich den Gaming-PC mit Chat/Bildern | auf dem PC `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`; Variante mit 64k Kontext |

## Ollama Tuning für knappen VRAM (z. B. 16 GB RX 6800 XT)

Wenn große MoE-Modelle (wie `qwen3-coder:30b`) fast den gesamten VRAM belegen, landet der stetig wachsende KV-Cache im langsamen System-RAM (DDR4). Das führt zu einer massiven Verzögerung vor dem ersten generierten Token (Pre-fill Bottleneck).

Abhilfe schaffen diese beiden Umgebungsvariablen:
- `OLLAMA_KV_CACHE_TYPE=q8_0`: Quantisiert den Kontextverlauf auf 8-Bit und spart bis zu 75 % Speicherbedarf für den Cache, damit er im schnellen VRAM bleibt.
- `OLLAMA_FLASH_ATTENTION=1`: Beschleunigt das massiv parallele Einlesen des Kontextes drastisch.

**Einrichtung unter Linux (systemd):**
```bash
sudo mkdir -p /etc/systemd/system/ollama.service.d
echo -e "[Service]\nEnvironment=\"OLLAMA_KV_CACHE_TYPE=q8_0\"\nEnvironment=\"OLLAMA_FLASH_ATTENTION=1\"" | sudo tee /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

> [!WARNING]
> **Achtung bei AMD RX 6000 Serie (RDNA2):** Die Parameter (insbesondere `OLLAMA_FLASH_ATTENTION=1`) führen bei älteren AMD ROCm-Treibern (z.B. RX 6800 XT) häufig zu Kernel-Freezes und Timeouts (Ollama hängt bei `/api/ps`). Bei diesen Karten darf Flash Attention **nicht** aktiviert werden! `OLLAMA_KV_CACHE_TYPE=q8_0` funktioniert hingegen fehlerfrei und ist dringend empfohlen.


## GPU Hardware-Tuning (Linux / RDNA2)

LLM-Inferenz (insbesondere für Qwen3-Coder) lastet die GPU-Shader kaum aus, skaliert aber massiv mit der Speicherbandbreite. Eine ungedrosselte RX 6800 XT verschwendet beim Generieren sinnlos Strom (Spikes bis 300 W).

**Empfohlenes Tuning (am sichersten via LACT):**
- **Power Limit:** Auf 150 W absenken (verhindert massive VDD-Spikes beim Pre-Fill)
- **Core Clock:** Max auf 2100 MHz begrenzen
- **Core Voltage Offset:** -100 mV (ca. 1050 mV)
- **VRAM Clock:** Maximal lassen (VRAM ist der Flaschenhals!)

Mit diesen Einstellungen sinkt der Verbrauch von >250W auf ca. 140W, während die Token-Rate (~50 Tokens/s bei 30B) bei RDNA2 exakt gleich bleibt, da der VRAM-Durchsatz erhalten bleibt.

## Home Assistant & MQTT Integration

Die Sandbox kann ihr Live-Log in Echtzeit an einen MQTT-Broker streamen, um das Agenten-Gedankengut z.B. in Home Assistant anzuzeigen. Dazu wird `mosquitto-clients` installiert und ein eigener Systemd-Service in der Sandbox eingerichtet, der das `worker.log` streamt:

```bash
apt-get update && apt-get install -y mosquitto-clients
cat > /etc/systemd/system/qwen-mqtt.service << 'EOF'
[Unit]
Description=Stream Qwen Log to MQTT
After=network.target

[Service]
ExecStart=/bin/bash -c "tail -n 0 -F /root/worker.log | mosquitto_pub -h HA_IP -u 'USER' -P 'PASS' -t 'sc_digger/worker/log' -l"
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload && systemctl enable --now qwen-mqtt.service
```
