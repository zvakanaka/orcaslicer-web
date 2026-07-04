# Stage 1: Extract OrcaSlicer (multi-arch)
FROM debian:trixie-slim AS extract

ARG TARGETARCH=amd64
# Pinned to v2.4.0 (2026-06-20). To upgrade: download both assets,
# run `sha256sum` on each, and update these args + the URLs below.
ARG ORCASLICER_AMD64_SHA256=46556197dcc2fb55140e0b1e70c28b4c4da3208f12a4a2522012837c9d77ee10
ARG ORCASLICER_ARM64_SHA256=aca22d99da7af05e011b8f3c6f5ac5d90b67ce02c933561462f85a035faf2b68

ENV DEBIAN_FRONTEND=noninteractive

RUN printf 'Types: deb\nURIs: http://deb.debian.org/debian\nSuites: trixie trixie-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.pgp\n' \
    > /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates file \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp

# amd64: extract AppImage
# arm64: extract flatpak bundle via ostree (avoids downloading full GNOME runtime)
RUN if [ "$TARGETARCH" = "amd64" ]; then \
      curl -fSL -o OrcaSlicer.AppImage \
        "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v2.4.0/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.4.0.AppImage" && \
      echo "${ORCASLICER_AMD64_SHA256}  OrcaSlicer.AppImage" | sha256sum --check && \
      chmod +x OrcaSlicer.AppImage && \
      ./OrcaSlicer.AppImage --appimage-extract && \
      mv squashfs-root /opt/orcaslicer && \
      rm OrcaSlicer.AppImage; \
    elif [ "$TARGETARCH" = "arm64" ]; then \
      apt-get update && apt-get install -y --no-install-recommends flatpak ostree && \
      curl -fSL -o OrcaSlicer.flatpak \
        "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v2.4.0/OrcaSlicer-Linux-flatpak_V2.4.0_aarch64.flatpak" && \
      echo "${ORCASLICER_ARM64_SHA256}  OrcaSlicer.flatpak" | sha256sum --check && \
      ostree init --repo=/tmp/repo --mode=bare-user && \
      flatpak build-import-bundle /tmp/repo OrcaSlicer.flatpak && \
      REF=$(ostree refs --repo=/tmp/repo | head -1) && \
      ostree checkout --repo=/tmp/repo "$REF" /tmp/orcaslicer-raw && \
      mkdir -p /opt/orcaslicer/resources && \
      cp -a /tmp/orcaslicer-raw/files/* /opt/orcaslicer/ && \
      ln -s ../share/OrcaSlicer/profiles /opt/orcaslicer/resources/profiles && \
      printf '#!/bin/sh\nDIR=$(dirname "$(readlink -f "$0")")\nexport LD_LIBRARY_PATH="$DIR/lib:$DIR/lib64:$LD_LIBRARY_PATH"\nexec "$DIR/bin/orca-slicer" "$@"\n' \
        > /opt/orcaslicer/AppRun && \
      chmod +x /opt/orcaslicer/AppRun && \
      rm -rf /tmp/repo /tmp/orcaslicer-raw OrcaSlicer.flatpak && \
      apt-get purge -y flatpak ostree && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*; \
    fi

# Stage 2: Runtime
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN printf 'Types: deb\nURIs: http://deb.debian.org/debian\nSuites: trixie trixie-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.pgp\n' \
    > /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    xvfb \
    libgl1 libgl1-mesa-dri libegl1 libopengl0 libglu1-mesa libcurl4 \
    libgtk-3-0 \
    libgstreamer1.0-0 libgstreamer-plugins-base1.0-0 \
    libwebkit2gtk-4.1-0 \
    libglib2.0-0 \
    libfuse2 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=extract /opt/orcaslicer /opt/orcaslicer

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app.py /app/app.py
COPY templates /app/templates
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV ORCASLICER_BIN=/opt/orcaslicer/AppRun \
    PROFILES_DIR=/data/profiles \
    TEMP_DIR=/tmp/slicing \
    FLASK_HOST=0.0.0.0 \
    FLASK_PORT=5000 \
    DISPLAY=:99

EXPOSE 5000
VOLUME /data

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/health')"

WORKDIR /app
ENTRYPOINT ["/app/entrypoint.sh"]
