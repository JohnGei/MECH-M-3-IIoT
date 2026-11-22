"""Simuliertes Gerät für die HTTP-API-Tests ohne Pico W.

Lädt die ECHTE Firmware aus src/raspi_firmware/code.py, ersetzt nur die
CircuitPython-Hardwaremodule durch Attrappen und stellt die HTTP-Handler
der Firmware über einen lokalen Server bereit. Damit lassen sich die
Testfälle auch ohne angeschlossenen Mikrocontroller ausführen, z.B. bei
der Bewertung oder in einer CI-Pipeline.

Start:
    python tests/mock_device.py            # http://127.0.0.1:8080
    python tests/mock_device.py --port 9000
"""

import argparse
import json
import shutil
import sys
import tempfile
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_DIR = REPO_ROOT / "src" / "raspi_firmware"


# ------------------------------------------------------------------
# Attrappen für die CircuitPython-Module
# ------------------------------------------------------------------
def install_stubs():
    microcontroller = types.ModuleType("microcontroller")
    microcontroller.cpu = types.SimpleNamespace(temperature=31.4, uid=b"\x12\x34\x56\x78")
    sys.modules["microcontroller"] = microcontroller

    wifi = types.ModuleType("wifi")
    wifi.radio = types.SimpleNamespace(connected=True, ipv4_address="127.0.0.1")
    sys.modules["wifi"] = wifi

    socketpool = types.ModuleType("socketpool")
    socketpool.SocketPool = lambda radio: object()
    sys.modules["socketpool"] = socketpool

    sys.modules["rtc"] = types.ModuleType("rtc")
    sys.modules["adafruit_ntp"] = types.ModuleType("adafruit_ntp")

    class FakeMQTT:
        def __init__(self, **kwargs):
            pass

        def will_set(self, *args, **kwargs):
            pass

        def connect(self):
            pass

        def publish(self, *args, **kwargs):
            pass

        def is_connected(self):
            return True

    minimqtt_pkg = types.ModuleType("adafruit_minimqtt")
    minimqtt = types.ModuleType("adafruit_minimqtt.adafruit_minimqtt")
    minimqtt.MQTT = FakeMQTT
    minimqtt_pkg.adafruit_minimqtt = minimqtt
    sys.modules["adafruit_minimqtt"] = minimqtt_pkg
    sys.modules["adafruit_minimqtt.adafruit_minimqtt"] = minimqtt

    class JSONResponse:
        def __init__(self, request, data, status=(200, "OK"), headers=None):
            self.data = data
            self.status = status
            self.headers = headers or {}

    class Server:
        """Merkt sich nur die Routen, die die Firmware registriert."""
        routes = {}

        def __init__(self, pool, debug=False):
            pass

        def route(self, path, methods):
            def register(handler):
                Server.routes[path] = handler
                return handler
            return register

        def start(self, host, port):
            pass

        def poll(self):
            pass

    httpserver = types.ModuleType("adafruit_httpserver")
    httpserver.Server = Server
    httpserver.JSONResponse = JSONResponse
    sys.modules["adafruit_httpserver"] = httpserver
    return Server


def load_firmware():
    """Führt code.py aus, aber ohne den abschließenden Aufruf von main()."""
    sys.path.insert(0, str(FIRMWARE_DIR / "lib"))   # für toml.py
    source = (FIRMWARE_DIR / "code.py").read_text(encoding="utf-8")
    source = source.rstrip()
    if source.endswith("main()"):
        source = source[: -len("main()")]
    namespace = {"__name__": "firmware"}
    exec(compile(source, "code.py", "exec"), namespace)
    return types.SimpleNamespace(**namespace)


def build_device():
    server_cls = install_stubs()
    fw = load_firmware()

    # Mit Kopien der Konfigurationsdateien arbeiten, damit POST /config
    # nicht die Datei im Repository überschreibt.
    workdir = Path(tempfile.mkdtemp(prefix="mock_device_"))
    shutil.copy(FIRMWARE_DIR / "settings.toml", workdir / "settings.toml")
    (workdir / "credentials.toml").write_text(
        'wifi_ssid = "mock"\nwifi_password = "mock"\n'
        'mqtt_username = "mock"\nmqtt_password = "mock"\n',
        encoding="utf-8",
    )

    config_manager = fw.ConfigManager(
        str(workdir / "settings.toml"), str(workdir / "credentials.toml")
    )
    config = config_manager.load_settings()
    network = fw.NetworkManager(config["wifi_ssid"], config["wifi_password"])
    mqtt_client = fw.MqttClient(config)
    web_server = fw.WebServer(config_manager, network, mqtt_client, start_time=fw.time.monotonic())

    # Eine "gesendete" Messung hinterlegen, damit /status Werte liefert.
    measurement = fw.Sensor(config["device_id"]).read_data()
    web_server.update_status({k: measurement[k] for k in ("timestamp", "value", "unit")})
    return server_cls.routes


# ------------------------------------------------------------------
# HTTP-Server, der Anfragen an die Firmware-Handler weiterreicht
# ------------------------------------------------------------------
def make_handler(routes):
    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self):
            path = self.path.split("?", 1)[0]
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""

            handler = routes.get(path)
            if handler is None:
                self._send(404, b"File not found", "text/plain")
                return

            request = types.SimpleNamespace(
                method=self.command,
                json=lambda: json.loads(body.decode("utf-8")),
            )
            response = handler(request)
            payload = json.dumps(response.data, ensure_ascii=False).encode("utf-8")
            self._send(response.status[0], payload, "application/json", response.headers)

        def _send(self, code, payload, content_type, headers=None):
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(payload)

        def __getattr__(self, name):
            # Jede HTTP-Methode (do_GET, do_TRACE, ...) an die Firmware weiterreichen.
            if name.startswith("do_"):
                return self._dispatch
            raise AttributeError(name)

        def log_message(self, fmt, *args):
            print("mock_device:", fmt % args)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="Simuliertes Pico-W-Gerät")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    routes = build_device()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(routes))
    print(f"Simuliertes Gerät läuft auf http://{args.host}:{args.port} (Strg+C beendet)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
