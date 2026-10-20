"""Build an unlabeled thermal cache from the conservative candidate plan only.

Candidate photos are for self-supervised domain adaptation, never an independent
validation set. Crops remain RGB renderings, not calibrated temperatures.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile

import numpy as np
from PIL import Image

from audit_curated_pv_pixels import PUBLISHED_MD5, file_md5


def prepare(archive, plan_dir, output, size=256, jpeg_quality=0):
    if output.exists():
        raise FileExistsError(output)
    if jpeg_quality not in (0, 95):
        raise ValueError('Use exact RGB cache (0) or documented JPEG quality 95')
    if size < 224:
        raise ValueError('Source cache must be at least 224 pixels')
    digest = file_md5(archive)
    if digest != PUBLISHED_MD5:
        raise ValueError('Publisher archive MD5 mismatch')
    plan_bytes = (plan_dir / 'plan.jsonl').read_bytes()
    records = [json.loads(line) for line in plan_bytes.splitlines()]
    candidates = sorted((r for r in records if r['status'] == 'candidate'), key=lambda r: r['path'])
    if not candidates or len({r['component'] for r in candidates}) != len(candidates):
        raise ValueError('Expected one candidate per source component')
    images, paths, components = [], [], []
    encoded, offsets = bytearray(), [0]
    with zipfile.ZipFile(archive) as source:
        for row in candidates:
            name = row['path']
            if (not name.startswith('Common_data/train/images/') or row['reasons']
                    or row['representative'] != name):
                raise ValueError('Candidate plan contains a forbidden/held-out image')
            with Image.open(io.BytesIO(source.read(name))) as im:
                image = im.convert('RGB').resize((size, size), Image.Resampling.BILINEAR)
                if jpeg_quality:
                    buffer = io.BytesIO()
                    image.save(buffer, format="JPEG", quality=jpeg_quality, subsampling=0)
                    encoded.extend(buffer.getvalue())
                    offsets.append(len(encoded))
                else:
                    images.append(np.asarray(image, dtype=np.uint8).copy())
            paths.append(name)
            components.append(row['component'])
    output.mkdir(parents=True)
    # Compression reduces transfer size; no defect labels are stored.
    contents = ({'jpeg_data': np.frombuffer(encoded, dtype=np.uint8),
                 'jpeg_offsets': np.asarray(offsets, dtype=np.int64)} if jpeg_quality
                else {'images': np.stack(images)})
    np.savez_compressed(output / 'unlabeled.npz', **contents,
                        source_path=np.array(paths), source_component=np.array(components))
    cache_hash = hashlib.sha256((output / 'unlabeled.npz').read_bytes()).hexdigest()
    report = {'source': 'https://zenodo.org/records/14644158', 'archive_md5': digest,
              'plan_sha256': hashlib.sha256(plan_bytes).hexdigest(),
              'plan_summary_sha256': hashlib.sha256((plan_dir / 'summary.json').read_bytes()).hexdigest(),
              'cache_sha256': cache_hash, 'images': len(paths), 'size': size,
              'image_encoding': 'jpeg_q95_444' if jpeg_quality else 'rgb_uint8_lossless_cache',
              'class_labels': 'not loaded or stored', 'method_scope': 'self-supervised adaptation only',
              'held_out_status': 'Publisher validation/test component images excluded by audited plan; project tests not opened',
              'limitations': ['Proxy filename/pixel groups are not verified plant or flight identities.',
                              'RGB resizing adds no measured thermal detail; overlays and palettes remain.',
                              'Cross-dataset crops/recolorings and source license lineage are not fully verified.',
                              'Compact JPEG quality 95 (if enabled) is lossy and only used in pretraining; downstream images unchanged.',
                              'Pretraining benefit must be tested by downstream paired comparisons.']}
    (output / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--size', type=int, default=256)
    parser.add_argument('--jpeg-quality', type=int, choices=(0, 95), default=0)
    args = parser.parse_args()
    prepare(args.archive, args.plan, args.output, args.size, args.jpeg_quality)
