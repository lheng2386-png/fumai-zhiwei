"""Multiscale temporal encoder with an explicit electrical-statistics branch.

An experimental architecture, not a claim of state-of-the-art performance.
"""
import torch
from torch import nn


class MultiscaleBlock(nn.Module):
    def __init__(self, width):
        super().__init__()
        self.norm = nn.GroupNorm(8, width)
        self.branches = nn.ModuleList([
            nn.Sequential(nn.Conv1d(width, width, k, padding=k//2, groups=width),
                          nn.Conv1d(width, width//2, 1), nn.GELU())
            for k in (3, 7, 15)
        ])
        self.mix = nn.Sequential(nn.Conv1d(width//2*3, width*2, 1), nn.GELU(),
                                 nn.Dropout(.1), nn.Conv1d(width*2, width, 1))

    def forward(self, x):
        z = self.norm(x)
        return x + self.mix(torch.cat([branch(z) for branch in self.branches], dim=1))


class MultiscalePV(nn.Module):
    def __init__(self, channels=13, width=128, depth=4, classes=5):
        super().__init__()
        if width % 8 or depth < 1:
            raise ValueError('width must be divisible by 8; depth must be positive')
        self.stem = nn.Conv1d(channels, width, 1)
        self.blocks = nn.Sequential(*[MultiscaleBlock(width) for _ in range(depth)])
        self.attention = nn.Conv1d(width, 1, 1)
        self.decoder = nn.Sequential(nn.Conv1d(width, width, 1), nn.GELU(),
                                     nn.Conv1d(width, channels, 1))
        self.statistics = nn.Sequential(nn.Linear(channels*3, width), nn.LayerNorm(width), nn.GELU())
        self.classifier = nn.Sequential(nn.Linear(width*4, width*2), nn.GELU(),
                                        nn.Dropout(.2), nn.Linear(width*2, classes))

    def forward(self, x, reconstruct=False):
        z = self.blocks(self.stem(x))
        if reconstruct:
            return self.decoder(z)
        attended = (z*self.attention(z).softmax(dim=-1)).sum(dim=-1)
        statistics = self.statistics(torch.cat([x.mean(-1), x.std(-1, correction=0), x[:, :, -1]], dim=1))
        return self.classifier(torch.cat([attended, z.mean(-1), z[:, :, -1], statistics], dim=1))
