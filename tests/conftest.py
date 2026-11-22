"""Gemeinsame Einstellungen für die Tests der HTTP-API.

Ziel-Gerät über die Umgebungsvariable PICO_URL festlegen, z.B.:
    $env:PICO_URL = "http://10.76.193.237"     (PowerShell)
    export PICO_URL=http://10.76.193.237       (Linux/macOS)
Ohne PICO_URL wird das simulierte Gerät (tests/mock_device.py) auf
http://127.0.0.1:8080 verwendet.
"""

import os
from pathlib import Path

import pytest
import requests
import yaml
from openapi_schema_validator import OAS30Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = REPO_ROOT / "docs" / "iot-specs" / "openapi.yaml"

# Der Pico W braucht mitunter etwas länger für eine Antwort.
TIMEOUT = 10


@pytest.fixture(scope="session")
def base_url():
    url = os.environ.get("PICO_URL", "http://127.0.0.1:8080").rstrip("/")
    try:
        requests.get(url + "/config", timeout=TIMEOUT)
    except requests.RequestException as error:
        pytest.exit(
            f"Gerät unter {url} nicht erreichbar ({error}). "
            "PICO_URL prüfen oder 'python tests/mock_device.py' starten.",
            returncode=2,
        )
    return url


EXPECTED_OPENAPI_VERSION = "1.2.0"


@pytest.fixture(scope="session")
def openapi():
    with open(OPENAPI_PATH, encoding="utf-8") as file:
        spec = yaml.safe_load(file)
    version = spec.get("info", {}).get("version")
    if version != EXPECTED_OPENAPI_VERSION:
        pytest.exit(
            f"{OPENAPI_PATH} hat Version {version}, erwartet wird "
            f"{EXPECTED_OPENAPI_VERSION}. Aktuelle Spezifikation aus dem "
            "Original-Repository übernehmen (GitHub: Sync fork).",
            returncode=2,
        )
    return spec


@pytest.fixture(scope="session")
def validate(openapi):
    """Prüft eine JSON-Antwort gegen ein Schema aus openapi.yaml.

    OAS30Validator versteht die OpenAPI-3.0-Besonderheiten wie
    'nullable: true', die ein normaler JSON-Schema-Validator nicht kennt.
    """

    def _validate(data, schema):
        if isinstance(schema, str):
            schema = {"$ref": f"#/components/schemas/{schema}"}
        full_schema = dict(schema, components=openapi["components"])
        OAS30Validator(full_schema).validate(data)

    return _validate


@pytest.fixture(scope="session")
def error_schema(openapi):
    """Schema der 400-Antwort von POST /config."""
    responses = openapi["paths"]["/config"]["post"]["responses"]
    return responses["400"]["content"]["application/json"]["schema"]


@pytest.fixture
def restore_interval(base_url):
    """Merkt sich das Sendeintervall und stellt es nach dem Test wieder her,
    damit das Gerät nach den Tests wie vorher sendet."""
    original = requests.get(base_url + "/config", timeout=TIMEOUT).json()[
        "reading_interval_seconds"
    ]
    yield original
    requests.post(
        base_url + "/config",
        json={"reading_interval_seconds": original},
        timeout=TIMEOUT,
    )
