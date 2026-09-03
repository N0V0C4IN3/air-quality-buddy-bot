#!/usr/bin/env bash
# Backpressure. A failure here makes a keep impossible, however good the metric.
#
#   1. the suite still passes
#   2. the chart still tells the truth (see .auto/fidelity.py)
#
# Output is errors only - this runs every iteration and noise costs context.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! python -m pytest -q -x > .auto/checks.log 2>&1; then
  echo "TESTS FAILED"
  tail -n 40 .auto/checks.log
  exit 1
fi

if ! python .auto/fidelity.py > .auto/fidelity.log 2>&1; then
  cat .auto/fidelity.log
  exit 1
fi
