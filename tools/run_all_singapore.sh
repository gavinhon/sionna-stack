#!/usr/bin/env bash
# Run every Singapore study at full accuracy, export each run for the viewer, and log to logs/.
# Takes about an hour on an RTX 5080 (under WSL2). Safe to re-run: every run gets its own dated folder.
#   bash tools/run_all_singapore.sh
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
export PATH="$HOME/.local/bin:$PATH"
mkdir -p logs
stamp=$(date +%Y%m%d-%H%M%S)
log="logs/run_all_${stamp}.log"
echo "log: $log"
step() { echo "=== $(date +%T) $*" | tee -a "$log"; }

for cfg in sg_raffles_place sg_macro sg_smallcell_mmwave; do
  step "experiment $cfg"
  uv run --frozen python -u scripts/run_experiment.py "configs/$cfg.toml" >> "$log" 2>&1 || { step "FAILED $cfg"; continue; }
  step "export $cfg"
  uv run --frozen python -u scripts/export_geo.py "outputs/$cfg/latest" >> "$log" 2>&1 || step "EXPORT FAILED $cfg"
done

step "sensitivity study"
uv run --frozen python -u scripts/sensitivity_study.py configs/sg_raffles_place.toml --bands 0.7 3.5 28 140 \
  --variants baseline=scenes/sg_raffles_place untagged_glass=scenes/sg_raffles_place_glass \
             specular=scenes/sg_raffles_place_specular rough_x2=scenes/sg_raffles_place_rough2 >> "$log" 2>&1 \
  || step "FAILED sensitivity"
step "ALL DONE"
