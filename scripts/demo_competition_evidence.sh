#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON_BIN="${PYTHON_BIN:-python3}"
OUTPUT="${1:-reports/competition_evidence_demo}"
for scenario in corroborated conflict quality_blocked vision_only different_asset; do
 "$PYTHON_BIN" src/competition_evidence.py \
  --input "examples/competition/${scenario}.json" --output "${OUTPUT}/${scenario}.json" \
  --plant-id p1 --asset-id a1 --as-of 2026-10-21T10:10:00Z
done
