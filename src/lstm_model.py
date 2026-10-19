"""Bidirectional LSTM baseline for short multivariate PV windows."""

import torch
from torch import nn


class LSTMPV(nn.Module):
    def __init__(self, channels=14, width=64, layers=2, classes=2):
        super().__init__()
        self.input = nn.Linear(channels, width)
        self.encoder = nn.LSTM(width, width, num_layers=layers, batch_first=True,
                               bidirectional=True, dropout=.1 if layers > 1 else 0)
        self.attention = nn.Linear(width * 2, 1)
        self.statistics = nn.Sequential(nn.Linear(channels * 3, width),
                                        nn.LayerNorm(width), nn.GELU())
        self.classifier = nn.Sequential(nn.Linear(width * 5, width * 2), nn.GELU(),
                                        nn.Dropout(.2), nn.Linear(width * 2, classes))

    def forward(self, x):
        sequence = self.input(x.transpose(1, 2))
        encoded, _ = self.encoder(sequence)
        weights = self.attention(encoded).softmax(dim=1)
        attended = (encoded * weights).sum(dim=1)
        stats = self.statistics(torch.cat((x.mean(-1), x.std(-1, correction=0),
                                           x[:, :, -1]), dim=1))
        return self.classifier(torch.cat((attended, encoded[:, -1], stats), dim=1))
