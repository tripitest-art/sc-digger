#!/usr/bin/env bash
# Neuen Stand von GitHub holen und den Container neu bauen/starten.
#
#   ./update.sh          # baut, wenn der Code neuer ist als der zuletzt erfolgreich gebaute Stand
#   ./update.sh --force  # immer neu bauen
#
# Zugriff über den Deploy-Key (nur lesen), siehe BETRIEB.md.
# Lokale Änderungen an versionierten Dateien brechen das Update bewusst ab
# (--ff-only), statt sie still zu überschreiben.
#
# Warum .last-build: Früher wurde nur gebaut, wenn `git pull` etwas Neues holte. Scheiterte
# danach der Build, sah der nächste Aufruf „nichts Neues“ und der Container lief still mit dem
# alten Image weiter (so passiert am 28.09.). Jetzt zählt, was zuletzt ERFOLGREICH gebaut und
# gestartet wurde; ein gescheiterter Build wird beim nächsten Aufruf automatisch wiederholt.
set -euo pipefail
cd "$(dirname "$0")"
GIT=(git -c safe.directory="$PWD")   # .git gehört dem Benutzer claude, Aufruf auch als root
STATE=.last-build                    # von Git ignoriert

"${GIT[@]}" pull --ff-only --quiet
current=$("${GIT[@]}" rev-parse --short HEAD)
built=$(cat "$STATE" 2>/dev/null || echo "unbekannt")

if [ "$current" = "$built" ] && [ "${1:-}" != "--force" ]; then
  echo "Schon aktuell ($current)."
  exit 0
fi
if [ "$built" != "unbekannt" ] && [ "$built" != "$current" ]; then
  "${GIT[@]}" log --oneline "$built..$current" 2>/dev/null || true
fi

if ! docker compose up -d --build; then
  echo "FEHLER: Build/Start fehlgeschlagen. Der Container läuft ggf. noch mit $built." >&2
  echo "Der nächste Aufruf von ./update.sh versucht es erneut." >&2
  exit 1
fi

# Rauchtest: startet das neue Image und läuft das Programm? Erst dann gilt der Stand als ausgerollt.
ok=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if docker compose exec -T sc-digger python -m sc_digger.main --help >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep "${UPDATE_RETRY_SLEEP:-3}"
done
if [ "$ok" != 1 ]; then
  echo "FEHLER: Neuer Container antwortet nicht (siehe: docker compose logs --tail 50)." >&2
  echo "Der nächste Aufruf von ./update.sh versucht es erneut." >&2
  exit 1
fi

echo "$current" > "$STATE"
docker image prune -f >/dev/null
echo "Aktualisiert: $built -> $current"
