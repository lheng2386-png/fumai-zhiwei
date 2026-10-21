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


class RNNPV(nn.Module):
    """Plain recurrent network with the same pooling/statistics head as LSTMPV."""

    def __init__(self, channels=14, width=64, layers=2, classes=2, recent_steps=None, cell_type="rnn"):
        super().__init__()
        if recent_steps is not None and recent_steps < 1:
            raise ValueError("recent_steps must be positive")
        if cell_type not in ("rnn", "gru"):
            raise ValueError("Unsupported recurrent cell")
        self.recent_steps = recent_steps
        self.cell_type = cell_type
        self.input = nn.Linear(channels, width)
        if cell_type == "rnn":
            self.encoder = nn.RNN(width, width, num_layers=layers, batch_first=True,
                                  bidirectional=True, nonlinearity="tanh",
                                  dropout=.1 if layers > 1 else 0)
        else:
            self.encoder = nn.GRU(width, width, num_layers=layers, batch_first=True,
                                  bidirectional=True, dropout=.1 if layers > 1 else 0)
        self.attention = nn.Linear(width * 2, 1)
        self.statistics = nn.Sequential(nn.Linear(channels * 3, width),
                                        nn.LayerNorm(width), nn.GELU())
        self.classifier = nn.Sequential(nn.Linear(width * 5, width * 2), nn.GELU(),
                                        nn.Dropout(.2), nn.Linear(width * 2, classes))

    def forward(self, x):
        sequence = self.input(x.transpose(1, 2))
        encoded, _ = self.encoder(sequence)
        pooled = encoded[:, -self.recent_steps:] if self.recent_steps is not None else encoded
        stats_x = x[:, :, -self.recent_steps:] if self.recent_steps is not None else x
        weights = self.attention(pooled).softmax(dim=1)
        attended = (pooled * weights).sum(dim=1)
        stats = self.statistics(torch.cat((stats_x.mean(-1), stats_x.std(-1, correction=0),
                                           stats_x[:, :, -1]), dim=1))
        return self.classifier(torch.cat((attended, encoded[:, -1], stats), dim=1))


class GRUPV(RNNPV):
    """GRU gates with the existing full-window RNN attention/statistics readout."""
    def __init__(self, channels=14, width=64, layers=2, classes=2):
        super().__init__(channels,width,layers,classes,cell_type="gru")
