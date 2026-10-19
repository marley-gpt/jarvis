# Sourced by bin/* wrappers. Picks a working Python 3.10+ (on Windows `python3` can be a Store stub).
for candidate in python3 python "py -3"; do
  if $candidate -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
    JARVIS_PY=$candidate
    break
  fi
done
if [ -z "$JARVIS_PY" ]; then
  echo "jarvis: Python 3.10+ not found. Install it from https://www.python.org/downloads/" >&2
  exit 3
fi
