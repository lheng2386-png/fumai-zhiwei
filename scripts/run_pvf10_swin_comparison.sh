#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PYTHON_BIN="${PYTHON_BIN:-python3}"
for seed in 2026 2027 2028; do
  output="reports/pvf10_swin_robust_a10_v1/seed${seed}"
  if [[ -f "$output/results.json" ]]; then continue; fi
  "$PYTHON_BIN" -u src/train_pvf10_transfer_swin_v1.py \
    --architecture swin_t --device cuda --input-size 224 \
    --augmentation robust --ema-decay 0 --binary-loss-weight 0 \
    --output "$output" --seed "$seed" --epochs 25 --patience 6 --batch-size 64
 done
