# code.py
# ===================================================================
# IoT Environmental Monitoring für yourmuesli.at
#
# Hardware: Raspberry Pi Pico W
# Messwert: interne CPU-Temperatur des RP2040 (nach Absprache mit der
#           LV-Leitung statt DHT11, da keine Ersatzsensoren verfügbar)
# Software: CircuitPython 10
#
# Funktionen:
#   - Konfiguration aus settings.toml (allgemein, im Git) und
#     credentials.toml (Zugangsdaten, NICHT im Git)
#   - WLAN-Verbindung mit Wiederholung, Uhrzeit per NTP
#   - MQTT-Telemetrie (JSON mit timestamp, value, unit)
#   - MQTT-Status als JSON ({"status": "ok"} / Last Will {"status": "offline"})
#   - HTTP-API: GET /config, POST /config, GET /status
# ===================================================================

import os
import time
import json
import binascii

import microcontroller      # Zugriff auf CPU-Temperatur und Chip-Seriennummer
import wifi                 # WLAN-Funkmodul des Pico W
import socketpool           # Netzwerk-Sockets (für MQTT, HTTP und NTP)
import rtc                  # interne Uhr des Pico
import toml                 # minimaler TOML-Parser aus lib/toml.py
import adafruit_ntp         # Uhrzeit aus dem Internet holen
import adafruit_minimqtt.adafruit_minimqtt as MQTT

from adafruit_httpserver import Server, JSONResponse


# ===================================================================
# Hilfsfunktion: Zeitstempel
# ===================================================================
def iso_timestamp():
    """Liefert die aktuelle Uhrzeit als ISO-8601-Text in UTC,
    z.B. "2026-11-15T09:35:12Z".

    CircuitPython hat kein strftime, deshalb wird der Text aus dem
    Tupel von time.localtime() zusammengebaut. {:02d} = ganze Zahl,
    mindestens zwei Stellen, vorne mit 0 aufgefüllt (9 -> "09").
    Das "Z" am Ende steht für UTC.
    """
    t = time.localtime()
    return "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}Z".format(
        t.tm_year, t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min, t.tm_sec
    )


# ===================================================================
# KLASSE: ConfigManager
# ===================================================================
class ConfigManager:
    """Liest die Konfiguration aus zwei Dateien:

    - settings.toml:    allgemeine Einstellungen (Broker, Topics, Intervall).
                        Liegt im Git-Repository.
    - credentials.toml: Zugangsdaten (WLAN, MQTT-Passwort). Steht in der
                        .gitignore und existiert nur auf dem Pico.
                        Vorlage: credentials.example.toml

    Beide werden zu einem Dictionary zusammengeführt. Gespeichert
    (POST /config) wird nur in settings.toml und nur ohne Zugangsdaten.
    """

    def __init__(self, filepath, credentials_path="credentials.toml"):
        self.filepath = filepath                    # allgemeine Einstellungen
        self.credentials_path = credentials_path    # Zugangsdaten
        self.settings = {}         # zusammengeführte Einstellungen (wird geteilt!)
        self.secret_keys = set()   # Schlüssel aus credentials.toml

    def _read_toml(self, path):
        """Liest eine TOML-Datei; fehlt sie, kommt ein leeres Dictionary."""
        try:
            with open(path, "r") as file:
                return toml.load(file)
        except OSError as error:
            print("Datei konnte nicht gelesen werden:", path, error)
            return {}

    def load_settings(self):
        """Lädt beide Dateien und prüft, ob alle Pflichtwerte vorhanden sind."""
        self.settings = self._read_toml(self.filepath)

        # Zugangsdaten ergänzen; bei doppelten Schlüsseln gewinnt credentials.toml.
        credentials = self._read_toml(self.credentials_path)
        self.secret_keys = set(credentials)
        self.settings.update(credentials)

        # Fallback auf CircuitPython-Umgebungsvariablen (os.getenv liest
        # ebenfalls settings.toml), falls der TOML-Parser einen Wert nicht
        # erkannt hat.
        string_keys = [
            "device_id", "wifi_ssid", "wifi_password", "broker_address",
            "mqtt_username", "mqtt_password", "telemetry_topic",
            "status_topic",
        ]
        for key in string_keys:
            if key not in self.settings:
                value = os.getenv(key)
                if value is not None:
                    self.settings[key] = value

        integer_keys = ["broker_port", "reading_interval_seconds"]
        for key in integer_keys:
            if key not in self.settings:
                value = os.getenv(key)
                if value is not None:
                    try:
                        self.settings[key] = int(value)
                    except ValueError:
                        pass

        # Sinnvolle Voreinstellung für das Sendeintervall.
        self.settings.setdefault("reading_interval_seconds", 60)

        # Ohne diese Werte kann das Gerät nicht arbeiten -> sofort melden.
        required = [
            "device_id", "wifi_ssid", "wifi_password", "broker_address", "broker_port",
            "mqtt_username", "mqtt_password", "telemetry_topic",
        ]
        missing = [key for key in required if key not in self.settings]
        if missing:
            raise ValueError(
                "Fehlende Einträge in settings.toml/credentials.toml: "
                + ", ".join(missing)
            )

        # Intervall immer als ganze Zahl führen.
        if type(self.settings["reading_interval_seconds"]) is not int:
            self.settings["reading_interval_seconds"] = int(
                self.settings["reading_interval_seconds"]
            )

        return self.settings

    def save_settings(self, settings):
        """Versucht, die Einstellungen in settings.toml zu speichern.

        Standardmäßig ist das Laufwerk für CircuitPython schreibgeschützt
        (nur der Laptop darf per USB schreiben). Dann bleibt die Änderung
        bis zum Neustart im RAM erhalten.

        Rückgabe: True = gespeichert, False = Speichern fehlgeschlagen.
        """
        self.settings.update(settings)

        # Zugangsdaten nie in settings.toml schreiben, sie bleiben
        # ausschließlich in credentials.toml.
        public = {}
        for key, value in self.settings.items():
            if key not in self.secret_keys:
                public[key] = value

        try:
            with open(self.filepath, "w") as file:
                toml.dump(public, file)
            print("Konfiguration gespeichert:", self.filepath)
            return True
        except OSError as error:
            # Schreibschutz ist in CircuitPython immer ein OSError.
            # (PermissionError gibt es in CircuitPython nicht.)
            print("Konfiguration nur temporär geändert; Speichern fehlgeschlagen:", error)
            return False


# ===================================================================
# KLASSE: NetworkManager
# ===================================================================
class NetworkManager:
    """Kümmert sich um WLAN-Verbindung und Uhrzeit."""

    def __init__(self, ssid, password):
        self.ssid = ssid
        self.password = password
        self.time_synced = False      # wurde die Uhr schon per NTP gestellt?
        self.last_time_sync_try = 0   # Zeitpunkt des letzten NTP-Versuchs

    def connect(self):
        """Verbindet mit dem WLAN, bis zu 5 Versuche.
        Der Pico W meldet beim ersten Versuch gelegentlich
        "Unknown failure 205", deshalb die Wiederholung."""
        for attempt in range(1, 6):
            if self.is_connected():
                return True
            try:
                print("Verbinde mit WLAN; Versuch", attempt)
                wifi.radio.connect(self.ssid, self.password)
                if self.is_connected():
                    print("WLAN verbunden:", self.get_ip())
                    return True
            except Exception as error:
                print("WLAN-Verbindungsfehler:", error)
                time.sleep(2)
        return False

    def is_connected(self):
        return wifi.radio.connected

    def get_ip(self):
        return str(wifi.radio.ipv4_address)

    def sync_time(self):
        """Stellt die Uhr des Pico per NTP auf UTC.

        Der Pico hat keine Uhr mit Batterie und startet im Jahr 2000.
        adafruit_ntp fragt standardmäßig 0.adafruit.pool.ntp.org über
        UDP Port 123 (wichtig für die Firewall-Freigabe).
        tz_offset=0 bedeutet UTC, so wie es die Spezifikation verlangt.
        """
        self.last_time_sync_try = time.monotonic()
        try:
            pool = socketpool.SocketPool(wifi.radio)
            ntp = adafruit_ntp.NTP(pool, tz_offset=0)
            rtc.RTC().datetime = ntp.datetime
            self.time_synced = True
            print("Uhrzeit per NTP gestellt:", iso_timestamp())
            return True
        except Exception as error:
            print("NTP fehlgeschlagen:", error)
            return False


# ===================================================================
# KLASSE: Sensor
# ===================================================================
class Sensor:
    """Liest die interne CPU-Temperatur des RP2040.

    Nach Absprache mit der LV-Leitung wird diese statt eines DHT11
    verwendet. Sie misst die Temperatur des Chips, nicht der Raumluft,
    und liegt daher einige Grad über der Umgebungstemperatur.
    """

    def __init__(self, sensor_id, pin_number=None):
        # sensor_id = device_id aus settings.toml; laut asyncapi.yaml ein
        # optionales Feld, hilft aber dem Collector, Sensoren zu unterscheiden.
        self.sensor_id = sensor_id
        # pin_number wird für die CPU-Temperatur nicht gebraucht, bleibt
        # aber erhalten, damit die Schnittstelle zur Vorlage passt.
        self.pin_number = pin_number

    def read_data(self):
        """Liefert einen Messwert im Format von asyncapi.yaml
        (timestamp, sensor_id, value, unit) oder None bei einem Fehler."""
        try:
            return {
                "timestamp": iso_timestamp(),
                "sensor_id": self.sensor_id,
                "value": round(microcontroller.cpu.temperature, 1),
                "unit": "°C",
            }
        except Exception as error:
            print("Temperaturmessung fehlgeschlagen:", error)
            return None


# ===================================================================
# KLASSE: MqttClient
# ===================================================================
class MqttClient:
    """Verwaltet die Verbindung zum Kurs-Broker."""

    def __init__(self, config):
        # Dieselbe Dictionary-Instanz wie im ConfigManager: Änderungen
        # über POST /config sind hier sofort sichtbar.
        self.config = config
        self.pool = socketpool.SocketPool(wifi.radio)

        # Eindeutige Client-ID aus der Seriennummer des Chips. Der Broker
        # erlaubt jede ID nur einmal, sonst werfen sich Geräte gegenseitig raus.
        self.client_id = "pico-" + binascii.hexlify(microcontroller.cpu.uid).decode()
        self.sensor_id = self.config["device_id"]

        self.status_topic = self.config.get(
            "status_topic", self.config["telemetry_topic"] + "/status"
        )

        self.client = MQTT.MQTT(
            broker=self.config["broker_address"],
            port=int(self.config["broker_port"]),
            username=self.config["mqtt_username"],
            password=self.config["mqtt_password"],
            client_id=self.client_id,
            socket_pool=self.pool,
            # Kleiner Timeout, damit loop() die Hauptschleife nicht lange
            # blockiert. Kleiner als ca. 0.2 s scheitert der Verbindungsaufbau.
            socket_timeout=0.2,
        )
        self.last_reconnect_attempt = 0   # für Reconnect höchstens alle 10 s
        self.last_loop = 0                # für loop() höchstens alle 5 s

    def connect(self):
        """Verbindet mit dem Broker. Der Last Will muss VOR connect()
        hinterlegt werden: Bricht die Verbindung ab, sendet der Broker
        selbst {"status": "offline"} (retained) auf das Status-Topic."""
        last_will = {"status": "offline", "sensor_id": self.sensor_id}
        self.client.will_set(self.status_topic, json.dumps(last_will), retain=True)
        self.client.connect()
        print("Mit MQTT-Broker verbunden. Client-ID:", self.client_id)

    def is_connected(self):
        try:
            return self.client.is_connected()
        except Exception:
            return False

    def publish_telemetry(self, data):
        """Sendet einen Messwert als JSON an das Telemetrie-Topic."""
        self.client.publish(self.config["telemetry_topic"], json.dumps(data))

    def publish_status(self, status):
        """Sendet den Gerätestatus als JSON, z.B.
        {"status": "ok", "timestamp": "...", "sensor_id": "..."}.
        Pflicht ist nur "status", timestamp und sensor_id sind optional.
        retain=True: Der Broker merkt sich die letzte Nachricht, neue
        Abonnenten sehen den Status also sofort."""
        if status not in ("ok", "error", "offline"):
            raise ValueError("Ungültiger Status: " + str(status))
        payload = {
            "status": status,
            "timestamp": iso_timestamp(),
            "sensor_id": self.sensor_id,
        }
        self.client.publish(self.status_topic, json.dumps(payload), retain=True)

    def loop(self):
        """Hält die Verbindung am Leben und verbindet bei Abbruch neu.

        Da nichts abonniert ist, reicht ein Aufruf alle paar Sekunden
        (Keep-Alive-Pings). Dazwischen kehrt die Methode sofort zurück,
        damit Webserver und Messung nicht ausgebremst werden.
        """
        now = time.monotonic()
        if now - self.last_loop < 5:
            return
        self.last_loop = now

        try:
            self.client.loop(timeout=0.2)   # muss >= socket_timeout sein
        except Exception as error:
            print("MQTT-Loop-Fehler:", error)

        # Verbindung verloren? Höchstens alle 10 s neu versuchen.
        if not self.is_connected():
            if now - self.last_reconnect_attempt >= 10:
                self.last_reconnect_attempt = now
                try:
                    print("Versuche MQTT erneut zu verbinden ...")
                    self.client.reconnect()
                    if self.is_connected():
                        self.publish_status("ok")
                        print("MQTT-Verbindung wiederhergestellt")
                except Exception as error:
                    print("MQTT-Reconnect fehlgeschlagen:", error)


# ===================================================================
# KLASSE: WebServer
# ===================================================================
class WebServer:
    """HTTP-API zur Fernkonfiguration (Bibliothek adafruit_httpserver).

    Schema: docs/iot-specs/openapi.yaml (Version 1.2.0)
    GET  /config  -> {"device_id", "reading_interval_seconds", ...}
    POST /config  <- {"reading_interval_seconds": 10}  (Minimum 2;
                     device_id darf nicht geändert werden)
    GET  /status  -> device_id, uptime_seconds, wifi_connected,
                     mqtt_connected, last_telemetry, last_humidity_percent
    Falsche Methode -> 405, unbekannter Pfad -> 404 (macht die Bibliothek),
    ungültiges JSON / unbekannte Felder / ungültige Werte -> 400.
    """

    def __init__(self, config_manager, network_manager, mqtt_client, start_time):
        # Referenzen auf die anderen Objekte, um Status und Konfiguration
        # abfragen zu können (keine Kopien!).
        self.config_manager = config_manager
        self.network_manager = network_manager
        self.mqtt_client = mqtt_client
        self.start_time = start_time
        self.status_data = {}   # letzte Messung, wird von main() aktualisiert

        self.pool = socketpool.SocketPool(wifi.radio)
        self.server = Server(self.pool, debug=False)
        self.port = 80

        # Alle HTTP-Methoden registrieren, damit falsche Methoden im Handler
        # mit 405 beantwortet werden (sonst liefert die Bibliothek 404).
        alle_methoden = [
            "GET", "POST", "PUT", "PATCH", "DELETE",
            "HEAD", "OPTIONS", "TRACE", "CONNECT", "QUERY",
        ]
        self.server.route("/config", alle_methoden)(self._handle_config_route)
        self.server.route("/status", alle_methoden)(self._handle_status_route)

    def start(self):
        """Startet den Server. Nach einem Auto-Reload ist Port 80 manchmal
        noch belegt, dann wird auf Port 5000 ausgewichen."""
        try:
            self.server.start(self.network_manager.get_ip(), self.port)
        except OSError as error:
            print("HTTP-Server auf Port 80 konnte nicht starten:", error)
            self.port = 5000
            self.server.start(self.network_manager.get_ip(), self.port)

        basis = "http://" + self.network_manager.get_ip() + ":" + str(self.port)
        print("HTTP-Endpunkte bereit:")
        print("  GET  " + basis + "/config")
        print("  POST " + basis + "/config")
        print("  GET  " + basis + "/status")

    def poll(self):
        """Bearbeitet eine anstehende Anfrage, ohne zu blockieren.
        Muss in jedem Durchlauf der Hauptschleife aufgerufen werden."""
        try:
            self.server.poll()
        except Exception as error:
            print("HTTP-Anfrage fehlgeschlagen:", error)

    def update_status(self, last_telemetry=None):
        """Wird von main() nach jeder erfolgreich gesendeten Messung
        aufgerufen. last_telemetry = {"timestamp", "value", "unit"}."""
        self.status_data = {"last_telemetry": last_telemetry}

    # ---------------- interne Methoden ----------------

    def _method_not_allowed(self, request, erlaubt):
        """405-Antwort mit Allow-Header (laut HTTP-Standard Pflicht bei 405)."""
        return JSONResponse(
            request,
            {"error": "Methode nicht erlaubt"},
            status=(405, "Method Not Allowed"),
            headers={"Allow": erlaubt},
        )

    def _handle_config_route(self, request):
        if request.method == "GET":
            return self._handle_get_request(request)
        if request.method == "POST":
            return self._handle_post_request(request)
        return self._method_not_allowed(request, "GET, POST")

    def _handle_status_route(self, request):
        if request.method != "GET":
            return self._method_not_allowed(request, "GET")

        settings = self.config_manager.settings
        status = {
            # Pflichtfelder laut openapi.yaml 1.2.0
            "device_id": settings.get("device_id", ""),
            "uptime_seconds": int(time.monotonic() - self.start_time),
            "wifi_connected": self.network_manager.is_connected(),
            "mqtt_connected": self.mqtt_client.is_connected(),
            # null, solange noch keine Messung gesendet wurde
            "last_telemetry": self.status_data.get("last_telemetry"),
            # Kein Feuchtesensor vorhanden (CPU-Temperatur statt DHT11):
            # Das Schema erlaubt hier ausdrücklich null.
            "last_humidity_percent": None,
            # Zusätzliches Feld (laut Spezifikation erlaubt)
            "reading_interval_seconds": settings.get("reading_interval_seconds", 60),
        }
        return JSONResponse(request, status)

    def _handle_get_request(self, request):
        # Zugangsdaten (WLAN, MQTT) werden absichtlich nicht ausgegeben.
        settings = self.config_manager.settings
        public_config = {
            # Pflichtfelder laut openapi.yaml 1.2.0
            "device_id": settings.get("device_id", ""),
            "reading_interval_seconds": settings.get("reading_interval_seconds", 60),
            # Zusätzliche Felder (laut Spezifikation erlaubt)
            "telemetry_topic": settings.get("telemetry_topic", ""),
            "status_topic": settings.get("status_topic", ""),
        }
        return JSONResponse(request, public_config)

    def _handle_post_request(self, request):
        # 1. Body als JSON lesen
        try:
            data = request.json()
        except Exception:
            return JSONResponse(
                request, {"error": "Ungültiges JSON"},
                status=(400, "Bad Request")
            )

        if not isinstance(data, dict):
            return JSONResponse(
                request, {"error": "JSON-Objekt erwartet"},
                status=(400, "Bad Request")
            )

        # 2. device_id darf laut Spezifikation nicht geändert werden
        if "device_id" in data:
            return JSONResponse(
                request, {"error": "device_id darf nicht über POST geändert werden"},
                status=(400, "Bad Request")
            )

        # 3. Nur bekannte Felder zulassen (Schema ConfigUpdate)
        allowed_fields = {"reading_interval_seconds"}
        unknown_fields = [key for key in data if key not in allowed_fields]
        if unknown_fields:
            return JSONResponse(
                request,
                {"error": "Unbekannte Felder: " + ", ".join(unknown_fields)},
                status=(400, "Bad Request"),
            )

        # 4. reading_interval_seconds ist Pflicht: ganze Zahl, mindestens 2.
        #    type(...) is int schließt auch true/false aus.
        interval = data.get("reading_interval_seconds")
        if type(interval) is not int or not 2 <= interval <= 3600:
            return JSONResponse(
                request,
                {"error": "reading_interval_seconds muss eine Ganzzahl zwischen 2 und 3600 sein"},
                status=(400, "Bad Request"),
            )
        new_values = {"reading_interval_seconds": interval}

        # 5. In-place aktualisieren, damit MQTT-Client und Hauptschleife die
        #    Änderung sofort sehen (gleiches Dictionary), dann speichern.
        self.config_manager.settings.update(new_values)
        saved = self.config_manager.save_settings(self.config_manager.settings)

        # Antwort nach Schema ConfigUpdate, plus Hinweis zum Speichern
        return JSONResponse(
            request,
            {
                "reading_interval_seconds": interval,
                "saved_to_file": saved,
                "note": None if saved else "Änderung gilt bis zum Neustart; settings.toml ist schreibgeschützt.",
            },
        )


# ===================================================================
# HAUPTPROGRAMM
# ===================================================================
def main():
    program_start = time.monotonic()

    # 1. Konfiguration laden
    config_manager = ConfigManager("settings.toml")
    config = config_manager.load_settings()
    if config["reading_interval_seconds"] < 2:   # Minimum laut openapi.yaml
        config["reading_interval_seconds"] = 60

    # 2. WLAN verbinden und Uhr stellen (für korrekte Zeitstempel)
    network = NetworkManager(config["wifi_ssid"], config["wifi_password"])
    if not network.connect():
        raise RuntimeError("WLAN-Verbindung nach 5 Versuchen fehlgeschlagen")
    network.sync_time()

    # 3. Sensor und MQTT einrichten, Status "ok" melden
    sensor = Sensor(config["device_id"])
    mqtt_client = MqttClient(config)
    mqtt_client.connect()
    mqtt_client.publish_status("ok")

    # 4. Webserver starten
    web_server = WebServer(config_manager, network, mqtt_client, program_start)
    web_server.start()

    next_publish = time.monotonic()   # erste Messung sofort
    last_wifi_retry = 0

    # 5. Hauptschleife: darf nie lange blockieren
    while True:
        web_server.poll()     # HTTP-Anfragen bearbeiten
        mqtt_client.loop()    # MQTT-Verbindung halten (intern nur alle 5 s)

        now = time.monotonic()

        # WLAN bei Abbruch höchstens alle 10 s neu verbinden
        if not network.is_connected() and now - last_wifi_retry >= 10:
            last_wifi_retry = now
            print("WLAN getrennt; versuche erneut zu verbinden")
            network.connect()

        # Falls NTP beim Start gescheitert ist: jede Minute neu versuchen
        if not network.time_synced and now - network.last_time_sync_try >= 60:
            network.sync_time()

        # Messen und senden, wenn das Intervall abgelaufen ist.
        # Das Intervall wird jedes Mal neu gelesen, damit POST /config
        # sofort wirkt.
        interval = config_manager.settings.get("reading_interval_seconds", 60)
        if now >= next_publish:
            measurement = sensor.read_data()
            if measurement is not None:
                try:
                    if mqtt_client.is_connected():
                        mqtt_client.publish_telemetry(measurement)
                        print("MQTT gesendet:", json.dumps(measurement))
                        # Für GET /status: letzte erfolgreich gesendete Messung
                        web_server.update_status({
                            "timestamp": measurement["timestamp"],
                            "value": measurement["value"],
                            "unit": measurement["unit"],
                        })
                    else:
                        print("Messwert nicht gesendet: MQTT nicht verbunden")
                except Exception as error:
                    print("Fehler beim Senden:", error)
            else:
                try:
                    mqtt_client.publish_status("error")
                except Exception:
                    pass

            next_publish = time.monotonic() + interval

        time.sleep(0.05)


main()
