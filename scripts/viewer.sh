#!/usr/bin/env bash
# Start the CesiumJS viewer (Vite dev server) on http://localhost:5180 . Installs npm packages from
# viewer/package-lock.json on first run (`npm ci` = exact locked versions).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/../viewer"
export PATH="$HOME/.local/bin:$PATH"
command -v node >/dev/null || { echo "Node.js missing: run  bash tools/install_node.sh"; exit 1; }
[[ -d node_modules ]] || npm ci --no-fund --no-audit
[[ -f public/data/index.json ]] || echo "NOTE: no exports yet. Run: uv run scripts/export_geo.py outputs/<experiment>/latest"
exec npm run dev
