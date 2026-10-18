#!/bin/sh
# Build an MK20 Agent firmware release. Host-only: needs GNU coreutils stat + GNU tar (the box / any Linux).
#   mk20-release-build.sh <firmware-dir> <outdir> [build-id]
# <firmware-dir> holds VERSION, KERNELS (allowed p4 sha256, one per line), release/ (payload) and rescue/.
# Output in <outdir>:
#   mk20-fw-<v>/            release dir (payload + MANIFEST), installed on the device as /data/mk20/releases/<v>/
#   mk20-fw-<v>.tar(.sha256) reproducible tarball of that dir (host pins this sha256 at install time)
#   rescue/                 rescue set with .sha256 files (watcher.sh, hardreset.sh, loader.sh, lunch.sh)
set -eu
FW=$1; OUT=$2; BID=${3:-dev}
[ "$(wc -l < "$FW/VERSION")" -le 1 ] || { echo "VERSION must be one line" >&2; exit 1; }
echo "$BID" | grep -Eq '^[A-Za-z0-9._-]{1,40}$' || { echo "bad build id" >&2; exit 1; }
V=$(head -n 1 "$FW/VERSION"); echo "$V" | grep -Eq '^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$' || { echo "bad VERSION $V" >&2; exit 1; }
K=$(grep -E '^[0-9a-f]{64}$' "$FW/KERNELS" | paste -sd, -); [ -n "$K" ] || { echo "no KERNELS" >&2; exit 1; }
D=$OUT/mk20-fw-$V; rm -rf "$D" "$OUT/rescue"; mkdir -p "$D" "$OUT/rescue"
(cd "$FW/release" && find . -type f | sed 's#^\./##' | LC_ALL=C sort) > "$OUT/.files"
while read -r f; do
  case "$f" in *' '*|*..*) echo "unsafe name $f" >&2; exit 1;; esac
  printf '%s' "$f" | LC_ALL=C grep -q '[^A-Za-z0-9._/-]' && { echo "unsafe chars in $f" >&2; exit 1; }
  [ -L "$FW/release/$f" ] && { echo "symlink not allowed: $f" >&2; exit 1; }
  mkdir -p "$D/$(dirname "$f")"; cp -p "$FW/release/$f" "$D/$f"
done < "$OUT/.files"
{
  echo "name=mk20-agent-firmware"; echo "version=$V"; echo "build=$BID"; echo "kernels=$K"; echo "--"
  while read -r f; do
    m=$(stat -c '%a' "$D/$f"); case "$m" in 7??|6??|5??|4??) ;; *) m=644;; esac
    echo "$(sha256sum "$D/$f" | cut -d' ' -f1) $m $f"
  done < "$OUT/.files"
} > "$D/MANIFEST"
rm -f "$OUT/.files"
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -C "$OUT" -cf "$OUT/mk20-fw-$V.tar" "mk20-fw-$V"
(cd "$OUT" && sha256sum "mk20-fw-$V.tar" > "mk20-fw-$V.tar.sha256")
for f in watcher.sh hardreset.sh loader.sh lunch.sh; do
  cp -p "$FW/rescue/$f" "$OUT/rescue/$f"; (cd "$OUT/rescue" && sha256sum "$f" | awk -v n="$f" '{print $1"  "n}' > "$f.sha256")
done
echo "built $V ($BID): $(cut -d' ' -f1 "$OUT/mk20-fw-$V.tar.sha256")  files=$(sed -n '/^--$/,$p' "$D/MANIFEST" | sed 1d | wc -l)"
