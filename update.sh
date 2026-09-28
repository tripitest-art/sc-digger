#!/usr/bin/env bash
# Neuen Stand von GitHub holen und den Container neu bauen/starten.
#
#   ./update.sh          # nur wenn es Neues gibt
#   ./update.sh --force  # immer neu bauen
#
# Zugriff auf das private Repo über den Deploy-Key (nur lesen), siehe README.
# Lokale Änderungen an versionierten Dateien brechen das Update bewusst ab
# (--ff-only), statt sie still zu überschreiben.
set -euo pipefail
cd "$(dirname "$0")"
GIT=(git -c safe.directory="$PWD")   # .git gehört dem Benutzer claude, Aufruf auch als root

before=$("${GIT[@]}" rev-parse --short HEAD)
"${GIT[@]}" pull --ff-only --quiet
after=$("${GIT[@]}" rev-parse --short HEAD)

if [ "$before" = "$after" ] && [ "${1:-}" != "--force" ]; then
  echo "Schon aktuell ($after)."
  exit 0
fi
[ "$before" != "$after" ] && "${GIT[@]}" log --oneline "$before..$after"

docker compose up -d --build
docker image prune -f >/dev/null
echo "Aktualisiert: $before -> $after"
