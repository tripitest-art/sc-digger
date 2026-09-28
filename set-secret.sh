#!/usr/bin/env bash
# Zugangsdaten sicher in die .env schreiben: verdeckte Eingabe, Formatprüfung,
# Zeile ersetzen statt anhängen, Container neu starten, Verbindung testen.
#
#   ./set-secret.sh TELEGRAM_BOT_TOKEN
#   ./set-secret.sh TELEGRAM_CHAT_ID
#   ./set-secret.sh SOUNDCLOUD_AUTH_TOKEN
set -euo pipefail
cd "$(dirname "$0")"

NAME="${1:-}"
case "$NAME" in
  TELEGRAM_BOT_TOKEN)    PATTERN='^[0-9]{6,12}:[A-Za-z0-9_-]{30,}$'; HINT='1234567890:AAH...' ;;
  TELEGRAM_CHAT_ID)      PATTERN='^-?[0-9]+$';                        HINT='eine Zahl, z. B. 518660030' ;;
  SOUNDCLOUD_AUTH_TOKEN) PATTERN='^2-[0-9]+-[0-9]+-[A-Za-z0-9]+$';    HINT='2-123456-12345678-AbCd...' ;;
  *) echo "Aufruf: $0 TELEGRAM_BOT_TOKEN | TELEGRAM_CHAT_ID | SOUNDCLOUD_AUTH_TOKEN"; exit 1 ;;
esac

read -rsp "Wert für $NAME einfügen (wird nicht angezeigt), dann Enter: " VALUE; echo
VALUE="$(printf '%s' "$VALUE" | tr -d '[:space:]')"
if ! [[ "$VALUE" =~ $PATTERN ]]; then
  echo "Das sieht nicht nach $NAME aus (erwartet: $HINT). Nichts geändert."
  exit 1
fi

touch .env
# Behalten: gültige VAR=...-Zeilen (außer der zu setzenden), Kommentare, Leerzeilen.
# Verworfen: kaputte Zeilen ohne gültigen Namen, z. B. ein eingefügter Token ohne "NAME=".
{ grep -E '^([A-Za-z_][A-Za-z0-9_]*=|#|$)' .env | grep -v "^${NAME}=" || true
  printf '%s=%s\n' "$NAME" "$VALUE"; } > .env.tmp
cat .env.tmp > .env && rm -f .env.tmp   # cat statt mv: Besitzer und Rechte der .env bleiben
unset VALUE
echo "$NAME gesetzt. Starte Container neu ..."
docker compose up -d >/dev/null 2>&1
sleep 5

docker compose exec -T sc-digger python - "$NAME" <<'PY'
import os, sys, requests
from sc_digger.output import telegram_call, TelegramError
name = sys.argv[1]
try:
    if name == "SOUNDCLOUD_AUTH_TOKEN":
        r = requests.get("https://api-v2.soundcloud.com/me", timeout=15,
                         headers={"Authorization": f"OAuth {os.environ[name]}"})
        print("SoundCloud-Login ok:", r.json().get("username") if r.ok else f"abgelehnt ({r.status_code})")
    elif name == "TELEGRAM_BOT_TOKEN":
        print("Telegram-Bot ok:", telegram_call(os.environ[name], "getMe", http="get")["result"]["username"])
    else:
        telegram_call(os.environ["TELEGRAM_BOT_TOKEN"], "sendMessage",
                      json={"chat_id": os.environ[name], "text": "✅ sc-digger: Chat-ID funktioniert"})
        print("Testnachricht an die Chat-ID gesendet")
except (TelegramError, requests.RequestException) as e:
    print("Test fehlgeschlagen:", type(e).__name__, str(e)[:200])
PY
