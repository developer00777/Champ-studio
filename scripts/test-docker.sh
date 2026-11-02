#!/usr/bin/env sh
# Run the engine test suite inside the dev image (python 3.12 + ffmpeg), with this repo mounted.
#   ./scripts/test-docker.sh                 # all tests
#   ./scripts/test-docker.sh tests/test_render.py -k title
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
docker build -q --target dev -t champstudio-dev -f "$ROOT/docker/Dockerfile" "$ROOT" >/dev/null
MSYS_NO_PATHCONV=1 docker run --rm -v "$ROOT:/src" champstudio-dev pytest -q -p no:cacheprovider "$@"
