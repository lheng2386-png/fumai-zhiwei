"""Conservative pretraining-only candidate list from the full thermal audit.

Never assigns a new validation/test split or fixes doubtful annotations. Source
validation/test components, invalid-box components, exact-pixel label conflicts,
and possible project-reference matches are quarantined as whole components.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path


def plan(rows, summary):
    paths = [r['path'] for r in rows]
    if len(paths) != len(set(paths)):
        raise ValueError('Duplicate audited paths')
    parents = list(range(len(rows)))
    def find(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parents[max(a, b)] = min(a, b)
    for field in ('pixel_sha256', 'normalized_name'):
        first = {}
        for index, row in enumerate(rows):
            key = row.get(field)
            if key is None:
                continue
            if key in first:
                union(index, first[key])
            else:
                first[key] = index
    by_pixel = defaultdict(list)
    for row in rows:
        if row.get('pixel_sha256'):
            by_pixel[row['pixel_sha256']].append(row)
    conflicts = {key for key, group in by_pixel.items()
                 if len({r.get('label_signature') for r in group}) > 1}
    bad_paths = set()
    for error in summary['errors']:
        p = error['path']
        if '/labels/' in p:
            p = p.replace('/labels/', '/images/').rsplit('.', 1)[0] + '.jpg'
        bad_paths.add(p)
    references = set()
    for key in ('reference_train_val_exact_pixel_matches', 'reference_train_val_same_dhash_candidate_examples'):
        for matches in summary.get(key, {}).values():
            references.update(matches)
    # Summary truncates dHash examples: refuse to pretend the omitted matches are cleared.
    if summary.get('reference_train_val_same_dhash_candidate_count', 0) > len(summary.get('reference_train_val_same_dhash_candidate_examples', {})):
        raise ValueError('Reference similarity list was truncated; audit the complete reference match list first')
    components = defaultdict(list)
    for index, row in enumerate(rows):
        components[find(index)].append(row)
    records, totals = [], Counter()
    for group in components.values():
        reasons = set()
        if any(r['split'] != 'train' for r in group):
            reasons.add('contains_publisher_validation_or_test')
        if any(r['path'] in bad_paths or not r.get('pixel_sha256') or 'label_signature' not in r for r in group):
            reasons.add('invalid_image_or_label')
        if any(r.get('pixel_sha256') in conflicts for r in group):
            reasons.add('exact_pixel_annotation_conflict')
        if any(r['path'] in references for r in group):
            reasons.add('possible_project_reference_overlap')
        if any(r.get('empty_label', False) or r.get('box_count', 0) == 0 for r in group):
            reasons.add('negative_or_empty_label_unverified')
        identity = hashlib.sha256('\n'.join(sorted(r['path'] for r in group)).encode()).hexdigest()
        # One representative per name/pixel connected component limits augmentation copies.
        selected = min(group, key=lambda r: r['path'])['path'] if not reasons else None
        for row in group:
            status = 'quarantined' if reasons else 'candidate' if row['path'] == selected else 'redundant_derivative'
            totals[status] += 1
            records.append({'path': row['path'], 'component': identity, 'status': status,
                            'reasons': sorted(reasons), 'representative': selected})
    return records, {'status_counts': dict(totals), 'components': len(components),
                     'protocol': 'Pretraining-only candidate plan; publisher val/test-related components excluded',
                     'limitations': ['Not a verified independent plant/flight split.',
                                     'Candidate labels and source permissions still require human/source review.',
                                     'Filename union is conservative and may quarantine unrelated images.',
                                     'No box clipping, label repair, dataset export or model training performed.']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    rows = [json.loads(line) for line in (args.audit / 'images.jsonl').read_text().splitlines()]
    summary = json.loads((args.audit / 'summary.json').read_text())
    records, report = plan(rows, summary)
    report['source_audit_summary_sha256'] = hashlib.sha256((args.audit / 'summary.json').read_bytes()).hexdigest()
    report['source_audit_rows_sha256'] = hashlib.sha256((args.audit / 'images.jsonl').read_bytes()).hexdigest()
    args.output.mkdir(parents=True)
    with (args.output / 'plan.jsonl').open('w') as stream:
        for record in records:
            stream.write(json.dumps(record) + '\n')
    (args.output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
