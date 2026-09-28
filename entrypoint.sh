#!/bin/sh
set -e
# Cron startet Jobs mit leerer Umgebung: kein /usr/local/bin im PATH (-> "python: not found")
# und keine Variablen aus der .env (Telegram, SoundCloud-Token). Deshalb die Umgebung des
# Containers einmal in eine Datei schreiben, die der Cron-Job vor dem Start lädt.
export -p > /app/cron.env
chmod 600 /app/cron.env
cron
# Bot-Listener als Hauptprozess. Er beendet sich nie von selbst (auch ohne Telegram-Daten),
# damit der Container und damit der tägliche Cron-Lauf weiterläuft.
exec python -m sc_digger.bot
