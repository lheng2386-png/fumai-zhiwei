"""Audit full curated thermal ZIP: checksum, decoded duplicates and YOLO labels.

No ZIP extraction or training cache deserialization. Does not repair labels or
invent field/site identities. Matching decoded pixels prove duplicate images;
filename reuse and matching dHash merely flag possible related derivatives.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import io
import json
import math
from pathlib import Path
import re
import time
import zipfile

import numpy as np
from PIL import Image
import yaml

PUBLISHED_MD5 = 'dd88e35595f4564b13233ad749dc4bcd'


def file_md5(path):
    digest = hashlib.md5()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def parse_boxes(text, classes):
    boxes, errors = [], []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split()
        try:
            values = [float(p) for p in parts]
        except ValueError:
            errors.append({'line': number, 'reason': 'nonnumeric'})
            continue
        if len(values) != 5 or not all(math.isfinite(v) for v in values):
            errors.append({'line': number, 'reason': 'not_five_finite_values'})
            continue
        label, cx, cy, width, height = values
        if label != int(label) or not 0 <= label < classes:
            errors.append({'line': number, 'reason': 'invalid_class'})
            continue
        if (not 0 <= cx <= 1 or not 0 <= cy <= 1 or not 0 < width <= 1 or not 0 < height <= 1
                or cx - width / 2 < -1e-5 or cy - height / 2 < -1e-5
                or cx + width / 2 > 1 + 1e-5 or cy + height / 2 > 1 + 1e-5):
            errors.append({'line': number, 'reason': 'invalid_or_outside_image_box'})
            continue
        boxes.append((int(label), cx, cy, width, height))
    return boxes, errors


def pixel_fingerprints(raw):
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        rgb = im.convert('RGB')
        pixels = np.asarray(rgb)
        digest = hashlib.sha256()
        digest.update(str(rgb.size).encode())
        digest.update(pixels.tobytes())
        small = np.asarray(rgb.convert('L').resize((9, 8), Image.Resampling.LANCZOS))
    bits = (small[:, 1:] > small[:, :-1]).ravel()
    dhash = sum(int(bit) << index for index, bit in enumerate(bits))
    return digest.hexdigest(), f'{dhash:016x}', list(pixels.shape)


def cross_split_groups(rows, field):
    groups = defaultdict(list)
    for row in rows:
        if row.get(field) is not None:
            groups[row[field]].append(row)
    return {key: values for key, values in groups.items()
            if len({r['split'] for r in values}) > 1}


def run(archive_path, output, expected_md5=PUBLISHED_MD5, reference=None):
    if output.exists():
        raise FileExistsError(output)
    digest = file_md5(archive_path)
    if digest != expected_md5:
        raise ValueError(f'Archive checksum mismatch: {digest}')
    output.mkdir(parents=True)
    started = time.monotonic()
    rows, labels_present, counts, errors = [], set(), Counter(), []
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        names_set = set(names)
        if len(names) != len(names_set):
            raise ValueError('Duplicate archive paths')
        # safe_load parses data only; bundled .cache files are never opened.
        config = yaml.safe_load(archive.read('Common_data/data.yaml'))
        classes = config['names']
        if isinstance(classes, dict):
            classes = [classes[i] for i in range(len(classes))]
        if not classes or not all(isinstance(x, str) for x in classes):
            raise ValueError('Invalid class map')
        for index, name in enumerate(sorted(n for n in names if n.lower().endswith('.jpg'))):
            parts = name.split('/')
            if len(parts) != 4 or parts[0] != 'Common_data' or parts[1] not in ('train', 'valid', 'test') or parts[2] != 'images':
                raise ValueError(f'Unexpected image path {name}')
            split = parts[1]
            row = {'path': name, 'split': split,
                   'normalized_name': re.sub(r'\.rf\.[0-9a-f]+$', '', Path(name).stem)}
            label_path = name.replace('/images/', '/labels/').rsplit('.', 1)[0] + '.txt'
            try:
                raw = archive.read(name)  # ZIP CRC checked by stdlib on each read.
                sha, dhash, shape = pixel_fingerprints(raw)
                row.update(pixel_sha256=sha, dhash=dhash, shape=shape)
            except Exception as exc:
                errors.append({'path': name, 'reason': 'image_read_failure', 'detail': str(exc)})
            if label_path not in names_set:
                errors.append({'path': name, 'reason': 'missing_label'})
            else:
                labels_present.add(label_path)
                try:
                    text = archive.read(label_path).decode('utf-8')
                    boxes, issues = parse_boxes(text, len(classes))
                    row['box_count'] = len(boxes)
                    # Comparison sorts rows but does not silently alter annotations.
                    row['label_signature'] = hashlib.sha256(json.dumps(sorted(boxes)).encode()).hexdigest()
                    for issue in issues:
                        errors.append({'path': label_path, **issue})
                    for box in boxes:
                        counts[(split, classes[box[0]])] += 1
                    row['empty_label'] = not text.strip()
                except Exception as exc:
                    errors.append({'path': label_path, 'reason': 'label_read_failure', 'detail': str(exc)})
            rows.append(row)
            if (index + 1) % 1000 == 0:
                print(json.dumps({'images_audited': index + 1, 'elapsed_seconds': time.monotonic() - started}), flush=True)
    reference_matches = {}
    reference_count = 0
    if reference is not None:
        manifest = reference / 'manifest.json'
        if not manifest.is_file():
            raise ValueError('Reference manifest required')
        by_sha = defaultdict(list)
        for row in rows:
            if row.get('pixel_sha256'):
                by_sha[row['pixel_sha256']].append(row['path'])
        for split in ('train', 'val'):
            for path in sorted((reference / split / 'images').iterdir()):
                if path.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
                    continue
                sha, _, _ = pixel_fingerprints(path.read_bytes())
                reference_count += 1
                if sha in by_sha:
                    reference_matches[str(path)] = by_sha[sha]
        # Original project test images are deliberately not opened.
    exact = cross_split_groups(rows, 'pixel_sha256')
    similar = cross_split_groups(rows, 'dhash')
    normalized = cross_split_groups(rows, 'normalized_name')
    conflicting = {key: group for key, group in exact.items()
                   if len({r.get('label_signature') for r in group}) > 1}
    orphan_labels = [n for n in names if n.endswith('.txt') and '/labels/' in n and n not in labels_present]
    report = {'source': 'https://zenodo.org/records/14644158', 'archive_md5': digest,
              'archive_bytes': archive_path.stat().st_size, 'classes': classes,
              'reference_train_val_images_read': reference_count,
              'reference_train_val_exact_pixel_matches': reference_matches,
              'reference_manifest_sha256': (hashlib.sha256((reference / 'manifest.json').read_bytes()).hexdigest() if reference is not None else None),
              'project_test_status': 'not opened',
              'images': len(rows), 'counts_by_split': dict(Counter(r['split'] for r in rows)),
              'empty_labels_by_split': dict(Counter(r['split'] for r in rows if r.get('empty_label'))),
              'boxes_by_split_class': {split: {label: counts[(split, label)] for label in classes}
                                      for split in ('train', 'valid', 'test')},
              'errors': errors, 'orphan_labels': orphan_labels,
              'exact_pixel_groups_crossing_splits': len(exact),
              'exact_pixel_groups_crossing_splits_with_conflicting_labels': len(conflicting),
              'same_dhash_groups_crossing_splits': len(similar),
              'normalized_name_groups_crossing_splits': len(normalized),
              'exact_pixel_overlap_examples': {key: [r['path'] for r in group] for key, group in list(exact.items())[:20]},
              'elapsed_seconds': time.monotonic() - started,
              'limitations': ['Same dHash or source filename alone is not proof of duplicate pixels.',
                              'No verified flight/plant IDs; no independent industrial split is established.',
                              'Empty labels do not prove healthy components or complete annotation.',
                              'Dataset aggregates five sources whose permissions and overlap with project data require verification.',
                              'No model training or performance evaluation is performed.']}
    with (output / 'images.jsonl').open('w') as stream:
        for row in rows:
            stream.write(json.dumps(row) + '\n')
    (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('images', 'exact_pixel_groups_crossing_splits', 'normalized_name_groups_crossing_splits', 'elapsed_seconds')}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reference', type=Path, help='Optional project dataset root; read train/val images only')
    args = parser.parse_args()
    run(args.archive, args.output, reference=args.reference)
