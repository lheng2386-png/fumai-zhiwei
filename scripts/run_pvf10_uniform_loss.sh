#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PYTHON_BIN="${PYTHON_BIN:-python3}"
for seed in 2026 2027 2028; do
 "$PYTHON_BIN" -u src/train_pvf10_transfer_uniform_v1.py --architecture convnext_tiny \
  --device cuda --input-size 224 --augmentation robust --class-weight-power 0 \
  --seed "$seed" --epochs 25 --patience 6 --batch-size 64 \
  --output "reports/pvf10_uniform_loss_a10_v1/seed${seed}"
done
