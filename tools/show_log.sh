#!/usr/bin/env bash
# Print the important lines (steps, timings, errors) of the newest log in logs/.
#   bash tools/show_log.sh [pattern-of-log-name]
cd "$(dirname "$(readlink -f "$0")")/../logs" || exit 1
f=$(ls -t ${1:-*}.log 2>/dev/null | head -1)
[ -z "$f" ] && { echo "no log"; exit 0; }
echo "== $f"
grep -E "^=== |volume .* cells in|volume .*height layers|Done in|Exported|Traceback|Error|FAILED" "$f" | tail -40
