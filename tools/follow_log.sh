#!/usr/bin/env bash
# Follow the newest log in logs/ and print only step markers, completions and errors (for long runs).
#   bash tools/follow_log.sh
f=$(ls -t "$(dirname "$(readlink -f "$0")")"/../logs/*.log | head -1)
echo "following $f"
tail -n +1 -f "$f" | grep --line-buffered -E "^=== |Traceback|Error|FAILED|Done in|snapshots in|cells in|height layers|Exported|ALL DONE"
