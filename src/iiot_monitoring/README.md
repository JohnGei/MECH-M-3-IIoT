# IIoT Monitoring

Containerisierte Erfassung und Visualisierung von MQTT-Sensordaten mit Python, InfluxDB 2 und Grafana OSS.

## Komponenten

- **MQTT-Collector (`mqtt_collector.py`)**: Abonniert `iiot/group/+/sensor/+/state` und `iiot/group/+/status`, prüft JSON-Nachrichten und übergibt gültige Werte an InfluxDB.
- **Simulation (`simulator.py`)**: Erzeugt ohne Broker kontinuierliche Temperatur-, Feuchtigkeits- und Statusmeldungen.
- **InfluxDB (`influx_writer.py`, Docker)**: Speichert Zeitreihen als `temperature`, `humidity` und `device_status` mit Gruppenkennung, gegebenenfalls Sensor-ID und Zeitstempel.
- **Grafana (Docker)**: Visualisiert Messwerte, Kennzahlen und den zuletzt gemeldeten Gruppenstatus.

## Voraussetzungen

- Docker Desktop mit Docker Compose
- Zugriff auf den MQTT-Broker für den Echtbetrieb
- Optional: Python und `uv` für die lokale Entwicklung

## Start auf einem bereits eingerichteten Rechner

Im Verzeichnis mit `compose.yaml`:

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f collector
```

- Grafana: <http://localhost:3000>
- InfluxDB: <http://localhost:8086>

Der Collector-Modus wird in `compose.yaml` unter `collector.command` eingestellt.

## Ersteinrichtung auf einem neuen Rechner

1. Projektdateien aus Git bereitstellen und `.env.example` nach `.env` kopieren (`Copy-Item .env.example .env` unter PowerShell).
2. `INFLUX_ORG`, `INFLUX_BUCKET`, MQTT-Zugangsdaten und zunächst einen Platzhalter für `INFLUX_TOKEN` in `.env` eintragen.
3. Zunächst nur die Datenbank starten: `docker compose up -d influxdb`.
4. In <http://localhost:8086> InfluxDB-Benutzer, Organisation und Bucket einmalig einrichten. Namen müssen zur `.env` passen. Ein API-Token mit Schreibberechtigung für den Bucket erstellen, in `.env` als `INFLUX_TOKEN` eintragen.
5. Alle Dienste starten: `docker compose up -d --build`.
6. In <http://localhost:3000> Grafana einrichten. Unter **Connections → Data sources** InfluxDB (Flux) konfigurieren: URL `http://influxdb:8086`, Organisation und Bucket aus `.env`, Token mit **Leseberechtigung**. Mit **Save & Test** prüfen.
7. Gesichertes Dashboard-JSON, falls vorhanden, in Grafana importieren; andernfalls die Panels neu anlegen.

**Hinweis:** Die Ersteinrichtung von InfluxDB und die Grafana-Datenquelle sind in dieser Projektversion noch nicht vollständig automatisiert. Für eine sofort einsatzbereite Neuinstallation können sie später per InfluxDB-Initialisierung und Grafana-Provisioning bereitgestellt werden.

## Betriebsarten

In `compose.yaml`, beim Dienst `collector`:

**Echter MQTT-Broker:**

```yaml
command: ["python", "-u", "mqtt_collector.py"]
```

**Kontinuierliche Offline-Simulation:**

```yaml
command: ["python", "-u", "mqtt_collector.py", "--simulate", "--continuous", "--interval", "5"]
```

Nach Änderungen: `docker compose up -d --build collector`.

Optional lokal mit `uv` testen (einen parallel laufenden Simulations-Collector zuvor stoppen):

```powershell
uv sync --locked
uv run python mqtt_collector.py --simulate
uv run python mqtt_collector.py --simulate --continuous --interval 5
```

## Datenformat und Prüfung

- Temperatur: `iiot/group/<gruppe>/sensor/temperature/state`, JSON mit `value`, `unit` (`°C` oder `K`), optional `sensor_id` und `timestamp`.
- Relative Feuchte: `iiot/group/<gruppe>/sensor/humidity/state`, JSON mit `value` und `unit: "%"` (**projektspezifische Annahme; Topic/Format abstimmen**).
- Gruppenstatus: `iiot/group/<gruppe>/status`, JSON mit `{"status":"ok"}`, `{"status":"error"}` oder `{"status":"offline"}`.

Mehrere gleichartige Sensoren einer Gruppe benötigen unterschiedliche `sensor_id`-Werte. Der Status gilt zunächst für die **Gruppe**, nicht für jeden einzelnen Sensor. Fehlerhafte Nachrichten werden protokolliert und verworfen.

Kontrolle: `docker compose logs -f collector`; anschließend neue Messwerte im InfluxDB Data Explorer und in Grafana prüfen (Zeitraum z. B. letzte 15 Minuten).

## Datenhaltung, Git und Sicherheit

- Messwerte und InfluxDB-Einstellungen liegen in den Docker-Volumes `influxdb_data` und `influxdb_config`; Grafana-Konfiguration/Dashboards im Volume `grafana_data`.
- Diese Volumes gehören **nicht zum Git-Repository**. Das Projekt muss bei Neuinstallation initialisiert werden; historische Daten erfordern gesonderte Backups/Restores.
- Dashboard-Definitionen für die Übergabe als **JSON (Classic)** exportieren und im Repository ablegen, etwa unter `grafana/dashboards/`.
- `.env`, `.venv/`, `__pycache__/`, lokale CSV-Dateien und Backups **nicht committen**. `.env.example` enthält nur Platzhalter.
- MQTT-Port 1883 ist in der Kurskonfiguration unverschlüsselt; Zugangsdaten nicht in Git veröffentlichen.

## Betrieb und Fehleranalyse

```powershell
docker compose ps                      # Status aller Dienste
docker compose logs -f collector        # Collector-Ausgabe
docker compose stop                    # Container anhalten
docker compose up -d                   # Container wieder starten
docker compose down                    # Container entfernen, Volumes behalten
```

**Nicht `docker compose down -v` verwenden**, wenn vorhandene Messwerte und Grafana-Einstellungen erhalten bleiben sollen.

Spezifikation: <https://github.com/jhumci/MECH-M-3-IIoT/blob/main/docs/iot-specs/conventions.md>
