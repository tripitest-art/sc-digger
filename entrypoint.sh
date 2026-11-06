#!/bin/sh
set -e
# Cron startet Jobs mit leerer Umgebung: kein /usr/local/bin im PATH (-> "python: not found")
# und keine Variablen aus der .env (Telegram, SoundCloud-Token). Deshalb die Umgebung des
# Containers einmal in eine Datei schreiben, die der Cron-Job vor dem Start lädt.
# Die Override-Datei müssen auch die Cron-Läufe sehen; `export -p` hält nur fest, was bisher
# gesetzt ist, deshalb steht der Standardwert hier (unabhängig vom Web-Passwort).
export SC_DIGGER_CONFIG_LOCAL="${SC_DIGGER_CONFIG_LOCAL:-/data/config.local.yaml}"
export -p > /app/cron.env
chmod 600 /app/cron.env

# Scheitert crontab, fehlen die Jobs still (Healthcheck prüft nur, ob cron läuft).
# Lieber laut abbrechen: der Neustart ist in docker compose ps sichtbar.
install_crontab() {
    crontab /etc/cron.d/sc-digger || {
        echo "FEHLER: crontab konnte nicht installiert werden, Cron-Jobs fehlen" >&2
        exit 1
    }
}

# Wöchentlicher Verfügbarkeits-Wächter (Sonntag 19:00, vor der Kaufliste um 20:00)
if [ -f /etc/cron.d/sc-digger ] && ! grep -q "sc_digger.watch" /etc/cron.d/sc-digger; then
    echo "0 19 * * 0 . /app/cron.env; cd /app && python -m sc_digger.watch >> /proc/1/fd/1 2>&1" >> /etc/cron.d/sc-digger
    install_crontab
fi

# Wöchentlicher Kaufliste-Lauf (Sonntag 20:00)
if [ -f /etc/cron.d/sc-digger ] && ! grep -q "send_kaufliste" /etc/cron.d/sc-digger; then
    echo "0 20 * * 0 . /app/cron.env; cd /app && python -c \"from sc_digger.models import Config; from sc_digger.output import send_kaufliste; send_kaufliste(Config.load('config.yaml'))\" >> /proc/1/fd/1 2>&1" >> /etc/cron.d/sc-digger
    install_crontab
fi

# Täglicher Scout-Lauf (18:00), getrennt vom discover-Lauf im Dockerfile
if [ -f /etc/cron.d/sc-digger ] && ! grep -q "sc_digger.main scout" /etc/cron.d/sc-digger; then
    echo "0 18 * * * . /app/cron.env; cd /app && python -m sc_digger.main scout >> /proc/1/fd/1 2>&1" >> /etc/cron.d/sc-digger
    install_crontab
fi

# Täglicher Übernommen-Abgleich (06:45, vor dem discover-Lauf): inkrementelles audit der
# Sammlung (Cache über mtime/Größe) und Fingerprint-Abgleich Inbox -> Sammlung (ROADMAP 3.12).
if [ -f /etc/cron.d/sc-digger ] && ! grep -q "audit --check-adopted" /etc/cron.d/sc-digger; then
    echo "45 6 * * * . /app/cron.env; cd /app && python -m sc_digger.main audit --check-adopted >> /proc/1/fd/1 2>&1" >> /etc/cron.d/sc-digger
    install_crontab
fi

# Tägliche Sicherung der SQLite-Datenbanken (06:30, vor dem Übernommen-Abgleich und discover)
if [ -f /etc/cron.d/sc-digger ] && ! grep -q "sc_digger.backup run" /etc/cron.d/sc-digger; then
    echo "30 6 * * * . /app/cron.env; cd /app && python -m sc_digger.backup run >> /proc/1/fd/1 2>&1" >> /etc/cron.d/sc-digger
    install_crontab
fi

# Täglicher API-Kanarienvogel (07:15, vor dem discover-Lauf)
if [ -f /etc/cron.d/sc-digger ] && ! grep -q "sc_digger.canary" /etc/cron.d/sc-digger; then
    echo "15 7 * * * . /app/cron.env; cd /app && python -m sc_digger.canary >> /proc/1/fd/1 2>&1" >> /etc/cron.d/sc-digger
    install_crontab
fi

cron

# Web-Oberfläche nur, wenn ein Passwort gesetzt ist (Standard: aus)
if [ -n "$SC_DIGGER_WEB_PASSWORD" ]; then
    # Konfigeditor standardmäßig aktivieren: die Variable ist oben schon gesetzt, diese
    # Zeile ist nur noch Absicherung (wer abweichen will, setzt sie selbst; leer = Editor aus).
    export SC_DIGGER_CONFIG_LOCAL="${SC_DIGGER_CONFIG_LOCAL:-/data/config.local.yaml}"
    python -m sc_digger.web --host 0.0.0.0 --port "${SC_DIGGER_WEB_PORT:-8080}" >> /proc/1/fd/1 2>&1 &
fi

# Bot-Listener als Hauptprozess. Er beendet sich nie von selbst (auch ohne Telegram-Daten),
# damit der Container und damit der tägliche Cron-Lauf weiterläuft.
exec python -m sc_digger.bot
