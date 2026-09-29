#!/bin/sh
set -e
# Cron startet Jobs mit leerer Umgebung: kein /usr/local/bin im PATH (-> "python: not found")
# und keine Variablen aus der .env (Telegram, SoundCloud-Token). Deshalb die Umgebung des
# Containers einmal in eine Datei schreiben, die der Cron-Job vor dem Start lädt.
export -p > /app/cron.env
chmod 600 /app/cron.env

# Wöchentlicher Kaufliste-Lauf (Sonntag 20:00)
# 0 20 * * 0 root . /app/cron.env; cd /app && python -c "from sc_digger.output import send_kaufliste; from sc_digger.models import Config; send_kaufliste(Config.load())" >> /var/log/cron.log 2>&1
if [ -f /etc/cron.d/sc-digger ]; then
    echo "0 20 * * 0 . /app/cron.env; cd /app && python -c 'from sc_digger.output import send_kaufliste; from sc_digger.models import Config; send_kaufliste(Config.load())' >> /proc/1/fd/1 2>&1" >> /etc/cron.d/sc-digger
    crontab /etc/cron.d/sc-digger 2>/dev/null || true
fi

cron
# Bot-Listener als Hauptprozess. Er beendet sich nie von selbst (auch ohne Telegram-Daten),
# damit der Container und damit der tägliche Cron-Lauf weiterläuft.
exec python -m sc_digger.bot
