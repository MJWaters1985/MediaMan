#!/bin/sh
# Build the MediaMan image on the TrueNAS host (run from the repo folder copied to the NAS):
#   sudo sh truenas/build-on-nas.sh
# Re-run after updating the source, then restart the app (Apps → mediaman → Restart) — or
# `sudo docker restart mediaman` — to pick up the new image.
set -e
cd "$(dirname "$0")/.."
docker build --pull -t mediaman:latest .
docker image prune -f >/dev/null 2>&1 || true
echo
echo "Built mediaman:latest. Install it with truenas/mediaman.yaml (Apps → Discover Apps → ⋮ → Install via YAML)."
