#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON_BIN="${PYTHON_BIN:-python3}"
DETECT_PYTHON="${DETECT_PYTHON:-.venv-detect/bin/python}"
INPUTS="${INPUTS:-reports/competition_real_inputs_v1}"
OUTPUT="${1:-reports/competition_inference_new_run}"
# Public sample capture times are unknown. The assigned visual times are DEMO ONLY.
"$PYTHON_BIN" src/competition_infer.py --task timeseries \
 --input "${INPUTS}/telemetry.csv" --output "${OUTPUT}/timeseries.json" \
 --plant-id reunion-public --asset-id paired-inverter --observation-id training-window-smoke \
 --data-kind public_research --device cpu
"$PYTHON_BIN" src/competition_infer.py --task classification \
 --input "${INPUTS}/module.png" --output "${OUTPUT}/classification.json" \
 --plant-id pvf10-public --asset-id unverified-module --observation-id pvf10-smoke \
 --observed-at 2026-10-21T10:00:00Z --data-kind synthetic_demo --device cpu
"$DETECT_PYTHON" src/competition_infer.py --task detection \
 --input "${INPUTS}/inspection.png" --output "${OUTPUT}/detection.json" \
 --plant-id pvhsd-public --asset-id unverified-array --observation-id pvhsd-smoke \
 --observed-at 2026-10-21T10:00:00Z --data-kind synthetic_demo --device cpu
