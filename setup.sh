#!/usr/bin/env bash
# Idempotent environment setup for sionna-stack (Ubuntu 24.04, native or WSL2). Safe to re-run.
#   1. checks system prerequisites (never runs sudo itself; prints the command instead)
#   2. installs uv (user-level) if missing
#   3. creates/updates .venv from uv.lock (exact pinned versions: `uv sync --frozen`)
#   4. checks the NVIDIA driver and, under WSL2, the user-level OptiX files
#   5. lists Mitsuba variants and prints the backend that will actually be used
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")"

ok()   { printf '  \033[32m[ok]\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m[!!]\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m[xx]\033[0m %s\n' "$*"; }
need_sudo=()

echo "== System"
. /etc/os-release
ok "$PRETTY_NAME, kernel $(uname -r)"
IS_WSL=0; grep -qi microsoft /proc/sys/kernel/osrelease && IS_WSL=1 && ok "running under WSL2"
[[ "${VERSION_ID}" == "24.04" ]] || warn "Sionna recommends Ubuntu 24.04 (found ${VERSION_ID})"
if [[ $EUID -eq 0 ]]; then warn "running as root; a normal user is recommended (see README)"; fi

# Dr.Jit links libatomic.so.1 (missing on minimal WSL images)
if ldconfig -p | grep -q 'libatomic.so.1 '; then ok "libatomic1 present"
else fail "libatomic1 missing (drjit cannot be imported)"; need_sudo+=("libatomic1"); fi
# LLVM for the CPU backend (Dr.Jit loads libLLVM.so dynamically; >= 11.1)
if ls /usr/lib/x86_64-linux-gnu/libLLVM*.so* >/dev/null 2>&1 || [[ -n "${DRJIT_LIBLLVM_PATH:-}" ]]; then
  ok "LLVM for CPU backend: $(ls /usr/lib/x86_64-linux-gnu/libLLVM*.so* 2>/dev/null | head -1)${DRJIT_LIBLLVM_PATH:-}"
else warn "no libLLVM found (CPU fallback unavailable)"; need_sudo+=("llvm"); fi
if ((${#need_sudo[@]})); then
  echo; fail "Please run:  sudo apt update && sudo apt install -y ${need_sudo[*]}"
  [[ " ${need_sudo[*]} " == *" libatomic1 "* ]] && exit 1
fi

echo "== Python environment (uv)"
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
  echo "  installing uv (user-level, ~/.local/bin) from https://astral.sh/uv/install.sh"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi
ok "$(uv --version)"
uv sync --frozen || { fail "uv sync failed"; exit 1; }
ok ".venv in sync with uv.lock ($(.venv/bin/python --version))"

echo "== NVIDIA driver / GPU"
if command -v nvidia-smi >/dev/null && nvidia-smi >/dev/null 2>&1; then
  read -r GPU DRV < <(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1 | awk -F', ' '{gsub(/ /,"_",$1); print $1, $2}')
  LINUX_BUILD=$(nvidia-smi | sed -n 's/.*NVIDIA-SMI \([0-9.]*\).*/\1/p' | head -1)
  ok "GPU ${GPU//_/ }, driver ${DRV} (Linux-side build ${LINUX_BUILD})"
  [[ "${DRV%%.*}" -ge 535 ]] || warn "Mitsuba GPU variants need driver >= 535"
  if ((IS_WSL)); then
    OPTIX_DIR=$(ls -d "$HOME"/.local/lib/nvidia-optix-* 2>/dev/null | sort | tail -1)
    if [[ -n "$OPTIX_DIR" ]]; then
      OV=$(basename "$OPTIX_DIR" | sed 's/nvidia-optix-//')
      ok "user-level OptiX files: $OPTIX_DIR"
      [[ "${OV%%.*}" == "${LINUX_BUILD%%.*}" ]] || warn "OptiX files are from branch ${OV%%.*}, driver is ${LINUX_BUILD%%.*}: re-run tools/optix_wsl_userlevel.sh <matching version> (see README)"
    else
      warn "WSL2: no user-level OptiX files -> GPU ray tracing unavailable, CPU fallback will be used."
      warn "      Enable GPU: bash tools/optix_wsl_userlevel.sh <linux-driver-version>   (see README: 'GPU / OptiX under WSL2')"
    fi
  fi
else
  warn "nvidia-smi not available -> CPU backend only"
fi

echo "== Viewer (Node.js + CesiumJS)"
if command -v node >/dev/null && [[ "$(command -v node)" != /mnt/* ]]; then
  ok "node $(node --version) ($(command -v node))"
  if [[ -f viewer/package-lock.json ]]; then
    if [[ ! -d viewer/node_modules ]]; then (cd viewer && npm ci --no-fund --no-audit >/dev/null) && ok "viewer npm packages installed (npm ci)"
    else ok "viewer npm packages present"; fi
  fi
else
  warn "no Linux Node.js (the Windows one is not used). Install:  bash tools/install_node.sh"
fi
[[ -f viewer/.env ]] && grep -q 'VITE_CESIUM_ION_TOKEN=.\+' viewer/.env && ok "Cesium ion token configured (viewer/.env)" \
  || ok "no Cesium ion token: viewer uses OpenStreetMap tiles / offline Natural Earth (optional, see README)"

echo "== Mitsuba variants and active backend"
.venv/bin/python - <<'PY'
from sionna_stack import backend
info = backend.init(quiet=True)
import mitsuba as mi
print("  variants available:", ", ".join(v for v in mi.variants() if "mono_polarized" in v), "(+ others)")
for line in info.summary().splitlines():
    print("  " + line)
PY
echo
echo "Next: uv run verify.py"
echo "      uv run scripts/run_experiment.py configs/sg_raffles_place.toml"
echo "      uv run scripts/export_geo.py outputs/sg_raffles_place/latest && ./scripts/viewer.sh   (http://localhost:5180)"
echo "      ./scripts/jupyter.sh                                                                   (http://localhost:8890)"
