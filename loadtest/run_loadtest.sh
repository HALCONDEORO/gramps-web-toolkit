#!/usr/bin/env bash
# Run by Server on the host of the loadtest stack. Needs: docker, the loadtest docker network name,
# and the loadadmin password in LT_PASS (read it from ADMIN.txt; do not paste it anywhere).
#   LT_PASS="$(awk '/^Contra/{print $2}' ~/gramps-loadtest/ADMIN.txt)" ./run_loadtest.sh <docker-network> [web-container]
# Disposable tree "LoadTest" only. bench.py refuses any other tree. Memory is capped at 1 GB with no swap,
# so a dump that does not fit ends with exit 137 (that is a result, not a failure of the host).
set -euo pipefail
NET="${1:?docker network of the loadtest stack (docker network ls)}"
WEB="${2:-gramps-loadtest-grampsweb-1}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HERE/out"
exec nice -n 15 docker run --rm --name gwt-loadtest -e SETUPTOOLS_SCM_PRETEND_VERSION=0.2.3 \
  --network "$NET" --memory 1g --memory-swap 1g --cpus 1 --pids-limit 256 \
  -e LT_URL="http://$WEB:5000" -e LT_PASS \
  -v "$HERE/bench.py:/bench.py:ro" -v "$HERE/out:/out" \
  python:3.11-slim bash -c '
    pip install -q --no-cache-dir https://github.com/HALCONDEORO/gramps-web-toolkit/archive/refs/heads/main.zip &&
    python /bench.py'
