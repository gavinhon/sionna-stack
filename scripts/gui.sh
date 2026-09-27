#!/usr/bin/env bash
# Optional: NVIDIA's standalone Sionna RT GUI (Polyscope/ImGui desktop app) in an ISOLATED environment.
#
# * sionna-rt-gui 0.1.1 pins sionna-rt==1.2.2, which conflicts with this stack's sionna-rt 2.1.0, so it
#   runs via `uvx` (its own cached venv) and never touches .venv.
# * Under WSL2 the GUI's CUDA variant crashes: it always enables CUDA<->OpenGL interop when the variant is
#   CUDA, and WSL2 does not support that ("CUDA_ERROR_OPERATING_SYSTEM ... gl_interop.cpp").
#   So under WSL2 this launcher pre-selects the LLVM (CPU) variant; the GUI then uses rasterised preview.
#   Override with GUI_VARIANT=cuda_ad_mono_polarized (native Linux).
# The window appears through WSLg.
#   ./scripts/gui.sh                 # scene picker
#   ./scripts/gui.sh munich          # built-in scene, or a path to a Mitsuba .xml scene
set -euo pipefail
ROOT="$(dirname "$(readlink -f "$0")")/.."
if grep -qi microsoft /proc/sys/kernel/osrelease; then
  GUI_VARIANT="${GUI_VARIANT:-llvm_ad_mono_polarized}"
  # LLVM variant has no OptiX denoiser; the GUI would still try to enable it (configs/gui_wsl.yaml).
  if [[ "$GUI_VARIANT" == llvm* && " $* " != *" --config "* && " $* " != *" -c "* ]]; then
    set -- --config "$ROOT/configs/gui_wsl.yaml" "$@"
  fi
else
  GUI_VARIANT="${GUI_VARIANT:-}"
fi
OPTIX_DIR=$(ls -d "$HOME"/.local/lib/nvidia-optix-* 2>/dev/null | sort | tail -1 || true)
if [[ -n "$OPTIX_DIR" ]]; then
  export DRJIT_LIBOPTIX_PATH="$OPTIX_DIR/libnvoptix.so.1"
  export LD_LIBRARY_PATH="$OPTIX_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
export GUI_VARIANT
exec "$HOME/.local/bin/uvx" --python 3.12 --from sionna-rt-gui==0.1.1 python -c '
import os, sys
v = os.environ.get("GUI_VARIANT")
if v:
    import mitsuba as mi
    mi.set_variant(v)   # must happen before sionna.rt is imported (it only picks a variant if none is set)
from scripts.run import main
sys.argv = ["sionna-rt-gui"] + sys.argv[1:]
sys.exit(main())
' "$@"
