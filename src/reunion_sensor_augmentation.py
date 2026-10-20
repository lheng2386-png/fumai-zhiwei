"""Synthetic per-window measured-power/current gains in physical units.

Uses the fold's training-only scaler. Derived ratios are recomputed from changed
measurements; never independently jitter a ratio and its constituent channels.
"""
import math
import torch


class SensorGainAugmentation:
    def __init__(self, mean, scale, feature_indices, bound=.05):
        self.indices = tuple(feature_indices)
        self.positions = {source: local for local, source in enumerate(self.indices)}
        if (not math.isfinite(bound) or not 0 < bound <= .1
                or len(set(self.indices)) != len(self.indices)
                or not set((0, 1, 6, 7, 12, 13)).issubset(self.positions)):
            raise ValueError('Gain augmentation needs measured power/current and both derived ratios')
        self.mean = torch.as_tensor(mean, dtype=torch.float32)[list(self.indices)]
        self.scale = torch.as_tensor(scale, dtype=torch.float32)[list(self.indices)]
        if not torch.isfinite(self.mean).all() or not torch.isfinite(self.scale).all() or (self.scale <= 0).any():
            raise ValueError('Invalid training scaler')
        self.bound = bound

    def __call__(self, batch, gains=None):
        if batch.ndim != 3 or batch.shape[1] != len(self.indices):
            raise ValueError('Batch feature contract mismatch')
        mean = self.mean.to(batch.device)[None, :, None]
        scale = self.scale.to(batch.device)[None, :, None]
        raw = batch * scale + mean
        if gains is None:
            gains = torch.empty((len(batch), 4, 1), device=batch.device).uniform_(1-self.bound, 1+self.bound)
        if gains.shape != (len(batch), 4, 1) or not torch.isfinite(gains).all() or (gains <= 0).any():
            raise ValueError('Invalid sensor gains')
        for column, source in enumerate((0, 1, 6, 7)):
            raw[:, self.positions[source]] *= gains[:, column]
        raw[:, self.positions[12]] = raw[:, self.positions[0]] / (raw[:, self.positions[6]] + 50.)
        raw[:, self.positions[13]] = raw[:, self.positions[1]] / (raw[:, self.positions[7]] + .2)
        result = (raw - mean) / scale
        if not torch.isfinite(result).all():
            raise ValueError('Synthetic sensor gains produced nonfinite features')
        return result
