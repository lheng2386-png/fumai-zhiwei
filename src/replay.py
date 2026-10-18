"""Replay saved power models with peer evidence and auditable events."""
import argparse
import hashlib
import json
from pathlib import Path
import joblib
import pandas as pd
from threadpoolctl import threadpool_limits
from power_data import load_power_frame
from events import screen_events
from event_report import render_report
from review import latest

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            result.update(chunk)
    return result.hexdigest()


def replay(data, model_path, output, start, end, db=None):
    if pd.Timestamp(start) < pd.Timestamp('2023-01-01') or pd.Timestamp(end) <= pd.Timestamp(start):
        raise ValueError('Replay requires a nonempty held-out range starting in 2023 or later')
    # Load only locally generated, trusted joblib files.
    bundle = joblib.load(model_path)
    d, channels, features, meta = load_power_frame(data)
    if features != bundle['features']:
        raise ValueError('Feature contract differs from saved model')
    d = d[d.measured_on.ge(start) & d.measured_on.lt(end)].copy()
    if d.empty:
        raise ValueError('No valid daylight observations in this range')
    parts = []
    for channel, entry in bundle['models'].items():
        if channel not in channels:
            raise ValueError(f'Missing trained channel: {channel}')
        actual = d[channel].where(d[channel].between(0,35))
        parts.append(pd.DataFrame({
            'time': d.measured_on, 'inverter': channel.split('_ac_power')[0],
            # Site-specific 2107 cohort assumption, not a generic auto-grouping rule.
            'peer_group': '2107_ac_reference_ratio', 'actual_kw': actual,
            'expected_kw': entry['model'].predict(d[features]),
            'threshold_kw': entry['threshold_kw']}))
    if not parts:
        raise ValueError('Model bundle contains no devices')
    samples, events = screen_events(pd.concat(parts, ignore_index=True))
    output.mkdir(parents=True, exist_ok=True)
    if db:
        reviews = latest(db).rename(columns={'status': 'human_status'})
        events = events.merge(reviews, on='event_id', how='left', validate='one_to_one')
        events['review_status'] = events.human_status.fillna(events.review_status)
        events['note'] = events.note.fillna('')
    samples.to_csv(output/'evidence.csv', index=False)
    events.to_csv(output/'events.csv', index=False)
    summary = {'devices': len(parts), 'daylight_timestamps': len(d), 'events': len(events),
               'evidence_counts': events.evidence.value_counts().to_dict(),
               'timezone': meta['System']['timezone_code'], 'start_inclusive': start,
               'end_exclusive': end, 'model_sha256': digest(model_path),
               'input_sha256': {name: digest(data/name) for name in (
                   '2107_electrical_data.csv','2107_irradiance_data.csv',
                   '2107_environment_data.csv','2107_system_metadata.json')},
               'rules': {'min_samples':3, 'cadence_minutes':5, 'min_other_peers':5, 'peer_margin':.20},
               'limitations': 'Unlabelled screening; no precision/recall. Peer cohort compatibility not independently validated.'}
    (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    render_report(events, {'设备':len(parts), '日间时刻':len(d), '候选事件':len(events)}, output/'index.html')
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--data-dir', type=Path, default=Path('data/input'))
    p.add_argument('--model', type=Path, default=ROOT/'models/power_reference_2107.joblib')
    p.add_argument('--output', type=Path, default=ROOT/'reports/replay')
    p.add_argument('--start', default='2023-01-01')
    p.add_argument('--end', default='2024-01-01')
    p.add_argument('--reviews-db', type=Path)
    a = p.parse_args()
    with threadpool_limits(limits=2):
        print(json.dumps(replay(a.data_dir,a.model,a.output,a.start,a.end,a.reviews_db),ensure_ascii=False,indent=2))
