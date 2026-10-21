"""Class-preserving photo-balanced replacement sampling for training only.

Within each class, equal expected mass per source photo. Across classes, keep
original expected image counts rather than oversampling rare classes again.
Source photos are acquisition groups, not verified independent flights/plants.
"""
import numpy as np
import torch


def photo_balanced_weights(labels, photos):
    y,groups=np.asarray(labels),np.asarray(photos)
    if (y.ndim!=1 or not len(y) or y.dtype.kind not in 'iu' or (y<0).any()
            or groups.shape!=y.shape or any(not isinstance(g,(str,np.str_)) or not g.strip() for g in groups)):
        raise ValueError('Expected training labels and nonempty source-photo strings')
    weights=np.empty(len(y),dtype=np.float64)
    for cls in np.unique(y):
        mask=y==cls
        _,inverse,counts=np.unique(groups[mask],return_inverse=True,return_counts=True)
        weights[mask]=mask.sum()/(len(counts)*counts[inverse])
    if not np.isfinite(weights).all() or (weights<=0).any():
        raise ValueError('Invalid sampling weights')
    return weights


def epoch_indices(n, device, weights=None):
    if n<1:raise ValueError('Expected positive epoch length')
    if weights is None:
        return torch.randperm(n,device=device)
    requested=torch.device(device)
    same_device=(weights.device.type==requested.type and (requested.index is None or weights.device.index==requested.index))
    if (weights.shape!=(n,) or not same_device
            or not torch.isfinite(weights).all() or (weights<=0).any()):
        raise ValueError('Sampling weights must be finite, positive and on the training device')
    return torch.multinomial(weights,n,replacement=True)
