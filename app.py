import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from io import BytesIO
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100MB

log = logging.getLogger(__name__)

ORCASLICER_BIN = os.environ.get("ORCASLICER_BIN", "/opt/orcaslicer/AppRun")
PROFILES_DIR = Path(os.environ.get("PROFILES_DIR", "/data/profiles"))
TEMP_DIR = Path(os.environ.get("TEMP_DIR", "/tmp/slicing"))
BUNDLED_PROFILES_DIR = Path(os.environ.get(
    "BUNDLED_PROFILES_DIR",
    "/opt/orcaslicer/resources/profiles",
))
# Starter profiles (Sovol SV08 + Protopasta PLA) shipped with the test suite.
# Seeded into an empty PROFILES_DIR/<category> on startup so a fresh instance
# is immediately usable; once a category has any profile (seeded, uploaded, or
# customized), it's left alone.
DEFAULT_PROFILES_DIR = Path(os.environ.get(
    "DEFAULT_PROFILES_DIR",
    str(Path(__file__).parent / "tests" / "fixtures" / "profiles"),
))
SLICE_TIMEOUT = 300  # seconds

VALID_CATEGORIES = {"printer", "process", "filament"}

# OrcaSlicer CLI requires a "type" field in each JSON profile.
# The GUI export omits it, so we inject it based on the upload category.
CATEGORY_TO_ORCA_TYPE = {
    "printer": "machine",
    "process": "process",
    "filament": "filament",
}
# Bundled profiles use "machine" as the subdirectory for printer profiles.
CATEGORY_TO_SUBDIR = {
    "printer": "machine",
    "process": "process",
    "filament": "filament",
}

VALID_FROM_VALUES = {"system", "User", "user"}

slice_lock = threading.Lock()
current_job = {"busy": False, "model": None, "started": None}

# Index of bundled system profiles: {subdir: {name: filepath}}
# Built once at startup, used for resolving "inherits" in user profiles.
_system_profile_index = {"machine": {}, "process": {}, "filament": {}}

# Names of profiles seeded from DEFAULT_PROFILES_DIR, per category. Used to
# flag them as defaults in the UI; a name is dropped once the profile is
# replaced, renamed, or deleted (i.e. no longer "the untouched default").
_default_profile_names = {"printer": set(), "process": set(), "filament": set()}


def build_system_profile_index():
    """Scan bundled OrcaSlicer profiles and index them by name."""
    if not BUNDLED_PROFILES_DIR.is_dir():
        log.warning("Bundled profiles dir not found: %s", BUNDLED_PROFILES_DIR)
        return

    # Sort so OrcaFilamentLibrary is processed last — its base profiles (e.g.
    # fdm_filament_pla) are the canonical system definitions and must win over
    # vendor-specific copies of the same profile name.
    vendor_dirs = sorted(
        BUNDLED_PROFILES_DIR.iterdir(),
        key=lambda p: (p.name == "OrcaFilamentLibrary", p.name),
    )

    count = 0
    for vendor_dir in vendor_dirs:
        if not vendor_dir.is_dir():
            continue
        for subdir in ("machine", "process", "filament"):
            cat_dir = vendor_dir / subdir
            if not cat_dir.is_dir():
                continue
            for json_file in cat_dir.rglob("*.json"):
                try:
                    with open(json_file) as f:
                        obj = json.load(f)
                    name = obj.get("name")
                    if name:
                        _system_profile_index[subdir][name] = json_file
                        count += 1
                except Exception:
                    pass

    log.info("Indexed %d bundled system profiles", count)


def resolve_system_profile(subdir, name, _seen=None):
    """Load a bundled system profile by name, recursively resolving inherits."""
    if _seen is None:
        _seen = set()
    if name in _seen:
        return {}
    _seen.add(name)

    path = _system_profile_index.get(subdir, {}).get(name)
    if not path:
        return {}

    with open(path) as f:
        obj = json.load(f)

    parent_name = obj.get("inherits")
    if parent_name:
        parent = resolve_system_profile(subdir, parent_name, _seen)
        parent.update(obj)
        return parent

    return obj


# --- Helpers ---


def ensure_orca_metadata(data_bytes, category):
    """Ensure OrcaSlicer CLI compatibility: set type/from, resolve inherits."""
    obj = json.loads(data_bytes)

    orca_type = CATEGORY_TO_ORCA_TYPE[category]
    obj["type"] = orca_type

    if obj.get("from") not in VALID_FROM_VALUES:
        obj["from"] = "User"

    # If this is a user override profile with "inherits", merge it on top of
    # the matching bundled system profile so the CLI gets all required keys.
    # Keep "inherits" pointing to the immediate parent so the CLI can use it
    # for compatible_printers matching (it checks printer inherits lineage).
    inherits_name = obj.get("inherits")
    if inherits_name:
        subdir = CATEGORY_TO_SUBDIR[category]
        base = resolve_system_profile(subdir, inherits_name)
        if base:
            base.update(obj)
            obj = base
            obj["inherits"] = inherits_name
            log.info("Resolved inherits '%s' for %s profile (%d keys)",
                     inherits_name, category, len(obj))
        else:
            log.warning("Could not resolve inherits '%s' for %s profile",
                        inherits_name, category)

    return json.dumps(obj, indent=4, ensure_ascii=False).encode("utf-8")


def build_gcode_filename(format_template, model_filename, process_data, filament_data):
    """Build a gcode filename from the filename_format template and profile data."""
    model_stem = Path(model_filename).stem

    # Build a flat lookup dict from process and filament profiles
    lookup = {}
    for data in (process_data, filament_data):
        for key, val in data.items():
            if key not in lookup:
                lookup[key] = val

    # Special-case: input_filename_base is the uploaded model's stem
    lookup["input_filename_base"] = model_stem

    # Replace {key[N]} indexed patterns first
    def replace_indexed(m):
        key = m.group(1)
        idx = int(m.group(2))
        val = lookup.get(key)
        if isinstance(val, list) and idx < len(val):
            return str(val[idx])
        return ""

    result = re.sub(r"\{(\w+)\[(\d+)\]\}", replace_indexed, format_template)

    # Replace {key} simple patterns
    def replace_simple(m):
        key = m.group(1)
        val = lookup.get(key)
        if val is None:
            return ""
        if isinstance(val, list):
            return str(val[0]) if val else ""
        return str(val)

    result = re.sub(r"\{(\w+)\}", replace_simple, result)

    # Clean up: collapse repeated separators, trim edges
    result = re.sub(r"[_\-]{2,}", "_", result)
    result = result.strip("_- ")

    # Ensure .gcode extension
    if not result.lower().endswith(".gcode"):
        result = re.sub(r"\.gcode$", "", result, flags=re.IGNORECASE)
        result += ".gcode"

    return result or f"{model_stem}.gcode"


LAYER_HEIGHT_RANGE = (0.04, 0.6)


def parse_process_overrides(form):
    """Parse optional per-slice process overrides from the request form.

    Returns (overrides, errors). Overrides are only applied on top of a
    temporary copy of the process profile, never written back to disk.
    """
    overrides = {}
    errors = []

    layer_height = form.get("layer_height", "").strip()
    if layer_height:
        try:
            value = float(layer_height)
        except ValueError:
            errors.append("layer_height must be a number")
        else:
            lo, hi = LAYER_HEIGHT_RANGE
            if not (lo <= value <= hi):
                errors.append(f"layer_height must be between {lo} and {hi}")
            else:
                overrides["layer_height"] = value

    fill_density = form.get("fill_density", "").strip().rstrip("%")
    if fill_density:
        try:
            value = int(fill_density)
        except ValueError:
            errors.append("fill_density must be an integer percentage")
        else:
            if not (0 <= value <= 100):
                errors.append("fill_density must be between 0 and 100")
            else:
                overrides["fill_density"] = f"{value}%"

    enable_support = form.get("enable_support", "").strip().lower()
    if enable_support in ("1", "true", "on", "yes"):
        overrides["enable_support"] = "1"
    elif enable_support in ("0", "false", "off", "no"):
        overrides["enable_support"] = "0"

    return overrides, errors


def apply_process_overrides(process_data, overrides):
    """Apply overrides onto a loaded process profile dict, matching the
    existing value's shape. OrcaSlicer's config format stores every value as
    a string (or a list of strings) even for numeric fields, so overrides
    must be stringified too or the CLI silently ignores them and falls back
    to the inherited default."""
    for key, value in overrides.items():
        if isinstance(process_data.get(key), list):
            process_data[key] = [str(value)]
        else:
            process_data[key] = str(value)
    return process_data


def sanitize_profile_name(name):
    name = name.lower()
    name = re.sub(r"[^a-z0-9-]", "-", name)
    name = re.sub(r"-+", "-", name)
    name = name.strip("-")
    name = name[:100]
    return name


def seed_default_profiles():
    """Copy starter profiles into any category that has none yet.

    Mirrors a normal upload (metadata injection, inherits resolution, name
    sanitizing) so seeded profiles are indistinguishable from user uploads
    except for the is_default flag. Categories that already have a profile
    (from a prior seed, a volume-mounted upload, etc.) are left untouched.
    """
    if not DEFAULT_PROFILES_DIR.is_dir():
        log.warning("Default profiles dir not found: %s", DEFAULT_PROFILES_DIR)
        return

    for category in VALID_CATEGORIES:
        profile_dir = PROFILES_DIR / category
        profile_dir.mkdir(parents=True, exist_ok=True)
        if any(profile_dir.glob("*.json")):
            continue

        src_dir = DEFAULT_PROFILES_DIR / category
        if not src_dir.is_dir():
            continue

        for src_file in sorted(src_dir.glob("*.json")):
            try:
                data = ensure_orca_metadata(src_file.read_bytes(), category)
            except (json.JSONDecodeError, UnicodeDecodeError):
                log.warning("Skipping invalid default profile %s", src_file)
                continue

            name = sanitize_profile_name(src_file.stem)
            if not name:
                continue

            get_profile_path(category, name).write_bytes(data)
            _default_profile_names[category].add(name)

        if _default_profile_names[category]:
            log.info("Seeded %d default %s profile(s)",
                      len(_default_profile_names[category]), category)


def validate_category(category):
    if category not in VALID_CATEGORIES:
        return jsonify(error=f"Invalid category: {category}. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}"), 400
    return None


def get_profile_path(category, name):
    return PROFILES_DIR / category / f"{name}.json"


def profile_info(category, name):
    path = get_profile_path(category, name)
    stat = path.stat()
    return {
        "name": name,
        "category": category,
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "is_default": name in _default_profile_names.get(category, set()),
    }


# --- Error handlers ---


@app.errorhandler(400)
def bad_request(e):
    return jsonify(error=str(e.description)), 400


@app.errorhandler(404)
def not_found(e):
    return jsonify(error="Not found"), 404


@app.errorhandler(409)
def conflict(e):
    return jsonify(error=str(e.description)), 409


@app.errorhandler(413)
def too_large(e):
    return jsonify(error="File too large. Maximum upload size is 100MB."), 413


@app.errorhandler(500)
def internal_error(e):
    return jsonify(error="Internal server error"), 500


# --- Routes ---


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    slicer_exists = os.path.isfile(ORCASLICER_BIN)
    return jsonify(
        status="ok" if slicer_exists else "degraded",
        slicer_found=slicer_exists,
    )


# --- Profile CRUD ---


@app.route("/api/profiles/<category>")
def list_profiles(category):
    err = validate_category(category)
    if err:
        return err

    profile_dir = PROFILES_DIR / category
    profile_dir.mkdir(parents=True, exist_ok=True)

    profiles = []
    for f in sorted(profile_dir.glob("*.json")):
        name = f.stem
        stat = f.stat()
        profiles.append({
            "name": name,
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "is_default": name in _default_profile_names.get(category, set()),
        })

    return jsonify(category=category, profiles=profiles)


@app.route("/api/profiles/<category>", methods=["POST"])
def upload_profile(category):
    err = validate_category(category)
    if err:
        return err

    if "file" not in request.files:
        return jsonify(error="No file provided. Use multipart field 'file'."), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify(error="Empty filename"), 400

    # Read and validate JSON
    try:
        data = file.read()
        json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return jsonify(error="File is not valid JSON"), 400

    data = ensure_orca_metadata(data, category)

    # Determine name
    name = request.form.get("name", "").strip()
    if not name:
        name = Path(file.filename).stem
    name = sanitize_profile_name(name)

    if not name:
        return jsonify(error="Could not derive a valid profile name"), 400

    profile_dir = PROFILES_DIR / category
    profile_dir.mkdir(parents=True, exist_ok=True)

    path = get_profile_path(category, name)
    if path.exists():
        return jsonify(error=f"Profile '{name}' already exists in {category}. Use PUT to replace."), 409

    path.write_bytes(data)
    return jsonify(**profile_info(category, name)), 201


@app.route("/api/profiles/<category>/<name>")
def get_profile(category, name):
    err = validate_category(category)
    if err:
        return err

    path = get_profile_path(category, name)
    if not path.exists():
        return jsonify(error=f"Profile '{name}' not found in {category}"), 404

    return send_file(path, mimetype="application/json", download_name=f"{name}.json")


@app.route("/api/profiles/<category>/<name>", methods=["PUT"])
def replace_profile(category, name):
    err = validate_category(category)
    if err:
        return err

    if "file" not in request.files:
        return jsonify(error="No file provided. Use multipart field 'file'."), 400

    file = request.files["file"]

    try:
        data = file.read()
        json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return jsonify(error="File is not valid JSON"), 400

    data = ensure_orca_metadata(data, category)

    profile_dir = PROFILES_DIR / category
    profile_dir.mkdir(parents=True, exist_ok=True)

    path = get_profile_path(category, name)
    path.write_bytes(data)
    _default_profile_names.get(category, set()).discard(name)
    return jsonify(**profile_info(category, name))


@app.route("/api/profiles/<category>/<name>", methods=["PATCH"])
def rename_profile(category, name):
    err = validate_category(category)
    if err:
        return err

    path = get_profile_path(category, name)
    if not path.exists():
        return jsonify(error=f"Profile '{name}' not found in {category}"), 404

    body = request.get_json(silent=True)
    if not body or "new_name" not in body:
        return jsonify(error="JSON body with 'new_name' required"), 400

    new_name = sanitize_profile_name(body["new_name"])
    if not new_name:
        return jsonify(error="Invalid new name"), 400

    if new_name == name:
        return jsonify(**profile_info(category, name))

    new_path = get_profile_path(category, new_name)
    if new_path.exists():
        return jsonify(error=f"Profile '{new_name}' already exists in {category}"), 409

    path.rename(new_path)
    _default_profile_names.get(category, set()).discard(name)
    return jsonify(**profile_info(category, new_name))


@app.route("/api/profiles/<category>/<name>", methods=["DELETE"])
def delete_profile(category, name):
    err = validate_category(category)
    if err:
        return err

    path = get_profile_path(category, name)
    if not path.exists():
        return jsonify(error=f"Profile '{name}' not found in {category}"), 404

    path.unlink()
    _default_profile_names.get(category, set()).discard(name)
    return jsonify(deleted=name, category=category)


# --- Slicing ---


@app.route("/api/slice/status")
def slice_status():
    if current_job["busy"]:
        return jsonify(
            busy=True,
            model=current_job["model"],
            started=current_job["started"],
        )
    return jsonify(busy=False)


@app.route("/api/slice", methods=["POST"])
def slice_model():
    # Validate model file
    if "model" not in request.files:
        return jsonify(error="No model file provided. Use multipart field 'model'."), 400

    model_file = request.files["model"]
    if not model_file.filename:
        return jsonify(error="Empty model filename"), 400

    model_filename = model_file.filename
    if not model_filename.lower().endswith((".stl", ".3mf")):
        return jsonify(error="Model must be an STL or 3MF file"), 400

    # Validate profile selections
    printer_name = request.form.get("printer", "").strip()
    process_name = request.form.get("process", "").strip()
    filament_name = request.form.get("filament", "").strip()

    if not all([printer_name, process_name, filament_name]):
        return jsonify(error="All three profile names required: printer, process, filament"), 400

    printer_path = get_profile_path("printer", printer_name)
    process_path = get_profile_path("process", process_name)
    filament_path = get_profile_path("filament", filament_name)

    missing = []
    if not printer_path.exists():
        missing.append(f"printer/{printer_name}")
    if not process_path.exists():
        missing.append(f"process/{process_name}")
    if not filament_path.exists():
        missing.append(f"filament/{filament_name}")

    if missing:
        return jsonify(error=f"Profiles not found: {', '.join(missing)}"), 404

    overrides, override_errors = parse_process_overrides(request.form)
    if override_errors:
        return jsonify(error="; ".join(override_errors)), 400

    # Acquire lock
    if not slice_lock.acquire(blocking=False):
        return jsonify(error="Slicer is busy. Try again later.", busy=True), 409

    job_id = str(uuid.uuid4())
    job_dir = TEMP_DIR / job_id
    start_time = time.time()

    try:
        current_job["busy"] = True
        current_job["model"] = model_filename
        current_job["started"] = start_time

        # Set up temp directory
        job_dir.mkdir(parents=True, exist_ok=True)
        output_dir = job_dir / "output"
        output_dir.mkdir()

        model_path = job_dir / model_filename
        model_file.save(str(model_path))

        # Load the process profile once; apply any per-slice overrides onto a
        # temp copy so the stored profile on disk is never mutated.
        with open(process_path) as f:
            process_data = json.load(f)

        if overrides:
            apply_process_overrides(process_data, overrides)
            effective_process_path = job_dir / "process_override.json"
            with open(effective_process_path, "w") as f:
                json.dump(process_data, f)
        else:
            effective_process_path = process_path

        # Build command
        orient = request.form.get("orient", "").strip().lower() in ("1", "true", "on", "yes")
        bed_type = request.form.get("bed_type", "").strip()
        VALID_BED_TYPES = {"Cool Plate", "Engineering Plate", "High Temp Plate", "Textured PEI Plate"}

        # Fall back to the printer profile's own default_bed_type when the
        # request doesn't specify one, rather than silently letting the CLI
        # use its built-in default (Cool Plate).
        if bed_type not in VALID_BED_TYPES:
            with open(printer_path) as f:
                printer_default_bed_type = json.load(f).get("default_bed_type", "").strip()
            if printer_default_bed_type in VALID_BED_TYPES:
                bed_type = printer_default_bed_type

        cmd = [
            ORCASLICER_BIN,
            "--slice", "0",
            "--load-settings", f"{printer_path};{effective_process_path}",
            "--load-filaments", str(filament_path),
            "--allow-newer-file",
            "--arrange", "1",
            "--ensure-on-bed",
        ]
        if orient:
            cmd.extend(["--orient", "1"])
        if bed_type in VALID_BED_TYPES:
            cmd.extend(["--curr-bed-type", bed_type])
        cmd.extend(["--outputdir", str(output_dir), str(model_path)])

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SLICE_TIMEOUT,
        )

        # Find output gcode
        gcode_files = list(output_dir.glob("*.gcode"))
        if not gcode_files:
            stdout_excerpt = (result.stdout or "")[:2000]
            stderr_excerpt = (result.stderr or "")[:2000]
            return jsonify(
                error="Slicing failed: no GCODE output produced",
                exit_code=result.returncode,
                stdout=stdout_excerpt,
                stderr=stderr_excerpt,
            ), 500

        gcode_path = gcode_files[0]
        gcode_data = BytesIO(gcode_path.read_bytes())

        # Build gcode filename from process profile's filename_format template
        model_stem = Path(model_filename).stem
        try:
            with open(filament_path) as f:
                filament_data = json.load(f)
        except Exception:
            filament_data = {}

        filename_format = process_data.get("filename_format", "")
        if filename_format:
            gcode_name = build_gcode_filename(
                filename_format, model_filename, process_data, filament_data,
            )
        else:
            gcode_name = f"{model_stem}.gcode"
        elapsed = round(time.time() - start_time, 2)

        response = send_file(
            gcode_data,
            mimetype="application/octet-stream",
            download_name=gcode_name,
            as_attachment=True,
        )
        response.headers["X-Slice-Time-Seconds"] = str(elapsed)
        stdout_header = (result.stdout or "")[:500].replace("\n", " ")
        response.headers["X-Slicer-Stdout"] = stdout_header
        return response

    except subprocess.TimeoutExpired:
        return jsonify(error=f"Slicing timed out after {SLICE_TIMEOUT} seconds"), 504
    except Exception as e:
        return jsonify(error=f"Slicing error: {str(e)}"), 500
    finally:
        current_job["busy"] = False
        current_job["model"] = None
        current_job["started"] = None
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
        slice_lock.release()


# --- Help ---


@app.route("/api/help")
def api_help():
    base = request.url_root.rstrip("/")
    return jsonify(examples=[
        {
            "title": "Health check",
            "command": f"curl {base}/api/health",
        },
        {
            "title": "List printer profiles",
            "command": f"curl {base}/api/profiles/printer",
        },
        {
            "title": "Upload a printer profile",
            "command": f"curl -X POST {base}/api/profiles/printer -F 'file=@printer.json' -F 'name=my-printer'",
        },
        {
            "title": "Upload a process profile",
            "command": f"curl -X POST {base}/api/profiles/process -F 'file=@process.json' -F 'name=my-process'",
        },
        {
            "title": "Upload a filament profile",
            "command": f"curl -X POST {base}/api/profiles/filament -F 'file=@filament.json' -F 'name=my-filament'",
        },
        {
            "title": "Download a profile",
            "command": f"curl -O {base}/api/profiles/printer/my-printer",
        },
        {
            "title": "Replace a profile",
            "command": f"curl -X PUT {base}/api/profiles/printer/my-printer -F 'file=@printer.json'",
        },
        {
            "title": "Rename a profile",
            "command": f"curl -X PATCH {base}/api/profiles/printer/my-printer -H 'Content-Type: application/json' -d '{{\"new_name\": \"new-name\"}}'",
        },
        {
            "title": "Delete a profile",
            "command": f"curl -X DELETE {base}/api/profiles/printer/my-printer",
        },
        {
            "title": "Check slicer status",
            "command": f"curl {base}/api/slice/status",
        },
        {
            "title": "Slice a model",
            "command": f"curl -X POST {base}/api/slice -F 'model=@model.stl' -F 'printer=my-printer' -F 'process=my-process' -F 'filament=my-filament' -o output.gcode",
        },
        {
            "title": "Slice with bed type and auto-orient",
            "command": f"curl -X POST {base}/api/slice -F 'model=@model.stl' -F 'printer=my-printer' -F 'process=my-process' -F 'filament=my-filament' -F 'bed_type=Textured PEI Plate' -F 'orient=1' -o output.gcode",
        },
        {
            "title": "Slice with per-job overrides (layer height, infill, supports)",
            "command": f"curl -X POST {base}/api/slice -F 'model=@model.stl' -F 'printer=my-printer' -F 'process=my-process' -F 'filament=my-filament' -F 'layer_height=0.28' -F 'fill_density=25' -F 'enable_support=1' -o output.gcode",
        },
    ])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    port = int(os.environ.get("FLASK_PORT", "5000"))

    for cat in VALID_CATEGORIES:
        (PROFILES_DIR / cat).mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    build_system_profile_index()
    seed_default_profiles()

    app.run(host=host, port=port)
