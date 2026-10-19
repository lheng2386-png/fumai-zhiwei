"""Input-quality gate for La Reunion style 14-channel, 30-minute windows.

An online caller must preserve observation provenance before any imputation.
The gate abstains when recent values are absent instead of emitting a normal
fault probability from mean-filled signals. It is not a trained fault model.
"""

import numpy as np


CHANNELS = 14
MINUTES = 30
RECENT_MINUTES = 5


def assess_windows(x, observed_mask):
    """Return one status per window; `observed_mask=True` means measured.

    Missing provenance is an error: a standardized zero may be either a valid
    mean observation or a mean-imputed missing observation.
    """
    values = np.asarray(x)
    if values.ndim != 3 or values.shape[1:] != (CHANNELS, MINUTES):
        raise ValueError("Expected input with shape (windows, 14, 30)")
    if observed_mask is None:
        raise ValueError("An explicit observed_mask is required before imputation")
    observed = np.asarray(observed_mask)
    if observed.shape != values.shape or observed.dtype != np.bool_:
        raise ValueError("observed_mask must be a boolean array with the input shape")
    status = []
    for window, mask in zip(values, observed):
        if not np.isfinite(window).all():
            status.append("abstain_nonfinite")
        elif not mask[:, -RECENT_MINUTES:].all():
            status.append("abstain_recent_missing")
        elif not mask.all():
            status.append("abstain_history_missing")
        else:
            status.append("ready")
    return status


def safe_scores(x, observed_mask, score_fn):
    """Score ready windows only; return null for all abstentions.

    score_fn receives only accepted windows and must return equal-length
    fault probabilities. No missing window may be silently scored as healthy.
    """
    statuses = assess_windows(x, observed_mask)
    accepted = np.asarray([value == "ready" for value in statuses])
    output = [{"status": value, "fault_probability": None} for value in statuses]
    if np.any(accepted):
        probabilities = np.asarray(score_fn(np.asarray(x)[accepted]), dtype=float)
        if probabilities.shape != (int(accepted.sum()),) or not np.isfinite(probabilities).all():
            raise ValueError("score_fn returned invalid probabilities")
        if np.any((probabilities < 0) | (probabilities > 1)):
            raise ValueError("score_fn probabilities must be in [0, 1]")
        for index, probability in zip(np.flatnonzero(accepted), probabilities):
            output[int(index)]["fault_probability"] = float(probability)
    return output
