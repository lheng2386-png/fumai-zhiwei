#!/usr/bin/env bash
# Research GPU pipeline: fixed-epoch domain adaptation, then three matched fine-tunes.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src
PYTHON_BIN="${PYTHON_BIN:-python3}"
pretrain="reports/curated_pv_simsiam_a10_v1"
if [[ ! -f "$pretrain/results.json" ]]; then
  resume=()
  if [[ -f "$pretrain/last.pt" ]]; then resume=(--resume); fi
  "$PYTHON_BIN" -u src/pretrain_pv_simsiam.py \
    --data data/processed/curated_pv_ssl_jpeg95_v1 --output "$pretrain" \
    --epochs 20 --batch-size 64 --workers 4 --seed 2026 "${resume[@]}"
fi
for seed in 2026 2027 2028; do
  output="reports/pvf10_ssl_transfer_a10_v1/seed${seed}"
  if [[ -f "$output/results.json" ]]; then continue; fi
  "$PYTHON_BIN" -u src/train_pvf10_transfer.py \
    --architecture convnext_tiny --device cuda --input-size 224 \
    --augmentation robust --binary-loss-weight 0 --ema-decay 0 \
    --backbone-init "$pretrain/backbone.pt" --output "$output" \
    --seed "$seed" --epochs 25 --patience 6 --batch-size 64
 done

if [[ ! -f reports/pvf10_ssl_transfer_comparison_a10_v1.json ]]; then
  "$PYTHON_BIN" -u src/compare_pvf10_adaptation.py \
    --reference reports/pvf10_robust_a10_v1 \
    --adapted reports/pvf10_ssl_transfer_a10_v1 \
    --output reports/pvf10_ssl_transfer_comparison_a10_v1.json
fi
