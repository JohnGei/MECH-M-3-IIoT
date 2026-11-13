"""InfluxDB-Schnittstelle für Sensormessungen und Gerätestatus."""

import os

from dotenv import load_dotenv
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS


class InfluxWriter:
    """Speichert Messwerte und Gerätestatus in InfluxDB."""

    def __init__(self):

        # Konfiguration aus der .env laden
        load_dotenv()

        self.org = os.environ["INFLUX_ORG"]
        self.bucket = os.environ["INFLUX_BUCKET"]

        # Verbindung zur Datenbank initialisieren
        self.client = InfluxDBClient(
            url=os.environ["INFLUX_URL"],
            token=os.environ["INFLUX_TOKEN"],
            org=self.org
        )

        # Synchrone Schreibvorgänge für einfache Fehleranalyse
        self.api = self.client.write_api(
            write_options=SYNCHRONOUS
        )

    def write(self, measurement: dict):
        """
        Speichert einen Temperatur- oder Feuchtigkeitsmesswert.

        Gruppe und Sensor-ID werden als Tags gespeichert.
        Der Messwert wird als numerisches Feld gespeichert.
        """

        point = (
            Point(measurement["type"])
            .tag("group", measurement["group"])
            .tag("sensor_id", measurement["sensor_id"])
            .field("value", measurement["value"])
            .field("unit", measurement["unit"])
            .time(measurement["timestamp"])
        )

        self.api.write(
            bucket=self.bucket,
            org=self.org,
            record=point
        )

    def write_status(self, group: str, status: str, timestamp):
        """Speichert den zuletzt gemeldeten Gerätestatus."""

        point = (
            Point("device_status")
            .tag("group", group)
            .field("status", status)
            .time(timestamp)
        )

        self.api.write(
            bucket=self.bucket,
            org=self.org,
            record=point
        )

    def close(self):
        """Schließt die Datenbankverbindung."""

        self.client.close()