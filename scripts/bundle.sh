#!/usr/bin/env bash
# Builds the offline install bundle, on a machine WITH internet access:
#
#   ./scripts/bundle.sh
#
# Output: dist/ot-energy-monitoring-<commit>.tar and a .sha256 file next to it.
# Carry both across the air gap (approved USB, data diode, transfer station).
#
# The bundle contains:
#   repo/       the committed repository (git archive: no .env, no scenario files)
#   images/     every container image the lab uses, and a manifest of them
#   install.sh  the offline installer
#   SHA256SUMS  checksums of all of the above
#
# Versions come from .env.example, the committed source of truth, not your
# local .env, so the bundle always matches the commit it was built from. The
# images are for this machine's platform (linux/amd64 on most PCs and servers),
# and the installer refuses a bundle built for a different one.
#
# SKIP_PULL=1 uses images already present locally instead of pulling.
set -euo pipefail
export MSYS_NO_PATHCONV=1  # stops Git Bash on Windows rewriting paths
cd "$(dirname "$0")/.."

# fingerprint <image>: its platform and a digest of its filesystem layers.
# Image IDs can't be compared between machines: Docker's containerd image store
# uses the digest of the image index, the classic store the digest of the
# config, so the same image gets a different ID. The layers are the same in
# both. The same function is in install-offline.sh.
fingerprint() {
  local platform layers
  platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$1" | tr -d '\r')"
  layers="$(docker image inspect --format '{{json .RootFS.Layers}}' "$1" | tr -d '\r' | sha256sum | cut -d' ' -f1)"
  echo "$platform sha256:$layers"
}

if [[ -n "$(git status --porcelain)" && "${ALLOW_DIRTY:-0}" != 1 ]]; then
  echo "Working tree has uncommitted changes. Commit first, so the bundle"
  echo "matches a commit (or set ALLOW_DIRTY=1 to override)."
  exit 1
fi

COMMIT="$(git rev-parse --short HEAD)"
NAME="ot-energy-monitoring-$COMMIT"
STAGE="dist/$NAME"
rm -rf "$STAGE" "dist/$NAME.tar" "dist/$NAME.tar.sha256"
mkdir -p "$STAGE/repo" "$STAGE/images"

echo "== Exporting repository at $COMMIT"
git archive --format=tar HEAD | tar -x -C "$STAGE/repo"
cp scripts/install-offline.sh "$STAGE/install.sh"

echo "== Resolving images from compose.yaml and .env.example"
# compose.yaml requires a Grafana password; a placeholder is enough to list images.
# The tools profile is included, so a tool on an image of its own isn't missed.
mapfile -t IMAGES < <(
  GRAFANA_ADMIN_PASSWORD=placeholder \
    docker compose --env-file .env.example --profile tools config --images | tr -d '\r' | sort -u
)
printf '   %s\n' "${IMAGES[@]}"

if [[ "${SKIP_PULL:-0}" != 1 ]]; then
  echo "== Pulling images"
  for img in "${IMAGES[@]}"; do docker pull -q "$img"; done
fi

echo "== Recording what each image contains"
for img in "${IMAGES[@]}"; do
  # Separate assignment, so set -e stops the script if an image is missing
  print="$(fingerprint "$img")"
  echo "$img $print"
done > "$STAGE/images/manifest.txt"
sed 's/^/   /' "$STAGE/images/manifest.txt"

echo "== Saving images (this takes a while)"
docker save "${IMAGES[@]}" | gzip > "$STAGE/images/images.tar.gz"

echo "== Writing checksums"
# Written outside the folder first, so the checksum file never lists itself
(cd "$STAGE" && find . -type f | sort | xargs sha256sum) > "$STAGE.sums"
mv "$STAGE.sums" "$STAGE/SHA256SUMS"

echo "== Packing"
tar -cf "dist/$NAME.tar" -C dist "$NAME"
(cd dist && sha256sum "$NAME.tar" > "$NAME.tar.sha256")
rm -rf "$STAGE"

echo
echo "Bundle ready:"
ls -lh "dist/$NAME.tar" "dist/$NAME.tar.sha256"