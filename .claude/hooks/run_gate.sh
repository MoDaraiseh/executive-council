#!/bin/sh
# Starts jurisdiction_gate.py with the first working Python 3.9+, whether this
# machine calls it python3, python or py (Windows launcher). Each candidate
# must actually run, so a Windows app-execution alias with no Python behind it
# is skipped.
#
# With --fail-closed (PreToolUse), exit 2 -- "block this tool call" to Claude
# Code -- if no usable Python is found or the gate itself fails. Otherwise a
# missing Python would be a non-blocking hook error and the call would go ahead.
# Other events only warn: PreToolUse is the one that allows or blocks commands.

# This script's folder, whether $0 uses / or (a Windows path) \ separators.
case "$0" in
  */*) here="${0%/*}" ;;
  *\\*) here="${0%\\*}" ;;
  *) here=. ;;
esac
gate="$here/jurisdiction_gate.py"
fail_closed=false
[ "${1:-}" = "--fail-closed" ] && fail_closed=true

for py in python3 python "py -3"; do
  # $py is unquoted on purpose, so "py -3" splits into command and flag.
  if $py -c 'import sys; sys.exit(sys.version_info < (3, 9))' </dev/null >/dev/null 2>&1; then
    $py "$gate"
    status=$?
    if [ "$status" -ne 0 ] && [ "$fail_closed" = true ]; then
      echo "executive-council jurisdiction gate failed (exit $status); blocking this tool call" >&2
      exit 2
    fi
    exit "$status"
  fi
done

echo "executive-council jurisdiction gate: no Python 3.9+ found (tried python3, python, py -3)" >&2
if [ "$fail_closed" = true ]; then
  exit 2
fi
exit 0
