# IIoT Environmental Monitoring für yourmuesli.at

Ein Raspberry Pi Pico W misst Temperatur, sendet sie per MQTT an den Kurs-Broker und lässt sich über eine HTTP-API konfigurieren. Eine Containerlösung aus Collector, InfluxDB und Grafana speichert die Daten **aller** Gruppen und zeigt sie in einem Dashboard an.

## Gruppe

| | |
|---|---|
| Mitglieder | `<Vorname>` Neubauer, `<Vorname>` Grünwald, Lukas Werlberger |
| MQTT-Gruppenname | `iiot/group/neubauer-gruenwald-werlberger` |
| Geräte-ID | `iot-gruppe-2` |

## Video

Bildschirmaufnahme des laufenden Systems: `<LINK ZUM VIDEO EINTRAGEN>`

## Erreichter Stand

| Teil | Umgesetzt |
|---|---|
| Termin 1: Firmware | Pico W sendet die Temperatur als JSON mit `timestamp`, `value`, `unit` (und `sensor_id`) nach `asyncapi.yaml`. Uhrzeit per NTP in UTC. Status als JSON (`ok`, `error`), bei Verbindungsabbruch `offline` per Last Will (retained). Code in der Klassenstruktur der Vorlage, Konfiguration in `settings.toml` und `credentials.toml`. |
| Termin 2: HTTP-API und Tests | `GET /config`, `POST /config`, `GET /status` nach `openapi.yaml` 1.2.0, Fehlerfälle mit 400/404/405. Testfälle mit pytest (inklusive Schema-Prüfung gegen `openapi.yaml`) und Schemathesis, siehe [HTTP-API](#http-api). |
| Termin 3: Containerlösung | Collector, InfluxDB und Grafana per `docker compose up`. Live-Daten aller Gruppen über `iiot/group/+/sensor/temperature/state`, Geräteübersicht aus `iiot/group/+/status` (Anzahl, letzte Verbindung, Status), Mittelwert, Minimum und Maximum im Dashboard. |

Beanspruchte Stufen laut Aufgabenstellung: Teststufe Termin 2 mit OpenAPI-Tooling (Schemathesis), Termin 3 „sehr gut“ mit Bonus-Kennzahlen.

**Abweichungen und Einschränkungen**

- **Kein DHT11:** Nach Absprache mit der LV-Leitung wird statt des DHT11 die interne Temperatur des RP2040-Chips gemessen, da keine Ersatzsensoren verfügbar waren. Sie liegt einige Grad über der Raumtemperatur. Am Pico muss nichts verkabelt werden.
- **Keine Luftfeuchte:** Deshalb liefert `GET /status` immer `"last_humidity_percent": null`. Das Schema in `openapi.yaml` erlaubt das ausdrücklich. Auf MQTT werden keine Feuchtewerte gesendet.
- **Konfiguration nicht dauerhaft:** `POST /config` übernimmt das neue Intervall sofort. Das Speichern in `settings.toml` scheitert am Schreibschutz von CircuitPython und wird in der Antwort mit `"saved_to_file": false` gemeldet; nach einem Neustart gilt wieder der Wert aus der Datei.

## Überblick

```
┌──────────────┐  MQTT (TCP 1883)   ┌─────────────────┐   MQTT    ┌───────────┐   ┌──────────┐   ┌─────────┐
│  Pico W      │ ─────────────────▶ │  Kurs-Broker    │ ────────▶ │ Collector │ ─▶│ InfluxDB │ ─▶│ Grafana │
│  CPU-Temp.   │   Messwerte +      │ 158.180.44.197  │  alle     │ (Python)  │   │          │   │ :3000   │
│  HTTP-API:80 │   Status (JSON)    └─────────────────┘  Gruppen  └───────────┘   └──────────┘   └─────────┘
└──────────────┘                                                  └──────── docker compose (Laptop) ───────┘
```

- **Pico W** (`src/raspi_firmware/`): misst alle `reading_interval_seconds` die CPU-Temperatur und sendet sie mit UTC-Zeitstempel. Der Status (`ok`, `error`, `offline` per Last Will) liegt retained auf dem Status-Topic. Die HTTP-API dient zur Konfiguration im lokalen Netz.
- **Collector** (`src/iiot_monitoring/mqtt_collector.py`): abonniert die Topics aller Gruppen, prüft jede Nachricht und schreibt gültige Werte in InfluxDB. Fehlerhafte Nachrichten anderer Gruppen werden protokolliert und verworfen.
- **InfluxDB** speichert die Zeitreihen, **Grafana** zeigt sie an. Datenquelle und Dashboard werden beim Start automatisch eingerichtet.

### Struktur des Repositorys

```
├── README.md                     ← dieses Dokument
├── docs/iot-specs/               Spezifikationen (openapi.yaml 1.2.0, asyncapi.yaml, conventions.md)
├── src/
│   ├── raspi_firmware/           Firmware für den Pico W (CircuitPython)
│   │   ├── code.py
│   │   ├── settings.toml         allgemeine Einstellungen
│   │   ├── credentials.example.toml   Vorlage für die Zugangsdaten
│   │   └── lib/toml.py
│   └── iiot_monitoring/          Containerlösung
│       ├── docker-compose.yml
│       ├── .env.example          Vorlage für Passwörter der Container
│       ├── mqtt_collector.py, influx_writer.py, simulator.py
│       ├── send_test_message.py  Testnachricht für einen „neuen Sensor“
│       └── grafana/              Datenquelle und Dashboard (Provisioning)
└── tests/                        Testfälle für die HTTP-API
    ├── test_http_api.py, conftest.py
    ├── schemathesis.toml
    └── mock_device.py            simuliertes Gerät für Tests ohne Pico
```

## Mikrocontroller einrichten

### Hardware

Benötigt wird nur ein **Raspberry Pi Pico W** und ein USB-Kabel mit Datenleitung (reine Ladekabel funktionieren nicht). Da die CPU-Temperatur gemessen wird, ist keine Verkabelung nötig.

### Firmware aufspielen

1. **CircuitPython installieren:** Die aktuelle `.uf2`-Datei für den Pico W von circuitpython.org herunterladen. Taste `BOOTSEL` gedrückt halten, USB einstecken, die `.uf2` auf das erscheinende Laufwerk `RPI-RP2` kopieren. Der Pico startet neu und erscheint als Laufwerk `CIRCUITPY`.
2. **Bibliotheken kopieren:** Das *Adafruit CircuitPython Bundle* passend zur CircuitPython-Version herunterladen (z.B. Bundle 10.x für CircuitPython 10). Nach `CIRCUITPY/lib/` kopieren:
   - `adafruit_minimqtt/` (ganzer Ordner)
   - `adafruit_httpserver/` (ganzer Ordner)
   - `adafruit_connection_manager.mpy`, `adafruit_ticks.mpy`, `adafruit_ntp.mpy`
   - `toml.py` aus `src/raspi_firmware/lib/` dieses Repositorys
3. **Firmware kopieren:** `code.py` und `settings.toml` aus `src/raspi_firmware/` in das Hauptverzeichnis von `CIRCUITPY` kopieren.

### Konfiguration und WLAN einbinden

Die Konfiguration ist auf zwei Dateien verteilt:

| Datei | Inhalt | Im Repository |
|---|---|---|
| `settings.toml` | Geräte-ID, Broker, Topics, Sendeintervall | ja |
| `credentials.toml` | WLAN-Name und -Passwort, MQTT-Zugang | **nein** (steht in `.gitignore`) |

1. `src/raspi_firmware/credentials.example.toml` auf `CIRCUITPY` kopieren und dort in **`credentials.toml`** umbenennen.
2. In `credentials.toml` die Zugangsdaten des bestehenden WLANs und das MQTT-Passwort eintragen:
   ```toml
   wifi_ssid = "Name-des-WLANs"
   wifi_password = "WLAN-Passwort"
   mqtt_username = "bobm"
   mqtt_password = "Passwort-aus-der-Vorlesung"
   ```
3. In `settings.toml` bei Bedarf `device_id` (muss eindeutig sein), die Topics und `reading_interval_seconds` (mindestens 2) anpassen.

**Anforderungen an das WLAN:** 2,4 GHz, WPA2 mit Passwort. Netze mit Benutzername (WPA2-Enterprise, z.B. eduroam) und reine WPA3-Netze unterstützt der Pico W nicht. Bei einem Handy-Hotspot am iPhone „Maximale Kompatibilität“ aktivieren. Für die Firewall siehe [Hinweise für die IT-Abteilung](#hinweise-für-die-it-abteilung).

### Prüfen, ob es funktioniert

1. **Serielle Konsole** öffnen (z.B. Erweiterung *Serial Monitor* in VS Code, Port „Serielles USB-Gerät“, 115200 Baud). Nach dem Start erscheinen:
   ```
   WLAN verbunden: 10.76.193.237
   Uhrzeit per NTP gestellt: 2026-11-21T14:00:03Z
   Mit MQTT-Broker verbunden. Client-ID: pico-…
   HTTP-Endpunkte bereit: …
   MQTT gesendet: {"timestamp": "2026-11-21T14:00:04Z", "sensor_id": "iot-gruppe-2", "value": 31.4, "unit": "°C"}
   ```
   Die angezeigte IP-Adresse wird für die HTTP-API gebraucht.
2. **MQTT Explorer** mit `158.180.44.197:1883` verbinden und `iiot/group/neubauer-gruenwald-werlberger` öffnen:
   - `…/sensor/temperature/state`: JSON mit `timestamp`, `value`, `unit`
   - `…/status`: `{"status": "ok", …}` (retained)
   - Pico abstecken: Nach etwa 90 Sekunden setzt der Broker den Status auf `{"status": "offline", …}`.

**Häufige Probleme:** `Unknown failure 205` beim ersten WLAN-Versuch ist normal, die Firmware versucht es bis zu fünfmal. „Fehlende Einträge in settings.toml/credentials.toml“ bedeutet, dass `credentials.toml` fehlt oder unvollständig ist.

## Container starten

### Voraussetzungen

- **Docker Desktop** (mit Docker Compose), gestartet und mit „Engine running“
- **Git**
- Netzzugang zum Broker (ausgehend TCP 1883)

### Vom `git clone` bis zum laufenden System

```powershell
git clone <URL-dieses-Repositorys>
cd MECH-M-3-IIoT/src/iiot_monitoring

# Vorlage für die Passwörter kopieren (Linux/macOS: cp .env.example .env)
Copy-Item .env.example .env
```

In `.env` alle `HIER_…`-Platzhalter ersetzen:

| Variable | Wert |
|---|---|
| `INFLUX_PASSWORD` | frei wählbar, **mindestens 8 Zeichen** |
| `INFLUX_TOKEN` | lange Zufallszeichenkette, z.B. `python -c "import secrets; print(secrets.token_hex(32))"` |
| `GRAFANA_PASSWORD` | frei wählbar |
| `MQTT_PASSWORD` | Broker-Passwort aus der Vorlesung |

Danach starten:

```powershell
docker compose up -d --build
docker compose ps                    # alle drei Container "running", InfluxDB "healthy"
docker compose logs -f collector     # "MQTT verbunden", danach "Messwert gespeichert: …"
```

Die Zugangsdaten in `.env` werden nur beim **ersten** Start übernommen. Für einen kompletten Neustart mit leerer Datenbank: `docker compose down -v` (löscht alle gespeicherten Messwerte). Weitere Details zur Containerlösung stehen in [`src/iiot_monitoring/README.md`](src/iiot_monitoring/README.md).

## Dashboard nutzen

- **URL:** <http://localhost:3000>
- **Login:** `GRAFANA_USER` / `GRAFANA_PASSWORD` aus der `.env`
- **Dashboard:** *Dashboards → IIoT Monitoring → Sensor_Data*

| Ansicht | Inhalt |
|---|---|
| Anzahl erkannter Sensoren | Zahl der Sensoren (Gruppe + Sensor-ID), die in den letzten 30 Tagen Messwerte gesendet haben |
| Letzte Sensormessungen | Tabelle mit Gruppe, Sensor-ID und Zeitpunkt der letzten Messung (letzte Verbindung) |
| Temperaturdiagramm | Zeitreihen aller Gruppen, eine Linie pro Sensor |
| Temp. mean / min / max | Kennzahlen über den gewählten Zeitraum |
| Status-Tabelle | letzter Status jeder Gruppe (`ok`, `error`, `offline`) und Zeitpunkt |
| Luftfeuchtigkeit | nur für Gruppen mit Feuchtesensor, bei uns leer |

Den Zeitraum der Diagramme oben rechts wählen (Standard: letzte 2 Tage). Das Dashboard aktualisiert sich alle 5 Sekunden.

**Neuer Sensor ohne Konfiguration:** Testnachrichten einer erfundenen Gruppe senden, sie erscheint danach im Dashboard:

```powershell
docker compose exec collector python send_test_message.py --group test-werlberger
```

## HTTP-API

Die API läuft auf Port 80 des Pico W und ist nur aus demselben Netz erreichbar. Sie entspricht [`docs/iot-specs/openapi.yaml`](docs/iot-specs/openapi.yaml) (Version 1.2.0).

| Endpunkt | Funktion |
|---|---|
| `GET /config` | `device_id`, `reading_interval_seconds` (zusätzlich die Topics) |
| `POST /config` | Sendeintervall ändern, z.B. `{"reading_interval_seconds": 10}` (Minimum 2; `device_id` ist nicht änderbar) |
| `GET /status` | `device_id`, `uptime_seconds`, `wifi_connected`, `mqtt_connected`, `last_telemetry`, `last_humidity_percent` |

Fehler: kaputtes JSON, ungültige Werte oder unbekannte Felder → **400**, unbekannter Pfad → **404**, falsche Methode → **405** mit `Allow`-Header.

### Beispiele mit curl

In der PowerShell `curl.exe` statt `curl` verwenden und Anführungszeichen im JSON mit `\"` schützen:

```powershell
curl.exe http://<ip-des-pico>/config
curl.exe http://<ip-des-pico>/status
curl.exe -X POST http://<ip-des-pico>/config -H "Content-Type: application/json" -d '{\"reading_interval_seconds\": 10}'
```

Linux/macOS:

```bash
curl -X POST http://<ip-des-pico>/config -H "Content-Type: application/json" -d '{"reading_interval_seconds": 10}'
```

### Testfälle ausführen

Die Tests laufen auf dem Laptop, nicht auf dem Pico. Sie schicken HTTP-Anfragen an das Gerät und prüfen die Antworten gegen [`docs/iot-specs/openapi.yaml`](docs/iot-specs/openapi.yaml). Laptop und Pico müssen dazu im selben WLAN sein.

**Einmalig einrichten** (im Hauptordner des Repositorys):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Linux/macOS: source .venv/bin/activate
python -m pip install -r tests/requirements.txt
```

Meldet die PowerShell beim Aktivieren, dass Skripte deaktiviert sind, vorher einmal `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` ausführen.

**pytest** (27 Testfälle: alle Endpunkte, Schema-Prüfung gegen `openapi.yaml`, Fehlerfälle 400/404/405, Robustheit):

```powershell
$env:PICO_URL = "http://<ip-des-pico>"      # Linux/macOS: export PICO_URL=http://<ip-des-pico>
python -m pytest -v tests
```

Erwartetes Ergebnis: `27 passed`. Die Tests setzen das Sendeintervall nach jedem Test auf den ursprünglichen Wert zurück.

**Schemathesis** (erzeugt automatisch Anfragen aus `openapi.yaml` und prüft die Antworten):

```powershell
schemathesis --config-file tests/schemathesis.toml run docs/iot-specs/openapi.yaml --url $env:PICO_URL
```

Erwartetes Ergebnis: alle generierten Fälle `passed`. Schemathesis setzt dabei zufällige gültige Intervalle; danach das gewünschte Intervall wieder per `POST /config` setzen (siehe curl-Beispiel oben).

**Ohne Pico:** `tests/mock_device.py` lädt die echte Firmware aus `src/raspi_firmware/code.py`, ersetzt nur die Hardware durch Attrappen und stellt die HTTP-API lokal bereit.

```powershell
# Terminal 1
python tests/mock_device.py                 # läuft auf http://127.0.0.1:8080

# Terminal 2 (.venv aktivieren)
$env:PICO_URL = "http://127.0.0.1:8080"
python -m pytest -v tests
```

**Häufige Probleme**

- „Gerät unter … nicht erreichbar“: falsche IP oder Laptop nicht im selben WLAN. Prüfen mit `curl.exe http://<ip-des-pico>/config`.
- „openapi.yaml hat Version …, erwartet wird 1.2.0“: Die Spezifikation im Repo ist veraltet. Aktuelle Version aus dem Original-Repository übernehmen.
- Timeouts: Der Pico bearbeitet Anfragen nacheinander; einen einzelnen Timeout einfach wiederholen.

## Hinweise für die IT-Abteilung

Das Gerät baut nur ausgehende Verbindungen auf, mit Ausnahme der HTTP-API im lokalen Netz.

**Ausgehend (in die Whitelist der Firewall aufnehmen)**

| Ziel | Port / Protokoll | Zweck |
|---|---|---|
| `158.180.44.197` | TCP 1883 (MQTT, unverschlüsselt) | Messwerte und Status an den Broker |
| `0.adafruit.pool.ntp.org` | UDP 123 (NTP) | Uhrzeit für die Zeitstempel |
| DNS-Server des Netzes | UDP/TCP 53 | Namensauflösung des NTP-Servers |
| DHCP-Server des Netzes | UDP 67/68 | IP-Adresse beziehen |

Der NTP-Pool wechselt seine IP-Adressen. Die Firewall muss ihn daher über den Hostnamen freigeben oder ausgehendes UDP 123 allgemein erlauben. Ohne NTP sendet das Gerät weiter, aber mit falschen Zeitstempeln. Manche Firewalls blockieren Port 1883 standardmäßig, weil er kein Web-Port ist; ohne diese Freigabe kommen keine Daten beim Broker an.

**Eingehend**

| Quelle | Port | Zweck |
|---|---|---|
| nur lokales Netz | TCP 80 (HTTP; nach einem Neustart ersatzweise 5000) | Konfiguration und Status über die HTTP-API |

Empfehlung: dem Gerät per DHCP-Reservierung eine feste IP-Adresse geben, damit die API immer unter derselben Adresse erreichbar ist. Das Gerät nicht in ein Gäste-WLAN mit Client-Isolation stellen, sonst ist die API auch lokal nicht erreichbar.

**Rechner mit der Containerlösung:** ausgehend TCP 1883 zum Broker, beim ersten Start zusätzlich HTTPS (443) zu Docker Hub und `ghcr.io` zum Herunterladen der Images. Grafana (3000) und InfluxDB (8086) lauschen nur auf `127.0.0.1` und sind von außen nicht erreichbar.

### Fernzugriff auf die HTTP-API

Die API ist bewusst nur im lokalen Netz erreichbar. Für einen Zugriff aus Innsbruck gibt es folgende Wege:

- **VPN in das Netz des Standorts** (empfohlen): Der Zugriff erfolgt verschlüsselt und nur für berechtigte Personen. Hindernis: Die örtliche IT muss einen VPN-Zugang einrichten und verwalten.
- **Portweiterleitung am Router:** technisch am einfachsten, aber **gefährlich**. Die API ist unverschlüsselt und hat keine Anmeldung; jeder im Internet könnte das Intervall ändern oder das Gerät mit Anfragen überlasten. Viele Standorte haben zudem keine öffentliche IPv4-Adresse (Carrier-Grade-NAT), dann ist eine Weiterleitung gar nicht möglich.
- **Reverse Proxy oder Gateway vor Ort** mit HTTPS und Anmeldung: erfüllt die Sicherheitsanforderungen, erfordert aber zusätzliche Hardware je Standort.
- **Konfiguration über MQTT statt HTTP:** Das Gerät abonniert ein Konfigurations-Topic auf dem Broker. Es bleibt bei ausgehenden Verbindungen, es sind keine Änderungen an der Firewall nötig, und es funktioniert hinter jedem NAT. Für den weltweiten Betrieb ist das der praktikabelste Weg, setzt aber abgesicherte MQTT-Zugänge voraus (siehe Sicherheit).

## Sicherheit

Die Lösung ist ein Prototyp. Für den Produktivbetrieb bräuchte es zusätzlich:

- **Verschlüsseltes MQTT** (TLS, Port 8883) statt Klartext auf 1883. Derzeit sind Passwort und Messwerte im Netz mitlesbar.
- **Eigene Zugangsdaten pro Gerät mit ACLs am Broker:** Aktuell nutzen alle Gruppen denselben Benutzer und dürfen auf jedes Topic schreiben, könnten also Messwerte oder Status anderer Standorte fälschen.
- **Anmeldung und HTTPS für die HTTP-API**, oder Wegfall der API zugunsten der Konfiguration über abgesichertes MQTT.
- **Geschützte Speicherung der Zugangsdaten** auf dem Gerät. Wer physischen Zugriff auf den Pico hat, kann `credentials.toml` über USB auslesen.
- **Dashboard nur über einen Reverse Proxy mit HTTPS** und personalisierten Konten, falls es für das Management von außen erreichbar sein soll (z.B. auf dem iPhone).
- **Betrieb:** Backups der InfluxDB, Aufbewahrungsfristen für Messdaten, Überwachung und ein Verfahren für Firmware-Updates aus der Ferne.
