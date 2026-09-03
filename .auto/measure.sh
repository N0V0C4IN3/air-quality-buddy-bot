#!/usr/bin/env bash
# The benchmark. Emits `METRIC name=value` lines on stdout.
#
# Primary metric is `card_ms_total`. Read `.auto/prompt.md` for its noise
# floor before trusting a small movement.
set -euo pipefail
cd "$(dirname "$0")/.."

# Fail fast on a broken tree: a syntax error should cost a second, not a sweep.
python -m compileall -q common reporter-bot web-api sensor-reader > /dev/null

python .auto/bench.py --reps 3
