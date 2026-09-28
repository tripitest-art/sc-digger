FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg cron libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sc_digger ./sc_digger
COPY config.yaml .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh
# Täglicher Lauf um 07:30 (TZ aus docker-compose). cron.env liefert PATH und .env-Variablen,
# ohne sie findet cron weder python noch die Telegram-Zugangsdaten (siehe entrypoint.sh).
RUN echo "30 7 * * * . /app/cron.env; cd /app && python -m sc_digger.main >> /proc/1/fd/1 2>&1" > /etc/cron.d/sc-digger \
    && chmod 0644 /etc/cron.d/sc-digger && crontab /etc/cron.d/sc-digger
# cron im Hintergrund für den täglichen Digest, Bot-Listener im Vordergrund für On-Demand-Checks
CMD ["./entrypoint.sh"]
