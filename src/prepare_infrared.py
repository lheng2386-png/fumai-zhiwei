"""Download original Raptor Maps data and audit exact decoded-pixel duplicates.

No archive extraction is needed. Published image labels are retained; no
synthetic labels or defect bounding boxes are created.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
from pathlib import Path
from urllib.request import urlopen
import zipfile

import numpy as np
from PIL import Image
from sklearn.model_selection import StratifiedGroupKFold

SOURCE = 'https://github.com/RaptorMaps/InfraredSolarModules'
BASE = 'https://raw.githubusercontent.com/RaptorMaps/InfraredSolarModules/master/'


def download(url, path):
    if path.exists():
        return
    temporary = path.with_suffix(path.suffix+'.part')
    with urlopen(url, timeout=60) as response, temporary.open('wb') as target:
        while block := response.read(1024*1024):
            target.write(block)
    temporary.replace(path)


def partition(labels, groups, seed):
    """60/20/20 split; exact duplicate groups never cross partitions."""
    labels, groups = np.asarray(labels), np.asarray(groups)
    outer = StratifiedGroupKFold(n_splits=5, shuffle=True, random_state=seed)
    development, test = next(outer.split(np.zeros(len(labels)), labels, groups))
    inner = StratifiedGroupKFold(n_splits=4, shuffle=True, random_state=seed+1)
    train_local, val_local = next(inner.split(np.zeros(len(development)), labels[development], groups[development]))
    split = np.full(len(labels), 'test', dtype='<U10')
    split[development[train_local]] = 'train'
    split[development[val_local]] = 'validation'
    for a, b in [('train', 'validation'), ('train', 'test'), ('validation', 'test')]:
        if set(groups[split == a]) & set(groups[split == b]):
            raise AssertionError('Duplicate group leakage')
    return split


def prepare(directory, seed=2026):
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory/'dataset.zip'
    download(BASE+'2020-02-14_InfraredSolarModules.zip', archive)
    download(BASE+'LICENSE', directory/'LICENSE.upstream')
    download(BASE+'README.md', directory/'README.upstream.md')
    rows, images = [], []
    with zipfile.ZipFile(archive) as data:
        metadata_name = next(n for n in data.namelist() if n.endswith('/module_metadata.json'))
        prefix = metadata_name.rsplit('/', 1)[0]+'/'
        metadata = json.loads(data.read(metadata_name))
        for key, record in sorted(metadata.items(), key=lambda item: int(item[0])):
            relative = record['image_filepath']
            if not relative.startswith('images/') or '..' in Path(relative).parts:
                raise ValueError('Unexpected image path')
            with Image.open(io.BytesIO(data.read(prefix+relative))) as image:
                pixels = np.asarray(image.convert('L')).copy()
            if pixels.shape != (40, 24):
                raise ValueError(f'Unexpected image size: {pixels.shape}')
            digest = hashlib.sha256(pixels.tobytes()).hexdigest()
            rows.append({'image_id': key, 'label': record['anomaly_class'], 'pixel_sha256': digest})
            images.append(pixels)
    classes = sorted({r['label'] for r in rows})
    group_labels = defaultdict(set)
    for row in rows:
        group_labels[row['pixel_sha256']].add(row['label'])
    conflicts = {key for key, labels in group_labels.items() if len(labels) > 1}
    clean = [i for i, row in enumerate(rows) if row['pixel_sha256'] not in conflicts]
    labels = np.array([classes.index(rows[i]['label']) for i in clean])
    groups = [rows[i]['pixel_sha256'] for i in clean]
    splits = partition(labels, groups, seed)
    for row in rows:
        row['split'] = 'quarantine'
    for i, split in zip(clean, splits):
        rows[i]['split'] = str(split)
    with (directory/'image_manifest.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    np.savez_compressed(directory/'prepared.npz', images=np.stack(images)[clean], labels=labels, split=splits,
                        image_ids=np.array([rows[i]['image_id'] for i in clean]), classes=np.array(classes))
    report = {'source': SOURCE, 'download_url': BASE+'2020-02-14_InfraredSolarModules.zip',
              'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
              'license': 'Upstream repository MIT; original license retained in LICENSE.upstream',
              'published_records': len(rows), 'usable_records': len(clean), 'classes': classes,
              'duplicate_extra_records': len(rows)-len(group_labels),
              'conflicting_duplicate_groups': len(conflicts), 'quarantined_records': len(rows)-len(clean),
              'split': '60/20/20 stratified exact-pixel duplicate groups, seed '+str(seed),
              'counts': {split: dict(Counter(row['label'] for row in rows if row['split'] == split))
                         for split in ['train', 'validation', 'test', 'quarantine']},
              'limitations': ['Image-level classification only: no boxes or segmentation masks.',
                              '24x40 grayscale images are not radiometric temperature measurements.',
                              'No plant/module/flight identifiers verified: cannot assert independent-site testing.',
                              'Exact duplicate grouping does not eliminate all near duplicates.']}
    (directory/'manifest.json').write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parents[1]/'data/raw/infrared_solar_modules')
    parser.add_argument('--seed', type=int, default=2026)
    args = parser.parse_args()
    prepare(args.directory, args.seed)
