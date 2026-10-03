#!/bin/sh
set -e
# Cron startet Jobs mit leerer Umgebung: kein /usr/local/bin im PATH (-> "python: not found")
# und keine Variablen aus der .env (Telegram, SoundCloud-Token). Deshalb die Umgebung des
# Containers einmal in eine Datei schreiben, die der Cron-Job vor dem Start lädt.
export -p > /app/cron.env
chmod 600 /app/cron.env

# Wöchentlicher Kaufliste-Lauf (Sonntag 20:00)
if [ -f /etc/cron.d/sc-digger ] && ! grep -q "send_kaufliste" /etc/cron.d/sc-digger; then
    echo "0 20 * * 0 . /app/cron.env; cd /app && python -c \"from sc_digger.models import Config; from sc_digger.output import send_kaufliste; send_kaufliste(Config.load('config.yaml'))\" >> /proc/1/fd/1 2>&1" >> /etc/cron.d/sc-digger
    crontab /etc/cron.d/sc-digger 2>/dev/null || true
fi

# Täglicher Scout-Lauf (18:00), getrennt vom discover-Lauf im Dockerfile
if [ -f /etc/cron.d/sc-digger ] && ! grep -q "sc_digger.main scout" /etc/cron.d/sc-digger; then
    echo "0 18 * * * . /app/cron.env; cd /app && python -m sc_digger.main scout >> /proc/1/fd/1 2>&1" >> /etc/cron.d/sc-digger
    crontab /etc/cron.d/sc-digger 2>/dev/null || true
fi

cron

# Web-Oberfläche nur, wenn ein Passwort gesetzt ist (Standard: aus)
if [ -n "$SC_DIGGER_WEB_PASSWORD" ]; then
    # Konfigeditor standardmäßig aktivieren (Override-Datei im persistenten Volume /data);
    # wer abweichen will, setzt SC_DIGGER_CONFIG_LOCAL selbst (leer = Editor aus).
    export SC_DIGGER_CONFIG_LOCAL="${SC_DIGGER_CONFIG_LOCAL:-/data/config.local.yaml}"
    python -m sc_digger.web --host 0.0.0.0 --port "${SC_DIGGER_WEB_PORT:-8080}" >> /proc/1/fd/1 2>&1 &
fi

# Bot-Listener als Hauptprozess. Er beendet sich nie von selbst (auch ohne Telegram-Daten),
# damit der Container und damit der tägliche Cron-Lauf weiterläuft.
exec python -m sc_digger.bot
