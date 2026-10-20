#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PYTHON_BIN="${PYTHON_BIN:-python3}"
for ((attempt=0; attempt<180; attempt++)); do
  if [[ -f reports/reunion_sensor_aug_a10_v1/dc_relative/results.json ]]; then
    exec "$PYTHON_BIN" -u src/evaluate_reunion_sensor_drift.py \
      --all reports/reunion_context_arch_a10_v1/rnn \
      --dc-relative reports/reunion_sensor_aug_a10_v1/dc_relative \
      --ratios-only reports/reunion_feature_ablation_a10_v1/ratios_only \
      --output reports/reunion_sensor_aug_drift_a10_v1
  fi
  sleep 5
done
printf '%s\n' 'Training completion not confirmed; drift audit not started.' >&2
exit 1
