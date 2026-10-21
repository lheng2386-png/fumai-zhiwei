#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PYTHON_BIN="${PYTHON_BIN:-python3}"
for seed in 2026 2027 2028; do
 "$PYTHON_BIN" -u src/train_pvf10_transfer_photo_v1.py --architecture convnext_tiny \
  --device cuda --input-size 224 --augmentation robust --sampling photo_balanced \
  --seed "$seed" --epochs 25 --patience 6 --batch-size 64 \
  --output "reports/pvf10_photo_balanced_a10_v1/seed${seed}"
done
"$PYTHON_BIN" src/report_pvf10_loss_ablation.py --ablation sampling \
 --reference reports/pvf10_robust_a10_v1 --candidate reports/pvf10_photo_balanced_a10_v1 \
 --output reports/pvf10_photo_balanced_comparison_v1.json
