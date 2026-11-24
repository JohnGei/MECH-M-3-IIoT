"""Sendet Testnachrichten eines erfundenen Sensors an den Kurs-Broker.

Damit lässt sich die Bewertungsstufe "gut" vorführen: Eine neue Gruppe
erscheint im Dashboard, ohne dass etwas konfiguriert werden muss.
Ersetzt mosquitto_pub, das unter Windows meist nicht installiert ist.

Ausführen im laufenden Collector-Container (nutzt die Werte aus .env):
    docker compose exec collector python send_test_message.py
    docker compose exec collector python send_test_message.py --group test-werlberger --count 10
"""

import argparse
import json
import os
import random
import time
from datetime import datetime, timezone

import paho.mqtt.client as mqtt


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    parser = argparse.ArgumentParser(description="Testnachrichten an den MQTT-Broker senden")
    parser.add_argument("--group", default="test-werlberger", help="Gruppenname im Topic")
    parser.add_argument("--count", type=int, default=5, help="Anzahl der Messwerte")
    parser.add_argument("--interval", type=float, default=2.0, help="Sekunden zwischen den Messwerten")
    args = parser.parse_args()

    telemetry_topic = f"iiot/group/{args.group}/sensor/temperature/state"
    status_topic = f"iiot/group/{args.group}/status"
    sensor_id = f"{args.group}-sensor"

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"{args.group}-publisher-{random.randint(1000, 9999)}",
    )
    client.username_pw_set(os.environ["MQTT_USER"], os.environ["MQTT_PASSWORD"])
    client.connect(os.getenv("MQTT_HOST", "158.180.44.197"), int(os.getenv("MQTT_PORT", "1883")))
    client.loop_start()

    status = {"status": "ok", "timestamp": iso_now(), "sensor_id": sensor_id}
    client.publish(status_topic, json.dumps(status), qos=1).wait_for_publish()
    print("Gesendet:", status_topic, status)

    for _ in range(args.count):
        payload = {
            "timestamp": iso_now(),
            "sensor_id": sensor_id,
            "value": round(random.uniform(20.0, 25.0), 1),
            "unit": "°C",
        }
        client.publish(telemetry_topic, json.dumps(payload), qos=1).wait_for_publish()
        print("Gesendet:", telemetry_topic, payload)
        time.sleep(args.interval)

    # Nicht retained: Der Teststatus bleibt nach dem Test nicht dauerhaft
    # auf dem gemeinsamen Broker liegen.
    client.loop_stop()
    client.disconnect()


if __name__ == "__main__":
    main()
