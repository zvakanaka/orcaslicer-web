# Stage 1: Extract OrcaSlicer AppImage
FROM ubuntu:24.04 AS extract

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates file \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp
RUN curl -fSL -o OrcaSlicer.AppImage \
    "https://github.com/OrcaSlicer/OrcaSlicer/releases/download/nightly-builds/OrcaSlicer_Linux_AppImage_Ubuntu2404_nightly.AppImage" \
    && chmod +x OrcaSlicer.AppImage \
    && ./OrcaSlicer.AppImage --appimage-extract \
    && mv squashfs-root /opt/orcaslicer \
    && rm OrcaSlicer.AppImage

# Stage 2: Runtime
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    xvfb \
    libgl1 libgl1-mesa-dri libegl1 \
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
