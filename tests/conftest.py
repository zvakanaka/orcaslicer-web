import base64
import os
import re
import uuid
import pytest
import requests
from io import BytesIO
from pathlib import Path

from PIL import Image

API_BASE = os.environ.get("ORCASLICER_API", "http://localhost:5000")
FIXTURES = Path(__file__).parent / "fixtures"
MODELS_DIR = FIXTURES / "models"
REFERENCE_GCODE_DIR = FIXTURES / "reference_gcode"

# Source files and expected sanitized API names for the three Sovol SV08 profiles
PROFILE_SOURCES = {
    "printer": {
        "path": FIXTURES / "profiles" / "printer" / "Sovol SV08 0.4 nozzle - Tuned.json",
        "name": "sovol-sv08-0-4-nozzle-tuned",
    },
    "process": {
        "path": FIXTURES / "profiles" / "process" / "0.20mm Standard @Sovol SV08 - Tuned.json",
        "name": "0-20mm-standard-sovol-sv08-tuned",
    },
    "filament": {
        "path": FIXTURES / "profiles" / "filament" / "Protopasta PLA.json",
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


_THUMBNAIL_RE = re.compile(
    r"; thumbnail begin (\d+)x(\d+) (\d+)\n"
    r"((?:; [^\n]*\n)*)"
    r"; thumbnail end",
)


def parse_gcode_thumbnails(content: bytes) -> dict:
    """Extract every embedded thumbnail from GCODE as {(width, height): PIL.Image}.

    Parses the "; thumbnail begin WxH SIZE" / base64-in-comments / "; thumbnail end"
    blocks directly from the raw text -- independent of the app's own injection code,
    so a test using this actually verifies the on-disk GCODE format, not just that the
    app's regex matches what it itself wrote.
    """
    text = content.decode("utf-8", errors="replace")
    thumbnails = {}
    for match in _THUMBNAIL_RE.finditer(text):
        width, height, declared_size = int(match.group(1)), int(match.group(2)), int(match.group(3))
        b64 = "".join(line[2:] for line in match.group(4).splitlines())
        assert len(b64) == declared_size, (
            f"{width}x{height} thumbnail: declared size {declared_size} != actual base64 length {len(b64)}"
        )
        image = Image.open(BytesIO(base64.b64decode(b64)))
        image.load()
        thumbnails[(width, height)] = image
    return thumbnails


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
