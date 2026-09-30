#!/usr/bin/env bash
# Richtet die Worker-Sandbox ein oder bringt sie auf den Stand von main. Läuft IM Container
# (CT 112) als root und darf beliebig oft laufen. Anleitung: sandbox/README.md.
#
#   bash /root/sc-digger/sandbox/install.sh            # einrichten / aktualisieren
#   MODEL=qwen3-coder-64k OLLAMA=http://… bash …       # andere Werte als die Vorgaben
#
# Der GitHub-Zugang wird hier bewusst NICHT eingerichtet: den Token gibt Stephan selbst
# verdeckt ein (README, Schritt 3). Das Skript prüft nur, ob er da ist.
set -euo pipefail

REPO_DIR=/root/sc-digger
OLLAMA=${OLLAMA:-http://192.168.0.210:11434}
MODEL=${MODEL:-qwen3-coder-64k}
export DEBIAN_FRONTEND=noninteractive

echo "== Pakete"
apt-get update -qq
apt-get install -y -qq git curl ca-certificates python3 python3-venv python3-pip ffmpeg \
  libsndfile1 libchromaprint-tools >/dev/null
# gh aus dem Paketarchiv von GitHub, nicht von Debian: Debians 2.23 fragt bei `gh pr edit` und
# `gh pr view` noch „Projects (classic)“ ab und bricht mit einem GraphQL-Fehler ab. Qwen hielt
# das für Erfolg; der PR-Text von #103 kam zweimal nicht an.
install -d -m 755 /etc/apt/keyrings
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  > /etc/apt/sources.list.d/github-cli.list
apt-get update -qq
apt-get install -y -qq gh >/dev/null

echo "== Repo und Python-Umgebung"
git config --global user.name >/dev/null || git config --global user.name "Stephan (Sandbox-Agent)"
git config --global user.email >/dev/null || git config --global user.email "tripitest@gmail.com"
[ -d "$REPO_DIR/.git" ] || git clone -q https://github.com/tripitest-art/sc-digger.git "$REPO_DIR"
[ -x /root/venv/bin/python ] || python3 -m venv /root/venv
/root/venv/bin/pip install -q -r "$REPO_DIR/requirements.txt" pytest
grep -q 'source /root/venv/bin/activate' /root/.bashrc || echo 'source /root/venv/bin/activate' >> /root/.bashrc

echo "== OpenCode"
[ -x /root/.opencode/bin/opencode ] || curl -fsSL https://opencode.ai/install | bash
mkdir -p /root/.config/opencode
# Nur im Projektordner schreiben (Standard von OpenCode), kein Web; Befehle ohne Rückfrage,
# weil der Timer ohne Terminal läuft und Rückfragen dort automatisch abgelehnt werden.
cat > /root/.config/opencode/opencode.json <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "ollama": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Ollama (Gaming-PC)",
      "options": { "baseURL": "$OLLAMA/v1" },
      "models": { "$MODEL": { "name": "$MODEL" } }
    }
  },
  "model": "ollama/$MODEL",
  "permission": { "edit": "allow", "bash": "allow", "webfetch": "deny" }
}
JSON

echo "== Taktgeber"
install -m 755 "$REPO_DIR/sandbox/worker_tick.py" /root/worker_tick.py
cat > /etc/systemd/system/qwen-worker.service <<UNIT
[Unit]
Description=Qwen-Worker: ein Auftrag pro Takt (sandbox/worker_tick.py)
[Service]
Type=oneshot
# Ohne HOME finden gh, git und OpenCode ihre Konfiguration nicht (gh endet mit Exit 4).
Environment=HOME=/root
Environment=OLLAMA=$OLLAMA
Environment=MODEL=$MODEL
ExecStart=/usr/bin/flock -n /run/qwen-worker.lock /usr/bin/python3 /root/worker_tick.py
StandardOutput=append:/root/worker.log
StandardError=append:/root/worker.log
UNIT
cat > /etc/systemd/system/qwen-worker.timer <<'UNIT'
[Unit]
Description=Qwen-Worker alle 30 Minuten
[Timer]
OnBootSec=5min
OnUnitActiveSec=30min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload

echo "== Prüfung"
ok=1
gh --version | head -1
if gh auth status >/dev/null 2>&1; then echo "GitHub: angemeldet"; else echo "GitHub: NICHT angemeldet (README, Schritt 3)"; ok=0; fi
if curl -s -m 10 "$OLLAMA/api/tags" | grep -q "\"$MODEL"; then echo "Ollama: $MODEL vorhanden"
else echo "Ollama: $MODEL nicht erreichbar (PC aus, Bildmodus oder Modell fehlt)"; fi
(cd "$REPO_DIR" && /root/venv/bin/python -m pytest -q 2>&1 | tail -1)

if [ "$ok" = 1 ]; then
  systemctl enable --now qwen-worker.timer >/dev/null
  echo "Timer aktiv:"; systemctl list-timers qwen-worker.timer --no-pager | sed -n 2p
else
  systemctl disable --now qwen-worker.timer >/dev/null 2>&1 || true
  echo "Timer bleibt aus, bis GitHub angemeldet ist. Danach dieses Skript erneut ausführen."
fi
