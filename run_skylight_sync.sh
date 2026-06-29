#!/bin/bash
# Wrapper invoked by cron (see `crontab -l`): runs one sync and exits.
# Usage: run_skylight_sync.sh   (cron) — runs `python skylight_sync.py --once`
set -euo pipefail

cd "$(dirname "$0")"

# Activate the project virtualenv if present.
if [ -f venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
elif [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

exec python skylight_sync.py --once
