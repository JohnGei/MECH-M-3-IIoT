# Python-Laufzeitumgebung
FROM python:3.11-slim

# uv-Paketmanager aus dem offiziellen Image kopieren
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /uvx /bin/

WORKDIR /app

# Python-Abhängigkeiten lokal im Container installieren
ENV UV_PYTHON_DOWNLOADS=0
ENV PATH="/app/.venv/bin:$PATH"

# Projektabhängigkeiten aus uv.lock installieren
COPY pyproject.toml uv.lock ./

RUN uv sync --locked --no-dev --no-install-project

# Programmdateien in den Container kopieren
COPY mqtt_collector.py simulator.py influx_writer.py send_test_message.py ./

# Standardmäßig den MQTT-Collector starten
CMD ["python", "-u", "mqtt_collector.py"]