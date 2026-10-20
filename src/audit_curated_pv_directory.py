"""Audit curated thermal ZIP directory names without loading images or caches.

Filename collisions flag possible source overlap, not confirmed pixel duplicates.
Zero-byte label files are not proof of a healthy, fully annotated image.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import zipfile


def audit(entries):
    names, examples = defaultdict(set), defaultdict(list)
    images, labels, empty = 0, 0, Counter()
    for row in entries:
        path = row['path']
        parts = path.split('/')
        if len(parts) < 4 or parts[0] != 'Common_data' or parts[1] not in ('train', 'valid', 'test'):
            continue
        split = parts[1]
        if path.lower().endswith('.jpg'):
            images += 1
            normalized = re.sub(r'\.rf\.[0-9a-f]+$', '', Path(path).stem)
            names[normalized].add(split)
            examples[normalized].append(path)
        elif path.lower().endswith('.txt'):
            labels += 1
            if row['bytes'] == 0:
                empty[split] += 1
    flagged = sorted(name for name, splits in names.items() if len(splits) > 1)
    return {'status': 'Filename-only audit; pixel duplicates and healthy labels unverified',
            'image_count': images, 'label_count': labels,
            'zero_byte_label_files_by_split': dict(empty),
            'unique_names_after_rf_hash_removal': len(names),
            'normalized_names_crossing_splits': len(flagged),
            'examples': {name: examples[name] for name in flagged[:10]},
            'limitations': ['Filename reuse can cause false overlap flags; verify decoded pixels and original source IDs.',
                            'Augmented derivatives and resized copies require near-duplicate checks.',
                            'Empty labels do not prove a healthy image or complete negative annotation.',
                            'Cached training .cache files are not loaded.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--directory-json', type=Path, help='Saved ZIP entries with path and bytes fields')
    source.add_argument('--zip', type=Path, help='Optional full local archive; read central directory only')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.directory_json:
        entries = json.loads(args.directory_json.read_text())['entries']
    else:
        with zipfile.ZipFile(args.zip) as archive:
            entries = [{'path': info.filename, 'bytes': info.file_size} for info in archive.infolist()]
    result = audit(entries)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('examples', 'limitations')}, indent=2))
