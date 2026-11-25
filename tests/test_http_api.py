"""Testfälle für die HTTP-API des Pico W nach docs/iot-specs/openapi.yaml (1.2.0).

Ausführen (Details im README):
    pip install -r tests/requirements.txt
    $env:PICO_URL = "http://<ip-des-pico>"
    pytest -v tests
"""

import time

import pytest
import requests

from conftest import TIMEOUT


def get(base_url, path):
    return requests.get(base_url + path, timeout=TIMEOUT)


def post_json(base_url, path, data):
    return requests.post(base_url + path, json=data, timeout=TIMEOUT)


# ------------------------------------------------------------------
# GET /config
# ------------------------------------------------------------------
def test_get_config_liefert_200_und_json(base_url):
    response = get(base_url, "/config")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("application/json")


def test_get_config_entspricht_schema(base_url, validate):
    validate(get(base_url, "/config").json(), "Config")


# ------------------------------------------------------------------
# GET /status
# ------------------------------------------------------------------
def test_get_status_entspricht_schema(base_url, validate):
    response = get(base_url, "/status")
    assert response.status_code == 200
    validate(response.json(), "DeviceStatus")


def test_status_und_config_haben_dieselbe_device_id(base_url):
    assert get(base_url, "/status").json()["device_id"] == get(base_url, "/config").json()["device_id"]


def test_status_luftfeuchte_passt_zur_simulation(base_url):
    # Statt DHT11 wird die CPU-Temperatur gemessen (mit der LV-Leitung
    # abgesprochen). Es gibt keinen Feuchtesensor: Mit simulate_humidity
    # liefert das Gerät einen simulierten Prozentwert, sonst null.
    status = get(base_url, "/status").json()
    humidity = status["last_humidity_percent"]
    if status.get("humidity_simulated"):
        assert humidity is None or 0 <= humidity <= 100
    else:
        assert humidity is None


def test_uptime_steigt(base_url):
    first = get(base_url, "/status").json()["uptime_seconds"]
    time.sleep(2)
    second = get(base_url, "/status").json()["uptime_seconds"]
    assert second > first


# ------------------------------------------------------------------
# POST /config: gültige Anfragen
# ------------------------------------------------------------------
def test_post_config_setzt_intervall(base_url, validate, restore_interval):
    new_interval = 17 if restore_interval != 17 else 18

    response = post_json(base_url, "/config", {"reading_interval_seconds": new_interval})
    assert response.status_code == 200
    validate(response.json(), "ConfigUpdate")
    assert response.json()["reading_interval_seconds"] == new_interval

    # Der neue Wert muss danach auch über GET /config zurückkommen.
    assert get(base_url, "/config").json()["reading_interval_seconds"] == new_interval


def test_post_config_minimum_2_wird_akzeptiert(base_url, restore_interval):
    response = post_json(base_url, "/config", {"reading_interval_seconds": 2})
    assert response.status_code == 200


# ------------------------------------------------------------------
# POST /config: ungültige Anfragen -> 400 mit {"error": "..."}
# ------------------------------------------------------------------
@pytest.mark.parametrize(
    "body",
    [
        {"reading_interval_seconds": 1},          # unter dem Minimum
        {"reading_interval_seconds": 0},
        {"reading_interval_seconds": -5},
        {"reading_interval_seconds": "10"},       # Text statt Zahl
        {"reading_interval_seconds": 2.5},        # keine ganze Zahl
        {"reading_interval_seconds": True},       # bool ist keine Zahl
        {},                                       # Pflichtfeld fehlt
        {"device_id": "neu"},                     # device_id nicht änderbar
        {"reading_interval_seconds": 10, "device_id": "neu"},
        {"reading_interval_seconds": 10, "farbe": "blau"},   # unbekanntes Feld
        [10],                                     # kein JSON-Objekt
    ],
    ids=[
        "intervall-1", "intervall-0", "intervall-negativ", "intervall-text",
        "intervall-kommazahl", "intervall-bool", "leeres-objekt", "device-id",
        "device-id-mit-intervall", "unbekanntes-feld", "json-liste",
    ],
)
def test_post_config_ungueltig_liefert_400(base_url, validate, error_schema, restore_interval, body):
    response = post_json(base_url, "/config", body)
    assert response.status_code == 400
    validate(response.json(), error_schema)

    # Das Intervall darf sich dabei nicht geändert haben.
    assert get(base_url, "/config").json()["reading_interval_seconds"] == restore_interval


def test_post_config_kaputtes_json_liefert_400(base_url, validate, error_schema):
    response = requests.post(
        base_url + "/config",
        data="{kaputt",
        headers={"Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    assert response.status_code == 400
    validate(response.json(), error_schema)


# ------------------------------------------------------------------
# Falscher Pfad und falsche Methode
# ------------------------------------------------------------------
def test_unbekannter_pfad_liefert_404(base_url):
    assert get(base_url, "/gibtsnicht").status_code == 404


@pytest.mark.parametrize(
    "method, path, allowed",
    [
        ("POST", "/status", "GET"),
        ("PUT", "/status", "GET"),
        ("DELETE", "/status", "GET"),
        ("PUT", "/config", "GET, POST"),
        ("DELETE", "/config", "GET, POST"),
    ],
)
def test_falsche_methode_liefert_405(base_url, method, path, allowed):
    response = requests.request(method, base_url + path, timeout=TIMEOUT)
    assert response.status_code == 405
    assert response.headers["Allow"] == allowed


# ------------------------------------------------------------------
# Robustheit: Das Gerät läuft nach Fehlern weiter
# ------------------------------------------------------------------
def test_geraet_antwortet_nach_fehlerhaften_anfragen(base_url):
    requests.post(base_url + "/config", data="{kaputt", timeout=TIMEOUT)
    requests.get(base_url + "/favicon.ico", timeout=TIMEOUT)   # wie ein Browser
    requests.request("PATCH", base_url + "/status", timeout=TIMEOUT)
    assert get(base_url, "/status").status_code == 200
