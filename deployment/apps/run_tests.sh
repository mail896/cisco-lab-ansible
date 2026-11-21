#!/bin/sh
# Run the complete local test suite with the same Python environment as the APP runtime.
# The runtime venv provides reportlab and PyJWT; project-local libnm and Playwright are used when present.
set -eu
cd "$(dirname "$0")/../.."
python="${LAB_TEST_PYTHON:-/opt/cisco-lab/.venv/bin/python}"
libnm="$PWD/.checks/deps/libnm/usr/lib/x86_64-linux-gnu"
if [ -d "$libnm" ]; then
    LD_LIBRARY_PATH="$libnm${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export LD_LIBRARY_PATH
fi
# Optional project-local Playwright library and Chromium for the browser workflows.
browser="$PWD/.checks/browser"
if [ -d "$browser/python/playwright" ]; then
    PYTHONPATH="$browser/python${PYTHONPATH:+:$PYTHONPATH}"
    PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$browser/ms-playwright}"
    export PYTHONPATH PLAYWRIGHT_BROWSERS_PATH
fi
PYTHONDONTWRITEBYTECODE=1 exec "$python" -B -m unittest discover -s tests -t . "$@"
