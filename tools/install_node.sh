#!/usr/bin/env bash
# Install a pinned Node.js LTS for the CesiumJS viewer, user-level (no sudo), from the official nodejs.org
# tarball, verified against the published SHA-256 list. Idempotent.
#   bash tools/install_node.sh            # default version below
#   bash tools/install_node.sh 24.21.0
# Undo: rm -rf ~/.local/opt/node-v* ~/.local/bin/{node,npm,npx}
set -euo pipefail
VER="${1:-24.21.0}"
NAME="node-v${VER}-linux-x64"
OPT="$HOME/.local/opt"
BIN="$HOME/.local/bin"
mkdir -p "$OPT" "$BIN"
if [[ ! -x "$OPT/$NAME/bin/node" ]]; then
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  echo "Downloading Node.js v${VER} from nodejs.org"
  curl -fsSL -o "$tmp/$NAME.tar.xz" "https://nodejs.org/dist/v${VER}/${NAME}.tar.xz"
  curl -fsSL -o "$tmp/SHASUMS256.txt" "https://nodejs.org/dist/v${VER}/SHASUMS256.txt"
  (cd "$tmp" && grep " ${NAME}.tar.xz\$" SHASUMS256.txt | sha256sum -c -)
  tar -xJf "$tmp/$NAME.tar.xz" -C "$OPT"
fi
for b in node npm npx; do ln -sf "$OPT/$NAME/bin/$b" "$BIN/$b"; done
echo "node $("$BIN/node" --version), npm $("$BIN/npm" --version) in $BIN"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "NOTE: add $BIN to PATH (e.g. 'source ~/.local/bin/env' or add it in ~/.bashrc)";;
esac
