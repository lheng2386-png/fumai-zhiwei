#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
python_bin="${PYTHON_BIN:-python3}"
archive="data/raw/pv_hsd_2025/PV-HSD-2025.zip"
prepared="data/processed/pv_hsd_grouped_v1"
output="reports/pv_hsd_yolo26s_cuda_v1"

printf '%s  %s\n' \
  '3fac0ae8e3e345e9f3d1c45afb4251530f79bfda8d772aacd708799a32416c32' \
  "$archive" | sha256sum --check --status

if [[ ! -e "$prepared" ]]; then
  PYTHONPATH=src "$python_bin" src/prepare_pv_hsd.py \
    --archive "$archive" --output "$prepared"
fi

"$python_bin" src/train_pv_hsd_yolo.py train \
  --data "$prepared" --output "$output" \
  --pretrained yolo26s.pt --device cuda \
  --epochs 80 --patience 20 --imgsz 640 --batch-size 8 --workers 4
