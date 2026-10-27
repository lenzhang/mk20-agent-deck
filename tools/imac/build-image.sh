#!/bin/sh
# Build the iMac's mk20-build:trixie image from the repo's deck/tools/build-image/Dockerfile
# (never from a hand-kept copy: the old ~/mk20-build/docker/Dockerfile lacked procps, so pkill was
# missing and 46 deck tests failed). usage: sh tools/imac/build-image.sh <repo-or-worktree>
set -eu
REPO=${1:?usage: build-image.sh <repo-or-worktree>}
DF="$REPO/deck/tools/build-image"
[ -f "$DF/Dockerfile" ] || { echo "no $DF/Dockerfile" >&2; exit 2; }
. "$HOME/mk20-build/tools/env.sh"
docker build -t mk20-build:trixie "$DF"
docker run --rm mk20-build:trixie sh -c 'command -v pkill && command -v nc' >/dev/null || { echo "image lacks pkill/nc" >&2; exit 1; }
echo "mk20-build:trixie built from $DF (pkill ok)"
