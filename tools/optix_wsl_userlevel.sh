#!/usr/bin/env bash
# Make OptiX usable by Dr.Jit/Mitsuba inside WSL2 WITHOUT touching C:\Windows\System32\lxss\lib.
#
# Background: in WSL2, /usr/lib/wsl/lib/libnvoptix.so.1 is only a ~10 KB dxcore loader stub, so Dr.Jit
# prints "libnvoptix.so.1 could not be loaded -- disabling OptiX backend!". Mitsuba's official fix
# (https://mitsuba.readthedocs.io/en/stable/src/optix_setup.html) extracts the matching *Linux* driver and
# copies 5 files into C:\Windows\System32\lxss\lib. This script puts the same 5 files into a per-user folder
# instead; setup.sh / env.sh then point Dr.Jit at them (DRJIT_LIBOPTIX_PATH + LD_LIBRARY_PATH).
#
# Undo:   rm -rf ~/.local/lib/nvidia-optix-<ver> ~/nvidia-driver   (nothing else is changed)
# Redo after EVERY Windows GPU driver update (pick the Linux driver of the same branch; see README).
set -euo pipefail

VER="${1:-590.48.01}"
DL_DIR="$HOME/nvidia-driver"
RUN="$DL_DIR/NVIDIA-Linux-x86_64-$VER.run"
EXTRACT="$DL_DIR/NVIDIA-Linux-x86_64-$VER"
DEST="$HOME/.local/lib/nvidia-optix-$VER"
URL="https://us.download.nvidia.com/XFree86/Linux-x86_64/$VER/NVIDIA-Linux-x86_64-$VER.run"

mkdir -p "$DL_DIR"
if [[ ! -f "$RUN" ]]; then
  echo "Downloading $URL"
  curl -fsSL --retry 3 -o "$RUN.part" "$URL"
  mv "$RUN.part" "$RUN"
fi
echo "Verifying the self-extracting archive's embedded checksum..."
sh "$RUN" --check
if [[ ! -d "$EXTRACT" ]]; then
  sh "$RUN" --extract-only --target "$EXTRACT"
fi

mkdir -p "$DEST"
# Same 5 files as Mitsuba's WSL2 guide (renames identical to the guide).
cp -v "$EXTRACT"/libnvoptix.so."$VER"               "$DEST/libnvoptix.so.1"
cp -v "$EXTRACT"/libnvidia-ptxjitcompiler.so."$VER" "$DEST/libnvidia-ptxjitcompiler.so.1"
cp -v "$EXTRACT"/libnvidia-rtcore.so."$VER"         "$DEST/"
cp -v "$EXTRACT"/libnvidia-gpucomp.so."$VER"        "$DEST/"
cp -v "$EXTRACT"/nvoptix.bin                        "$DEST/"
echo "$VER" > "$DEST/VERSION"
echo
echo "OptiX user-level files installed in: $DEST"
echo "setup.sh/env.sh will export DRJIT_LIBOPTIX_PATH=$DEST/libnvoptix.so.1 and prepend $DEST to LD_LIBRARY_PATH."
