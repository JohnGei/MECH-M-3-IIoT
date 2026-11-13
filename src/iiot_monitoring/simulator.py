"""Erzeugt kontinuierliche IIoT-Testdaten ohne MQTT-Broker."""

import json
import random
import time
from datetime import datetime, timezone


# Jede Gruppe besitzt zwei Sensoren.
# Sensoren desselben Typs haben unterschiedliche IDs.
SENSORS = [
    ("test-01", "temperature", "temp-01", 22.3, "°C"),
    ("test-01", "humidity", "hum-01", 51.0, "%"),
    ("test-02", "temperature", "temp-01", 21.9, "°C"),
    ("test-02", "temperature", "temp-02", 24.1, "°C"),
    ("test-03", "humidity", "hum-01", 43.0, "%"),
    ("test-03", "humidity", "hum-02", 49.0, "%")
]


def run_continuous_simulation(process_message, writer, interval=5.0):
    """
    Erzeugt fortlaufend neue Sensormessungen.

    - Alle Sensoren liefern regelmäßig Messwerte.
    - Die Werte schwanken leicht um einen Ausgangswert.
    - Die Gruppenstatus wechseln zeitweise.
    - Fehlerhafte bzw. offline geschaltete Gruppen senden
      während dieser Phase keine Sensormessungen.

    Alle Nachrichten werden über process_message() verarbeitet.
    """

    # Fester Seed für reproduzierbare Zufallszahlen
    rng = random.Random(42)

    previous_status = {}
    cycle = 0

    def emit(topic, data):
        """Übergibt eine simulierte MQTT-Nachricht an den Collector."""

        payload = json.dumps(data).encode("utf-8")
        process_message(topic, payload, writer)

    print(
        f"Kontinuierliche Simulation gestartet "
        f"({interval:g} s pro Zyklus)"
    )

    while True:

        phase = cycle % 12

        # Statusänderungen für definierte Simulationsphasen
        statuses = {
            "test-01": "ok",
            "test-02": "error" if 4 <= phase < 6 else "ok",
            "test-03": "offline" if 7 <= phase < 10 else "ok"
        }

        timestamp = datetime.now(timezone.utc).isoformat()

        # Status bei Änderung oder alle sechs Zyklen übertragen
        for group, status in statuses.items():

            if previous_status.get(group) != status or cycle % 6 == 0:

                emit(
                    f"iiot/group/{group}/status",
                    {
                        "status": status,
                        "timestamp": timestamp
                    }
                )

        previous_status = statuses

        # Neue Temperatur- und Feuchtigkeitswerte erzeugen
        for group, kind, sensor_id, base, unit in SENSORS:

            if statuses[group] != "ok":
                continue

            # Zufällige Abweichung von maximal ±0,6 Einheiten
            value = round(
                base + rng.uniform(-0.6, 0.6),
                2
            )

            emit(
                f"iiot/group/{group}/sensor/{kind}/state",
                {
                    "sensor_id": sensor_id,
                    "value": value,
                    "unit": unit,
                    "timestamp": timestamp
                }
            )

        cycle += 1
        time.sleep(interval)