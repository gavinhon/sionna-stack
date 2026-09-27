#!/usr/bin/env bash
# Add the 3-D signal volume (coverage shells + height slider) to the latest run of every Singapore study and
# re-export them for the viewer. ~20 min on an RTX 5080 (the 18-beam small cell dominates).
#   bash tools/run_all_volumes.sh
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
export PATH="$HOME/.local/bin:$PATH"
mkdir -p logs
log="logs/volumes_$(date +%Y%m%d-%H%M%S).log"
echo "log: $log"
for cfg in sg_raffles_place sg_macro sg_smallcell_mmwave; do
  echo "=== $(date +%T) volume $cfg" | tee -a "$log"
  uv run --frozen python -u scripts/run_volume.py "configs/$cfg.toml" "outputs/$cfg/latest" >> "$log" 2>&1 \
    || { echo "=== FAILED $cfg" | tee -a "$log"; continue; }
  echo "=== $(date +%T) export $cfg" | tee -a "$log"
  uv run --frozen python -u scripts/export_geo.py "outputs/$cfg/latest" >> "$log" 2>&1 || echo "=== EXPORT FAILED $cfg" | tee -a "$log"
done
echo "=== $(date +%T) ALL DONE" | tee -a "$log"
