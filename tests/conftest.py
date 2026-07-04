import os
import re
import uuid
import pytest
import requests
from pathlib import Path

API_BASE = os.environ.get("ORCASLICER_API", "http://localhost:5000")
TEST_FILES = Path(__file__).parent.parent / "test_files_for_now"

# Source files and expected sanitized API names for the three Sovol SV08 profiles
PROFILE_SOURCES = {
    "printer": {
        "path": TEST_FILES / "printer" / "Sovol SV08 0.4 nozzle - Tuned.json",
        "name": "sovol-sv08-0-4-nozzle-tuned",
    },
    "process": {
        "path": TEST_FILES / "0.20mm Standard @Sovol SV08 - Tuned.json",
        "name": "0-20mm-standard-sovol-sv08-tuned",
    },
    "filament": {
        "path": TEST_FILES / "Protopasta PLA.json",
        "name": "protopasta-pla",
    },
}

# Params derived from uploaded profiles — stable across OrcaSlicer versions.
# Excludes params that come from bundled system profiles (jerk, fan speeds, etc.)
# which differ between the API's OrcaSlicer version and the GUI's version.
ASSERT_PARAMS = [
    "layer_height",
    "nozzle_diameter",
    "filament_type",
    "temperature",
    "first_layer_temperature",
    "first_layer_bed_temperature",
    "curr_bed_type",
    "print_settings_id",
    "filament_settings_id",
    "printer_settings_id",
]


def parse_gcode_header(content: bytes) -> dict:
    """Parse all '; key = value' comment lines from a GCODE file.

    OrcaSlicer writes params in two places: a short header block at the top
    and a full config block near the end. We scan the whole file so both are
    captured, with later values overwriting earlier ones (config block wins).
    """
    params = {}
    for line in content.decode("utf-8", errors="replace").splitlines():
        if not line.startswith(";"):
            continue
        m = re.match(r";\s*(\w+)\s*=\s*(.+)", line)
        if m:
            params[m.group(1).strip()] = m.group(2).strip()
    return params


def unique_name() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="session")
def api():
    return API_BASE


@pytest.fixture(scope="session")
def core_profiles(api):
    """Upload the three Sovol/Protopasta profiles once per session, clean up after."""
    uploaded = []
    for category, info in PROFILE_SOURCES.items():
        requests.delete(f"{api}/api/profiles/{category}/{info['name']}")
        with open(info["path"], "rb") as f:
            r = requests.post(
                f"{api}/api/profiles/{category}",
                files={"file": (info["path"].name, f, "application/json")},
            )
        assert r.status_code == 201, f"Failed to upload {category} profile: {r.text}"
        uploaded.append((category, info["name"]))

    yield {cat: info["name"] for cat, info in PROFILE_SOURCES.items()}

    for category, name in uploaded:
        requests.delete(f"{api}/api/profiles/{category}/{name}")
