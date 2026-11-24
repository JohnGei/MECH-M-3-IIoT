"""Empfängt MQTT-Messwerte und Gerätestatus oder simuliert Testdaten."""

import argparse
import getpass
import json
import math
import os
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from influx_writer import InfluxWriter
from simulator import run_continuous_simulation

BROKER, PORT = "158.180.44.197", 1883
TOPICS = ["iiot/group/+/sensor/+/state", "iiot/group/+/status"]
VALID_STATUS = {"ok", "error", "offline"}
MAX_CLOCK_SKEW_SECONDS = 24 * 60 * 60  # max. 1 Tag Abweichung


def parse_timestamp(value):
    """Liest einen UTC-Zeitstempel; fehlt er, gilt die Empfangszeit."""
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, str):
        raise ValueError("Zeitstempel muss ein String sein")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Zeitstempel ohne Zeitzone")
    dt = dt.astimezone(timezone.utc)

    # Geräte ohne gestellte Uhr senden z.B. das Jahr 2000. Solche Werte
    # wären im Dashboard unsichtbar, daher gilt dann die Empfangszeit.
    now = datetime.now(timezone.utc)
    if abs((now - dt).total_seconds()) > MAX_CLOCK_SKEW_SECONDS:
        print(f"Warnung: unplausibler Zeitstempel {value}, nutze Empfangszeit")
        return now
    return dt


def process_message(topic, payload, writer):
    """Prüft Topic und JSON und speichert Messwerte oder Gruppenstatus in InfluxDB.

    Fehlerhafte MQTT-Nachrichten werden protokolliert und nicht gespeichert.
    """
    try:
        parts = topic.split("/")
        if len(parts) not in (4, 6) or parts[:2] != ["iiot", "group"] or not parts[2]:
            return
        group = parts[2]

        text = payload.decode("utf-8").strip()

        # Ältere Firmware anderer Gruppen sendet den Status noch als reinen
        # Text ("ok", "offline"). Wird toleriert, damit die Geräteübersicht
        # trotzdem vollständig ist.
        if len(parts) == 4 and parts[3] == "status" and text in VALID_STATUS:
            writer.write_status(group, text, datetime.now(timezone.utc))
            print(f"Status gespeichert (Textformat): {group} | {text}")
            return

        # MQTT-Payload dekodieren; erwartet wird ein JSON-Objekt.
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("JSON-Objekt erwartet")
        timestamp = parse_timestamp(data.get("timestamp"))

        # Gerätestatus über das MQTT-Topic verarbeiten.
        if len(parts) == 4 and parts[3] == "status":
            status = data.get("status")
            if status not in VALID_STATUS:
                raise ValueError("Ungültiger Status")
            writer.write_status(group, status, timestamp)
            print(f"Status gespeichert: {group} | {status}")
            return

        # Sensor-Topic: Temperatur oder Luftfeuchtigkeit.
        if len(parts) != 6 or parts[3] != "sensor" or parts[5] != "state":
            return
        sensor_type = parts[4]
        if sensor_type not in ("temperature", "humidity"):
            return  # Weitere Sensortypen bei Bedarf hier ergänzen.

        # Messwert muss numerisch und endlich sein.
        raw_value = data["value"]
        if isinstance(raw_value, bool):
            raise ValueError("Boolescher Messwert")
        value = float(raw_value)
        if not math.isfinite(value):
            raise ValueError("Ungültiger Messwert")

        # Einheiten: Temperatur °C/K, relative Feuchtigkeit %.
        # Bei abweichenden MQTT-Formaten hier anpassen.
        unit = data.get("unit")
        if sensor_type == "temperature":
            if unit == "K":
                value -= 273.15
            elif unit != "°C":
                raise ValueError(f"Unbekannte Temperatureinheit: {unit}")
            unit = "°C"
        else:
            if unit != "%" or not 0 <= value <= 100:
                raise ValueError("Feuchte benötigt 0–100 % relative Feuchtigkeit")

        # Eindeutige Sensor-ID für mehrere gleichartige Sensoren.
        # Alternative Feldnamen bei Bedarf ergänzen.
        sensor_id = data.get("sensor_id") or data.get("sensorId")
        if not isinstance(sensor_id, str) or not sensor_id.strip():
            sensor_id = f"{group}-{sensor_type}-unknown"
            print(f"Warnung: Keine Sensor-ID in {topic}")

        writer.write({
            "type": sensor_type, "group": group, "sensor_id": sensor_id,
            "value": value, "unit": unit, "timestamp": timestamp
        })
        print(f"Messwert gespeichert: {group} | {sensor_id} | {value:.2f} {unit}")

    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        print(f"Ungültige Nachricht ({topic}): {error}")
    except Exception as error:
        print(f"Fehler beim Speichern ({topic}): {error}")


def on_connect(client, userdata, flags, reason_code, properties):
    """Abonniert nach erfolgreicher Verbindung Sensor- und Status-Topics."""
    if reason_code == 0:
        for topic in TOPICS:
            client.subscribe(topic)
        print("MQTT verbunden; Sensoren und Status abonniert")
    else:
        print("MQTT-Verbindungsfehler:", reason_code)


def on_message(client, userdata, msg):
    """Leitet eingehende MQTT-Nachrichten an die Verarbeitung weiter."""
    process_message(msg.topic, msg.payload, userdata)


def run_mqtt(writer):
    """Verbindet sich mit dem Broker und empfängt Nachrichten dauerhaft."""
    load_dotenv()

    # Docker nutzt Umgebungsvariablen; lokal ist Eingabe möglich.
    username = os.getenv("MQTT_USER") or input("MQTT-Benutzername: ")
    password = os.getenv("MQTT_PASSWORD") or getpass.getpass("MQTT-Passwort: ")

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        protocol=mqtt.MQTTv5, userdata=writer
    )
    client.username_pw_set(username, password)
    client.on_connect, client.on_message = on_connect, on_message
    client.connect(
        os.getenv("MQTT_HOST", BROKER),
        int(os.getenv("MQTT_PORT", PORT)),
        keepalive=60
    )
    client.loop_forever()


def run_simulation(writer):
    """Testet drei Gruppenkombinationen und fehlerhafte Nachrichten ohne Broker."""
    now = datetime.now(timezone.utc).isoformat()
    cases = [
        ("test-01", "temperature", "temp-01", 22.3, "°C"),
        ("test-01", "humidity", "hum-01", 51.0, "%"),
        ("test-02", "temperature", "temp-01", 21.9, "°C"),
        ("test-02", "temperature", "temp-02", 24.1, "°C"),
        ("test-03", "humidity", "hum-01", 43.0, "%"),
        ("test-03", "humidity", "hum-02", 49.0, "%"),
    ]
    for group, kind, sensor_id, value, unit in cases:
        topic = f"iiot/group/{group}/sensor/{kind}/state"
        payload = {"sensor_id": sensor_id, "value": value, "unit": unit, "timestamp": now}
        process_message(topic, json.dumps(payload).encode(), writer)

    # MQTT-Gerätestatus separat übertragen.
    for group, status in [("test-01", "ok"), ("test-02", "error"), ("test-03", "offline")]:
        process_message(f"iiot/group/{group}/status", json.dumps({"status": status}).encode(), writer)

    # Fehlerhafte Nachrichten dürfen den Collector nicht beenden.
    process_message("iiot/group/test-01/status", b"offline", writer)
    process_message("iiot/group/test-01/sensor/humidity/state", b'{"value": 125, "unit": "%"}', writer)
    print("Simulation abgeschlossen")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--continuous", action="store_true")
    parser.add_argument("--interval", type=float, default=5.0)
    args = parser.parse_args()

    if args.interval <= 0:
        parser.error("--interval muss größer als 0 sein")
    if args.continuous and not args.simulate:
        parser.error("--continuous benötigt --simulate")

    writer = InfluxWriter()
    try:
        if args.simulate and args.continuous:
            run_continuous_simulation(process_message, writer, args.interval)
        elif args.simulate:
            run_simulation(writer)
        else:
            run_mqtt(writer)
    except KeyboardInterrupt:
        print("Collector beendet")
    finally:
        writer.close()