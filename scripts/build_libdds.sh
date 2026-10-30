#!/usr/bin/env bash
# Builds bin/libdds.so, the double-dummy solver the Linux image loads, from a DDS
# release tag. Ben's ctypes wrapper (src/ddsolver/dds.py) uses DDS's classic C API
# (SolveBoard, CalcDDtable, SetMaxThreads, ...), which DDS 3.x keeps; the build is
# the library sources alone, without Bazel, as upstream Ben builds its dds3 module.
#
# It builds inside ubuntu:24.04, the image's base, so the glibc it links against is
# the one it runs on. libstdc++ and libgcc are linked statically.
#
# Usage: scripts/build_libdds.sh [tag]   (default v3.1.0; writes bin/libdds.so)
set -euo pipefail
TAG=${1:-v3.1.0}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
git clone --quiet --depth 1 --branch "$TAG" https://github.com/dds-bridge/dds.git "$WORK/dds"
mkdir -p "$WORK/out"
docker run --rm --network host -e https_proxy -e HTTPS_PROXY \
  -v "$WORK/dds:/dds:ro" -v "$WORK/out:/out" "${BASE_IMAGE:-ubuntu:24.04}" bash -c '
    set -e
    apt-get update -qq && apt-get install -y -qq --no-install-recommends g++ >/dev/null
    g++ -std=c++20 -O3 -fPIC -shared -pthread -static-libstdc++ -static-libgcc -DNDEBUG \
      -I/dds/library/src -I/dds/library/src/api \
      $(find /dds/library/src -name "*.cpp" | sort) -o /out/libdds.so
    strip --strip-unneeded /out/libdds.so'
cp "$WORK/out/libdds.so" "$ROOT/bin/libdds.so"
# The wrapper reads its version from here (src/ddsolver/dds.py, library_version).
echo "${TAG#v} dds-bridge/dds $TAG $(git -C "$WORK/dds" rev-parse --short HEAD)" > "$ROOT/bin/libdds.so.version"
echo "bin/libdds.so built from dds-bridge/dds $TAG ($(git -C "$WORK/dds" rev-parse --short HEAD))"
