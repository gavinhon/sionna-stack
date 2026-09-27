#!/usr/bin/env bash
# Wait until no process matches the given pattern (e.g. a long simulation), then print its last log lines.
#   bash tools/wait_for.sh "run_experiment.py configs/sg_macro" [logfile]
# This script and all its ancestor shells are ignored: their command lines contain the pattern text too
# (a first version matched its own parent shell and waited forever).
pat="$1"
ancestors=" "
p=$$
while [ -n "$p" ] && [ "$p" -gt 1 ]; do
  ancestors+="$p "
  p=$(ps -o ppid= -p "$p" | tr -d ' ')
done
while true; do
  others=""
  for q in $(pgrep -f -- "$pat"); do
    [[ "$ancestors" == *" $q "* ]] || others+="$q "
  done
  [ -z "$others" ] && break
  sleep 5
done
echo "finished: $pat"
[ -n "${2:-}" ] && tail -n 30 "$2"
exit 0
