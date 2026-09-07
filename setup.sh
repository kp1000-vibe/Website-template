#!/usr/bin/env bash
# One time setup. Safe to re-run: it never overwrites a config you have edited.
set -euo pipefail

cd "$(dirname "$0")"

# Mirror everything to setup-log.txt so a failed run can be shared as a file.
exec > >(tee setup-log.txt) 2>&1

say() { printf "\n\033[1m%s\033[0m\n" "$*"; }

say "1/5  checking python"
PY=""
for c in python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    v=$("$c" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
    if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then
      PY="$c"; echo "  using $c (python $v)"; break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "  need python 3.10 or newer. On a mac: brew install python@3.12" >&2
  exit 1
fi

say "2/5  creating the virtual environment"
[ -d .venv ] || "$PY" -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r requirements.txt
echo "  dependencies installed"

say "3/5  config files"
for f in config profile; do
  if [ -f "config/$f.yaml" ]; then
    echo "  config/$f.yaml already exists, left alone"
  else
    cp "config/$f.example.yaml" "config/$f.yaml"
    echo "  created config/$f.yaml from the example"
  fi
done
[ -f config/manual_urls.txt ] || cp config/manual_urls.txt.example config/manual_urls.txt

say "4/5  browser"
if ! ./.venv/bin/python -c "
from jobagent.fill.browser import find_chrome
import sys; sys.exit(0 if find_chrome(None) else 1)" 2>/dev/null; then
  echo "  Chrome not found. Install Google Chrome, or set browser.chrome_binary in config/config.yaml"
else
  echo "  Chrome found"
fi

say "5/5  checking everything"
set +e
./.venv/bin/python -m jobagent doctor
set -e

cat <<'NEXT'

Next, in order:

  1. edit config/profile.yaml           your details, and the resume path
  2. export ANTHROPIC_API_KEY=sk-ant-   or run: ant auth login
  3. ./.venv/bin/python -m jobagent verify-boards
  4. ./.venv/bin/python -m jobagent chrome     log in once, then leave it open
  5. ./.venv/bin/python -m jobagent run
  6. ./.venv/bin/python -m jobagent review     then open http://127.0.0.1:8765

NEXT
