# OrcaSlicer API & Web UI

> [!WARNING]
> This software is still in its very early stages

Containerized web application that exposes OrcaSlicer's CLI slicing capability via a web UI and HTTP API. Upload slicer profiles (printer, process, filament JSONs), then slice STL/3MF files to download GCODE.

<img width="620" height="850" alt="image" src="https://github.com/user-attachments/assets/0b0bc0c7-1ad7-494f-805a-5d67775011a0" />

> Web UI

<img width="947" height="513" alt="image" src="https://github.com/user-attachments/assets/dc1428be-5328-4784-9d22-e72c2212ccd9" />

> Viewing GCODE downloaded from this in OrcaSlicer (with auto-orient enabled, otherwise it'd print rightside-up like the STL)

## Quick Start

```bash
podman build -t orcaslicer-web .
podman run -d --name orcaslicer-web -p 5000:5000 -v orcaslicer-profiles:/data orcaslicer-web
```

Open http://localhost:5000 for the web UI, or use the API directly.

## Web UI

Printer, process, and filament profiles are managed together in one unified view (upload, select, delete) rather than switching between separate tabs.

On first startup with no profiles uploaded, each category is seeded from the Sovol SV08 / Protopasta PLA starter profiles in `tests/fixtures/profiles/` (shown with a "Default" badge) so the app is immediately usable. Seeding only happens per-category while it's empty -- once you upload, replace, or delete a profile in a category, it's left alone.

On first use, the filament and bed type selectors default to a PLA filament profile (matched by name) and `Textured PEI Plate`. After that, the UI remembers the most recently used printer/process/filament profiles, bed type, auto-orient setting, and print option overrides in the browser's `localStorage`, and preselects them on the next visit.

The slice form also lets you override a few common process profile settings per job -- layer height, infill density, and support generation -- without editing the stored profile. Leave a field blank to use the profile's own setting.

## API

### Health Check

```bash
curl http://localhost:5000/api/health
```

### Upload Profiles

Profiles are stored on the server and reused across slicing jobs. Three categories: `printer`, `process`, `filament`.

Export profiles from OrcaSlicer's GUI (right-click a preset, "Export"). The API automatically resolves inherited settings from OrcaSlicer's bundled system profiles and injects required CLI metadata.

```bash
# Upload a printer profile
curl -X POST http://localhost:5000/api/profiles/printer \
  -F 'file=@my-printer.json' \
  -F 'name=my-printer'

# Upload a process profile
curl -X POST http://localhost:5000/api/profiles/process \
  -F 'file=@my-process.json' \
  -F 'name=my-process'

# Upload a filament profile
curl -X POST http://localhost:5000/api/profiles/filament \
  -F 'file=@my-filament.json' \
  -F 'name=my-filament'
```

The `name` field is optional. If omitted, the filename is used.

### List Profiles

```bash
curl http://localhost:5000/api/profiles/printer
curl http://localhost:5000/api/profiles/process
curl http://localhost:5000/api/profiles/filament
```

### Manage Profiles

```bash
# Download a profile
curl http://localhost:5000/api/profiles/printer/my-printer -o my-printer.json

# Replace a profile
curl -X PUT http://localhost:5000/api/profiles/printer/my-printer -F 'file=@updated.json'

# Rename a profile
curl -X PATCH http://localhost:5000/api/profiles/printer/my-printer \
  -H 'Content-Type: application/json' \
  -d '{"new_name": "new-name"}'

# Delete a profile
curl -X DELETE http://localhost:5000/api/profiles/printer/my-printer
```

### Slice a Model

```bash
curl -X POST http://localhost:5000/api/slice \
  -F 'model=@model.stl' \
  -F 'printer=my-printer' \
  -F 'process=my-process' \
  -F 'filament=my-filament' \
  -o output.gcode
```

The `printer`, `process`, and `filament` fields reference profile names already uploaded to the server. Accepts STL and 3MF files up to 100MB.

Optional parameters:

- `bed_type` -- one of `Textured PEI Plate`, `Cool Plate`, `Engineering Plate`, `High Temp Plate`. Defaults to the printer profile's setting if omitted.
- `orient` -- set to `1` to auto-orient the model for printing.
- `layer_height` -- mm, between `0.04` and `0.6`. Overrides the process profile's layer height for this job only.
- `fill_density` -- integer percentage, `0`-`100`. Overrides the process profile's infill density for this job only.
- `enable_support` -- `1` or `0`. Overrides the process profile's support generation for this job only.

Overrides are applied to an in-memory copy of the process profile and never mutate the stored profile on disk.

```bash
curl -X POST http://localhost:5000/api/slice \
  -F 'model=@model.stl' \
  -F 'printer=my-printer' \
  -F 'process=my-process' \
  -F 'filament=my-filament' \
  -F 'bed_type=Textured PEI Plate' \
  -F 'orient=1' \
  -F 'layer_height=0.28' \
  -F 'fill_density=25' \
  -F 'enable_support=1' \
  -o output.gcode
```

The response includes headers `X-Slice-Time-Seconds` and `X-Slicer-Stdout` for diagnostics.

### Check Slicer Status

Only one slicing job runs at a time. Returns 409 if busy.

```bash
curl http://localhost:5000/api/slice/status
```

## How It Works

- Profiles are stored as JSON files in `/data/profiles/{printer,process,filament}/` (persisted via volume mount)
- On upload, profiles exported from OrcaSlicer's GUI are automatically merged with their base system profiles to resolve `inherits` chains, and the `type`/`from` metadata fields required by the CLI are injected
- Slicing runs `OrcaSlicer --slice 0` in a subprocess with a 300 second timeout
- STL and GCODE temp files are cleaned up immediately after the response
- A threading lock prevents concurrent slicing (returns HTTP 409 if busy)
- Xvfb provides a virtual display for OrcaSlicer's headless operation

## Storage

Uploaded profiles are stored as flat JSON files under `/data/profiles/` inside the container, organized by category:

```
/data/
  profiles/
    printer/
    process/
    filament/
```

The container creates these directories on startup if they don't exist. No manual setup is required.

The `-v orcaslicer-profiles:/data` flag in the run command creates a named Podman/Docker volume that persists across container restarts, rebuilds, and removals. Profiles survive `podman rm` and `podman run` cycles as long as the volume exists.

If you omit the `-v` flag entirely, profiles are stored in the container's ephemeral filesystem and are lost when the container is removed.

To use a host directory instead of a named volume:

```bash
podman run -d --name orcaslicer-web -p 5000:5000 -v /path/on/host:/data orcaslicer-web
```

To inspect or back up the named volume:

```bash
podman volume inspect orcaslicer-profiles
podman volume export orcaslicer-profiles -o backup.tar
```

To start fresh, delete the volume:

```bash
podman volume rm orcaslicer-profiles
```

STL uploads and GCODE output are temporary -- they are written to `/tmp/slicing/` inside the container and deleted immediately after the response. Only profiles persist.

## Container Details

- Debian Trixie (slim) base with OrcaSlicer nightly (AppImage on amd64, flatpak on arm64)
- Runtime deps: xvfb, libgl1, libgtk-3-0, libwebkit2gtk, python3, Flask
- Exposed port: 5000
- Volume: `/data` for persistent profiles

## Development

Build and run with local source files mounted for quick iteration:

```bash
podman build -t orcaslicer-web .
podman run -d --name orcaslicer-web -p 5000:5000 \
  -v orcaslicer-profiles:/data \
  -v $(pwd)/app.py:/app/app.py:ro \
  -v $(pwd)/templates:/app/templates:ro \
  orcaslicer-web
```

After editing `app.py` or `templates/index.html`, restart the container to pick up changes:

```bash
podman restart orcaslicer-web
```

To rebuild from scratch (e.g. after changing the Containerfile or requirements):

```bash
podman rm -f orcaslicer-web
podman build -t orcaslicer-web .
podman run -d --name orcaslicer-web -p 5000:5000 \
  -v orcaslicer-profiles:/data \
  -v $(pwd)/app.py:/app/app.py:ro \
  -v $(pwd)/templates:/app/templates:ro \
  orcaslicer-web
```

To view container logs:

```bash
podman logs -f orcaslicer-web
```

## Testing

End-to-end tests run against a live container. They upload profiles, slice real STL models, and compare GCODE output against reference files sliced with the OrcaSlicer GUI.

**Prerequisites:** container running on port 5000, [`uv`](https://github.com/astral-sh/uv) installed.

```bash
# Start the container if not already running
podman run -d --name orcaslicer-web -p 5000:5000 -v orcaslicer-profiles:/data orcaslicer-web

# Run the full suite
uv run --with pytest --with requests pytest tests/ -v
```

To run against a different host:

```bash
ORCASLICER_API=http://other-host:5000 uv run --with pytest --with requests pytest tests/ -v
```

### Test files

`tests/fixtures/` is tracked in git and contains everything the test suite needs:

- `fixtures/models/` — STL models (`cube_20mm.stl`, `needs_orient.stl`)
- `fixtures/reference_gcode/` — reference GCODE sliced with the OrcaSlicer GUI (ground truth), named `<model>_PLA_reference.gcode`
- `fixtures/profiles/{printer,process,filament}/` — the Sovol SV08 printer/process and Protopasta PLA filament profiles used by every test

The reference GCODE files were sliced in the GUI with **Textured PEI Plate** bed type at 55°C. The `needs_orient` reference used auto-orient enabled. Tests assert that key GCODE header params (layer height, temperatures, nozzle diameter, filament type, profile IDs) match between the API and the GUI reference.

`test_files_for_now/` (git-ignored) holds extra scratch assets not needed by the automated suite -- alternate filament profiles, `.scad` sources, exported preset bundles, and one-off GCODE captured while debugging. Promote a file out of it into `tests/fixtures/` if a test starts depending on it.

### Adding new reference files

1. Create or export your STL and profiles
2. Slice in the OrcaSlicer GUI with your chosen settings, save the `.gcode`
3. Place the STL under `tests/fixtures/models/`, the GCODE under `tests/fixtures/reference_gcode/` (as `<prefix>_PLA_<anything>.gcode`), and profiles under `tests/fixtures/profiles/<category>/`
4. Add a test case in `tests/test_slice.py` following the existing pattern

## Inspiration

- Kevin O'Connor (creator of Klipper) [mentioned slicing could be easier](https://youtube.com/watch?v=tODfTn9Yr8s&t=1620s)
- https://github.com/OrcaSlicer/OrcaSlicer/discussions/1603
