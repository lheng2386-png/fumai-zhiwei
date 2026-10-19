#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
repo_dir="$PWD"
python_bin="${PYTHON_BIN:-python3}"
dfine_dir="$repo_dir/research/D-FINE"
prepared="data/processed/pv_hsd_grouped_v2"
config="reports/pv_hsd_dfine_s_cuda_v2.yml"
output="reports/pv_hsd_dfine_s_cuda_v2"

printf '%s  %s\n' \
  '48a6c8cc43eb57186843f752e2e8461ddd3326e0d3c575e71e6e960844683e89' \
  "$dfine_dir/dfine_s_coco.pth" | sha256sum --check --status

if [[ ! -f "$prepared/coco/instances_train.json" ]]; then
  "$python_bin" src/convert_pv_hsd_coco.py --data "$prepared"
fi

"$python_bin" src/configure_pv_hsd_dfine.py \
  --data "$prepared" --dfine "$dfine_dir" \
  --output "$config" --training-output "$output"

cd "$dfine_dir"
"$python_bin" train.py -c "$repo_dir/$config" \
  --use-amp --seed=2026 -t dfine_s_coco.pth
