# IIoT Monitoring

Erfassung, Speicherung und Visualisierung von MQTT-Sensordaten mit einem Python-Collector, **InfluxDB 2** und **Grafana OSS**. Alle drei Dienste werden mit Docker Compose gestartet. InfluxDB wird bei einer Neuinstallation initialisiert; Grafana erhält Datenquelle und Dashboard automatisch über Provisioning.

## Komponenten

| Komponente | Aufgabe |
| --- | --- |
| `mqtt_collector.py` | Empfängt MQTT-Nachrichten, prüft Topic, JSON, Messwerte und Einheiten. |
| `simulator.py` | Erzeugt kontinuierliche Testdaten ohne MQTT-Broker. |
| `influx_writer.py` | Schreibt Sensormesswerte und Gruppenstatus in InfluxDB. |
| InfluxDB 2 | Speichert die Messreihen `temperature`, `humidity` und `device_status`. |
| Grafana | Zeigt Messwerte, Statistik und den Gruppenstatus im Dashboard `Sensor_Data` an. |

Grafana lädt die InfluxDB-Datenquelle aus `grafana/provisioning/datasources/influxdb.yaml` und das Dashboard aus `grafana/dashboards/sensor_data.json` über `grafana/provisioning/dashboards/dashboards.yaml`.

## Voraussetzungen

- Docker Desktop mit Docker Compose
- Für den echten MQTT-Betrieb: Netzwerkzugang zum Kurs-Broker und gültige MQTT-Zugangsdaten
- Optional für die lokale Python-Entwicklung: `uv`

## Erststart nach Git-Clone

Im Repository in das Monitoring-Verzeichnis wechseln:

```powershell
cd src/iiot_monitoring
Copy-Item .env.example .env
```

In `.env` **vor dem ersten Start** sichere Werte für `INFLUX_USER`, `INFLUX_PASSWORD`, `INFLUX_TOKEN`, `GRAFANA_USER` und `GRAFANA_PASSWORD` eintragen. Die Werte von `INFLUX_ORG=iiot` und `INFLUX_BUCKET=sensor_data` zum Projekt passend beibehalten. Für den Live-Betrieb zusätzlich `MQTT_USER` und `MQTT_PASSWORD` eintragen.

```powershell
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Weboberflächen:

- **InfluxDB:** <http://localhost:8086> — Anmeldung mit `INFLUX_USER` / `INFLUX_PASSWORD`
- **Grafana:** <http://localhost:3000> — Anmeldung mit `GRAFANA_USER` / `GRAFANA_PASSWORD`

Beim **ersten Start mit leeren Volumes** erstellt InfluxDB Organisation, Bucket und initiales Token aus `.env`; Grafana lädt Datenquelle und Dashboard aus den eingecheckten Konfigurationsdateien. Dies wurde mit einer separaten Docker-Compose-Testumgebung und neuen Volumes geprüft.

**Wichtig:** Die Initialisierungsvariablen ändern **keine** bereits eingerichteten Benutzer, Passwörter oder InfluxDB-Tokens. Bei bestehenden Volumes gelten die bereits gespeicherten Zugangsdaten.

## Betriebsarten des Collectors

Standardmäßig empfängt der Collector die **Live-Daten aller Gruppen vom Kurs-Broker** (`MQTT_USER` und `MQTT_PASSWORD` in `.env` erforderlich):

```yaml
command: ["python", "-u", "mqtt_collector.py"]
```

Zum Testen ohne Broker kann in `docker-compose.yml` unter `collector.command` die kontinuierliche Simulation gewählt werden:

```yaml
command: ["python", "-u", "mqtt_collector.py", "--simulate", "--continuous", "--interval", "5"]
```

Danach den Collector aktualisieren:

```powershell
docker compose up -d --build collector
docker compose logs --tail=50 collector
```

### Neuen Sensor testen

`send_test_message.py` sendet Messwerte und einen Status für eine erfundene Gruppe. Sie erscheint im Dashboard, ohne dass etwas konfiguriert werden muss:

```powershell
docker compose exec collector python send_test_message.py --group test-werlberger
```

## Datenformat

| Inhalt | MQTT-Topic | Erwartete Angaben |
| --- | --- | --- |
| Temperatur | `iiot/group/+/sensor/temperature/state` | JSON: `value`, `unit` (`°C` oder `K`), `sensor_id` empfohlen, `timestamp` optional |
| Relative Feuchte | `iiot/group/+/sensor/humidity/state` | JSON: `value`, `unit` (`%`), `sensor_id` empfohlen, `timestamp` optional |
| Gruppenstatus | `iiot/group/+/status` | JSON: `status` (`ok`, `error`, `offline`) |

**Projektannahme:** Das Topic für Luftfeuchtigkeit muss mit den sendenden Gruppen abgestimmt werden. Mehrere gleichartige Sensoren in einer Gruppe benötigen eindeutige `sensor_id`-Werte, andernfalls können Messreihen zusammenfallen. Fehlerhafte Nachrichten werden verworfen und protokolliert.

## Funktion überprüfen

```powershell
docker compose ps
docker compose logs --tail=50 collector
docker compose logs --tail=50 grafana
```

In Grafana das Dashboard **`Sensor_Data`** öffnen, den Zeitraum z. B. auf **Last 15 minutes** stellen und prüfen, ob neue Messpunkte erscheinen. In InfluxDB kann zusätzlich der Bucket `sensor_data` kontrolliert werden.

Für einen vollständigen Test mit echten Geräten müssen Verbindung zum Broker, tatsächlich empfangene Topics, gespeicherte Werte und Grafana-Darstellung separat überprüft werden. Der erfolgreiche Simulationstest allein bestätigt diesen Echtbetrieb nicht.

## Lokale Entwicklung (optional)

```powershell
uv sync --locked
uv run python mqtt_collector.py --simulate
uv run python mqtt_collector.py --simulate --continuous --interval 5
```

Für die lokale Ausführung muss `INFLUX_URL=http://localhost:8086` in `.env` stehen und InfluxDB laufen. Vor einer lokalen Dauersimulation den Collector-Container stoppen, damit dieselben Testdaten nicht doppelt geschrieben werden.

## Stoppen, Neustarten und Datenhaltung

```powershell
docker compose stop                 # Container stoppen, erhalten
docker compose start                # Gestoppte Container starten
docker compose down                 # Container/Netzwerk entfernen; Volumes behalten
docker compose up -d --build        # Anwendung neu starten bzw. aktualisieren
```

InfluxDB und Grafana verwenden **benannte Docker-Volumes**. Messwerte und gespeicherte Benutzerkonten bleiben bei `docker compose down` erhalten, sind aber **nicht** Teil des Git-Repositories und werden nicht auf einen anderen Rechner übertragen. Für historische Messwerte ist ein separates Backup erforderlich.

**Nicht `docker compose down -v` für die reguläre Installation ausführen:** Dies löscht die zugehörigen Volumes.

## Git und Zugangsdaten

Ins Repository gehören Quellcode, `Dockerfile`, `docker-compose.yml`, `pyproject.toml`, `uv.lock`, `.env.example`, `README.md` und die Dateien unter `grafana/` einschließlich des exportierten Dashboard-JSON.

**Nicht einchecken:** `.env`, `.env.test`, `.venv/`, Datenbank-Backups oder echte API-Tokens/Passwörter. Die `.env.example` enthält nur Platzhalter. Grafana-Dashboards, die in der Oberfläche verändert werden, müssen für die Versionsverwaltung erneut als JSON exportiert werden.

## Separate Testumgebung (optional)

Für einen erneuten Erstinstallationstest kann eine eigene `compose.test.yaml` mit dem Projektnamen `iiot_monitoring_test`, anderen Host-Ports und **eigenen Volumes** genutzt werden. Nach sorgfältiger Prüfung dieser Trennung lässt sie sich vollständig entfernen:

```powershell
docker compose -f compose.test.yaml down -v
```

**Nur für die Testumgebung verwenden, niemals für das reguläre Compose-Projekt.** `compose.test.yaml` und `.env.test` werden für den normalen Projektbetrieb nicht benötigt.

MQTT-Konventionen: <https://github.com/jhumci/MECH-M-3-IIoT/blob/main/docs/iot-specs/conventions.md>
