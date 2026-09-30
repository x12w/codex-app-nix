#!/usr/bin/env bash
# Refresh the version and hash in package.nix from OpenAI's apt repository.
#
# The repository index is the same one the .deb's postinst adds to
# /etc/apt/sources.list.d/chatgpt.sources, and it is signed with the key that
# ships in that postinst. Reading the version from it and letting the build
# verify the hash is equivalent to what apt does.
#
# Requires: curl, nix.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

REPO="https://persistent.oaistatic.com/codex-app-prod/linux/deb"
INDEX="$REPO/dists/stable/main/binary-amd64/Packages"

index=$(curl -fsSL "$INDEX")

field() {
  printf '%s\n' "$index" | awk -v k="$1:" '$1 == k { print $2; exit }'
}

version=$(field Version)
filename=$(field Filename)
expected=$(field SHA256)

if [ -z "$version" ] || [ -z "$filename" ] || [ -z "$expected" ]; then
  echo "could not parse $INDEX" >&2
  exit 1
fi

# The index gives a hex digest; nixpkgs wants SRI.
hash=$(nix --extra-experimental-features nix-command \
  hash convert --to sri --hash-algo sha256 "$expected")

current=$(sed -n 's/^  version = "\(.*\)";$/\1/p' package.nix)

if [ "$current" = "$version" ]; then
  echo "already at $version"
else
  echo "updating $current -> $version"
fi

sed -i \
  -e "s|^  version = \".*\";$|  version = \"$version\";|" \
  -e "s|^    hash = \"sha256-.*\";$|    hash = \"$hash\";|" \
  package.nix

echo "$REPO/$filename"
echo "hash: $hash"
