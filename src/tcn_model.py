"""Small residual temporal convolution network for PV windows."""

import torch
from torch import nn


class CausalResidualBlock(nn.Module):
    def __init__(self, width, dilation, dropout=0.1):
        super().__init__()
        kernel = 3
        self.padding = (kernel - 1) * dilation
        self.conv1 = nn.Conv1d(width, width, kernel, padding=self.padding, dilation=dilation)
        self.conv2 = nn.Conv1d(width, width, kernel, padding=self.padding, dilation=dilation)
        self.norm1 = nn.GroupNorm(1, width)
        self.norm2 = nn.GroupNorm(1, width)
        self.dropout = nn.Dropout(dropout)
        self.activation = nn.GELU()

    def _trim(self, x):
        return x[..., :-self.padding] if self.padding else x

    def forward(self, x):
        residual = x
        x = self._trim(self.conv1(x))
        x = self.activation(self.norm1(x))
        x = self.dropout(x)
        x = self._trim(self.conv2(x))
        x = self.activation(self.norm2(x))
        return residual + self.dropout(x)


class TCN_PV(nn.Module):
    """Residual dilated TCN with pooled, last-step, and summary features."""
    def __init__(self, channels=14, width=64, dilations=(1, 2, 4, 8), classes=2):
        super().__init__()
        self.input = nn.Conv1d(channels, width, 1)
        self.blocks = nn.Sequential(*(CausalResidualBlock(width, dilation) for dilation in dilations))
        self.statistics = nn.Sequential(nn.Linear(channels * 3, width), nn.LayerNorm(width), nn.GELU())
        self.classifier = nn.Sequential(nn.Linear(width * 3, width * 2), nn.GELU(),
                                        nn.Dropout(0.2), nn.Linear(width * 2, classes))

    def forward(self, x):
        z = self.blocks(self.input(x))
        pooled = z.mean(-1)
        stats = self.statistics(torch.cat([x.mean(-1), x.std(-1, correction=0), x[:, :, -1]], 1))
        return self.classifier(torch.cat([pooled, z[:, :, -1], stats], 1))
