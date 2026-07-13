# Stage 1: Extract OrcaSlicer (multi-arch)
FROM debian:trixie-slim AS extract

ARG TARGETARCH=amd64
# Pinned to v2.4.2 (2026-07-12). To upgrade: download both assets,
# run `sha256sum` on each, and update these args + the URLs below.
ARG ORCASLICER_AMD64_SHA256=d12fb8c8eac1aecd2dfb6377acd48f994f8fa439ed5292fa532dd82880f029fd
ARG ORCASLICER_ARM64_SHA256=e1a07275a25f176626c55a5df39e91bc4476d8c28ee4a3192ff758e29dd5c3ba

ENV DEBIAN_FRONTEND=noninteractive

RUN printf 'Types: deb\nURIs: http://deb.debian.org/debian\nSuites: trixie trixie-updates\nComponents: main\nSigned-By: /usr/share/keyrings/debian-archive-keyring.pgp\n' \
    > /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates file \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp

# amd64: extract AppImage via its self-extractor (native arch, runs fine).
# arm64: extract the AppImage's embedded squashfs directly with unsquashfs
# instead of self-extracting. The arm64 AppImage is a static-pie ELF, which
# QEMU user-mode emulation (used to build this image on non-arm64 hosts)
# cannot execute, so `--appimage-extract` fails there with "Exec format
# error". unsquashfs only needs to read the file, not run it.
# (We previously used the arm64 flatpak build, but it's linked against
# GNOME Platform 50's glibc 2.42, newer than trixie's 2.41, causing
# `GLIBC_2.42' not found` at runtime. The AppImage build links against
# normal system libs, like the amd64 build, and bundles its own
# lib/orca-runtime for the rest.)
RUN if [ "$TARGETARCH" = "amd64" ]; then \
      curl -fSL -o OrcaSlicer.AppImage \
        "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v2.4.2/OrcaSlicer_Linux_AppImage_Ubuntu2404_V2.4.2.AppImage" && \
      echo "${ORCASLICER_AMD64_SHA256}  OrcaSlicer.AppImage" | sha256sum --check && \
      chmod +x OrcaSlicer.AppImage && \
      ./OrcaSlicer.AppImage --appimage-extract && \
      mv squashfs-root /opt/orcaslicer && \
      rm OrcaSlicer.AppImage; \
    elif [ "$TARGETARCH" = "arm64" ]; then \
      apt-get update && apt-get install -y --no-install-recommends squashfs-tools && \
      curl -fSL -o OrcaSlicer.AppImage \
        "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/v2.4.2/OrcaSlicer_Linux_AppImage_Ubuntu2404_aarch64_V2.4.2.AppImage" && \
      echo "${ORCASLICER_ARM64_SHA256}  OrcaSlicer.AppImage" | sha256sum --check && \
      OFFSET=$(grep -a -b -o -m1 -P '\x68\x73\x71\x73' OrcaSlicer.AppImage | head -1 | cut -d: -f1) && \
      unsquashfs -o "$OFFSET" -d /opt/orcaslicer OrcaSlicer.AppImage && \
      rm OrcaSlicer.AppImage && \
      apt-get purge -y squashfs-tools && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*; \
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
    libgtk-3-0 libnotify4 \
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
COPY tests/fixtures/profiles /app/tests/fixtures/profiles
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
