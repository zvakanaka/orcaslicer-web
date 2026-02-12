# OrcaSlicer API & Web UI

> [!WARNING]
> This software is still in its very early stages. I have NOT printed GCODE downloaded from it yet and I wouldn't recommend doing so yet.

Containerized web application that exposes OrcaSlicer's CLI slicing capability via a web UI and HTTP API. Upload slicer profiles (printer, process, filament JSONs), then slice STL/3MF files to download GCODE.

<img width="620" height="850" alt="image" src="https://github.com/user-attachments/assets/0b0bc0c7-1ad7-494f-805a-5d67775011a0" />

> Web UI

<img width="947" height="513" alt="image" src="https://github.com/user-attachments/assets/dc1428be-5328-4784-9d22-e72c2212ccd9" />

> Viewing GCODE downloaded from this in OrcaSlicer (with auto orient enabled otherwise it'd be upside-down like the STL)

## Quick Start

```bash
podman build -t orcaslicer-api .
podman run -d --name orcaslicer-api -p 5000:5000 -v orcaslicer-profiles:/data orcaslicer-api
```

Open http://localhost:5000 for the web UI, or use the API directly.

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
podman run -d --name orcaslicer-api -p 5000:5000 -v /path/on/host:/data orcaslicer-api
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

- Ubuntu 24.04 base with OrcaSlicer nightly AppImage (extracted)
- Runtime deps: xvfb, libgl1, libgtk-3-0, python3, Flask
- Exposed port: 5000
- Volume: `/data` for persistent profiles

## Inspiration

- Kevin O'Connor (creator of Klipper) [mentioned slicing could be easier](https://youtube.com/watch?v=tODfTn9Yr8s&t=1620s)
- https://github.com/OrcaSlicer/OrcaSlicer/discussions/1603
