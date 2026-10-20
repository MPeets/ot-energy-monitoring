#!/usr/bin/env bash
# Installs the lab from an offline bundle. Needs Docker with Compose, and
# nothing from the internet.
#
#   sha256sum -c ot-energy-monitoring-<commit>.tar.sha256
#   tar -xf ot-energy-monitoring-<commit>.tar
#   cd ot-energy-monitoring-<commit>
#   ./install.sh
#
# Checks every file against its checksum and the bundle's platform against
# this machine, loads the images and checks each one against the manifest,
# validates the configuration, then starts the lab without pulling anything.
#
# A newer bundle installs over an older one the same way. The Compose project
# name is fixed in compose.yaml, so the data volumes carry over, and running
# the older bundle's install.sh again rolls back.
set -euo pipefail
export MSYS_NO_PATHCONV=1  # stops Git Bash on Windows rewriting paths
cd "$(dirname "$0")"

# fingerprint <image>: its platform and a digest of its filesystem layers,
# the same on Docker's classic and containerd image stores. Same as bundle.sh.
fingerprint() {
  local platform layers
  platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$1" | tr -d '\r')"
  layers="$(docker image inspect --format '{{json .RootFS.Layers}}' "$1" | tr -d '\r' | sha256sum | cut -d' ' -f1)"
  echo "$platform sha256:$layers"
}

echo "== Verifying checksums"
sha256sum -c --quiet SHA256SUMS
echo "   all files match"

echo "== Checking the platform"
host="$(docker version --format '{{.Server.Os}}/{{.Server.Arch}}' | tr -d '\r')"
bundle="$(cut -d' ' -f2 images/manifest.txt | tr -d '\r' | sort -u)"
if [[ "$bundle" != "$host" ]]; then
  echo "   This bundle's images are for $bundle, and this machine runs $host."
  echo "   Build the bundle on a machine with the same architecture as this one."
  exit 1
fi
echo "   $host"

echo "== Loading images"
docker load -i images/images.tar.gz

echo "== Checking each image against the manifest"
while read -r img platform layers; do
  actual="$(fingerprint "$img" 2>/dev/null || true)"
  if [[ "$actual" != "$platform $layers" ]]; then
    echo "   MISMATCH: $img"
    echo "     expected $platform $layers"
    echo "     got      ${actual:-<missing>}"
    exit 1
  fi
  echo "   ok  $img"
done < <(tr -d '\r' < images/manifest.txt)

cd repo

if [[ ! -f .env ]]; then
  cp .env.example .env
fi
if ! grep -qE '^GRAFANA_ADMIN_PASSWORD=.+' .env; then
  echo
  echo "Set GRAFANA_ADMIN_PASSWORD in $(pwd)/.env, then run ./install.sh again."
  exit 1
fi

# Scripts are run with bash explicitly, so an execute bit lost on the way
# across can't stop the install.
echo "== Validating configuration"
bash scripts/check.sh

echo "== Starting the lab (no pulls allowed)"
docker compose up -d --pull never
docker compose ps

echo
echo "Installed. To check it end to end and prove the network segmentation, from $(pwd):"
echo "  bash scripts/smoke-test.sh"
echo "  bash scripts/check-segmentation.sh"