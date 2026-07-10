#!/usr/bin/env bash
# Updates the OrcaSlicer version pinned in Containerfile.
#
# Usage:
#   scripts/update-orcaslicer.sh              # update to latest stable release
#   scripts/update-orcaslicer.sh v2.4.3        # update to a specific tag
#   scripts/update-orcaslicer.sh --build       # also build+smoke-test the image after updating
#
# Requires: curl, jq, sha256sum. --build additionally requires podman.
set -euo pipefail

REPO="OrcaSlicer/OrcaSlicer"
CONTAINERFILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/Containerfile"

DO_BUILD=0
TAG_ARG=""
for arg in "$@"; do
  case "$arg" in
    --build) DO_BUILD=1 ;;
    -*) echo "Unknown option: $arg" >&2; exit 1 ;;
    *) TAG_ARG="$arg" ;;
  esac
done

for cmd in curl jq sha256sum; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "Missing required command: $cmd" >&2; exit 1; }
done

if [ -n "$TAG_ARG" ]; then
  RELEASE_JSON=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/tags/${TAG_ARG}")
else
  # /releases/latest already excludes drafts and prereleases.
  RELEASE_JSON=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases/latest")
fi

TAG=$(jq -r '.tag_name' <<<"$RELEASE_JSON")
if [ -z "$TAG" ] || [ "$TAG" = "null" ]; then
  echo "Could not resolve a release tag." >&2
  exit 1
fi

CURRENT_TAG="v$(grep -oP '(?<=Pinned to v)[0-9.]+' "$CONTAINERFILE" | head -1)"
if [ "$TAG" = "$CURRENT_TAG" ]; then
  echo "Already pinned to ${TAG}. Nothing to do."
  exit 0
fi

AMD64_URL=$(jq -r '.assets[].browser_download_url | select(test("^https://.*/OrcaSlicer_Linux_AppImage_Ubuntu2404_V[0-9.]+\\.AppImage$"))' <<<"$RELEASE_JSON")
ARM64_URL=$(jq -r '.assets[].browser_download_url | select(test("^https://.*/OrcaSlicer-Linux-flatpak_V[0-9.]+_aarch64\\.flatpak$"))' <<<"$RELEASE_JSON")

if [ -z "$AMD64_URL" ] || [ -z "$ARM64_URL" ]; then
  echo "Could not find expected release assets for ${TAG}. Asset names may have changed upstream:" >&2
  jq -r '.assets[].name' <<<"$RELEASE_JSON" >&2
  exit 1
fi

echo "Updating OrcaSlicer: ${CURRENT_TAG} -> ${TAG}"

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

echo "Downloading amd64 AppImage..."
curl -fSL -o "$WORKDIR/amd64.AppImage" "$AMD64_URL"
echo "Downloading arm64 flatpak..."
curl -fSL -o "$WORKDIR/arm64.flatpak" "$ARM64_URL"

AMD64_SHA256=$(sha256sum "$WORKDIR/amd64.AppImage" | cut -d' ' -f1)
ARM64_SHA256=$(sha256sum "$WORKDIR/arm64.flatpak" | cut -d' ' -f1)

VERSION_NUM="${TAG#v}"
TODAY=$(date -u +%Y-%m-%d)

sed -i \
  -e "s#Pinned to v[0-9.]\+ ([0-9-]\+)#Pinned to ${TAG} (${TODAY})#" \
  -e "s#ORCASLICER_AMD64_SHA256=[0-9a-f]\+#ORCASLICER_AMD64_SHA256=${AMD64_SHA256}#" \
  -e "s#ORCASLICER_ARM64_SHA256=[0-9a-f]\+#ORCASLICER_ARM64_SHA256=${ARM64_SHA256}#" \
  -e "s#releases/download/v[0-9.]\+/OrcaSlicer_Linux_AppImage_Ubuntu2404_V[0-9.]\+\.AppImage#releases/download/${TAG}/OrcaSlicer_Linux_AppImage_Ubuntu2404_V${VERSION_NUM}.AppImage#" \
  -e "s#releases/download/v[0-9.]\+/OrcaSlicer-Linux-flatpak_V[0-9.]\+_aarch64\.flatpak#releases/download/${TAG}/OrcaSlicer-Linux-flatpak_V${VERSION_NUM}_aarch64.flatpak#" \
  "$CONTAINERFILE"

echo "Containerfile updated to ${TAG}."

if [ "$DO_BUILD" = "1" ]; then
  command -v podman >/dev/null 2>&1 || { echo "podman not found, cannot --build" >&2; exit 1; }
  echo "Building and smoke-testing image (amd64)..."
  IMAGE_TAG="orcaslicer-web:update-test"
  podman build --platform linux/amd64 -t "$IMAGE_TAG" -f "$CONTAINERFILE" "$(dirname "$CONTAINERFILE")"
  CID=$(podman run -d --rm -p 15000:5000 "$IMAGE_TAG")
  cleanup_build() { podman stop "$CID" >/dev/null 2>&1 || true; podman rmi "$IMAGE_TAG" >/dev/null 2>&1 || true; }
  trap cleanup_build EXIT
  for i in $(seq 1 30); do
    if curl -fsS http://localhost:15000/api/health | grep -q '"slicer_found":true'; then
      echo "Smoke test passed: health check OK."
      break
    fi
    if [ "$i" = "30" ]; then
      echo "Smoke test failed: health check did not report slicer_found=true." >&2
      podman logs "$CID" >&2 || true
      exit 1
    fi
    sleep 1
  done
fi

echo
echo "Next steps:"
echo "  git diff Containerfile"
echo "  podman build -t orcaslicer-web ."
echo "  git add Containerfile && git commit -m 'Update OrcaSlicer to ${TAG}'"
