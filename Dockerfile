FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg cron libsndfile1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sc_digger ./sc_digger
COPY config.yaml .
# Täglicher Lauf um 07:30, Ausgabe ins Container-Log
RUN echo "30 7 * * * cd /app && python -m sc_digger.main >> /proc/1/fd/1 2>&1" > /etc/cron.d/sc-digger \
    && chmod 0644 /etc/cron.d/sc-digger && crontab /etc/cron.d/sc-digger
CMD ["cron", "-f"]
