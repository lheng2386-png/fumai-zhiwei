"""Interpretable, unlabelled screening. Scores are evidence, not probabilities."""
import hashlib
import numpy as np
import pandas as pd


def screen_events(frame, min_samples=3, min_peers=5, peer_margin=.20):
    """Input uses local wall time, kW and per-device validation thresholds.

    Peer groups must be supplied explicitly for compatible devices. Compare
    actual/reference ratios, excluding the device itself from the median.
    Missing samples, invalid readings and group changes break event runs.
    """
    required = {'time', 'inverter', 'peer_group', 'actual_kw', 'expected_kw', 'threshold_kw'}
    if required - set(frame):
        raise ValueError(f'Missing columns: {sorted(required-set(frame))}')
    if min_samples < 1 or min_peers < 1 or not 0 < peer_margin < 1:
        raise ValueError('Invalid screening parameters')
    d = frame.copy()
    d = d.reset_index(drop=True)
    if d[['inverter', 'peer_group']].isna().any().any():
        raise ValueError('Device and peer group are required')
    d['time'] = pd.to_datetime(d.time, errors='raise')
    if d.time.isna().any() or d.duplicated(['time', 'inverter']).any():
        raise ValueError('Missing timestamps or duplicate time/device keys')
    for c in ['actual_kw', 'expected_kw', 'threshold_kw']:
        d[c] = pd.to_numeric(d[c], errors='coerce')
    valid = (np.isfinite(d[['actual_kw', 'expected_kw', 'threshold_kw']]).all(axis=1)
             & d.actual_kw.ge(0) & d.expected_kw.gt(5) & d.threshold_kw.gt(0))
    d['valid'] = valid
    d['ratio'] = (d.actual_kw / d.expected_kw).where(valid)
    d['peer_count'] = 0
    d['peer_ratio'] = np.nan
    # Each peer must have a valid, daylight reference at this exact timestamp.
    for _, group in d.groupby(['time', 'peer_group'], sort=False):
        ratios = group.ratio.to_numpy()
        count = np.isfinite(ratios).sum() - np.isfinite(ratios).astype(int)
        d.loc[group.index, 'peer_count'] = count
        if count.max() >= min_peers:
            others = np.tile(ratios, (len(ratios), 1))
            np.fill_diagonal(others, np.nan)
            median = np.full(len(ratios), np.nan)
            enough = count >= min_peers
            median[enough] = np.nanmedian(others[enough], axis=1)
            d.loc[group.index, 'peer_ratio'] = median
    d['residual_kw'] = d.expected_kw - d.actual_kw
    d['peer_gap'] = d.peer_ratio - d.ratio
    d['residual_flag'] = valid & d.residual_kw.gt(d.threshold_kw)
    d['peer_flag'] = valid & d.peer_gap.gt(peer_margin)
    d['evidence'] = np.select(
        [d.residual_flag & d.peer_flag,
         d.residual_flag & d.peer_ratio.isna(), d.residual_flag],
        ['device_specific_candidate', 'insufficient_peers', 'shared_or_model_candidate'],
        default='none')
    rows = []
    for inverter, g in d.sort_values('time').groupby('inverter'):
        breaks = (g.time.diff().ne(pd.Timedelta('5min'))
                  | g.residual_flag.ne(g.residual_flag.shift())
                  | g.peer_group.ne(g.peer_group.shift()))
        for _, run in g.groupby(breaks.cumsum()):
            if not run.residual_flag.iloc[0] or len(run) < min_samples:
                continue
            key = f'2107|{inverter}|{run.time.iloc[0].isoformat()}'
            peer_hits = int(run.peer_flag.sum())
            # Integrate only between observed endpoints. No extrapolated tail.
            shortfall = np.maximum(run.residual_kw.to_numpy(), 0)
            energy = float(np.trapezoid(shortfall, dx=5/60))
            rows.append({
                'event_id': hashlib.sha256(key.encode()).hexdigest()[:16],
                'inverter': inverter, 'peer_group': str(run.peer_group.iloc[0]),
                'start_local': str(run.time.iloc[0]),
                'last_sample_local': str(run.time.iloc[-1]),
                'samples': len(run), 'observed_span_minutes': (len(run)-1)*5,
                'mean_actual_kw': float(run.actual_kw.mean()),
                'mean_reference_kw': float(run.expected_kw.mean()),
                'reference_shortfall_kwh': energy,
                'peer_flag_samples': peer_hits,
                'min_peer_count': int(run.peer_count.min()),
                'mean_peer_gap': None if run.peer_gap.isna().all() else float(run.peer_gap.mean()),
                'evidence': ('device_specific_candidate' if peer_hits >= min_samples
                             else 'insufficient_peers' if run.peer_ratio.isna().all()
                             else 'shared_or_model_candidate'),
                'review_status': 'unreviewed',
            })
    events = pd.DataFrame(rows, columns=[
        'event_id','inverter','peer_group','start_local','last_sample_local','samples',
        'observed_span_minutes','mean_actual_kw','mean_reference_kw',
        'reference_shortfall_kwh','peer_flag_samples','min_peer_count',
        'mean_peer_gap','evidence','review_status'])
    return d, events
